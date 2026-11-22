#!/usr/bin/env python3
"""Drive the live pipeline with real Bengali speech and report what comes out.

This is the end-to-end counterpart to ``scripts/eval_bn_asr.py``: it feeds the
same Piper-rendered Bengali clips through ``ClassroomPipeline`` at real-time
pace (as a microphone would) and records the stored captions, the English
translations, and the latency counters.

    # after the fix (Bengali model configured)
    .venv/bin/python scripts/bench_bangla_pipeline.py --mode segment --language bn

    # before the fix (base model only)
    .venv/bin/python scripts/bench_bangla_pipeline.py --mode segment --language bn \
        --bn-model-dir "" --out reports/results/bangla_pipeline_segment_before.json
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from guru.asr import WhisperAsr
from guru.bilingual import BidirectionalTranslator, NullBidirectionalTts
from guru.bus import EventBus
from guru.config import load_settings
from guru.pipeline import ClassroomPipeline
from guru.storage import Storage

SAMPLE_RATE = 16000
BENGALI = re.compile(r"[\u0980-\u09FF]")
LATIN = re.compile(r"[A-Za-z]")
CHUNK = 1600  # 100 ms


def load_wav(path: str) -> np.ndarray:
    import wave

    with wave.open(path) as handle:
        channels, rate = handle.getnchannels(), handle.getframerate()
        width = handle.getsampwidth()
        raw = handle.readframes(handle.getnframes())
    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[width]
    audio = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    audio = (audio - 128.0) / 128.0 if width == 1 else audio / float(np.iinfo(dtype).max)
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != SAMPLE_RATE:
        n = round(len(audio) * SAMPLE_RATE / rate)
        audio = np.interp(
            np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio
        ).astype(np.float32)
    return audio


def bengali_ratio(text: str) -> float:
    letters = len(BENGALI.findall(text or "")) + len(LATIN.findall(text or ""))
    return 0.0 if letters == 0 else len(BENGALI.findall(text or "")) / letters


def build_audio(db_path: Path, clips: int, gap_s: float = 0.7) -> tuple[np.ndarray, list[str]]:
    wavs = {
        os.path.splitext(os.path.basename(p))[0]: p
        for p in glob.glob(str(ROOT / "data" / "audio" / "**" / "*.wav"), recursive=True)
    }
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    ids = [
        row["id"]
        for row in con.execute("select id, bn from segments where bn != '' order by created_at, id")
        if wavs.get(row["id"]) and BENGALI.search(row["bn"] or "")
    ]
    con.close()
    chosen = ids[:clips]
    pieces = [np.zeros(int(gap_s * SAMPLE_RATE), dtype=np.float32)]
    for cid in chosen:
        pieces.append(load_wav(wavs[cid]))
        pieces.append(np.zeros(int(gap_s * SAMPLE_RATE), dtype=np.float32))
    return np.concatenate(pieces), chosen


def _drain(sub, sink: list[dict]) -> None:
    while True:
        try:
            sink.append(sub.queue.get_nowait())
        except asyncio.QueueEmpty:
            return


async def run(args) -> dict:
    settings = load_settings()
    if args.asr_model:
        settings.asr.model_dir = args.asr_model
    settings.asr.bn_model_dir = args.bn_model_dir
    settings.asr.streaming = args.mode == "stream"
    settings.asr.partial_enabled = args.partials
    settings.asr.hotwords = ""

    tmp = ROOT / "reports" / "results" / "_bench"
    tmp.mkdir(parents=True, exist_ok=True)
    db_path = tmp / f"bench_{args.mode}_{args.language}_{int(time.time())}.db"
    storage = Storage(db_path)
    bus = EventBus(queue_size=4096)
    asr = WhisperAsr(settings)
    pipeline = ClassroomPipeline(
        settings, bus, storage, asr,
        BidirectionalTranslator(settings), NullBidirectionalTts(),
    )
    sid = storage.create_session(args.title, "tok", "BENCH1", source_lang=args.language)
    pipeline.session_id = sid
    pipeline.configure_languages(args.language)

    if args.wav:
        audio = load_wav(args.wav)
        if args.seconds:
            audio = audio[: int(args.seconds * SAMPLE_RATE)]
        clip_ids = [os.path.basename(args.wav)]
    else:
        audio, clip_ids = build_audio(ROOT / args.db, args.clips, args.gap)
    sub = bus.subscribe("teacher", sid)
    events: list[dict] = []
    t0 = time.perf_counter()

    async def feed() -> None:
        for i in range(0, len(audio), CHUNK):
            pipeline.submit_audio(audio[i : i + CHUNK])
            await asyncio.sleep(CHUNK / SAMPLE_RATE)  # real-time pace

    pipeline.start(sid, asyncio.get_running_loop())
    await feed()
    # Let the workers drain the tail of the lecture.
    deadline = time.perf_counter() + args.drain_s
    while time.perf_counter() < deadline:
        await asyncio.sleep(0.2)
        _drain(sub, events)
    await asyncio.to_thread(pipeline.stop)
    _drain(sub, events)

    captions = [e["segment"] for e in events if e.get("type") == "segment"]
    stored = storage.list_segments(sid)
    spans = [e for e in events if e.get("type") in {"transcript.final", "transcript.stable"}]

    result = {
        "mode": args.mode,
        "language": args.language,
        "asr_model": str(settings.resolve(settings.asr.model_dir)),
        "bn_model_dir": str(settings.resolve(settings.asr.bn_model_dir))
        if settings.asr.bn_model_dir else "",
        "clips_in": len(clip_ids),
        "audio_s": round(len(audio) / SAMPLE_RATE, 1),
        "transcripts": len(spans),
        "stored_segments": len(stored),
        "captions": len(captions),
        "bengali_script_ratio": round(
            sum(bengali_ratio(c.get("source_text") or "") for c in stored) / len(stored), 2
        ) if stored else 0.0,
        "captions_with_english": sum(
            1 for c in stored
            if (c.get("en") or "").strip() and (c.get("en") or "").strip() != (c.get("bn") or "").strip()
        ),
        # Bengali captions echoed untranslated (script the translator left alone).
        "untranslated_echoes": sum(
            1 for c in stored
            if args.language != "en" and (c.get("en") or "").strip()
            and (c.get("en") or "") == (c.get("source_text") or "")
        ),
        "first_caption_ms": pipeline.stats.first_caption_ms,
        "stats": pipeline.health()["stats"],
        "samples": [
            {"source": c.get("source_text"), "bn": c.get("bn"), "en": c.get("en")}
            for c in stored[:8]
        ],
        "wall_s": round(time.perf_counter() - t0, 1),
        "db": str(db_path),
    }
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["segment", "stream"], default="segment")
    ap.add_argument("--language", choices=["en", "bn", "auto"], default="bn")
    ap.add_argument("--asr-model", default="")
    ap.add_argument("--bn-model-dir", default=None,
                    help="override asr.bn_model_dir ('' disables the Bengali model)")
    ap.add_argument("--clips", type=int, default=8)
    ap.add_argument("--wav", default="", help="use this WAV instead of the Bengali corpus")
    ap.add_argument("--seconds", type=float, default=40.0, help="limit for --wav input")
    ap.add_argument("--gap", type=float, default=0.7)
    ap.add_argument("--drain-s", type=float, default=12.0)
    ap.add_argument("--partials", action="store_true")
    ap.add_argument("--db", default="data/guru.db")
    ap.add_argument("--title", default="Bangla bench")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if args.bn_model_dir is None:
        args.bn_model_dir = load_settings().asr.bn_model_dir

    result = asyncio.run(run(args))
    out = args.out or f"reports/results/bangla_pipeline_{args.mode}_{args.language}.json"
    path = ROOT / out
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "samples"},
                     ensure_ascii=False, indent=2))
    for sample in result["samples"]:
        print(f"  BN: {sample['bn']!r}\n  EN: {sample['en']!r}")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
