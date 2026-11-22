"""Audio capture and streaming segmentation.

The segmenter is deliberately pure logic (no threads, no I/O) so it can be unit
tested with synthetic audio.  Capture back-ends only produce 16 kHz mono
``float32`` frames.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 320


@dataclass
class Segment:
    """A finalized chunk of speech, ready for ASR."""

    audio: np.ndarray  # float32 mono, 16 kHz
    start_s: float  # offset of speech start within the session
    end_s: float  # offset of speech end (excludes trailing silence)
    speech_s: float

    @property
    def duration_s(self) -> float:
        return len(self.audio) / SAMPLE_RATE


class Segmenter:
    """Energy-based voice activity segmentation with pre-roll.

    Design notes
    ------------
    * A short pre-roll keeps the first phoneme of a sentence from being cut.
    * Trailing silence is trimmed, which measurably improves Whisper output on
      short classroom utterances (fewer hallucinated tails).
    * Long monologues are force-cut at ``max_segment_s`` so latency stays
      bounded; the teacher's continuous speech is not lost.
    * A hard minimum keeps coughs/desk knocks out of the transcript.
    """

    def __init__(
        self,
        min_segment_s: float = 0.8,
        max_segment_s: float = 12.0,
        silence_ms: int = 600,
        vad_threshold: float = 0.006,
        preroll_ms: int = 300,
    ) -> None:
        self.min_segment_s = min_segment_s
        self.max_segment_s = max_segment_s
        self.silence_frames_needed = max(1, silence_ms // FRAME_MS)
        self.vad_threshold = vad_threshold
        self.preroll_frames = max(0, preroll_ms // FRAME_MS)

        self._preroll: deque[np.ndarray] = deque(maxlen=max(1, self.preroll_frames))
        self._frames: list[np.ndarray] = []
        self._in_speech = False
        self._silence_run = 0
        self._total_frames = 0
        self._speech_start_frame = 0
        self._noise_floor = 0.0
        self.dropped_segments = 0

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def rms(frame: np.ndarray) -> float:
        if frame.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(frame, dtype=np.float64)) + 1e-12))

    def _start_time(self) -> float:
        return self._speech_start_frame * FRAME_MS / 1000.0

    def _finish(self, end_frame: int) -> Segment | None:
        audio = np.concatenate(self._frames) if self._frames else np.zeros(0, dtype=np.float32)
        speech_s = (end_frame - self._speech_start_frame) * FRAME_MS / 1000.0
        seg = Segment(
            audio=audio,
            start_s=self._start_time(),
            end_s=end_frame * FRAME_MS / 1000.0,
            speech_s=max(0.0, speech_s),
        )
        self._frames = []
        self._in_speech = False
        self._silence_run = 0
        if seg.duration_s < self.min_segment_s or seg.speech_s < self.min_segment_s * 0.5:
            self.dropped_segments += 1
            return None
        return seg

    # -- public API ------------------------------------------------------
    def feed(self, samples: np.ndarray) -> list[Segment]:
        """Consume arbitrary-length float32 audio, return finalized segments."""
        out: list[Segment] = []
        if samples.dtype != np.float32:
            samples = samples.astype(np.float32, copy=False)
        # Split into whole frames; keep caller responsible for frame alignment.
        n = len(samples) // FRAME_SAMPLES
        for i in range(n):
            frame = samples[i * FRAME_SAMPLES : (i + 1) * FRAME_SAMPLES]
            seg = self._feed_frame(frame)
            if seg is not None:
                out.append(seg)
        return out

    def _feed_frame(self, frame: np.ndarray) -> Segment | None:
        rms = self.rms(frame)
        # Adaptive noise floor. Only quiet frames teach it, and it is capped at
        # ``vad_threshold``: if the very first frame is speech (a teacher who
        # starts mid-sentence) it must not raise the trigger above the speech
        # level, which would silence the whole room.
        if not self._in_speech and rms < self.vad_threshold:
            self._noise_floor = (
                0.9 * self._noise_floor + 0.1 * rms if self._noise_floor else rms
            )
        self._noise_floor = min(self._noise_floor, self.vad_threshold)
        trigger = max(self.vad_threshold, self._noise_floor * 2.5)
        frame_index = self._total_frames
        self._total_frames += 1

        if not self._in_speech:
            self._preroll.append(frame)
            if rms >= trigger:
                self._in_speech = True
                self._frames = list(self._preroll)
                self._silence_run = 0
                # Re-anchor the start to the actual pre-roll window length.
                self._speech_start_frame = frame_index - len(self._frames) + 1
            return None

        self._frames.append(frame)
        if rms < trigger * 0.8:
            self._silence_run += 1
        else:
            self._silence_run = 0

        duration = len(self._frames) * FRAME_MS / 1000.0
        if duration >= self.max_segment_s:
            end_frame = self._total_frames
            return self._finish(end_frame)
        if self._silence_run >= self.silence_frames_needed:
            # Trim trailing silence, keep ~150 ms for natural ending.
            keep = max(1, len(self._frames) - self._silence_run + 7)
            self._frames = self._frames[:keep]
            end_frame = self._speech_start_frame + keep
            return self._finish(end_frame)
        return None

    def flush(self) -> Segment | None:
        """Finalize any in-progress speech (used when a session stops)."""
        if not self._in_speech or not self._frames:
            return None
        return self._finish(self._total_frames)

    def partial_audio(self) -> np.ndarray | None:
        """Current in-progress speech buffer, for provisional captions."""
        if not self._in_speech or not self._frames:
            return None
        return np.concatenate(self._frames)


class WavSource:
    """Replay a WAV/FLAC file at real-time pace (testing and offline demos)."""

    def __init__(self, path: str | Path, speed: float = 1.0) -> None:
        self.path = Path(path)
        self.speed = max(0.1, speed)

    def frames(self, chunk_ms: int = 100) -> Iterator[np.ndarray]:
        import soundfile as sf

        with sf.SoundFile(str(self.path)) as fh:
            if fh.samplerate != SAMPLE_RATE:
                raise ValueError(f"{self.path}: expected {SAMPLE_RATE} Hz, got {fh.samplerate}")
            if fh.channels != 1:
                raise ValueError(f"{self.path}: expected mono, got {fh.channels} channels")
            block = SAMPLE_RATE * chunk_ms // 1000
            while True:
                data = fh.read(block, dtype="float32")
                if len(data) == 0:
                    return
                yield np.asarray(data, dtype=np.float32)
                time.sleep(chunk_ms / 1000.0 / self.speed)


class MicCapture:
    """Capture from ALSA via ``arecord`` (no PortAudio/sounddevice dependency).

    Raw PCM comes out of arecord's stdout as int16le mono 16 kHz; a reader
    thread converts to float32 frames and hands them to the callback.

    A daemon stderr drainer thread is started alongside the reader thread to
    prevent the arecord stderr pipe from filling the 64 KiB kernel buffer,
    which would cause arecord to block and silently stop producing audio.
    The drainer keeps the last few KB of stderr for shutdown diagnostics.

    Design constraint: the drainer must not use unbounded memory, must not
    block arecord on its own output, and must not delay stdout reading.
    """

    def __init__(self, device: str = "default", sample_rate: int = SAMPLE_RATE) -> None:
        self.device = device
        self.sample_rate = sample_rate
        self._proc: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.last_error: str | None = None
        self._stderr_tail = b""  # last few KB of stderr, kept for shutdown diagnostics

    def start(self, on_frames: Callable[[np.ndarray], None]) -> None:
        cmd = [
            "arecord", "-q", "-t", "raw", "-f", "S16_LE",
            "-r", str(self.sample_rate), "-c", "1", "-D", self.device,
        ]
        log.info("starting capture: %s", " ".join(cmd))
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0
        )
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(on_frames,), daemon=True)
        self._thread.start()
        # Drain stderr continuously so arecord never blocks on its own output.
        self._stderr_thread = threading.Thread(target=self._stderr_drainer, daemon=True)
        self._stderr_thread.start()

    def _stderr_drainer(self) -> None:
        """Continuously read stderr so the arecord pipe never fills.

        The Linux kernel gives arecord's stderr a 64 KiB buffer. If arecord
        writes more than 64 KiB without reading, it blocks in write(2) and
        stops producing PCM on stdout. This daemon thread consumes stderr data
        as soon as it appears, keeping the pipe bounded.

        A bounded tail (last 4 KiB) is retained so shutdown diagnostics
        still name the reason arecord exited.
        """
        while not self._stop.is_set():
            try:
                # Read up to 4 KiB — enough to stay ahead of the arecord
                # output rate but bounded so we never block for too long.
                chunk = self._proc.stderr.read(4096)
                if not chunk:
                    # stderr closed — process is ending
                    break
                self._stderr_tail = (self._stderr_tail + chunk)[-4096:]
            except Exception:  # best-effort; drainer dying is not fatal
                log.debug("stderr drainer exception", exc_info=True)
                break
        # Drain any remaining data after the loop exits.
        try:
            remaining = self._proc.stderr.read()
            if remaining:
                self._stderr_tail = (self._stderr_tail + remaining)[-4096:]
                log.debug("stderr drainer final %d bytes", len(remaining))
        except Exception:
            pass

    def _run(self, on_frames: Callable[[np.ndarray], None]) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        bytes_per_frame = 2
        block_frames = FRAME_SAMPLES * 5  # 100 ms
        while not self._stop.is_set():
            raw = self._proc.stdout.read(block_frames * bytes_per_frame)
            if not raw:
                # Prefer the drainer's retained tail: it captured stderr
                # continuously, so it has the exit reason even if the pipe
                # is already closed at this point.
                err = self._stderr_tail[-400:]
                if not err and self._proc.stderr is not None:
                    try:
                        err = self._proc.stderr.read(400) or b""
                    except Exception:  # best-effort diagnostics  # pragma: no cover
                        log.debug("could not read arecord stderr", exc_info=True)
                self.last_error = err.decode("utf-8", "replace") or "arecord exited"
                log.warning("capture ended: %s", self.last_error)
                return
            audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            on_frames(audio)

    def stop(self) -> None:
        self._stop.set()
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:  # pragma: no cover
                self._proc.kill()
        if self._thread is not None:
            self._thread.join(timeout=2)
        # The _stderr_thread is a daemon thread; it will terminate when the
        # process exits. No join needed — joining a daemon thread that may not
        # have started yet can hang.
