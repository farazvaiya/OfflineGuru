#!/usr/bin/env python3
"""Headless run of the full lecture pipeline on a WAV file.

Useful for offline verification, demos without a microphone, and repeatable
measurements.  It exercises the same segmentation, ASR, translation, TTS and
education code paths the web server uses.

    .venv/bin/python scripts/pipeline_cli.py data/demo/lecture.wav --speed 1
    .venv/bin/python scripts/pipeline_cli.py data/demo/lecture.wav --speed 0 --no-education

``--speed 0`` processes as fast as the CPU allows (used for capacity estimates,
not for latency numbers).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from guru.asr import WhisperAsr
from guru.audio import WavSource
from guru.bus import EventBus
from guru.config import load_settings
from guru.education.service import EducationService
from guru.pipeline import ClassroomPipeline
from guru.storage import Storage
from guru.translate import build_translator
from guru.tts import NullTts, PiperTts


def build(settings):
    storage = Storage(settings.resolve(settings.storage.db_path))
    bus = EventBus()
    asr = WhisperAsr(settings)
    translator = build_translator(settings)
    tts = PiperTts(settings) if settings.tts.enabled else NullTts()
    pipeline = ClassroomPipeline(settings, bus, storage, asr, translator, tts)
    education = EducationService(settings, storage, translator)
    return storage, bus, pipeline, education


async def main(args: argparse.Namespace) -> int:
    settings = load_settings()
    if args.whisper:
        settings.asr.model_dir = f"models/whisper-{args.whisper}"
    if args.no_tts:
        settings.tts.enabled = False
    if args.hotwords:
        settings.asr.hotwords = args.hotwords
    storage, bus, pipeline, education = build(settings)

    import secrets

    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    code = "".join(secrets.choice(alphabet) for _ in range(6))
    while storage.get_session_by_code(code):
        code = "".join(secrets.choice(alphabet) for _ in range(6))
    source, target = ("auto", "auto") if args.language == "auto" else (
        (args.language, "bn" if args.language == "en" else "en")
    )
    session_id = storage.create_session(args.title, "cli-token", code, source, target)
    loop = asyncio.get_running_loop()
    pipeline.start(session_id, loop)
    sub = bus.subscribe("teacher", session_id)
    t0 = time.time()

    async def consume() -> None:
        while True:
            event = await sub.queue.get()
            kind = event.get("type")
            if kind == "transcript.partial" and args.verbose:
                print(f"  … {event['en'][:100]}", flush=True)
            elif kind == "transcript.final":
                seg = event["segment"]
                lang = (seg.get("source_language") or "en").upper()
                print(f"[{seg['start_s']:6.1f}s] {lang} {seg['source_text'][:110]}", flush=True)
            elif kind == "segment":
                seg = event["segment"]
                lang = (seg.get("target_language") or "bn").upper()
                text = seg.get("en") if lang == "EN" else seg.get("bn")
                print(f"           {lang} {text[:110]}  (e2e {seg['e2e_ms']:.0f} ms)", flush=True)
            elif kind == "classroom":
                print(f"-- classroom {event.get('status')} --", flush=True)

    consumer = asyncio.create_task(consume())

    speed = 1e6 if args.speed == 0 else args.speed
    print(f"feeding {args.wav} at speed {args.speed or 'max'}…", flush=True)
    if args.speed == 0:
        print(
            "NOTE: max-speed mode measures throughput, not latency — e2e_ms includes "
            "queueing and dropped segments are expected when the CPU saturates.",
            flush=True,
        )

    def feed() -> None:
        for chunk in WavSource(args.wav, speed=speed).frames(chunk_ms=100):
            pipeline.submit_audio(chunk)

    await asyncio.to_thread(feed)
    await asyncio.to_thread(pipeline.stop)
    await asyncio.sleep(0.2)
    consumer.cancel()

    elapsed = time.time() - t0
    segments = storage.list_segments(session_id)
    print(f"\n=== {len(segments)} segments in {elapsed:.1f}s "
          f"({pipeline.stats.segments_translated} translated, "
          f"{pipeline.stats.asr_queue_drops} ASR drops) ===")
    for kind in ("asr_ms", "translate_ms", "tts_ms", "e2e_ms"):
        print(f"{kind:14s} {storage.metric_summary(kind, session_id)}")

    if not args.no_education and len(segments) >= 3:
        print("\nGenerating summary + quiz (offline, grounded on transcript)…")
        t1 = time.time()
        summary = await asyncio.to_thread(education.generate_summary, session_id, False)
        print(f"summary: {len(summary['summary_bn'])} sentences, "
              f"{len(summary['key_terms'])} key terms ({time.time() - t1:.1f}s)")
        for item in summary["summary_bn"][:3]:
            print(f"  • {item['text'][:120]}")
        quiz = await asyncio.to_thread(education.generate_quiz, session_id, 5, True)
        print(f"quiz: {len(quiz['questions'])} questions")
        for q in quiz["questions"]:
            print(f"  [{q['type']:5s}] {q['question_en'][:100]}")
            if q["type"] == "mcq":
                print(f"          correct={q['correct_option_id']} "
                      f"({next(o['en'] for o in q['options'] if o['id'] == q['correct_option_id'])})")
        out = {
            "session_id": session_id,
            "segments": segments,
            "metrics": {k: storage.metric_summary(k, session_id)
                        for k in ("asr_ms", "translate_ms", "tts_ms", "e2e_ms")},
            "summary": summary,
            "quiz": quiz,
            "elapsed_s": round(elapsed, 1),
        }
        out_path = Path(args.out) if args.out else ROOT / "data" / "demo" / "run.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
        print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("wav", nargs="?", default="data/demo/lecture.wav")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="1 = real time, 0 = as fast as possible")
    ap.add_argument("--whisper", default=None, help="tiny|base|small (default: config)")
    ap.add_argument("--hotwords", default=None,
                    help="comma-separated domain terms to bias ASR decoding")
    ap.add_argument("--no-tts", action="store_true")
    ap.add_argument("--no-education", action="store_true")
    ap.add_argument("--language", choices=["en", "bn", "auto"], default="en",
                    help="spoken language of the WAV (bn routes Bengali ASR)")
    ap.add_argument("--title", default="Demo lecture")
    ap.add_argument("--out", default=None)
    ap.add_argument("--verbose", action="store_true")
    raise SystemExit(asyncio.run(main(ap.parse_args())))
