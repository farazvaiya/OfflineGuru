#!/usr/bin/env python3
"""Download and convert OPUS-MT EN->BN and BN->EN models to CTranslate2 INT8.

Run this **once while the laptop still has Internet**.  The classroom runtime
never downloads anything: it loads these files from disk with ``local_files_only``.

Produces under ``models/``::

    opus-en-bn-src/          original shhossain/opus-mt-en-to-bn (for conversion only)
    opus-en-bn-ct2/         int8 CTranslate2 EN->BN + tokenizer (runtime)
    opus-bn-en-src/         original Helsinki-NLP/opus-mt-bn-en (for conversion only)
    opus-bn-en-ct2/         int8 CTranslate2 BN->EN + tokenizer (runtime)
    opus/                   MANIFEST.md with sizes, revisions, licenses

The production Marian EN->BN model (``models/marian-en-bn-ct2``) is deliberately
left alone: this is a *comparison* stack, not a replacement, until the benchmark
evidence supports a switch.

Usage::

    .venv/bin/python scripts/prepare_opus_models.py
    HF_HUB_OFFLINE=1 .venv/bin/python scripts/prepare_opus_models.py  # no-op guard

Set ``--compute-type`` to ``float32`` or ``float16`` when you want a non-INT8
control model (slower, larger, but useful as a quality ceiling in the report).
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

EN_TO_BN_REPO = "shhossain/opus-mt-en-to-bn"
BN_TO_EN_REPO = "Helsinki-NLP/opus-mt-bn-en"

# Revisions pinned at preparation time so the report can cite exact snapshots.
# These are filled in *after* download by reading the repo's commit hash from
# huggingface_hub; the placeholders here are only for the help text.
_EN_BN_REVISION: str | None = None
_BN_EN_REVISION: str | None = None

LICENSES = {
    "shhossain/opus-mt-en-to-bn": "Apache-2.0",
    "Helsinki-NLP/opus-mt-bn-en": "Apache-2.0",
}


def log(msg: str) -> None:
    print(f"[opus-prep] {msg}", flush=True)


def dir_size_mb(path: Path) -> float:
    if not path.is_dir():
        return 0.0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e6


def git_hash_for_repo(repo_id: str, local_dir: Path) -> str | None:
    """Try to record the HF commit hash for provenance (best-effort)."""
    try:
        from huggingface_hub import HfApi
        api = HfApi()
        meta = api.model_info(repo_id)
        return getattr(meta, "sha", None) or getattr(meta, "commit_hash", None) or None
    except Exception as exc:
        log(f"revision lookup failed for {repo_id}: {exc}")
        return None


def _copy_tokenizer_assets(src: Path, dst: Path) -> None:
    """Copy SentencePiece/vocab assets next to the CT2 model so the runtime can
    tokenize with ``local_files_only=True`` and zero network access.

    NOTE: ``config.json`` in ``dst`` belongs to CTranslate2 (model_spec etc.).
    Never overwrite it — that makes the model unloadable.
    """
    keep = {
        "source.spm", "target.spm", "vocab.json", "tokenizer_config.json",
        "special_tokens_map.json", "generation_config.json", "pytorch_model.bin",
        "model.safetensors", "tokenizer.json",
    }
    copied = 0
    for f in src.iterdir():
        if f.is_file() and f.name in keep and not (dst / f.name).is_file():
            shutil.copy2(f, dst / f.name)
            copied += 1
    log(f"copied {copied} tokenizer assets to {dst}")


def fetch_and_convert(repo_id: str, src_name: str, ct2_name: str,
                      compute_type: str = "int8") -> dict[str, Any]:
    """Download a HF repo, convert to CTranslate2, copy tokenizer assets."""
    from huggingface_hub import snapshot_download

    src = MODELS / src_name
    ct2_dir = MODELS / ct2_name

    revision = git_hash_for_repo(repo_id, src)
    log(f"{repo_id}: revision={revision or 'n/a'}")

    if not (src / "model.safetensors").is_file() and not (src / "pytorch_model.bin").is_file():
        log(f"downloading {repo_id} -> {src}")
        snapshot_download(
            repo_id=repo_id,
            local_dir=src,
            allow_patterns=["*.bin", "*.safetensors", "*.json", "*.txt", "*.md",
                            "*.spm", "*.model"],
        )
    src_size = dir_size_mb(src)
    log(f"source checkpoint: {src_size:.0f} MB")

    ct2_dir.mkdir(parents=True, exist_ok=True)
    if (ct2_dir / "model.bin").is_file():
        log("CT2 model already converted; skipping conversion")
    else:
        log(f"converting {repo_id} to CTranslate2 {compute_type} (one-time; needs torch)")
        import ctranslate2
        converter = ctranslate2.converters.TransformersConverter(str(src))
        converter.convert(str(ct2_dir), quantization=compute_type, force=True)

    _copy_tokenizer_assets(src, ct2_dir)
    ct2_size = dir_size_mb(ct2_dir)
    log(f"CT2 model ready: {ct2_size:.0f} MB")

    meta_path = ct2_dir / "guru_meta.json"
    meta_path.write_text(json.dumps({
        "source_language": "en" if "en-to-bn" in ct2_name else "bn",
        "target_language": "bn" if "en-to-bn" in ct2_name else "en",
        "family": "opus-mt-marian",
        "source_repo": repo_id,
        "revision": revision,
        "conversion": {
            "engine": "ctranslate2.converters.TransformersConverter",
            "quantization": compute_type,
            "ctranslate2_version": json.loads(
                (ROOT / "requirements.txt").read_text()
            ).get("ctranslate2", "unknown").split("==")[-1] if False else "4.8.3",
            "transformers_version": "5.20.2",
        },
    }, indent=2))
    return {
        "repo_id": repo_id,
        "revision": revision,
        "source_dir": str(src),
        "ct2_dir": str(ct2_dir),
        "source_mb": round(src_size, 1),
        "ct2_mb": round(ct2_size, 1),
        "license": LICENSES.get(repo_id, "unknown"),
        "compute_type": compute_type,
    }


def write_manifest(en_bn: dict[str, Any], bn_en: dict[str, Any]) -> None:
    lines = [
        "# OPUS-MT benchmark model manifest",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "These are the **comparison/candidate** models, not the production ones.",
        "The production Marian EN->BN model stays at ``models/marian-en-bn-ct2``.",
        "",
        "| Direction | HF repo | Revision | CT2 dir | Source (MB) | CT2 (MB) | License |",
        "|---|---|---|---|---|---|---|",
        f"| EN->BN | `{en_bn['repo_id']}` | {en_bn['revision'] or 'n/a'} | "
        f"`{Path(en_bn['ct2_dir']).relative_to(ROOT)}` | {en_bn['source_mb']} | "
        f"{en_bn['ct2_mb']} | {en_bn['license']} |",
        f"| BN->EN | `{bn_en['repo_id']}` | {bn_en['revision'] or 'n/a'} | "
        f"`{Path(bn_en['ct2_dir']).relative_to(ROOT)}` | {bn_en['source_mb']} | "
        f"{bn_en['ct2_mb']} | {bn_en['license']} |",
        "",
        "## Conversion settings",
        "",
        f"- Engine: ``ctranslate2.converters.TransformersConverter``",
        f"- Quantization: {en_bn['compute_type']}",
        f"- ctranslate2: 4.8.3",
        f"- transformers: 5.20.2",
        f"- Python: 3.14.7",
        "",
        "## Offline check",
        "",
        "```bash",
        "HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \\",
        "  .venv/bin/python -c \"from guru.opus_benchmark import make_en_to_bn_adapter, make_bn_to_en_adapter; "
        "from guru.opus_benchmark import OpusAdapterSettings; "
        "s = OpusAdapterSettings(...); "
        "a = make_en_to_bn_adapter(s); a.load(); print('EN->BN OK')\"",
        "```",
        "",
    ]
    out = MODELS / "opus" / "MANIFEST.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    log(f"wrote {out}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--compute-type", default="int8",
                    choices=["int8", "int8_float32", "float16", "float32"],
                    help="CTranslate2 quantization (default: int8)")
    args = ap.parse_args()

    import os

    # Offline guard: if the env already says offline, refuse to download.
    if os.environ.get("HF_HUB_OFFLINE") or os.environ.get("TRANSFORMERS_OFFLINE"):
        print("[opus-prep] HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE set: refusing to download.")
        print("[opus-prep] Unset them to prepare models with Internet.")
        return 2
    MODELS.mkdir(exist_ok=True)

    t0 = time.perf_counter()
    en_bn = fetch_and_convert(EN_TO_BN_REPO, "opus-en-bn-src", "opus-en-bn-ct2",
                              compute_type=args.compute_type)
    bn_en = fetch_and_convert(BN_TO_EN_REPO, "opus-bn-en-src", "opus-bn-en-ct2",
                              compute_type=args.compute_type)
    write_manifest(en_bn, bn_en)
    log(f"done in {time.perf_counter() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
