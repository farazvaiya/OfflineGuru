"""English -> Bangla translation quality harness.

Runs the configured local translator over a fixed, author-written reference set
and reports automatic metrics plus a review sheet.

What is measured
----------------
* **chrF** (character n-gram F-score, n=1..6, beta=2) against the reference.
  chrF is used instead of BLEU because Bangla is morphologically rich and word
  order differs; chrF is the metric the Indic MT literature reports.
* **Number preservation** - every digit group in the source must appear in the
  output (as ASCII or Bengali digits).
* **Term coverage** - the share of expected Bangla terms present in the output.
* **Latency** - per-sentence translation time on this CPU.

What is NOT measured
--------------------
There is no human evaluation here. The reference translations were written by
the engineer who built the pipeline, not by a native-speaker panel, and the
report says so. The generated markdown includes an empty review column for a
teacher to fill in.

Usage
-----
    .venv/bin/python scripts/eval_translation_quality.py
    .venv/bin/python scripts/eval_translation_quality.py --backend nllb
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from guru.config import load_settings  # noqa: E402
from guru.translate import build_translator  # noqa: E402

BN_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")


def chrf(hypothesis: str, reference: str, max_n: int = 6, beta: float = 2.0) -> float:
    """Character n-gram F-score (standard chrF, whitespace included)."""
    hyp = " ".join(hypothesis.split())
    ref = " ".join(reference.split())
    if not hyp or not ref:
        return 0.0
    precisions: list[float] = []
    recalls: list[float] = []
    for n in range(1, max_n + 1):
        hyp_ngrams = [hyp[i : i + n] for i in range(max(0, len(hyp) - n + 1))]
        ref_ngrams = [ref[i : i + n] for i in range(max(0, len(ref) - n + 1))]
        if not hyp_ngrams or not ref_ngrams:
            continue
        ref_counts: dict[str, int] = {}
        for gram in ref_ngrams:
            ref_counts[gram] = ref_counts.get(gram, 0) + 1
        matches = 0
        for gram in hyp_ngrams:
            if ref_counts.get(gram, 0) > 0:
                ref_counts[gram] -= 1
                matches += 1
        precisions.append(matches / len(hyp_ngrams))
        recalls.append(matches / len(ref_ngrams))
    if not precisions:
        return 0.0
    precision = statistics.fmean(precisions)
    recall = statistics.fmean(recalls)
    if precision + recall == 0:
        return 0.0
    beta2 = beta * beta
    return (1 + beta2) * precision * recall / (beta2 * precision + recall)


def numbers_in(text: str) -> list[str]:
    """Digit groups, with Bengali digits normalised to ASCII."""
    normalised = text.translate(str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789"))
    out: list[str] = []
    current = ""
    for char in normalised:
        if char.isdigit():
            current += char
        elif current:
            out.append(current)
            current = ""
    if current:
        out.append(current)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/eval/en_bn_quality.json")
    parser.add_argument("--backend", default=None, help="marian | nllb | passthrough")
    parser.add_argument("--out", default="reports/results/translation_quality.json")
    parser.add_argument("--md", default="reports/TRANSLATION_QUALITY.md")
    args = parser.parse_args()

    dataset = json.loads((ROOT / args.dataset).read_text(encoding="utf-8"))
    settings = load_settings()
    if args.backend:
        settings.translate.backend = args.backend
    translator = build_translator(settings)
    translator.load()

    rows = []
    by_category: dict[str, list[float]] = defaultdict(list)
    latencies: list[float] = []
    for entry in dataset["entries"]:
        t0 = time.perf_counter()
        result = translator.translate(entry["en"])
        latency_ms = (time.perf_counter() - t0) * 1000
        latencies.append(latency_ms)
        score = chrf(result.text, entry["reference_bn"])
        by_category[entry["category"]].append(score)
        src_numbers = numbers_in(entry["en"])
        out_numbers = numbers_in(result.text)
        numbers_ok = all(n in out_numbers for n in src_numbers) if src_numbers else True
        terms = entry.get("terms") or []
        terms_hit = [t for t in terms if t in result.text]
        rows.append({
            "id": entry["id"],
            "category": entry["category"],
            "en": entry["en"],
            "reference_bn": entry["reference_bn"],
            "hypothesis_bn": result.text,
            "chrf": round(score, 4),
            "numbers_ok": numbers_ok,
            "terms_expected": terms,
            "terms_hit": terms_hit,
            "term_coverage": round(len(terms_hit) / len(terms), 3) if terms else None,
            "latency_ms": round(latency_ms, 1),
            "skipped": bool(getattr(result, "skipped", False)),
        })

    overall = statistics.fmean(r["chrf"] for r in rows)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "backend": settings.translate.backend,
        "model_dir": str(settings.translate.model_dir),
        "dataset": args.dataset,
        "entries": len(rows),
        "chrf_mean": round(overall, 4),
        "chrf_by_category": {
            k: round(statistics.fmean(v), 4) for k, v in sorted(by_category.items())
        },
        "numbers_preserved": sum(1 for r in rows if r["numbers_ok"]),
        "latency_ms": {
            "p50": round(statistics.median(latencies), 1),
            "max": round(max(latencies), 1),
        },
        "human_review": "not performed - see reports/TRANSLATION_QUALITY.md",
        "rows": rows,
    }
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# English -> Bangla Translation Quality",
        "",
        f"Generated: {payload['generated_at']}  ",
        f"Backend: **{payload['backend']}** ({payload['model_dir']})  ",
        f"Dataset: {args.dataset} ({payload['entries']} sentences)  ",
        f"chrF mean: **{payload['chrf_mean']}**  ",
        f"Numbers preserved: {payload['numbers_preserved']}/{payload['entries']}  ",
        f"Latency: p50 {payload['latency_ms']['p50']} ms, max {payload['latency_ms']['max']} ms",
        "",
        "> The reference translations were written by the engineer who built the",
        "> pipeline, not by a native-speaker panel. chrF against these references is",
        "> a regression signal, not a claim of human-evaluated quality. The review",
        "> column is left empty for a teacher to fill in.",
        "",
        "## chrF by category",
        "",
        "| Category | chrF |",
        "|---|---:|",
    ]
    for category, score in payload["chrf_by_category"].items():
        lines.append(f"| {category} | {score} |")
    lines += [
        "",
        "## Per-sentence results",
        "",
        "| id | category | source | reference | model output | chrF | numbers | review |",
        "|---|---|---|---|---|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {id} | {cat} | {en} | {ref} | {hyp} | {chrf} | {num} |  |".format(
                id=row["id"],
                cat=row["category"],
                en=row["en"].replace("|", "/"),
                ref=row["reference_bn"].replace("|", "/"),
                hyp=row["hypothesis_bn"].replace("|", "/"),
                chrf=row["chrf"],
                num="yes" if row["numbers_ok"] else "**no**",
            )
        )
    (ROOT / args.md).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k != "rows"}, ensure_ascii=False, indent=2))
    print(f"wrote {out} and {ROOT / args.md}")


if __name__ == "__main__":
    main()
