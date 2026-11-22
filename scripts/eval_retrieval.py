#!/usr/bin/env python3
"""Compare the shipped lexical retrieval with a local multilingual encoder.

The classroom corpus is small (a few hundred pages), so the shipped retriever is a
deterministic lexical/vector hybrid with no model in the runtime. This script
answers the only question that could justify changing that: does a multilingual
encoder actually retrieve better, including Bangla queries against English slides,
and what does it cost in RAM, disk and latency?

    .venv/bin/python scripts/eval_retrieval.py --download   # fetch the encoder
    .venv/bin/python scripts/eval_retrieval.py              # measure

Nothing here is imported by the application. The encoder is optional and only
loaded when the model directory exists, so the lexical path is always the
deterministic fallback.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from guru.education.documents import search_chunks

DEFAULT_MODEL_DIR = Path("/tmp/guru-embed/paraphrase-multilingual-MiniLM-L12-v2")

# Six concepts, each as an English slide page and its Bangla counterpart.
CONCEPTS: list[dict[str, str]] = [
    {
        "en": "Photosynthesis is the process by which green plants make food using "
              "sunlight. Chlorophyll is the green pigment that captures light energy.",
        "bn": "সালোকসংশ্লেষণ হলো সেই প্রক্রিয়া যেখানে সবুজ উদ্ভিদ সূর্যালোক ব্যবহার করে "
              "খাদ্য তৈরি করে। ক্লোরোফিল সবুজ রঞ্জক যা আলোর শক্তি ধারণ করে।",
    },
    {
        "en": "The water cycle moves water from the sea to the sky and back. Heat causes "
              "evaporation, the vapour condenses into clouds, and rain returns it to the ground.",
        "bn": "জলচক্র সমুদ্রের জল থেকে আকাশে এবং আবার মাটিতে ফিরিয়ে আনে। তাপে বাষ্পীভবন ঘটে, "
              "বাষ্প ঘনীভূত হয়ে মেঘ হয় এবং বৃষ্টি তা মাটিতে ফিরিয়ে দেয়।",
    },
    {
        "en": "Newton's second law says force equals mass times acceleration. A larger "
              "force produces a larger acceleration for the same mass.",
        "bn": "নিউটনের দ্বিতীয় সূত্র বলে বল = ভর × ত্বরণ। একই ভরের জন্য বড় বল বড় ত্বরণ তৈরি করে।",
    },
    {
        "en": "The stomach breaks food down with acid and enzymes. Digestion continues in "
              "the small intestine, where nutrients are absorbed into the blood.",
        "bn": "পেট অ্যাসিড ও এনজাইম দিয়ে খাদ্য ভাঙে। পরিপাক ক্ষুদ্রান্ত্রে চলতে থাকে, যেখানে "
              "পুষ্টি রক্তে শোষিত হয়।",
    },
    {
        "en": "Electric current is the flow of charge. Voltage pushes the current and "
              "resistance opposes it, which is Ohm's law.",
        "bn": "তড়িৎ প্রবাহ হলো আধানের প্রবাহ। ভোল্টেজ প্রবাহকে চালায় এবং রোধ বাধা দেয়, "
              "এটাই ওহমের সূত্র।",
    },
    {
        "en": "The nitrogen cycle depends on bacteria in the soil. They convert nitrogen "
              "from the air into compounds that plants can use for growth.",
        "bn": "নাইট্রোজেন চক্র মাটির ব্যাকটেরিয়ার উপর নির্ভর করে। তারা বাতাসের নাইট্রোজেনকে "
              "এমন যৌগে বদলে দেয় যা উদ্ভিদ বৃদ্ধির জন্য ব্যবহার করতে পারে।",
    },
]

QUERIES: list[dict[str, object]] = [
    {"text": "how do plants make food from sunlight", "target": 0, "lang": "en"},
    {"text": "why does it rain and where does vapour go", "target": 1, "lang": "en"},
    {"text": "force mass and acceleration relationship", "target": 2, "lang": "en"},
    {"text": "which organ digests food and absorbs nutrients", "target": 3, "lang": "en"},
    {"text": "what opposes electric current", "target": 4, "lang": "en"},
    {"text": "role of bacteria in soil for plants", "target": 5, "lang": "en"},
    {"text": "উদ্ভিদ কীভাবে সূর্যালোক দিয়ে খাদ্য তৈরি করে", "target": 0, "lang": "bn"},
    {"text": "বৃষ্টি কেন হয় ও বাষ্প কোথায় যায়", "target": 1, "lang": "bn"},
    {"text": "বল ভর ও ত্বরণের সম্পর্ক", "target": 2, "lang": "bn"},
    {"text": "কোন অঙ্গ খাদ্য পরিপাক ও পুষ্টি শোষণ করে", "target": 3, "lang": "bn"},
    {"text": "তড়িৎ প্রবাহে বাধা দেয় কী", "target": 4, "lang": "bn"},
    {"text": "মাটির ব্যাকটেরিয়ার ভূমিকা", "target": 5, "lang": "bn"},
]


def corpus(lang: str) -> list[dict]:
    return [
        {"id": f"c{i}", "document": f"lesson{i + 1}.pptx", "page": i + 1, "text": c[lang]}
        for i, c in enumerate(CONCEPTS)
    ]


def rank_lexical(query: str, chunks: list[dict], k: int = 5) -> list[int]:
    hits = search_chunks(query, chunks, k=k)
    by_doc = {c["id"]: i for i, c in enumerate(chunks)}
    return [by_doc[h["chunk_id"]] for h in hits if h["chunk_id"] in by_doc]


def bilingual_corpus() -> tuple[list[dict], float]:
    """English pages with the Bangla side the upload path now stores.

    The shipped Marian model is en->bn only, so the fix indexes the Bangla side of
the corpus instead of translating the Bangla query. This measures exactly that
    change: same retriever, same queries, Bangla text present alongside English.
    """
    from guru.config import load_settings
    from guru.translate import build_translator

    chunks = corpus("en")
    translator = build_translator(load_settings())
    if not getattr(translator, "available", False):
        return [], 0.0
    t0 = time.perf_counter()
    results = translator.translate_batch([c["text"] for c in chunks])
    elapsed_ms = (time.perf_counter() - t0) * 1000
    for chunk, result in zip(chunks, results):
        chunk["text_bn"] = result.text if not result.skipped else ""
    return chunks, elapsed_ms


class Encoder:
    def __init__(self, model_dir: Path) -> None:
        import torch
        from transformers import AutoModel, AutoTokenizer

        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True)
        self.model = AutoModel.from_pretrained(str(model_dir), local_files_only=True).eval()
        self.model_dir = model_dir

    def encode(self, texts: list[str]) -> Any:
        torch = self.torch
        with torch.no_grad():
            batch = self.tok(texts, padding=True, truncation=True, max_length=256,
                             return_tensors="pt")
            hidden = self.model(**batch).last_hidden_state
            mask = batch["attention_mask"].unsqueeze(-1).float()
            pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-6)
            return torch.nn.functional.normalize(pooled, dim=1)

    def rank(self, query: str, doc_vectors, k: int = 5) -> list[int]:
        scores = (doc_vectors @ self.encode([query])[0]).tolist()
        return sorted(range(len(scores)), key=lambda i: -scores[i])[:k]


def score(ranks: list[list[int]], queries: list[dict], lang: str | None = None) -> dict:
    usable = [
        (r, q) for r, q in zip(ranks, queries) if lang is None or q["lang"] == lang
    ]
    if not usable:
        return {"n": 0, "top1": 0.0, "mrr": 0.0}
    top1 = sum(1 for r, q in usable if r and r[0] == q["target"]) / len(usable)
    mrr = statistics.fmean(
        (1.0 / (r.index(q["target"]) + 1)) if q["target"] in r else 0.0 for r, q in usable
    )
    return {"n": len(usable), "top1": round(top1, 3), "mrr": round(mrr, 3)}


def timed(fn, repeats: int = 5) -> tuple[object, float]:
    best = float("inf")
    out = None
    for _ in range(repeats):
        t0 = time.perf_counter()
        out = fn()
        best = min(best, (time.perf_counter() - t0) * 1000)
    return out, best


def dir_size_mb(path: Path) -> float:
    return round(sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e6, 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=str(DEFAULT_MODEL_DIR))
    ap.add_argument("--download", action="store_true",
                    help="fetch the multilingual encoder before measuring")
    args = ap.parse_args()
    model_dir = Path(args.model_dir)

    if args.download:
        from huggingface_hub import snapshot_download

        print(f"downloading encoder into {model_dir} …")
        t0 = time.perf_counter()
        # Only the PyTorch weights and tokenizer: the repo also ships TF, Flax and
        # ONNX copies, which would quadruple the download for no benefit here.
        snapshot_download(
            repo_id="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            local_dir=str(model_dir),
            allow_patterns=["*.json", "*.txt", "*.bin", "sentencepiece*", "vocab*"],
        )
        print(f"  done in {time.perf_counter() - t0:.1f}s, {dir_size_mb(model_dir)} MB")

    results: dict = {"encoder_available": model_dir.is_dir()}
    rss0 = psutil.Process().memory_info().rss / 1e6
    encoder = None
    if model_dir.is_dir():
        t0 = time.perf_counter()
        try:
            encoder = Encoder(model_dir)
            results["model_load_s"] = round(time.perf_counter() - t0, 2)
            results["model_size_mb"] = dir_size_mb(model_dir)
            results["rss_after_load_mb"] = round(psutil.Process().memory_info().rss / 1e6, 1)
            results["rss_delta_mb"] = round(results["rss_after_load_mb"] - rss0, 1)
        except Exception as exc:  # noqa: BLE001 - a missing encoder is a valid outcome
            results["encoder_error"] = str(exc)
            encoder = None

    rows = []
    for corpus_lang in ("en", "bn"):
        chunks = corpus(corpus_lang)
        vectors = None
        if encoder is not None:
            vectors, index_ms = timed(lambda _c=chunks: encoder.encode([x["text"] for x in _c]))
            results[f"encoder_index_ms_{corpus_lang}"] = round(index_ms, 1)
        _, lex_index_ms = timed(lambda _c=chunks: [x["text"] for x in _c])
        results[f"lexical_index_ms_{corpus_lang}"] = round(lex_index_ms, 1)

        lex_ranks = [rank_lexical(q["text"], chunks) for q in QUERIES]
        enc_ranks = [encoder.rank(q["text"], vectors) for q in QUERIES] if encoder else None

        for lang_filter in ("en", "bn"):
            label = f"{lang_filter}->{corpus_lang}"
            row = {"pair": label, "lexical": score(lex_ranks, QUERIES, lang_filter)}
            if enc_ranks is not None:
                row["encoder"] = score(enc_ranks, QUERIES, lang_filter)
            rows.append(row)

        if encoder is not None:
            _, q_ms = timed(lambda _v=vectors: [encoder.rank(q["text"], _v) for q in QUERIES])
            results[f"encoder_query_ms_{corpus_lang}"] = round(q_ms / len(QUERIES), 1)
        _, lex_q_ms = timed(lambda _c=chunks: [rank_lexical(q["text"], _c) for q in QUERIES])
        results[f"lexical_query_ms_{corpus_lang}"] = round(lex_q_ms / len(QUERIES), 1)

    # Before/after for the measured weak case: Bangla query, English material.
    bi_chunks, bi_translate_ms = bilingual_corpus()
    if bi_chunks:
        before = [rank_lexical(q["text"], corpus("en")) for q in QUERIES]
        after = [rank_lexical(q["text"], bi_chunks) for q in QUERIES]
        _, bi_q_ms = timed(lambda: [rank_lexical(q["text"], bi_chunks) for q in QUERIES])
        results["bilingual_index_translate_ms"] = round(bi_translate_ms, 1)
        results["bilingual_query_ms"] = round(bi_q_ms / len(QUERIES), 1)
        rows.append({
            "pair": "bn->en",
            "lexical": score(before, QUERIES, "bn"),
            "bilingual": score(after, QUERIES, "bn"),
        })
        rows.append({
            "pair": "en->en",
            "lexical": score(before, QUERIES, "en"),
            "bilingual": score(after, QUERIES, "en"),
        })

    results["rows"] = rows
    print("\n=== relevance (top-1 / MRR over 6 queries per row) ===")
    print(f"{'pair':10} {'lexical':>18} {'bilingual':>18} {'encoder':>18}")
    for row in rows:
        lex = row["lexical"]
        bi = row.get("bilingual")
        enc = row.get("encoder")
        lex_s = f"{lex['top1']:.3f} / {lex['mrr']:.3f}"
        bi_s = f"{bi['top1']:.3f} / {bi['mrr']:.3f}" if bi else "n/a"
        enc_s = f"{enc['top1']:.3f} / {enc['mrr']:.3f}" if enc else "n/a"
        print(f"{row['pair']:10} {lex_s:>18} {bi_s:>18} {enc_s:>18}")

    print("\n=== cost ===")
    for key in ("model_size_mb", "model_load_s", "rss_delta_mb",
                "lexical_index_ms_en", "encoder_index_ms_en",
                "lexical_query_ms_en", "encoder_query_ms_en",
                "bilingual_index_translate_ms", "bilingual_query_ms"):
        if key in results:
            print(f"{key:22} {results[key]}")

    out_dir = ROOT / "docs" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "retrieval_benchmark.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
