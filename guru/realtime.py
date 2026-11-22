"""Streaming primitives for real-time speech translation.

This module contains the pieces that turn Offline Guru's segment-at-a-time
pipeline into a continuous one: a rolling, timestamped audio window, a
LocalAgreement hypothesis buffer that decides which words are stable enough to
translate, a phrase builder that groups stable words into translatable units,
and a small latency recorder.

Attribution
-----------
LocalAgreement is adapted from WhisperLiveKit
(https://github.com/QuentinFuxa/WhisperLiveKit), file
whisperlivekit/local_agreement/online_asr.py, Copyright Quentin Fuxa,
licensed under the Apache License, Version 2.0.  The adaptation operates on this
project's own Word dataclass, compares words case- and punctuation-insensitively,
and adds flush_all() for end-of-speech.  See LICENSES.md for the full notice.

The window/overlap/pause-flush discipline and the translation flush policy are
inspired by PolyTalk (AGPL-3.0).  No PolyTalk code is copied: the
implementation below is original, written against the same requirements.

Nothing here loads a model, touches the network, or starts a thread.
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000

# Characters that carry no meaning for word comparison.
_EDGE_PUNCT = " \t\r\n.,!?;:…\"'()[]{}।؟-–—"
_TERMINAL = re.compile(r"[.!?।؟]\s*$")
_CLAUSE_END = re.compile(r"[,;:]\s*$")
_CONJUNCTIONS = {
    "and", "but", "because", "which", "that", "so", "while", "when", "if",
    "or", "then", "although", "whereas",
}


def normalize_word(text: str) -> str:
    """Comparison key for a word: no edge punctuation, case-folded."""
    return text.strip(_EDGE_PUNCT).casefold()


@dataclass(frozen=True)
class Word:
    """One recognised word with absolute timestamps (seconds)."""

    text: str
    start: float
    end: float
    prob: float = 0.0


@dataclass
class Phrase:
    """A group of stable words that is safe to translate as one unit."""

    text: str
    start: float
    end: float
    words: list[Word] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end - self.start)


# ---------------------------------------------------------------------------
# LocalAgreement-2
# ---------------------------------------------------------------------------
class LocalAgreement:
    """Commit only words that two consecutive hypotheses agree on.

    Three lists are kept, exactly as in the LocalAgreement-2 policy:

    * committed - words already handed to translation (kept for prompting),
    * buffer    - the previous hypothesis' uncommitted tail,
    * new       - the current hypothesis' uncommitted tail.

    commit() returns the longest common prefix of buffer and new.  A word whose
    model probability is at or above confidence is committed immediately even
    without agreement; that is what keeps first-caption latency at one decode
    instead of two on a slow CPU.  Set confidence=0 for strict LocalAgreement-2.
    """

    def __init__(self, confidence: float = 0.0, max_committed: int = 600) -> None:
        self.confidence = max(0.0, min(1.0, float(confidence)))
        self.max_committed = max(50, int(max_committed))
        self.committed: list[Word] = []
        self.buffer: list[Word] = []
        self.new: list[Word] = []
        self.last_committed_time = 0.0
        # True only when insert() actually produced a hypothesis. Without this,
        # a silent decode would wipe the buffer and break agreement.
        self._fresh = False

    # -- lifecycle -------------------------------------------------------
    def reset(self, offset: float = 0.0) -> None:
        self.committed.clear()
        self.buffer.clear()
        self.new.clear()
        self.last_committed_time = float(offset)
        self._fresh = False

    # -- input -----------------------------------------------------------
    def insert(self, words: Iterable[Word], offset: float = 0.0) -> None:
        """Insert a hypothesis, shifting its timestamps by offset seconds."""
        shifted = [
            Word(w.text, w.start + offset, w.end + offset, w.prob) for w in words
        ]
        # Ignore words that end before what we already committed.
        self.new = [w for w in shifted if w.start > self.last_committed_time - 0.1]
        self._fresh = bool(self.new)
        if not self.new or not self.committed:
            return
        # Drop the overlap with the committed tail (1..5 words).
        if abs(self.new[0].start - self.last_committed_time) < 1.0:
            max_n = min(len(self.committed), len(self.new), 5)
            for i in range(1, max_n + 1):
                tail = [normalize_word(w.text) for w in self.committed[-i:]]
                head = [normalize_word(w.text) for w in self.new[:i]]
                if tail == head:
                    del self.new[:i]
                    break

    # -- output ----------------------------------------------------------
    def commit(self) -> list[Word]:
        """Return newly stable words and advance the committed frontier."""
        if not self._fresh:
            return []  # no new hypothesis: keep the buffer intact
        out: list[Word] = []
        while self.new:
            current = self.new[0]
            if self.confidence and current.prob >= self.confidence:
                out.append(current)
                self.last_committed_time = current.end
                if self.buffer:
                    self.buffer.pop(0)
                self.new.pop(0)
            elif not self.buffer:
                break  # need a second hypothesis before agreeing
            elif normalize_word(current.text) == normalize_word(self.buffer[0].text):
                out.append(current)
                self.last_committed_time = current.end
                self.buffer.pop(0)
                self.new.pop(0)
            else:
                break  # disagreement: stop at the first unstable word
        self.buffer = self.new
        self.new = []
        self._fresh = False
        if out:
            self.committed.extend(out)
            if len(self.committed) > self.max_committed:
                del self.committed[: len(self.committed) - self.max_committed]
        return out

    def flush_all(self) -> list[Word]:
        """Commit everything still pending (end of speech)."""
        tail = list(self.buffer)
        # new is a superset tail of buffer: drop the overlapping prefix.
        if tail and self.new:
            max_n = min(len(tail), len(self.new), 5)
            for i in range(max_n, 0, -1):
                if [normalize_word(w.text) for w in tail[-i:]] == [
                    normalize_word(w.text) for w in self.new[:i]
                ]:
                    tail = tail[: len(tail) - i]
                    break
        tail.extend(self.new)
        self.buffer = []
        self.new = []
        self._fresh = False
        if tail:
            self.last_committed_time = tail[-1].end
            self.committed.extend(tail)
            if len(self.committed) > self.max_committed:
                del self.committed[: len(self.committed) - self.max_committed]
        return tail

    def pending_text(self) -> str:
        """Best current guess for the unstable tail (English only)."""
        words = self.new or self.buffer
        return " ".join(w.text.strip() for w in words if w.text.strip()).strip()

    def prompt(self, max_chars: int = 200) -> str:
        """Last committed words, for Whisper's initial_prompt."""
        if not self.committed or max_chars <= 0:
            return ""
        picked: list[str] = []
        used = 0
        for word in reversed(self.committed):
            text = word.text.strip()
            if not text:
                continue
            if used + len(text) + 1 > max_chars:
                break
            picked.append(text)
            used += len(text) + 1
        return " ".join(reversed(picked))


# ---------------------------------------------------------------------------
# Rolling audio window
# ---------------------------------------------------------------------------
class SpeechWindow:
    """A timestamped rolling audio buffer with energy VAD gating.

    Unlike audio.Segmenter this keeps the audio instead of emitting a finished
    segment: the streaming ASR loop needs to re-decode a window that grows while
    the teacher speaks, and to trim it once words are committed.
    """

    def __init__(
        self,
        vad_threshold: float = 0.006,
        preroll_ms: int = 300,
        silence_ms: int = 600,
        max_window_s: float = 15.0,
        context_s: float = 1.0,
    ) -> None:
        self.vad_threshold = float(vad_threshold)
        self.preroll_frames = max(0, int(preroll_ms) // FRAME_MS)
        self.silence_frames_needed = max(1, int(silence_ms) // FRAME_MS)
        self.max_window_s = max(2.0, float(max_window_s))
        self.context_s = max(0.0, float(context_s))

        self._buf = np.zeros(0, dtype=np.float32)
        self._buf_start = 0.0
        self._clock = 0.0  # absolute time of the next sample
        self._pending = np.zeros(0, dtype=np.float32)
        self._preroll: deque[tuple[float, np.ndarray]] = deque(
            maxlen=max(1, self.preroll_frames)
        )
        self._in_speech = False
        self._silence_run = 0
        self._speech_start = 0.0
        self._ended = False
        self._noise_floor = 0.0
        self.dropped_frames = 0

    # -- properties ------------------------------------------------------
    @property
    def in_speech(self) -> bool:
        return self._in_speech

    @property
    def start_time(self) -> float:
        return self._buf_start

    @property
    def end_time(self) -> float:
        return self._buf_start + len(self._buf) / SAMPLE_RATE

    @property
    def duration_s(self) -> float:
        return len(self._buf) / SAMPLE_RATE

    @property
    def speech_start(self) -> float:
        return self._speech_start

    def audio(self) -> np.ndarray:
        return self._buf

    def take_ended(self) -> bool:
        """Return True once after speech has just ended."""
        if self._ended:
            self._ended = False
            return True
        return False

    # -- input -----------------------------------------------------------
    @staticmethod
    def rms(frame: np.ndarray) -> float:
        if frame.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(frame, dtype=np.float64)) + 1e-12))

    def feed(self, samples: np.ndarray) -> tuple[bool, bool]:
        """Consume float32 audio; return (speech_started, speech_ended)."""
        if samples.dtype != np.float32:
            samples = samples.astype(np.float32, copy=False)
        if samples.size:
            self._pending = (
                np.concatenate([self._pending, samples])
                if self._pending.size
                else samples
            )
        started = ended = False
        n = len(self._pending) // FRAME_SAMPLES
        for i in range(n):
            frame = self._pending[i * FRAME_SAMPLES : (i + 1) * FRAME_SAMPLES]
            frame_start = self._clock
            self._clock += FRAME_MS / 1000.0
            s, e = self._feed_frame(frame, frame_start)
            started = started or s
            ended = ended or e
        if n:
            self._pending = self._pending[n * FRAME_SAMPLES :]
        return started, ended

    def _feed_frame(self, frame: np.ndarray, frame_start: float) -> tuple[bool, bool]:
        rms = self.rms(frame)
        if not self._in_speech and rms < self.vad_threshold:
            self._noise_floor = (
                0.9 * self._noise_floor + 0.1 * rms if self._noise_floor else rms
            )
        self._noise_floor = min(self._noise_floor, self.vad_threshold)
        trigger = max(self.vad_threshold, self._noise_floor * 2.5)

        if not self._in_speech:
            self._preroll.append((frame_start, frame))
            if rms < trigger:
                return False, False
            # Speech starts: seed the buffer with the pre-roll.
            frames = list(self._preroll)
            self._buf = np.concatenate([f for _, f in frames]) if frames else frame.copy()
            self._buf_start = frames[0][0] if frames else frame_start
            self._speech_start = frame_start
            self._in_speech = True
            self._silence_run = 0
            self._preroll.clear()
            return True, False

        self._buf = np.concatenate([self._buf, frame])
        if rms < trigger * 0.8:
            self._silence_run += 1
        else:
            self._silence_run = 0

        if self._silence_run >= self.silence_frames_needed:
            self._in_speech = False
            self._ended = True
            self._silence_run = 0
            return False, True
        self._cap_window()
        return False, False

    def _cap_window(self) -> None:
        max_samples = int(self.max_window_s * SAMPLE_RATE)
        if len(self._buf) > max_samples:
            drop = len(self._buf) - max_samples
            self._buf = self._buf[drop:]
            self._buf_start += drop / SAMPLE_RATE

    # -- maintenance -----------------------------------------------------
    def trim_to(self, time_s: float) -> None:
        """Drop audio before time_s - context_s (keeps decode context).

        The cut is clamped to the end of the buffer: a timestamp that runs past
        the audio we actually hold must never move the window's clock forward.
        """
        cut = max(self._buf_start, float(time_s) - self.context_s)
        cut = min(cut, self.end_time)
        n = int((cut - self._buf_start) * SAMPLE_RATE)
        if 0 < n < len(self._buf):
            self._buf = self._buf[n:]
            self._buf_start = cut

    def reset(self) -> None:
        self._buf = np.zeros(0, dtype=np.float32)
        self._buf_start = self._clock
        self._in_speech = False
        self._silence_run = 0
        self._ended = False
        self._preroll.clear()

    def rebase(self, frontier: float, keep_context: bool = True) -> float:
        """Drop audio before ``frontier`` but keep everything after it.

        ``reset()`` is only correct when nothing has been spoken since the last
        decode. In the streaming pipeline it is called at the end of an
        utterance, i.e. *after* a decode that took about a second, so the buffer
        usually holds audio that arrived while the model was running. Discarding
        it loses the beginning of the next sentence (and, when the teacher
        resumes during the decode, the whole of it).

        This keeps the tail and re-anchors the clock there. A slice of audio
        before the frontier is retained so the next decode has acoustic context;
        the agreement buffer drops words that were already committed, so the
        overlap cannot be committed twice.

        Returns the number of seconds of audio retained.
        """
        end = self.end_time
        cut = float(frontier)
        if keep_context:
            cut -= self.context_s
        cut = min(max(cut, self._buf_start), end)
        n = int(round((cut - self._buf_start) * SAMPLE_RATE))
        if n <= 0:
            pass  # the whole buffer is newer than the frontier: keep all of it
        elif n < len(self._buf):
            self._buf = self._buf[n:].copy()
        else:
            self._buf = np.zeros(0, dtype=np.float32)
        self._buf_start = cut
        # Audio that survived is un-decoded speech; keep the VAD in its current
        # state so it does not need a fresh trigger to re-enter speech.
        self._in_speech = bool(self._buf.size)
        self._silence_run = 0
        self._ended = False
        self._preroll.clear()
        return float(self._buf.size) / SAMPLE_RATE


# ---------------------------------------------------------------------------
# Phrase building
# ---------------------------------------------------------------------------
class PhraseBuilder:
    """Group stable words into translatable phrases.

    A phrase closes when any of these is true:

    * the text ends with sentence punctuation,
    * it reaches max_chars (split at the last clause boundary if possible),
    * it spans max_s seconds of speech,
    * it has been open for flush_age_s and holds at least min_chars.

    The last rule is what makes translation start while the teacher is still
    speaking instead of waiting for the end of the sentence.
    """

    def __init__(
        self,
        max_chars: int = 160,
        max_s: float = 8.0,
        min_chars: int = 12,
        flush_age_s: float = 1.5,
    ) -> None:
        self.max_chars = max(20, int(max_chars))
        self.max_s = max(1.0, float(max_s))
        self.min_chars = max(1, int(min_chars))
        self.flush_age_s = max(0.0, float(flush_age_s))
        self._words: list[Word] = []
        self._opened_at: float | None = None

    def __len__(self) -> int:
        return len(self._words)

    def text(self) -> str:
        return " ".join(w.text.strip() for w in self._words if w.text.strip()).strip()

    def add(self, words: Iterable[Word], now: float | None = None) -> list[Phrase]:
        now = time.monotonic() if now is None else now
        out: list[Phrase] = []
        for word in words:
            if not word.text.strip():
                continue
            if not self._words:
                self._opened_at = now
            self._words.append(word)
            text = self.text()
            if _TERMINAL.search(text):
                out.append(self._close())
            elif len(text) >= self.max_chars:
                out.extend(self._close_at_clause(now))
            elif self._words[-1].end - self._words[0].start >= self.max_s:
                out.append(self._close())
        return out

    def tick(self, now: float | None = None) -> list[Phrase]:
        """Close an ageing phrase so translation does not wait for a pause."""
        now = time.monotonic() if now is None else now
        if not self._words or self._opened_at is None:
            return []
        if len(self.text()) < self.min_chars:
            return []
        if now - self._opened_at < self.flush_age_s:
            return []
        return [self._close()]

    def flush(self) -> Phrase | None:
        if not self._words:
            return None
        return self._close()

    def reset(self) -> None:
        self._words = []
        self._opened_at = None

    # -- internals -------------------------------------------------------
    def _close(self) -> Phrase:
        words = self._words
        phrase = Phrase(
            text=self.text(),
            start=words[0].start if words else 0.0,
            end=words[-1].end if words else 0.0,
            words=list(words),
        )
        self._words = []
        self._opened_at = None
        return phrase

    def _close_at_clause(self, now: float) -> list[Phrase]:
        """Close at the last clause boundary, keeping the remainder open."""
        split = self._clause_split()
        if split <= 0 or split >= len(self._words):
            return [self._close()]
        head = self._words[:split]
        tail = self._words[split:]
        phrase = Phrase(
            text=" ".join(w.text.strip() for w in head if w.text.strip()).strip(),
            start=head[0].start,
            end=head[-1].end,
            words=list(head),
        )
        self._words = tail
        self._opened_at = now
        return [phrase]

    def _clause_split(self) -> int:
        for i in range(len(self._words) - 1, 2, -1):
            if _CLAUSE_END.search(self._words[i - 1].text):
                return i
            if normalize_word(self._words[i].text) in _CONJUNCTIONS:
                return i
        return len(self._words)


# ---------------------------------------------------------------------------
# Latency bookkeeping
# ---------------------------------------------------------------------------
class LatencyRecorder:
    """Small rolling recorder for per-stage milliseconds."""

    def __init__(self, window: int = 200) -> None:
        self._values: dict[str, deque[float]] = {}
        self._window = max(10, int(window))

    def add(self, name: str, value_ms: float) -> None:
        bucket = self._values.setdefault(name, deque(maxlen=self._window))
        bucket.append(float(value_ms))

    def last(self, name: str) -> float | None:
        bucket = self._values.get(name)
        return bucket[-1] if bucket else None

    def summary(self, name: str) -> dict[str, float]:
        bucket = self._values.get(name)
        if not bucket:
            return {"n": 0}
        values = sorted(bucket)
        n = len(values)

        def pct(p: float) -> float:
            return round(values[min(n - 1, max(0, round((p / 100) * (n - 1))))], 1)

        return {
            "n": n,
            "p50": pct(50),
            "p95": pct(95),
            "max": round(values[-1], 1),
        }

    def names(self) -> list[str]:
        return sorted(self._values)
