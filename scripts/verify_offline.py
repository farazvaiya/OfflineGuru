#!/usr/bin/env python3
"""Prove the runtime works with no network access.

This script is deliberately paranoid:

1. Sets the Hugging Face / Transformers offline environment variables.
2. Installs a socket guard that records and blocks every non-loopback
   connection *before* any model is imported.
3. Loads ASR + translation + TTS from local disk, runs a real lecture excerpt
   through the pipeline, and generates a summary.
4. Fails (exit 1) if anything tried to reach the network.

    .venv/bin/python scripts/verify_offline.py
    .venv/bin/python scripts/verify_offline.py --seconds 30
"""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
os.environ["GURU_SERVER__ONLINE_PROBE"] = "false"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BLOCKED: list[str] = []
_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex
_original_getaddrinfo = socket.getaddrinfo


def _is_local(address) -> bool:
    if not isinstance(address, tuple):
        return True  # unix sockets / fd
    host = address[0]
    if host in ("localhost", "::1", "127.0.0.1", "0.0.0.0"):
        return True
    try:
        ip = socket.inet_aton(host) if ":" not in host else None
        del ip
    except OSError:
        pass
    import ipaddress

    try:
        addr = ipaddress.ip_address(host)
        return addr.is_loopback or addr.is_private or addr.is_link_local
    except ValueError:
        return False


def _guarded_connect(self, address):
    if not _is_local(address):
        BLOCKED.append(f"connect {address}")
        raise OSError(f"offline guard blocked connection to {address}")
    return _original_connect(self, address)


def _guarded_connect_ex(self, address):
    if not _is_local(address):
        BLOCKED.append(f"connect_ex {address}")
        raise OSError(f"offline guard blocked connection to {address}")
    return _original_connect_ex(self, address)


def _guarded_getaddrinfo(host, *args, **kwargs):
    if host not in ("localhost", "127.0.0.1", "::1", None):
        BLOCKED.append(f"dns {host}")
        raise socket.gaierror(f"offline guard blocked DNS lookup for {host}")
    return _original_getaddrinfo(host, *args, **kwargs)


def install_guard() -> None:
    socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = _guarded_getaddrinfo  # type: ignore[assignment]


async def run(seconds: float) -> int:
    import numpy as np
    import soundfile as sf

    from guru.asr import WhisperAsr
    from guru.bus import EventBus
    from guru.config import load_settings
    from guru.education.service import EducationService
    from guru.pipeline import ClassroomPipeline
    from guru.storage import Storage
    from guru.translate import MarianTranslator
    from guru.tts import PiperTts

    settings = load_settings()
    missing = []
    for label, path in (
        ("ASR", settings.asr.model_dir),
        ("translation", settings.translate.model_dir),
        ("TTS", settings.tts.voice_onnx),
    ):
        p = settings.resolve(path)
        if not p or not p.exists():
            missing.append(f"{label}: {p}")
    wav = ROOT / "data" / "demo" / "lecture.wav"
    if not wav.is_file():
        missing.append(f"demo audio: {wav} (run scripts/make_demo_lecture.py)")
    if missing:
        print("Missing offline assets:")
        for m in missing:
            print("  -", m)
        return 2

    storage = Storage(settings.resolve(settings.storage.db_path) or ROOT / "data" / "verify.db")
    bus = EventBus()
    asr = WhisperAsr(settings)
    translator = MarianTranslator(settings)
    tts = PiperTts(settings)
    pipeline = ClassroomPipeline(settings, bus, storage, asr, translator, tts)
    education = EducationService(settings, storage, translator)

    print("loading models from disk (no network allowed)…")
    for name, model in (("asr", asr), ("translator", translator), ("tts", tts)):
        try:
            model.load()
            print(f"  {name}: loaded")
        except Exception as exc:  # noqa: BLE001  # report, then exit non-zero
            print(f"  {name}: FAILED -> {exc}")
            return 3

    sid = storage.create_session("Offline verification", "tok", "VERIFY")
    pipeline.start(sid, asyncio.get_running_loop())
    audio, sr = sf.read(str(wav), dtype="float32")
    if sr != 16000:
        print(f"demo audio must be 16 kHz, got {sr}")
        return 2
    limit = int(seconds * sr)
    chunk = 1600
    for i in range(0, min(len(audio), limit), chunk):
        pipeline.submit_audio(np.asarray(audio[i : i + chunk]))
    await asyncio.sleep(0.3)
    await asyncio.to_thread(pipeline.stop)

    segments = storage.list_segments(sid)
    print(f"transcribed {len(segments)} segments")
    if segments:
        print(f"  EN: {segments[-1]['en'][:100]}")
        print(f"  BN: {segments[-1]['bn'][:100]}")
    summary = await asyncio.to_thread(education.generate_summary, sid, False)
    print(f"summary sentences: {len(summary['summary_bn'])}, key terms: {len(summary['key_terms'])}")
    quiz = await asyncio.to_thread(education.generate_quiz, sid, 4, True)
    print(f"quiz questions: {len(quiz['questions'])}")
    storage.purge_session(sid, ROOT / "data" / "audio")

    if BLOCKED:
        print("\nFAIL: network access was attempted:")
        for item in BLOCKED[:10]:
            print("  -", item)
        return 1
    print("\nPASS: models loaded and pipeline ran with zero external connections.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=25.0)
    args = ap.parse_args()
    install_guard()
    try:
        return asyncio.run(run(args.seconds))
    finally:
        socket.socket.connect = _original_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = _original_connect_ex  # type: ignore[method-assign]
        socket.getaddrinfo = _original_getaddrinfo  # type: ignore[assignment]


if __name__ == "__main__":
    raise SystemExit(main())
