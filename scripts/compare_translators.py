#!/usr/bin/env python3
"""Side-by-side EN→BN translation comparison (Marian vs NLLB-600M).

Reports per-sentence latency and output so a human can judge adequacy. There is
no reference translation here, so this deliberately does **not** print a BLEU
score that would look more authoritative than the evidence allows.

    .venv/bin/python scripts/compare_translators.py
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from guru.config import load_settings
from guru.translate import MarianTranslator

SENTENCES = [
    "Photosynthesis is the process by which green plants make their own food using sunlight.",
    "Plants use a green pigment called chlorophyll to capture light energy.",
    "During photosynthesis, plants take in carbon dioxide from the air through tiny pores called stomata.",
    "Water is absorbed from the soil by the roots.",
    "Glucose is a simple sugar that stores chemical energy.",
    "Respiration is the process by which living cells release energy from glucose.",
    "The rate is fastest at around 25 to 35 degrees Celsius.",
    "Above 40 degrees Celsius, the enzymes stop working properly.",
    "An enzyme is a protein that speeds up a chemical reaction.",
    "The Pythagorean theorem states that a squared plus b squared equals c squared.",
    "A triangle has three sides and the sum of its angles is 180 degrees.",
    "Please copy the diagram from the board and label the stomata and chloroplasts.",
]


class NllbTranslator:
    """CTranslate2 NLLB-200 with the official SentencePiece tokenizer."""

    def __init__(self, model_dir: Path, tokenizer_dir: Path, src="eng_Latn", tgt="ben_Beng") -> None:
        import ctranslate2
        from transformers import AutoTokenizer

        self.tok = AutoTokenizer.from_pretrained(str(tokenizer_dir), src_lang=src, local_files_only=True)
        self.model = ctranslate2.Translator(str(model_dir), device="cpu", compute_type="int8",
                                            inter_threads=1, intra_threads=4)
        self.tgt = tgt

    def translate(self, text: str):
        tokens = self.tok.convert_ids_to_tokens(self.tok.encode(text))
        t0 = time.perf_counter()
        res = self.model.translate_batch(
            [tokens], target_prefix=[[self.tgt]], beam_size=4, max_decoding_length=256
        )
        ms = (time.perf_counter() - t0) * 1000
        out = res[0].hypotheses[0]
        out = [t for t in out if t != self.tgt]
        text_out = self.tok.decode(self.tok.convert_tokens_to_ids(out), skip_special_tokens=True)
        return text_out.strip(), ms


def main() -> int:
    settings = load_settings()
    results: dict[str, list] = {"sentences": SENTENCES, "outputs": {}, "latency_ms": {}}

    marian = MarianTranslator(settings)
    marian.load()
    outs, times = [], []
    for s in SENTENCES:
        r = marian.translate(s)
        outs.append(r.text)
        times.append(r.latency_ms)
    results["outputs"]["marian-opus-mt-en-bn"] = outs
    results["latency_ms"]["marian-opus-mt-en-bn"] = times

    nllb_dir = ROOT / "models" / "nllb-600m-ct2-int8"
    tok_dir = ROOT / "models" / "nllb-tokenizer"
    if nllb_dir.is_dir() and tok_dir.is_dir():
        nllb = NllbTranslator(nllb_dir, tok_dir)
        outs, times = [], []
        for s in SENTENCES:
            text, ms = nllb.translate(s)
            outs.append(text)
            times.append(round(ms, 1))
        results["outputs"]["nllb-200-distilled-600M-int8"] = outs
        results["latency_ms"]["nllb-200-distilled-600M-int8"] = times
    else:
        print("NLLB model missing; run the download step in docs/MODEL_SELECTION.md")

    for i, s in enumerate(SENTENCES):
        print(f"\nEN  {s}")
        for name, outs in results["outputs"].items():
            print(f"    [{name} {results['latency_ms'][name][i]:.0f} ms]")
            print(f"    {outs[i]}")
    print("\n=== latency summary ===")
    for name, times in results["latency_ms"].items():
        print(f"{name}: p50 {statistics.median(times):.0f} ms  max {max(times):.0f} ms")

    out = ROOT / "data" / "eval" / "translation_compare.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
