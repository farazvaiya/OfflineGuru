#!/usr/bin/env python3
"""Instrument the streaming pipeline's end-of-speech flush.

Runs the real ClassroomPipeline (ASR only) on a WAV at real-time pace and records
every window reset, trim, and decode. The question it answers: when speech ends and
the ASR worker performs its final decode, what happens to audio that arrived during
that decode?

    .venv/bin/python engineering_harness/probes/stream_flush.py --seconds 60
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import evidence  # noqa: E402
from guru import realtime  # noqa: E402
from guru.audio import WavSource  # noqa: E402
from guru.asr import WhisperAsr  # noqa: E402
from guru.bus import EventBus  # noqa: E402
from guru.config import load_settings  # noqa: E402
from guru.pipeline import ClassroomPipeline  # noqa: E402
from guru.storage import Storage  # noqa: E402
from guru.translate import PassthroughTranslator  # noqa: E402
from guru.tts import NullTts  # noqa: E402

LOG: list[dict] = []
T0 = 0.0


def _t() -> float:
    return round(time.perf_counter() - T0, 2)


def install() -> None:
    orig_reset = realtime.SpeechWindow.reset
    orig_trim = realtime.SpeechWindow.trim_to
    orig_rebase = realtime.SpeechWindow.rebase
    orig_flush = ClassroomPipeline._flush_stream_tail
    orig_decode = ClassroomPipeline._stream_decode

    def reset(self):
        LOG.append({"ev": "window.reset", "t": _t(), "in_speech": self.in_speech,
                    "win_start": round(self.start_time, 2),
                    "win_end": round(self.end_time, 2),
                    "dur": round(self.duration_s, 2)})
        return orig_reset(self)

    def rebase(self, frontier, keep_context=True):
        before = round(self.duration_s, 2)
        r = orig_rebase(self, frontier, keep_context)
        LOG.append({"ev": "window.rebase", "t": _t(), "frontier": round(frontier, 2),
                    "before_s": before, "kept_s": round(r, 2),
                    "after_s": round(self.duration_s, 2)})
        return r

    def trim_to(self, t):
        before = (round(self.start_time, 2), round(self.end_time, 2))
        r = orig_trim(self, t)
        LOG.append({"ev": "window.trim", "t": _t(), "to": round(t, 2), "before": before,
                    "after": (round(self.start_time, 2), round(self.end_time, 2))})
        return r

    def flush_tail(self):
        w = self._window
        LOG.append({"ev": "flush_tail", "t": _t(), "in_speech": w.in_speech,
                    "win_end": round(w.end_time, 2),
                    "last_committed": round(self._agreement.last_committed_time, 2),
                    "uncommitted_s": round(max(0.0, w.end_time - max(
                        self._agreement.last_committed_time, w.start_time)), 2)})
        return orig_flush(self)

    def stream_decode(self, ended):
        w = self._window
        LOG.append({"ev": "decode_start", "t": _t(), "ended": ended,
                    "in_speech": w.in_speech, "dur": round(w.duration_s, 2),
                    "end": round(w.end_time, 2)})
        r = orig_decode(self, ended)
        LOG.append({"ev": "decode_end", "t": _t(),
                    "committed_total": self.stats.stream_commits,
                    "buffer": len(self._agreement.buffer) if self._agreement else 0})
        return r

    realtime.SpeechWindow.reset = reset
    realtime.SpeechWindow.rebase = rebase
    realtime.SpeechWindow.trim_to = trim_to
    ClassroomPipeline._flush_stream_tail = flush_tail
    ClassroomPipeline._stream_decode = stream_decode


async def main(seconds: float, wav: str) -> dict:
    global T0
    settings = load_settings()
    settings.translate.enabled = False
    settings.tts.enabled = False
    workdir = Path("/tmp/og/probe_flush")
    (workdir / "data").mkdir(parents=True, exist_ok=True)
    settings.storage.data_dir = str(workdir / "data")
    settings.storage.db_path = str(workdir / "data" / "probe.db")
    (workdir / "data" / "probe.db").unlink(missing_ok=True)

    storage = Storage(settings.resolve(settings.storage.db_path))
    pipeline = ClassroomPipeline(settings, EventBus(), storage, WhisperAsr(settings),
                                 PassthroughTranslator(), NullTts())
    sid = storage.create_session("flush probe", "probe", "PRB02", "en", "bn")
    pipeline.start(sid, asyncio.get_running_loop())
    T0 = time.perf_counter()
    fed = 0
    for chunk in WavSource(evidence.REPO / wav, speed=1.0).frames():
        pipeline.submit_audio(np.asarray(chunk))
        fed += len(chunk)
        if time.perf_counter() - T0 >= seconds:
            break
    await asyncio.to_thread(pipeline.stop)
    stats = pipeline.stats
    segments = storage.list_segments(sid)
    storage.purge_session(sid)
    storage.close()

    resets = [e for e in LOG if e["ev"] == "window.reset"]
    rebases = [e for e in LOG if e["ev"] == "window.rebase"]
    return {
        "wav": wav,
        "requested_seconds": seconds,
        "fed_audio_s": round(fed / 16000, 1),
        "decodes": sum(1 for e in LOG if e["ev"] == "decode_start"),
        "ended_decodes_already_resumed": sum(
            1 for e in LOG if e["ev"] == "decode_start" and e["ended"] and e["in_speech"]),
        "window_resets": len(resets),
        "window_rebases": len(rebases),
        "audio_carried_s": round(sum(e["kept_s"] for e in rebases), 2),
        "audio_discarded_at_reset_s": round(sum(e["dur"] for e in resets), 2),
        "resets_while_speech_active": sum(1 for e in resets if e["in_speech"]),
        "audio_discarded_while_speech_active_s": round(
            sum(e["dur"] for e in resets if e["in_speech"]), 2),
        "segments_published": len(segments),
        "events": LOG[:400],
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--wav", default="data/demo/lecture.wav")
    ap.add_argument("--label", default="after_fix",
                    help="artifact suffix; the pre-fix run is stored as stream_flush")
    args = ap.parse_args()
    install()
    payload = asyncio.run(main(args.seconds, args.wav))
    path = evidence.write(f"stream_flush_{args.label}", payload,
                          command=" ".join(sys.argv))
    summary = {k: v for k, v in payload.items() if k != "events"}
    print(json.dumps(summary, indent=2))
    print(f"wrote {path}")
