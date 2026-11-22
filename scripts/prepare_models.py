#!/usr/bin/env python3
"""Download and prepare every offline model asset.

Run this **once, while the laptop still has Internet**. The classroom runtime
never downloads anything: it loads these files from disk and refuses to fall
back to a network model.

Artifacts produced under ``models/``::

    whisper-tiny/            faster-whisper tiny  (~75 MB, benchmark only)
    whisper-base/            faster-whisper base  (~145 MB, benchmark only)
    whisper-small/           faster-whisper small (~490 MB, default)
    whisper-base-bn-ct2/     Bengali whisper-base fine-tune int8 (~80 MB, Bengali ASR)
    marian-en-bn-src/        original Marian checkpoint (for conversion only)
    marian-en-bn-ct2/        int8 CTranslate2 model + tokenizer (runtime)
    piper/bn_BD-google-medium.onnx{,.json}   Bangla TTS voice
    MANIFEST.md              sizes, licenses, provenance

Usage::

    .venv/bin/python scripts/prepare_models.py                # everything
    .venv/bin/python scripts/prepare_models.py --skip-whisper-bench
    .venv/bin/python scripts/prepare_models.py --only whisper,piper
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"

WHISPER_REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
}
MARIAN_REPO = "shhossain/opus-mt-en-to-bn"
# Bengali ASR fine-tune. Stock whisper-base cannot emit Bengali script at all
# (see reports/BANGLA_TO_ENGLISH_ROOT_CAUSE.md), so Bengali decodes use this.
BN_ASR_REPO = "Faiza07/whisper-base-bengali"
BN_ASR_FILES = ["model.safetensors", "config.json", "generation_config.json",
                "tokenizer_config.json", "special_tokens_map.json"]
PIPER_REPO = "rhasspy/piper-voices"
PIPER_FILES = [
    "bn/bn_BD/google/medium/bn_BD-google-medium.onnx",
    "bn/bn_BD/google/medium/bn_BD-google-medium.onnx.json",
    "bn/bn_BD/google/medium/MODEL_CARD",
]
# English voice used only to synthesise repeatable demo/evaluation lecture audio
# (see scripts/make_demo_lecture.py). Not required at classroom runtime.
PIPER_EN_FILES = [
    "en/en_US/lessac/medium/en_US-lessac-medium.onnx",
    "en/en_US/lessac/medium/en_US-lessac-medium.onnx.json",
]

LICENSES = {
    "faster-whisper (Systran)": "MIT (runtime); Whisper weights MIT",
    "opus-mt-en-to-bn (shhossain)": "Apache-2.0",
    "piper-voices bn_BD-google-medium": "MIT runtime; voice from Google Bengali dataset (see MODEL_CARD)",
    "whisper-base-bengali (Faiza07)": "NOT DECLARED on the model card - vet before redistribution",
}


def log(msg: str) -> None:
    print(f"[prepare] {msg}", flush=True)


def dir_size_mb(path: Path) -> float:
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return total / 1e6


def ensure_hf() -> None:
    try:
        import huggingface_hub  # noqa: F401
    except ImportError:  # pragma: no cover - setup guidance
        sys.exit("huggingface_hub missing. Run: .venv/bin/pip install huggingface_hub")


def fetch_whisper(sizes: list[str]) -> dict[str, float]:
    from huggingface_hub import snapshot_download

    out: dict[str, float] = {}
    for size in sizes:
        repo = WHISPER_REPOS[size]
        dest = MODELS / f"whisper-{size}"
        if (dest / "model.bin").is_file():
            out[size] = dir_size_mb(dest)
            log(f"whisper-{size}: already present ({out[size]:.0f} MB)")
            continue
        log(f"downloading {repo} -> {dest}")
        snapshot_download(
            repo_id=repo,
            local_dir=dest,
            allow_patterns=["*.bin", "*.json", "*.txt", "*.md"],
        )
        out[size] = dir_size_mb(dest)
        log(f"whisper-{size}: {out[size]:.0f} MB")
    return out


def fetch_marian() -> float:
    """Download the Marian EN->BN checkpoint and convert it to int8 CTranslate2."""
    from huggingface_hub import snapshot_download

    src = MODELS / "marian-en-bn-src"
    ct2_dir = MODELS / "marian-en-bn-ct2"

    if not (src / "model.safetensors").is_file():
        log(f"downloading {MARIAN_REPO}")
        snapshot_download(repo_id=MARIAN_REPO, local_dir=src)
    size = dir_size_mb(src)
    log(f"source checkpoint: {size:.0f} MB")

    if (ct2_dir / "model.bin").is_file():
        log("CT2 model already converted")
    else:
        log("converting to CTranslate2 int8 (one-time; needs torch)")
        import ctranslate2

        ct2_dir.mkdir(parents=True, exist_ok=True)
        converter = ctranslate2.converters.TransformersConverter(str(src))
        converter.convert(str(ct2_dir), quantization="int8", force=True)

    # Copy the SentencePiece / vocab assets next to the CT2 model so the runtime
    # can tokenize with local_files_only=True and zero network access.
    # NOTE: ``config.json`` in ct2_dir belongs to CTranslate2 (model_spec etc.).
    # Never overwrite it — that makes the model unloadable.
    for name in ("source.spm", "target.spm", "vocab.json", "tokenizer_config.json",
                 "special_tokens_map.json", "generation_config.json"):
        f = src / name
        if f.is_file():
            shutil.copy2(f, ct2_dir / name)
    meta = {"source_language": "en", "target_language": "bn", "family": "marian",
            "source_repo": MARIAN_REPO}
    (ct2_dir / "guru_meta.json").write_text(json.dumps(meta, indent=2))
    log(f"CT2 model ready: {dir_size_mb(ct2_dir):.0f} MB")
    return dir_size_mb(ct2_dir)


def fetch_bn_asr() -> float:
    """Download the Bengali whisper-base fine-tune and convert it to int8 CT2.

    The fine-tune's vocabulary is byte-identical to ``models/whisper-base``, so
    the tokenizer assets are copied from there instead of re-downloaded.
    """
    from huggingface_hub import snapshot_download

    src = MODELS / "_bn_asr_src"
    ct2_dir = MODELS / "whisper-base-bn-ct2"
    if not (src / "model.safetensors").is_file():
        log(f"downloading {BN_ASR_REPO}")
        snapshot_download(repo_id=BN_ASR_REPO, local_dir=src, allow_patterns=BN_ASR_FILES)
    if (ct2_dir / "model.bin").is_file():
        log("Bengali CT2 model already converted")
    else:
        log("converting Bengali model to CTranslate2 int8 (one-time; needs torch)")
        import ctranslate2

        ct2_dir.mkdir(parents=True, exist_ok=True)
        converter = ctranslate2.converters.TransformersConverter(str(src))
        converter.convert(str(ct2_dir), quantization="int8", force=True)
    base = MODELS / "whisper-base"
    for name in ("tokenizer.json", "vocabulary.txt", "preprocessor_config.json"):
        if (base / name).is_file():
            shutil.copy2(base / name, ct2_dir / name)
    meta = {"language": "bn", "family": "whisper", "source_repo": BN_ASR_REPO,
            "license": "not declared on the model card",
            "self_reported_wer": 0.2932}
    (ct2_dir / "guru_meta.json").write_text(json.dumps(meta, indent=2))
    log(f"Bengali CT2 model ready: {dir_size_mb(ct2_dir):.0f} MB")
    return dir_size_mb(ct2_dir)


def fetch_piper(with_en: bool = False) -> float:
    from huggingface_hub import hf_hub_download

    dest = MODELS / "piper"
    dest.mkdir(parents=True, exist_ok=True)
    files = PIPER_FILES + (PIPER_EN_FILES if with_en else [])
    for rel in files:
        local = dest / Path(rel).name
        if local.is_file():
            log(f"piper: {local.name} already present")
            continue
        log(f"downloading {rel}")
        path = hf_hub_download(repo_id=PIPER_REPO, filename=rel)
        shutil.copy2(path, local)
    return dir_size_mb(dest)


def write_manifest(whisper_sizes: dict[str, float], marian_mb: float, piper_mb: float,
                   bn_asr_mb: float = 0.0) -> None:
    lines = [
        "# Offline model manifest",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Host: {Path('/etc/os-release').read_text().splitlines()[0] if Path('/etc/os-release').is_file() else 'unknown'}",
        "",
        "These files are the complete set of AI assets required offline.",
        "Copy the whole `models/` directory to the target laptop to deploy.",
        "",
        "| Asset | Location | Size (MB) | License |",
        "|---|---|---|---|",
    ]
    for size, mb in whisper_sizes.items():
        lines.append(f"| faster-whisper {size} | `models/whisper-{size}/` | {mb:.0f} | MIT |")
    lines.append(f"| Marian EN→BN (CT2 int8) | `models/marian-en-bn-ct2/` | {marian_mb:.0f} | Apache-2.0 |")
    if bn_asr_mb:
        lines.append(
            f"| whisper-base-bengali (CT2 int8) | `models/whisper-base-bn-ct2/` | "
            f"{bn_asr_mb:.0f} | not declared - vet before redistribution |"
        )
    lines.append(f"| Piper bn_BD voice | `models/piper/` | {piper_mb:.0f} | MIT runtime / see card |")
    lines += [
        "",
        "## Provenance",
        "",
    ]
    for name, lic in LICENSES.items():
        lines.append(f"- **{name}** — {lic}")
    lines += [
        "",
        "## Offline check",
        "",
        "```bash",
        "HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \\",
        "  .venv/bin/python scripts/verify_offline.py",
        "```",
        "",
    ]
    (MODELS / "MANIFEST.md").write_text("\n".join(lines))
    log("wrote models/MANIFEST.md")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-en-demo-voice", action="store_true",
                    help="also fetch the English voice used to synthesise demo lecture audio")
    ap.add_argument("--only", default="whisper,bn-asr,marian,piper")
    ap.add_argument("--whisper-sizes", default="tiny,base,small")
    ap.add_argument("--skip-whisper-bench", action="store_true",
                    help="download only the default whisper model (small)")
    args = ap.parse_args()

    ensure_hf()
    MODELS.mkdir(exist_ok=True)
    wanted = {s.strip() for s in args.only.split(",") if s.strip()}

    sizes = ["small"] if args.skip_whisper_bench else [s.strip() for s in args.whisper_sizes.split(",")]
    whisper_sizes: dict[str, float] = {}
    marian_mb = 0.0
    piper_mb = 0.0
    bn_asr_mb = 0.0
    if "whisper" in wanted:
        whisper_sizes = fetch_whisper(sizes)
    if "bn-asr" in wanted:
        bn_asr_mb = fetch_bn_asr()
    if "marian" in wanted:
        marian_mb = fetch_marian()
    if "piper" in wanted:
        piper_mb = fetch_piper(with_en=args.with_en_demo_voice)
    write_manifest(whisper_sizes, marian_mb, piper_mb, bn_asr_mb)
    log("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
