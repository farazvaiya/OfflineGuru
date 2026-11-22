"""Streaming lecture pipeline: capture -> ASR -> translation -> TTS.

Two modes
---------
**Streaming (default).** A rolling, timestamped audio window is decoded
continuously while the teacher speaks.  A LocalAgreement buffer commits only
words that are stable, a phrase builder groups them into translatable units, and
each phrase is translated and voiced as soon as it closes.  Captions therefore
appear during a sentence, not after it.

**Segment (fallback).** The original behaviour: an energy VAD segmenter emits a
finished segment after a pause, and that segment is decoded once.  Kept because
it is proven, it is what the existing tests exercise, and it is the right choice
when the CPU cannot sustain continuous decoding.

Concurrency model
-----------------
Three worker threads, never one per segment:

* **ASR thread** decodes the rolling window (streaming) or finalized segments
  (segment mode).
* **Enrichment thread** translates and publishes captions in final order.
* **TTS thread** synthesises audio, deliberately decoupled from text delivery so
  a slow voice engine never delays a Bangla caption.

The capture thread only feeds audio and sets an event.  Every queue is bounded,
so backpressure is observable (drop counters) instead of silently growing memory.

No thread ever calls the network. No model is loaded unless it is present on disk.
"""

from __future__ import annotations

import inspect
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .audio import SAMPLE_RATE, Segment, Segmenter, WavSource
from .bus import EventBus
from .bilingual import TurnLanguageRouter
from .config import Settings
from .realtime import (
    LatencyRecorder,
    LocalAgreement,
    Phrase,
    PhraseBuilder,
    SpeechWindow,
)
from .storage import Storage

log = logging.getLogger(__name__)


@dataclass
class PipelineStats:
    segments_finalized: int = 0
    segments_translated: int = 0
    segments_empty: int = 0
    asr_queue_drops: int = 0
    enrichment_queue_drops: int = 0
    tts_queue_drops: int = 0
    partials_emitted: int = 0
    # Streaming counters
    stream_decodes: int = 0
    stream_commits: int = 0
    stream_phrases: int = 0
    stream_partials: int = 0
    stream_backlog: int = 0
    language_turn_switches: int = 0
    queue_backlog_peak: int = 0
    playback_order_errors: int = 0
    audio_carried_s: float = 0.0
    tts_failures: int = 0
    translate_failures: int = 0
    first_caption_ms: float | None = None
    first_audio_ms: float | None = None
    started_at: float = 0.0
    last_error: str | None = None


class ClassroomPipeline:
    def __init__(
        self,
        settings: Settings,
        bus: EventBus,
        storage: Storage,
        asr: Any,
        translator: Any,
        tts: Any,
    ) -> None:
        self.settings = settings
        self.bus = bus
        self.storage = storage
        self.asr = asr
        self.translator = translator
        self.tts = tts

        self.session_id: str | None = None
        self.loop: Any = None
        self.status = "idle"  # idle | running | paused
        self.stats = PipelineStats()

        self._segmenter = self._new_segmenter()
        self._final_q: queue.Queue[tuple[Segment, float]] = queue.Queue(maxsize=8)
        self._partial_q: queue.Queue[tuple[np.ndarray, float]] = queue.Queue(maxsize=1)
        self._enrich_q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=12)
        # TTS is deliberately decoupled from text delivery: a slow voice engine
        # must never delay the Bangla caption on a student's screen.
        self._tts_q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=16)
        self._running = threading.Event()
        self._stopping = threading.Event()
        self._threads: list[threading.Thread] = []
        self._last_partial_at = 0.0
        self._last_partial_text = ""
        self._pause = threading.Event()
        self.audio_dir = (settings.resolve(settings.storage.data_dir) or Path("data")) / "audio"
        self._on_final: Callable[[dict[str, Any]], None] | None = None

        # Streaming state (built in start() so a restart begins clean).
        self.streaming = bool(getattr(settings.asr, "streaming", False))
        self._window: SpeechWindow | None = None
        self._agreement: LocalAgreement | None = None
        self._phrases: PhraseBuilder | None = None
        self._asr_wake = threading.Event()
        self._latency = LatencyRecorder()
        self._speech_started_at: float | None = None
        self._last_decode_end = 0.0
        self._decode_in_flight = False
        self._language_router = TurnLanguageRouter("en")
        self._auto_language: str | None = None
        self._auto_probed_at = 0.0

    # -- lifecycle -------------------------------------------------------
    def _new_segmenter(self) -> Segmenter:
        a = self.settings.asr
        return Segmenter(
            min_segment_s=a.min_segment_s,
            max_segment_s=a.max_segment_s,
            silence_ms=a.silence_ms,
            vad_threshold=a.vad_threshold,
            preroll_ms=a.preroll_ms,
        )

    def configure_languages(self, source_language: str) -> None:
        self._language_router = TurnLanguageRouter(source_language)

    def prepare_direction(self) -> None:
        source, target = self._language_router.source_language, self._language_router.target_language
        # Warm the recognizer that this route needs: the Bengali direction uses a
        # Bengali fine-tune, and loading it up front keeps it off the first
        # caption's critical path. A missing model is a warning, not a failure.
        if hasattr(self.asr, "load_for"):
            try:
                self.asr.load_for(source)
            except Exception:  # a missing model is a supported state
                log.warning("ASR model for %s unavailable", source, exc_info=True)
        if hasattr(self.translator, "load_direction"):
            self.translator.load_direction(source, target)
        if hasattr(self.tts, "load_for"):
            self.tts.load_for(target)

    # -- language plumbing ------------------------------------------------
    def _decode_language(self) -> str:
        """Language argument for the next decode (``"auto"`` = let ASR detect).

        Auto mode caches the last probe for ``auto_probe_interval_s`` so a
        Bengali lecture does not pay a ~1.2 s detection pass on every phrase,
        while a genuine language switch is still picked up within that window.
        """
        language = self._language_router.decode_language()
        if language != "auto":
            return language
        interval = self.settings.asr.auto_probe_interval_s
        if self._auto_language and (time.monotonic() - self._auto_probed_at) < interval:
            return self._auto_language
        return "auto"

    def _note_detection(self) -> None:
        """Route this turn from the recognizer's language probe, if it ran."""
        if self._language_router.requested_source != "auto":
            return
        language = getattr(self.asr, "last_detected_language", None)
        if language not in {"bn", "en"}:
            return
        prob = float(getattr(self.asr, "last_detection_prob", 1.0) or 1.0)
        self._auto_language = language
        self._auto_probed_at = time.monotonic()
        if self._language_router.observe_detected(language, prob):
            log.info("auto language route -> %s (prob %.2f)", language, prob)
            self.stats.language_turn_switches = self._language_router.turn_switches

    def _build_stream(self) -> None:
        a = self.settings.asr
        t = self.settings.translate
        self._window = SpeechWindow(
            vad_threshold=a.vad_threshold,
            preroll_ms=a.preroll_ms,
            silence_ms=a.silence_ms,
            max_window_s=a.stream_max_window_s,
            context_s=a.stream_context_s,
        )
        self._agreement = LocalAgreement(confidence=a.commit_confidence)
        self._phrases = PhraseBuilder(
            max_chars=t.phrase_max_chars,
            max_s=t.phrase_max_s,
            min_chars=t.phrase_min_chars,
            flush_age_s=t.phrase_flush_age_s,
        )
        self._last_decode_end = 0.0
        self._last_partial_text = ""
        self._speech_started_at = None

    def set_final_callback(self, cb: Callable[[dict[str, Any]], None] | None) -> None:
        """Called (in the ASR thread) whenever a final segment is stored."""
        self._on_final = cb

    def start(self, session_id: str, loop: Any) -> None:
        if self.status == "running":
            return
        self.session_id = session_id
        session = self.storage.get_session(session_id) or {}
        self.configure_languages(session.get("source_lang", "en"))
        self.loop = loop
        self._segmenter = self._new_segmenter()
        # A custom ASR adapter that cannot emit word timestamps falls back to
        # the segment path instead of failing every decode.
        if self.streaming and not hasattr(self.asr, "transcribe_words"):
            log.warning("ASR adapter has no transcribe_words(); using segment mode")
            self.streaming = False
        if self.streaming:
            self._build_stream()
        self.status = "running"
        self.stats = PipelineStats(started_at=time.time())
        self._running.set()
        self._stopping.clear()
        self._pause.clear()
        self._asr_wake.clear()
        asr_target = self._stream_asr_loop if self.streaming else self._asr_loop
        self._threads = [
            threading.Thread(target=asr_target, name="guru-asr", daemon=True),
            threading.Thread(target=self._enrich_loop, name="guru-enrich", daemon=True),
            threading.Thread(target=self._tts_loop, name="guru-tts", daemon=True),
        ]
        for t in self._threads:
            t.start()
        self._publish({"type": "classroom", "status": "running", "session_id": session_id})
        self._publish({
            "type": "pipeline.status",
            "session_id": session_id,
            "status": "running",
            "mode": "streaming" if self.streaming else "segment",
        })
        log.info(
            "pipeline started for session %s (%s)",
            session_id,
            "streaming" if self.streaming else "segment",
        )

    def pause(self) -> None:
        if self.status == "running":
            self._pause.set()
            self.status = "paused"
            self._publish({"type": "classroom", "status": "paused", "session_id": self.session_id})
            self._publish({"type": "pipeline.status", "status": "paused",
                           "session_id": self.session_id})

    def resume(self) -> None:
        if self.status == "paused":
            self._pause.clear()
            self.status = "running"
            self._publish({"type": "classroom", "status": "running", "session_id": self.session_id})
            self._publish({"type": "pipeline.status", "status": "running",
                           "session_id": self.session_id})

    def stop(self) -> None:
        """Drain every queue so the teacher's last sentence is not lost.

        Sentinels travel capture -> ASR -> translate -> TTS, so each worker exits
        only after the previous stage has finished. Clearing a shared flag is
        not enough: the flush below can enqueue work after a worker checked it.
        """
        if self.status == "idle":
            return
        self._stopping.set()
        self._pause.clear()
        self._asr_wake.set()
        if not self.streaming:
            try:
                tail = self._segmenter.flush()
                if tail is not None:
                    self._enqueue_final(tail)
            except Exception:  # pragma: no cover - defensive
                log.exception("flush failed")
            self._send(self._final_q, None)
        self._threads = [t for t in self._threads if t is not None]
        for t in self._threads:
            t.join(timeout=60)
            if t.is_alive():  # pragma: no cover - only on stuck models
                log.warning("thread %s did not stop in time", t.name)
        self._threads = []
        self._running.clear()
        self.status = "idle"
        self._publish({"type": "classroom", "status": "stopped", "session_id": self.session_id})
        self._publish({"type": "pipeline.status", "status": "stopped",
                       "session_id": self.session_id})
        log.info("pipeline stopped: %s", self.stats)

    @staticmethod
    def _send(q: queue.Queue[Any], item: Any, timeout: float = 10.0) -> None:
        """Blocking send used only during shutdown (never on the audio path)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                q.put(item, timeout=0.2)
                return
            except queue.Full:
                continue
        log.error("queue %s stayed full during shutdown", getattr(q, "name", q))

    # -- capture side (any thread) ---------------------------------------
    def submit_audio(self, samples: np.ndarray) -> None:
        if self.status != "running" or self._pause.is_set():
            return
        if self.streaming:
            self._submit_stream(samples)
            return
        try:
            for seg in self._segmenter.feed(samples):
                self._enqueue_final(seg)
            self._maybe_partial()
        except Exception:  # pragma: no cover - defensive, never kill capture
            log.exception("segmenter error")
            self.stats.last_error = "segmenter error"

    def _submit_stream(self, samples: np.ndarray) -> None:
        """Feed the rolling window. Never blocks, never decodes."""
        try:
            started, ended = self._window.feed(samples)
            if started:
                self._speech_started_at = time.perf_counter()
                self._publish({
                    "type": "pipeline.status",
                    "session_id": self.session_id,
                    "status": "speech",
                    "at": round(self._window.speech_start, 2),
                })
            if ended or self._window.in_speech:
                self._asr_wake.set()
        except Exception:  # pragma: no cover - defensive, never kill capture
            log.exception("stream window error")
            self.stats.last_error = "stream window error"

    def _enqueue_final(self, seg: Segment) -> None:
        finalized_at = time.perf_counter()
        self.stats.segments_finalized += 1
        try:
            self._final_q.put_nowait((seg, finalized_at))
        except queue.Full:
            # Never block the audio thread: drop the oldest unprocessed segment.
            try:
                dropped, _ = self._final_q.get_nowait()
                self.stats.asr_queue_drops += 1
                log.warning("ASR queue full, dropped %.1fs of audio", dropped.duration_s)
            except queue.Empty:  # pragma: no cover
                pass
            try:
                self._final_q.put_nowait((seg, finalized_at))
            except queue.Full:  # pragma: no cover
                pass

    def _maybe_partial(self) -> None:
        a = self.settings.asr
        if not a.partial_enabled:
            return
        now = time.monotonic()
        if now - self._last_partial_at < a.partial_interval_s:
            return
        buf = self._segmenter.partial_audio()
        if buf is None or len(buf) < a.partial_min_s * SAMPLE_RATE:
            return
        self._last_partial_at = now
        try:
            self._partial_q.put_nowait((buf.copy(), now))
        except queue.Full:
            pass

    # -- streaming ASR worker -------------------------------------------
    def _stream_asr_loop(self) -> None:
        """Decode the rolling window continuously while speech is present."""
        a = self.settings.asr
        while not self._stopping.is_set():
            self._asr_wake.wait(timeout=0.1)
            self._asr_wake.clear()
            if self._pause.is_set():
                continue
            window = self._window
            if window is None:
                continue
            ended = window.take_ended()
            if not window.in_speech and not ended:
                continue
            if not ended and window.duration_s < a.stream_min_audio_s:
                continue
            if not ended and (window.end_time - self._last_decode_end) < a.stream_step_s:
                continue
            if ended and not self._worth_final_decode(window):
                # The tail is only a few hundred milliseconds of audio. Whisper
                # pads to a 30 s window, so decoding it costs a full call
                # (~1.3 s on this CPU) for nothing: commit what we already have.
                self._last_decode_end = window.end_time
                self._flush_stream_tail()
                continue
            self._last_decode_end = window.end_time
            try:
                self._stream_decode(ended)
            except Exception as exc:  # pragma: no cover - defensive
                log.exception("stream decode failed")
                self.stats.last_error = str(exc)
                self._publish({
                    "type": "pipeline.error",
                    "session_id": self.session_id,
                    "stage": "asr",
                    "error": str(exc),
                })
        # Final flush: decode whatever is left, commit it, and close phrases.
        try:
            if self._window is not None and self._worth_final_decode(self._window):
                self._stream_decode(True)
            else:
                self._flush_stream_tail()
        except Exception:  # pragma: no cover - defensive
            log.exception("final stream flush failed")
        if self._stopping.is_set():
            self._send(self._enrich_q, None)

    def _worth_final_decode(self, window: SpeechWindow) -> bool:
        """Whether enough un-decoded audio remains to justify a final call."""
        if window.duration_s <= 0:
            return False
        un_decoded = window.end_time - self._agreement.last_committed_time
        if un_decoded >= 0.35:
            return True
        # No audio worth decoding, but commit whatever the last hypothesis held.
        return False

    def _stream_decode(self, ended: bool) -> None:
        a = self.settings.asr
        window = self._window
        audio = window.audio()
        if audio.size == 0:
            return
        offset = window.start_time
        prompt = self._agreement.prompt()
        t0 = time.perf_counter()
        asr_kwargs = {"offset": offset, "beam_size": a.stream_beam_size, "prompt": prompt}
        if "language" in inspect.signature(self.asr.transcribe_words).parameters:
            asr_kwargs["language"] = self._decode_language()
        words = self.asr.transcribe_words(audio, **asr_kwargs)
        self._note_detection()
        asr_ms = (time.perf_counter() - t0) * 1000
        self.stats.stream_decodes += 1
        rtf = asr_ms / max(1.0, window.duration_s * 1000)
        self._latency.add("asr_ms", asr_ms)
        self._latency.add("asr_rtf", rtf)
        self._metrics([("asr_ms", asr_ms), ("asr_rtf", rtf)])
        if rtf > 1.0:
            self.stats.stream_backlog += 1
            log.warning("ASR slower than real time (rtf %.2f, %.1fs window)", rtf, window.duration_s)

        # transcribe_words() already returns absolute timestamps (offset applied),
        # so the agreement buffer must not shift them a second time.
        self._agreement.insert(words)
        committed = self._agreement.commit()
        now = time.monotonic()
        phrases: list[Phrase] = []
        if committed:
            self.stats.stream_commits += len(committed)
            self._language_router.observe_stable(" ".join(w.text for w in committed))
            window.trim_to(committed[-1].end)
            phrases.extend(self._phrases.add(committed, now))
            self._publish({
                "type": "transcript.stable",
                "session_id": self.session_id,
                "text": " ".join(w.text.strip() for w in committed).strip(),
                "source_language": self._language_router.source_language,
                "target_language": self._language_router.target_language,
                "start_s": round(committed[0].start, 2),
                "end_s": round(committed[-1].end, 2),
            })
        else:
            partial = self._agreement.pending_text()
            if partial and partial != self._last_partial_text:
                self._last_partial_text = partial
                self.stats.stream_partials += 1
                self._publish({
                    "type": "transcript.partial",
                    "session_id": self.session_id,
                    "en": partial,
                    "source_text": partial,
                    "source_language": self._language_router.source_language,
                    "target_language": self._language_router.target_language,
                    "start_s": round(window.start_time, 2),
                })
        phrases.extend(self._phrases.tick(now))
        for phrase in phrases:
            self._finalize_phrase(phrase)
        if ended:
            self._flush_stream_tail()

    def _flush_stream_tail(self) -> None:
        """Commit the unstable tail and close every open phrase.

        The window is *rebased* rather than reset: the flush normally runs after
        a decode that took ~1 s, and during that second the capture thread kept
        appending audio. Resetting threw that audio away, which is how the
        streaming path lost 37 % of a lecture (finding F-03).
        """
        tail = self._agreement.flush_all()
        now = time.monotonic()
        phrases: list[Phrase] = []
        if tail:
            phrases.extend(self._phrases.add(tail, now))
        last = self._phrases.flush()
        if last is not None:
            phrases.append(last)
        for phrase in phrases:
            self._finalize_phrase(phrase)

        frontier = self._agreement.last_committed_time
        if tail:
            frontier = max(frontier, tail[-1].end)
        kept = self._window.rebase(frontier)
        if kept > 0.05:
            log.debug("carried %.2fs of audio across the phrase boundary", kept)
            self.stats.audio_carried_s += kept
        # Reset at the committed frontier, not at the window start: the window
        # deliberately keeps ~1 s of already-committed audio as acoustic context,
        # and resetting earlier re-committed those words (duplicated captions).
        self._language_router.end_turn()
        self.stats.language_turn_switches = self._language_router.turn_switches
        self._agreement.reset(frontier)
        self._phrases.reset()
        self._last_partial_text = ""
        self._last_decode_end = self._window.end_time
        if kept > 0:
            # Audio is waiting; do not idle until the next capture callback.
            self._asr_wake.set()

    def _finalize_phrase(self, phrase: Phrase) -> None:
        """Store a stable phrase, publish it, and hand it to translation."""
        text = phrase.text.strip()
        if not text:
            return
        seq = self.storage.next_seq(self.session_id or "")
        source_language = self._language_router.source_language
        target_language = self._language_router.target_language
        record = self.storage.add_segment(
            self.session_id or "",
            seq=seq,
            start_s=phrase.start,
            end_s=phrase.end,
            en=text if source_language == "en" else "",
            bn=text if source_language == "bn" else "",
            source_language=source_language,
            target_language=target_language,
            source_text=text,
            confidence=0.0,
            asr_ms=0.0,
        )
        self.stats.segments_finalized += 1
        self.stats.stream_phrases += 1
        self._publish({
            "type": "transcript.final",
            "session_id": self.session_id,
            "segment": {
                "id": record["id"],
                "seq": seq,
                "start_s": round(phrase.start, 2),
                "end_s": round(phrase.end, 2),
                "en": text if source_language == "en" else "",
                "bn": text if source_language == "bn" else "",
                "source_text": text,
                "source_language": source_language,
                "target_language": target_language,
                "confidence": 0.0,
                "segment_status": "final",
            },
        })
        if self._on_final:
            try:
                self._on_final(record)
            except Exception:  # pragma: no cover
                log.exception("final callback failed")

        # finalized_at approximates the wall-clock moment the phrase was spoken,
        # so e2e latency measures speech -> caption, not decode -> caption.
        lag = max(0.0, (self._window.end_time if self._window else phrase.end) - phrase.end)
        job = {
            "segment": record,
            "finalized_at": time.perf_counter() - lag,
            "speech_end_at": time.perf_counter(),
            "source_language": source_language,
            "target_language": target_language,
        }
        # Bounded backpressure runs on the ASR worker, never microphone capture.
        self._enrich_q.put(job)

    # -- segment-mode ASR worker ----------------------------------------
    def _asr_loop(self) -> None:
        while True:
            job = None
            try:
                job = self._final_q.get(timeout=0.05)
            except queue.Empty:
                if self._stopping.is_set():
                    break  # queue drained and no new work can arrive
                job = None
            if job is None and self._stopping.is_set():
                break  # sentinel
            if job is not None:
                try:
                    self._process_final(*job)
                except Exception as exc:  # pragma: no cover - defensive
                    log.exception("ASR job failed")
                    self.stats.last_error = str(exc)
                continue
            # Idle: spend the time on a provisional caption.
            try:
                buf, _ts = self._partial_q.get_nowait()
            except queue.Empty:
                continue
            try:
                self._process_partial(buf)
            except Exception:  # pragma: no cover
                log.debug("partial failed", exc_info=True)
        # Tell the next stage there is no more ASR output.
        if self._stopping.is_set():
            self._send(self._enrich_q, None)

    def _thread_alive(self, name: str) -> bool:
        return any(t.name == name and t.is_alive() for t in self._threads)

    def _process_partial(self, buf: np.ndarray) -> None:
        # Only decode the tail and only when no finalized audio is waiting:
        # provisional captions must never delay a real segment.
        if not self._final_q.empty() or not self._enrich_q.empty():
            return
        max_samples = int(self.settings.asr.partial_max_audio_s * SAMPLE_RATE)
        kwargs: dict[str, Any] = {"beam_size": 1}
        if "language" in inspect.signature(self.asr.transcribe).parameters:
            kwargs["language"] = self._decode_language()
        result = self.asr.transcribe(
            buf[-max_samples:] if len(buf) > max_samples else buf, **kwargs
        )
        self._note_detection()
        text = result.text.strip()
        if not text or text == self._last_partial_text:
            return
        if result.no_speech_prob > 0.85:
            return
        self._last_partial_text = text
        self.stats.partials_emitted += 1
        self._publish(
            {
                "type": "transcript.partial",
                "session_id": self.session_id,
                "en": text,
                "confidence": round(result.confidence, 2),
            }
        )

    def _process_final(self, seg: Segment, finalized_at: float) -> None:
        kwargs: dict[str, Any] = {}
        if "language" in inspect.signature(self.asr.transcribe).parameters:
            kwargs["language"] = self._decode_language()
        asr_res = self.asr.transcribe(seg.audio, **kwargs)
        self._note_detection()
        text = asr_res.text.strip()
        if not text or asr_res.no_speech_prob > 0.9:
            self.stats.segments_empty += 1
            self._metrics([("segment_empty", 1.0)])
            return

        seq = self.storage.next_seq(self.session_id or "")
        record = self.storage.add_segment(
            self.session_id or "",
            seq=seq,
            start_s=seg.start_s,
            end_s=seg.end_s,
            en=text if self._language_router.source_language == "en" else "",
            bn=text if self._language_router.source_language == "bn" else "",
            source_language=self._language_router.source_language,
            target_language=self._language_router.target_language,
            source_text=text,
            confidence=asr_res.confidence,
            asr_ms=asr_res.latency_ms,
        )
        self._metrics([
            ("asr_ms", asr_res.latency_ms),
            ("asr_rtf", asr_res.latency_ms / max(1.0, seg.duration_s * 1000)),
        ])

        event = {
            "type": "transcript.final",
            "session_id": self.session_id,
            "segment": {
                "id": record["id"],
                "seq": seq,
                "start_s": round(seg.start_s, 2),
                "end_s": round(seg.end_s, 2),
                "en": text if self._language_router.source_language == "en" else "",
                "bn": text if self._language_router.source_language == "bn" else "",
                "source_text": text,
                "source_language": self._language_router.source_language,
                "target_language": self._language_router.target_language,
                "confidence": round(asr_res.confidence, 2),
                "segment_status": "final",
            },
        }
        self._publish(event)
        if self._on_final:
            try:
                self._on_final(record)
            except Exception:  # pragma: no cover
                log.exception("final callback failed")

        job = {
            "segment": record,
            "finalized_at": finalized_at,
            "speech_end_at": time.perf_counter(),
            "source_language": self._language_router.source_language,
            "target_language": self._language_router.target_language,
        }
        # Bounded backpressure runs on the ASR worker, never microphone capture.
        self._enrich_q.put(job)

    # -- enrichment worker ----------------------------------------------
    def _enrich_loop(self) -> None:
        while self._running.is_set() or not self._enrich_q.empty():
            try:
                job = self._enrich_q.get(timeout=0.1)
            except queue.Empty:
                if self._stopping.is_set() and not self._thread_alive("guru-asr"):
                    break
                continue
            if job is None:  # sentinel from the ASR worker
                break
            try:
                self._enrich(job)
            except Exception as exc:  # pragma: no cover - defensive
                log.exception("enrichment failed")
                self.stats.last_error = str(exc)
                self._publish({
                    "type": "pipeline.error",
                    "session_id": self.session_id,
                    "stage": "translate",
                    "error": str(exc),
                })
            finally:
                self._enrich_q.task_done()
        # Tell the TTS stage there is no more text to voice.
        if self._stopping.is_set():
            self._send(self._tts_q, None)

    def _enrich(self, job: dict[str, Any]) -> None:
        """Translate, publish the caption immediately, then hand off to TTS."""
        seg = job["segment"]
        try:
            source_language = job.get("source_language", seg.get("source_language", "en"))
            target_language = job.get("target_language", seg.get("target_language", "bn"))
            source_text = seg.get("source_text") or seg.get(source_language, "")
            translation = (
                self.translator.translate_for(source_text, source_language, target_language)
                if hasattr(self.translator, "translate_for")
                else self.translator.translate(source_text)
            )
        except Exception as exc:
            # A translation failure must not lose the English transcript.
            self.stats.translate_failures += 1
            log.exception("translation failed for %s", seg["id"])
            self._publish({
                "type": "pipeline.error",
                "session_id": self.session_id,
                "stage": "translate",
                "segment_id": seg["id"],
                "error": str(exc),
            })
            self._publish({
                "type": "segment",
                "session_id": self.session_id,
                "segment": self._segment_payload(seg, None, 0.0, job["finalized_at"]),
            })
            return

        e2e_ms = (time.perf_counter() - job["finalized_at"]) * 1000
        audio_status = "pending" if (self.settings.tts.enabled and translation.text) else "disabled"
        # Caption and its timing land in one UPDATE, so one commit per segment.
        self.storage.update_segment(
            seg["id"],
            en=translation.text if target_language == "en" else seg.get("en", ""),
            bn=translation.text if target_language == "bn" else seg.get("bn", ""),
            translated_text=translation.text,
            source_language=source_language,
            target_language=target_language,
            source_text=source_text,
            total_ms=round(e2e_ms, 1),
            translate_ms=round(translation.latency_ms, 1),
            numbers_preserved=translation.numbers_preserved,
            translation_at=time.time(),
            audio_status=audio_status,
            segment_status="final",
        )
        self._metrics([("translate_ms", translation.latency_ms), ("e2e_ms", e2e_ms)])
        self._latency.add("translate_ms", translation.latency_ms)
        self._latency.add("e2e_ms", e2e_ms)
        if not translation.numbers_preserved:
            log.warning("numbers lost in translation %s: %s", seg["id"], seg["missing_numbers"])

        if self.stats.first_caption_ms is None and self._speech_started_at is not None:
            self.stats.first_caption_ms = round(
                (time.perf_counter() - self._speech_started_at) * 1000, 1
            )
            self._metrics([("first_caption_ms", self.stats.first_caption_ms)])

        payload = self._segment_payload(seg, translation, e2e_ms, job["finalized_at"],
                                        source_text=source_text,
                                        source_language=source_language,
                                        target_language=target_language)
        payload["audio_status"] = audio_status
        self.stats.segments_translated += 1
        self._publish({"type": "segment", "session_id": self.session_id, "segment": payload})

        if self.settings.tts.enabled and translation.text:
            # Bounded backpressure stays on enrichment, never capture or ASR.
            self._tts_q.put(
                {
                    "segment_id": seg["id"],
                    "text": translation.text,
                    "target_language": target_language,
                    "seq": seg["seq"],
                    "finalized_at": job["finalized_at"],
                }
            )

    def _segment_payload(
        self,
        seg: dict[str, Any],
        translation: Any,
        e2e_ms: float,
        finalized_at: float,
        source_text: str | None = None,
        source_language: str | None = None,
        target_language: str | None = None,
    ) -> dict[str, Any]:
        """Wire payload for one translated segment.

        Carries both the legacy field names (id/seq/en/bn) and the explicit
        real-time contract fields, so old and new clients agree.
        """
        source_language = source_language or seg.get("source_language", "en")
        target_language = target_language or seg.get("target_language", "bn")
        source_text = source_text or seg.get("source_text") or seg.get(source_language, "")
        translated = translation.text if translation is not None else ""
        en = translated if target_language == "en" else (source_text if source_language == "en" else "")
        bn = translated if target_language == "bn" else (source_text if source_language == "bn" else "")
        return {
            "id": seg["id"],
            "seq": seg["seq"],
            "start_s": round(float(seg["start_s"]), 2),
            "end_s": round(float(seg["end_s"]), 2),
            "en": en,
            "bn": bn,
            "audio_url": None,  # arrives via the audio.ready event
            "asr_ms": round(float(seg.get("asr_ms", 0) or 0), 0),
            "translate_ms": round(float(getattr(translation, "latency_ms", 0.0) or 0.0), 0),
            "e2e_ms": round(e2e_ms, 0),
            "confidence": seg.get("confidence", 0),
            "numbers_preserved": getattr(translation, "numbers_preserved", True),
            "translation_skipped": getattr(translation, "skipped", False),
            # -- real-time contract --
            "session_id": self.session_id,
            "segment_id": seg["id"],
            "sequence_number": seg["seq"],
            "source_text": source_text,
            "translated_text": translated,
            "source_language": source_language,
            "target_language": target_language,
            "source_start_time": round(float(seg["start_s"]), 2),
            "source_end_time": round(float(seg["end_s"]), 2),
            "translation_timestamp": round(time.time(), 3),
            "audio_status": "pending",
            "audio_reference": None,
            "segment_status": "final",
        }

    # -- TTS worker ------------------------------------------------------
    def _tts_loop(self) -> None:
        while True:
            try:
                job = self._tts_q.get(timeout=0.1)
            except queue.Empty:
                if self._stopping.is_set() and not self._thread_alive("guru-enrich"):
                    break
                continue
            if job is None:  # sentinel from the enrichment worker
                break
            try:
                self._synthesize(job)
            except Exception:  # pragma: no cover - voice failure must not stop class
                log.exception("TTS failed for %s", job.get("segment_id"))
                self._mark_audio_failed(job.get("segment_id"))
            finally:
                self._tts_q.task_done()

    def _mark_audio_failed(self, segment_id: str | None) -> None:
        if not segment_id:
            return
        self.stats.tts_failures += 1
        try:
            self.storage.update_segment(segment_id, audio_status="failed")
        except Exception:  # pragma: no cover
            log.warning("could not mark audio failed", exc_info=True)
        self._publish({
            "type": "segment.updated",
            "session_id": self.session_id,
            "segment": {"id": segment_id, "audio_status": "failed"},
        })

    def _synthesize(self, job: dict[str, Any]) -> None:
        wav, tts_ms = (
            self.tts.synthesize_for(job["text"], job.get("target_language", "bn"))
            if hasattr(self.tts, "synthesize_for")
            else self.tts.synthesize(job["text"])
        )
        if not wav:
            self._mark_audio_failed(job.get("segment_id"))
            return
        session_audio = self.audio_dir / (self.session_id or "unknown")
        session_audio.mkdir(parents=True, exist_ok=True)
        path = session_audio / f"{job['segment_id']}.wav"
        path.write_bytes(wav)
        audio_e2e_ms = (time.perf_counter() - job["finalized_at"]) * 1000
        url = f"/api/student/audio/{job['segment_id']}"
        self.storage.update_segment(
            job["segment_id"],
            audio_path=str(path),
            tts_ms=round(tts_ms, 1),
            audio_status="ready",
        )
        self._metrics([
            ("tts_ms", tts_ms),
            ("audio_e2e_ms", audio_e2e_ms),
            ("audio_kb", len(wav) / 1024),
        ])
        self._latency.add("tts_ms", tts_ms)
        self._latency.add("audio_e2e_ms", audio_e2e_ms)
        if self.stats.first_audio_ms is None and self._speech_started_at is not None:
            self.stats.first_audio_ms = round(
                (time.perf_counter() - self._speech_started_at) * 1000, 1
            )
            self._metrics([("first_audio_ms", self.stats.first_audio_ms)])
        self._publish(
            {
                "type": "audio.ready",
                "session_id": self.session_id,
                "segment_id": job["segment_id"],
                "seq": job.get("seq"),
                "sequence_number": job.get("seq"),
                "audio_url": url,
                "audio_reference": url,
                "audio_status": "ready",
                "tts_ms": round(tts_ms, 0),
                "audio_e2e_ms": round(audio_e2e_ms, 0),
            }
        )

    # -- helpers ---------------------------------------------------------
    def _publish(self, event: dict[str, Any]) -> None:
        if self.loop is None:
            return
        try:
            # Scope every pipeline event to the active session so students still
            # subscribed to an older session never see this lecture's data.
            self.loop.call_soon_threadsafe(
                self.bus.publish, event, None, self.session_id
            )
        except RuntimeError:  # loop closed during shutdown
            pass

    def _metrics(self, items: list[tuple[str, float]]) -> None:
        """Write a stage's metrics in one transaction; never breaks class.

        Failures are logged at WARNING: swallowing them at DEBUG is how a broken
        two-tuple/three-tuple contract hid every latency measurement for a whole
        release (FINAL_VERIFICATION_REPORT.md, HIGH-1).
        """
        try:
            self.storage.record_metrics(items, self.session_id)
        except Exception:  # pragma: no cover - metrics must never break class
            log.warning("metric write failed", exc_info=True)

    # -- file replay (tests / demos) ------------------------------------
    def run_wav(self, path: str | Path, loop: Any, session_id: str,
                speed: float = 1.0, realtime: bool = True) -> dict[str, Any]:
        """Feed a WAV file through the live pipeline (used offline demos)."""
        self.start(session_id, loop)
        t0 = time.time()
        source = WavSource(path, speed=speed if realtime else 1e6)
        for chunk in source.frames():
            self.submit_audio(chunk)
        self.stop()
        return {
            "segments": self.stats.segments_translated,
            "asr_dropped": self.stats.asr_queue_drops,
            "elapsed_s": round(time.time() - t0, 1),
        }

    def health(self) -> dict[str, Any]:
        self.stats.queue_backlog_peak = max(
            self.stats.queue_backlog_peak, self._enrich_q.qsize() + self._tts_q.qsize()
        )
        return {
            "status": self.status,
            "session_id": self.session_id,
            "mode": "streaming" if self.streaming else "segment",
            "stats": {
                "segments_finalized": self.stats.segments_finalized,
                "segments_translated": self.stats.segments_translated,
                "segments_empty": self.stats.segments_empty,
                "asr_queue_drops": self.stats.asr_queue_drops,
                "enrichment_queue_drops": self.stats.enrichment_queue_drops,
                "tts_queue_drops": self.stats.tts_queue_drops,
                "partials_emitted": self.stats.partials_emitted,
                "stream_decodes": self.stats.stream_decodes,
                "stream_commits": self.stats.stream_commits,
                "stream_phrases": self.stats.stream_phrases,
                "stream_partials": self.stats.stream_partials,
                "stream_backlog": self.stats.stream_backlog,
                "audio_carried_s": round(self.stats.audio_carried_s, 2),
                "tts_failures": self.stats.tts_failures,
                "language_turn_switches": self.stats.language_turn_switches,
                "finalized_segment_drops": self.stats.enrichment_queue_drops,
                "playback_order_errors": self.stats.playback_order_errors,
                "queue_backlog_peak": self.stats.queue_backlog_peak,
                "translate_failures": self.stats.translate_failures,
                "first_caption_ms": self.stats.first_caption_ms,
                "first_audio_ms": self.stats.first_audio_ms,
                "last_error": self.stats.last_error,
            },
            "queues": {
                "asr": self._final_q.qsize(),
                "enrichment": self._enrich_q.qsize(),
                "tts": self._tts_q.qsize(),
            },
            "latency": {
                name: self._latency.summary(name) for name in self._latency.names()
            },
            "window": (
                {
                    "in_speech": self._window.in_speech,
                    "duration_s": round(self._window.duration_s, 2),
                    "start_s": round(self._window.start_time, 2),
                    "pending_words": len(self._phrases) if self._phrases else 0,
                }
                if self._window is not None
                else None
            ),
        }
