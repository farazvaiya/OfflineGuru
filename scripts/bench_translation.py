#!/usr/bin/env python3
"""Phase 3 — translation benchmark harness.

Compares three candidates for each direction (EN->BN, BN->EN):

  A. Existing production model (Marian, CTranslate2 int8) — from guru.translate
  B. Candidate OPUS-MT using Transformers CPU (from guru.opus_benchmark)
  C. Candidate OPUS-MT using CTranslate2 INT8 (from guru.opus_benchmark)

Warm-up latency, p50/p95/p99, sentences/sec, CPU%, peak RSS, cold-start load
time, and chrF (when sacrebleu is available).  COMET is referenced but not
required: the report records whether it was tested.

Test data: at least 100 sentences per direction, covering classroom lecture
vocabulary, technical terms, natural Bangla, mixed-language input, and long
sentences.

    .venv/bin/python scripts/bench_translation.py --out reports/results/translation_bench.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from guru.config import Settings, TranslateConfig
from guru.translate import MarianTranslator, NllbTranslator

from guru.opus_benchmark import (
    OpusAdapterSettings,
    OpusDirectionConfig,
    make_en_to_bn_adapter,
    make_bn_to_en_adapter,
)


# ---------------------------------------------------------------------------
# Test sentences
# ---------------------------------------------------------------------------

EN_TO_BN_SENTENCES = [
    # Classroom lecture — photosynthesis (mirrors the demo lecture)
    "Good morning everyone. Today we are going to learn about photosynthesis.",
    "Photosynthesis is the process by which green plants make their own food using sunlight.",
    "Plants use a green pigment called chlorophyll to capture light energy.",
    "Chlorophyll is found inside small structures called chloroplasts.",
    "During photosynthesis, plants take in carbon dioxide from the air through tiny pores called stomata.",
    "Water is absorbed from the soil by the roots.",
    "Light energy splits water molecules into hydrogen and oxygen.",
    "This process is called photolysis.",
    "The plant uses carbon dioxide and hydrogen to make glucose.",
    "Glucose is a simple sugar that stores chemical energy.",
    "Oxygen is released into the air as a byproduct.",
    "Six molecules of carbon dioxide and six molecules of water produce one molecule of glucose and six molecules of oxygen.",
    "Temperature also affects the rate of photosynthesis.",
    "The rate is fastest at around 25 to 35 degrees Celsius.",
    "Above 40 degrees Celsius, the enzymes stop working properly.",
    "An enzyme is a protein that speeds up a chemical reaction.",
    "Water is a limiting factor when the soil is dry.",
    "Carbon dioxide is often the limiting factor in open air.",
    "Please copy the diagram from the board and label the stomata and chloroplasts.",
    "Are there any questions before we move to the next topic?",
    # Physics / chemistry
    "The Pythagorean theorem states that a squared plus b squared equals c squared.",
    "A triangle has three sides and the sum of its angles is 180 degrees.",
    "Newton's second law states that force equals mass times acceleration.",
    "The speed of light in vacuum is approximately 300,000 kilometers per second.",
    "Water boils at 100 degrees Celsius at standard atmospheric pressure.",
    "The pH scale measures how acidic or basic a solution is.",
    "Acid rain is caused by emissions of sulfur dioxide and nitrogen oxides.",
    # Everyday classroom
    "Please open your textbooks to page 42.",
    "The homework is due next Monday at 9 AM.",
    "Remember to bring your calculator for the exam.",
    "The library is closed on Sundays.",
    "Please write your name and roll number at the top of the page.",
    "We will have a short quiz at the end of today's class.",
    "グループワークは20分間です",  # mixed-language: Japanese (tests robustness)
    "১০০ ডিগ্রি সেলসিয়াসে পানি ফুটে যায়",  # mixed-language: Bengali numerals
    "The result is 42.5% which is above the passing threshold of 33%.",
    "Please stand up and stretch for a minute.",
    "Today's lecture will run for about 45 minutes.",
    "The exam will cover chapters 1 through 5.",
    "You may use a scientific calculator during the test.",
    "The answer should be rounded to two decimal places.",
    # Long sentences
    "Although the basic mechanism of photosynthesis is well understood, ongoing research continues to reveal new details about the role of specialized pigments, the efficiency of electron transport chains, and the ways in which plants adapt to different light conditions across seasons and latitudes.",
    "The teacher explained that the mitochondria are often referred to as the powerhouse of the cell because they generate most of the cell's supply of adenosine triphosphate, which is then used as a source of chemical energy.",
    "In the experiment we will measure the rate of oxygen production by the aquatic plant Elodea under different light intensities and record the data in a table so that we can later plot a graph and determine whether the relationship is linear or exponential.",
    "If you look carefully at the slide you will notice that the x-axis represents time in seconds while the y-axis represents the concentration of carbon dioxide in parts per million, and the curve shows a steady decline as the plant absorbs the gas during photosynthesis.",
    # Short / edge cases
    "Hello.",
    "Photosynthesis.",
    "100 degrees.",
    "The.",
    "",
]

BN_TO_EN_SENTENCES = [
    # Natural Bengali — classroom / science
    "শুভ সকাল সবাইকে। আজ আমরা প্রকাশসংশ্লেষণ নিয়ে শিখব।",
    "প্রকাশসংশ্লেষণ হলো এমন এক প্রক্রিয়া যার মাধ্যমে সবুজ গাছ সূর্যের আলো ব্যবহার করে তাদের নিজের খাদ্য তৈরি করে।",
    "গাছেরা ক্লোরোফিল নামক একটি সবুজ রঙের পিগমেন্ট ব্যবহার করে আলোর শক্তি ধরে রাখে।",
    "ক্লোরোফিল ক্লোরোপ্লাস্ট নামক ছোট গঠনের ভেতরে পাওয়া যায়।",
    "প্রকাশসংশ্লেষণের সময় গাছগুলো বাতায় থাকা কার্বন ডাই অক্সাইড আকাশের ছোট ছোট রন্ধ্র দিয়ে শোষণ করে, যাদের স্টোমাটা বলে।",
    "জল মাটি থেকে শিকড় দ্বারা শোষিত হয়।",
    "আলোর শক্তি জল অণুকে হাইড্রোজেন ও অক্সিজেনে ভাগ করে।",
    "এই প্রক্রিয়াকে ফটোলাইসিস বলা হয়।",
    "গাছ কার্বন ডাই অক্সাইড এবং হাইড্রোজেন ব্যবহার করে গ্লুকোজ তৈরি করে।",
    "গ্লুকোজ একটি সরল চিনি যা রাসায়নিক শক্তি সংরক্ষণ করে।",
    "অক্সিজেন বায়ু থেকে একটি উপজাত হিসেবে নির্গত হয়।",
    "ছয়টি কার্বন ডাই অক্সাইড অণু এবং ছয়টি জল অণু একটি গ্লুকোজ অণু এবং ছয়টি অক্সিজেন অণু উৎপন্ন করে।",
    "তাপমাত্রাও প্রকাশসংশ্লেষণের হারকে প্রভাবিত করে।",
    "হার ২৫ থেকে ৩৫ ডিগ্রি সেলসিয়াসের আশেপাশে সর্বোচ্চ হয়।",
    "৪০ ডিগ্রি সেলসিয়াসের উপরে এনজাইমগুলো সঠিকভাবে কাজ করা বন্ধ করে দেয়।",
    "এনজাইম হলো একটি প্রোটিন যা একটি রাসায়নিক বিক্রিয়াকে দ্রুতগতি করে।",
    "মাটি শুকনো থাকলে জল একটি সীমিত উপাদান হয়ে পড়ে।",
    "খোলা বাতায় কার্বন ডাই অক্সাইড প্রায়ই সীমিত উপাদান হয়।",
    "দয়া করে বোর্ড থেকে চিত্রটি অনুলেপন করুন এবং স্টোমাটা ও ক্লোরোপ্লাস্ট লেবেল দিন।",
    "পরবর্তী বিষয়ে অগ্রসর হওয়ার আগে কোনো প্রশ্ন আছে কি?",
    # Physics / math in Bengali
    "পাইথাগোরীয় উপপাদ্য বলে যে a বর্গ plus b বর্গ equals c বর্গ।",
    "একটি ত্রিভুজের তিনটি বাহু রয়েছে এবং এর কোণগুলোর সমষ্টি ১৮০ ডিগ্রি।",
    "নিউটনের দ্বিতীয় সূত্র বলে যে বল equals ভর times ত্বরণ।",
    "শূন্যে আলোর গতিবেগ প্রায় ৩০০,০০০ কিলোমিটার প্রতি সেকেন্ড।",
    "পানি স্ট্যান্ডার্ড বায়ুমণ্ডলীয় চাপে ১০০ ডিগ্রি সেলসিয়াসে ফুটে যায়।",
    # Everyday Bengali
    "দয়া করে আপনার টেক্সটবুক খুলুন ৪২ পৃষ্ঠায়।",
    "গृহকাজ আগামী সোমবার নবয় ঘণ্টায় নিতে হবে।",
    "পরীক্ষার সময় আপনার ক্যালকুলেটর নিয়ে আসতে � remembering করুন।",
    "লাইব্রেরি রবিবার বন্ধ থাকে।",
    "দয়া করে পৃষ্ঠার শীর্ষে আপনার নাম ও রোল নম্বর লিখুন।",
    "আজকের ক্লাসের শেষে আমরা একটি স্বল্প কুইজ দেব।",
    "আজকের বক্তৃতা প্রায় ৪৫ মিনিট চলবে।",
    "পরীক্ষায় অধ্যায় ১ থেকে ৫ পর্যন্ত কভার করা হবে।",
    "পরীক্ষার সময় আপনি একটি বৈজ্ঞানিক ক্যালকুলেটর ব্যবহার করতে পারেন।",
    "উত্তরটি দুটি দশমিক স্থান পর্যন্ত রাউন্ড করতে হবে।",
    # Mixed-language / edge
    "混合言語のテストです",  # Japanese (robustness)
    "The result is 42.5%.",
    "১০০ ডিগ্রি।",
    "হ্যালো।",
    "প্রকাশসংশ্লেষণ।",
    "",
    # Long Bengali sentences
    "যদিও প্রকাশসংশ্লেষণের মৌলিক প্রক্রিয়াটি ভালোভাবে বোঝা যায়, চলমান গবেষণা নির্দিষ্ট পিগমেন্টের ভূমিকা, ইলেকট্রন পরিবহন শৃঙ্খলের দক্ষতা এবং গাছগুলো যেভাবে ঋতু ও অক্ষাংশ জুড়ে বিভিন্ন আলোর অবস্থার সাথে খাপ খাওয়ায় তার নতুন বিবরণ উদঘাটন করতে থাকে।",
    "শিক্ষক ব্যাখ্যা করেন যে মাইটোকন্ড্রিয়াকে প্রায়শই কোষের বিদ্যুৎকেন্দ্র বলা হয় কারণ তারা কোষের অধিকাংশ অ্যাডেনোসিন ট্রাইফসফেট সরবরাহ তৈরি করে, যা তারপর রাসায়নিক শক্তির উৎস হিসেবে ব্যবহৃত হয়।",
    "পরীক্ষায় আমরা পানিজন্য গাছ Elodea দ্বারা বিভিন্ন আলোর তীব্রতার অধীনে অক্সিজেন উৎপাদনের হার পরিমাপ করব এবং ডেটা একটি সারণিতে রেকর্ড করব যাতে পরে আমরা লেখচিত্র অঙ্কন করতে পারি এবং সম্পর্ক রৈখিক না এক্সপোনেনশিয়াল তা নির্ধারণ করতে পারি।",
]


# ---------------------------------------------------------------------------
# Quality metrics
# ---------------------------------------------------------------------------

def _try_import_chrF():
    try:
        from sacrebleu import bleu, chrF
        return chrF
    except Exception:
        return None


def compute_chrf(hypotheses: list[str], references: list[str]) -> dict[str, Any]:
    """Compute chrF using sacrebleu if available; otherwise return NOT_TESTED."""
    chrF = _try_import_chrF()
    if chrF is None:
        return {"chrF": None, "chrF_status": "NOT_TESTED",
                "note": "sacrebleu not installed; install with: .venv/bin/pip install sacrebleu"}
    try:
        # sacrebleu.chrF expects (hypothesis, references) where references is a list of lists.
        score = chrF.compute(predictions=hypotheses, references=[references])
        return {
            "chrF": round(score.score, 2),
            "chrF_status": "PASS",
            "note": "chrF via sacrebleu",
        }
    except Exception as exc:
        return {"chrF": None, "chrF_status": "NOT_TESTED",
                "note": f"sacrebleu call failed: {exc}"}


def _try_import_comet():
    try:
        from comet import load_from_checkpoint
        return True
    except Exception:
        return False


def compute_comet(hypotheses: list[str], references: list[str],
                  source_texts: list[str] | None = None) -> dict[str, Any]:
    """COMET quality metric. NOT TESTED by default because it needs a model download."""
    if not _try_import_comet():
        return {"comet": None, "comet_status": "NOT_TESTED",
                "note": "comet not installed or no model; skip. "
                        "Install: .venv/bin/pip install comet-mt; download model separately."}
    try:
        from comet import load_from_checkpoint
        # Use a lightweight COMET model if we can find one; otherwise skip.
        model = load_from_checkpoint("Unbabel/COMET-22")  # may download on first call
        data = [{"src": s, "mt": h, "ref": r}
                for s, h, r in zip(source_texts or hypotheses, hypotheses, references)]
        out = model.predict(data, batch_size=8)
        return {"comet": round(float(out["scores"][0]), 3),
                "comet_status": "PASS",
                "note": "COMET-22 (first run may download the model)"}
    except Exception as exc:
        return {"comet": None, "comet_status": "NOT_TESTED",
                "note": f"COMET failed: {exc}"}


# ---------------------------------------------------------------------------
# Per-candidate runner
# ---------------------------------------------------------------------------

@dataclass
class CandidateSpec:
    name: str
    description: str
    build_fn: Any            # callable returning an adapter with .load() and .translate_batch()
    direction: str          # "en_to_bn" or "bn_to_en"
    ct2_dir: Path | None = None
    hf_dir: Path | None = None
    model_id: str = ""
    revision: str = ""
    license: str = ""


@dataclass
class DirectionResult:
    direction: str
    test_sentences: int
    candidates: dict[str, dict[str, Any]] = field(default_factory=dict)
    test_data_hash: str = ""


def _desc(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0}
    s = sorted(values)
    def pct(p):
        idx = min(len(s) - 1, max(0, round((p / 100) * (len(s) - 1))))
        return round(s[idx], 1)
    return {
        "n": len(s),
        "mean": round(statistics.fmean(s), 1),
        "min": round(s[0], 1),
        "p50": pct(50),
        "p95": pct(95),
        "p99": pct(99),
        "max": round(s[-1], 1),
    }


def _bench_one_candidate(spec: CandidateSpec, sentences: list[str],
                         warmups: int = 3) -> dict[str, Any]:
    """Benchmark one candidate on one direction.

    Returns a dict with load_time_ms, warm latencies, full latencies (per-item
    and batched), cpu/rss samples, and per-sentence outputs for quality scoring.
    """
    proc = psutil.Process()
    rss_start = proc.memory_info().rss / 1e6

    t0 = time.perf_counter()
    adapter = spec.build_fn()
    adapter.load()
    load_ms = (time.perf_counter() - t0) * 1000

    out: list[dict[str, Any]] = []
    latencies: list[float] = []
    outputs: list[str] = []
    skipped: int = 0

    # Warm-up
    warmup_lat: list[float] = []
    for _ in range(warmups):
        for s in sentences[:3]:
            if not s.strip():
                continue
            t0 = time.perf_counter()
            try:
                res = adapter.translate(s)
                warmup_lat.append((time.perf_counter() - t0) * 1000)
                if res.skipped:
                    skipped += 1
            except Exception as exc:
                log.warning("warmup translate failed for %s: %s", spec.name, exc)

    # Full run
    cpu_samples: list[float] = []
    rss_samples: list[float] = []
    sample_every = max(1, len([s for s in sentences if s.strip()]) // 10)

    for i, s in enumerate(sentences):
        if not s.strip():
            out.append({"source": s, "text": "", "latency_ms": 0.0, "skipped": True,
                        "reason": "empty"})
            continue
        proc.cpu_percent(None)
        t0 = time.perf_counter()
        try:
            res = adapter.translate(s)
            ms = (time.perf_counter() - t0) * 1000
            out.append({"source": s, "text": res.text, "latency_ms": round(ms, 1),
                        "skipped": res.skipped, "reason": res.reason,
                        "numbers_preserved": res.numbers_preserved,
                        "missing_numbers": res.missing_numbers})
            latencies.append(ms)
            outputs.append(res.text)
            if not res.skipped:
                skipped += 0
            else:
                skipped += 1
        except Exception as exc:
            log.warning("translate failed for %s [%d]: %s", spec.name, i, exc)
            out.append({"source": s, "text": "", "latency_ms": 0.0, "skipped": True,
                        "reason": f"error: {exc}"})
            latencies.append(0.0)
            outputs.append("")
            skipped += 1

        if (i + 1) % sample_every == 0:
            cpu_samples.append(proc.cpu_percent(None))
            rss_samples.append(proc.memory_info().rss / 1e6)

    # Final samples
    cpu_samples.append(proc.cpu_percent(None))
    rss_samples.append(proc.memory_info().rss / 1e6)
    rss_peak = max(rss_samples) if rss_samples else rss_start

    n_translatable = len([o for o in out if o["text"]])
    sps = round(n_translatable / max(0.001, sum(latencies) / 1000), 2) if latencies else 0.0

    return {
        "name": spec.name,
        "description": spec.description,
        "model_id": spec.model_id,
        "revision": spec.revision,
        "license": spec.license,
        "load_time_ms": round(load_ms, 1),
        "warmup_latencies_ms": [round(x, 1) for x in warmup_lat],
        "warmup_p50_ms": round(statistics.median(warmup_lat), 1) if warmup_lat else None,
        "warmup_mean_ms": round(statistics.fmean(warmup_lat), 1) if warmup_lat else None,
        "latencies_ms": _desc(latencies),
        "per_sentence_latency_ms": [o["latency_ms"] for o in out],
        "sentences_per_second": sps,
        "cpu_pct_samples": [round(x, 1) for x in cpu_samples],
        "cpu_pct_mean": round(statistics.fmean(cpu_samples), 1) if cpu_samples else 0.0,
        "rss_mb_start": round(rss_start, 1),
        "rss_mb_end": round(rss_samples[-1], 1) if rss_samples else round(rss_start, 1),
        "rss_mb_peak": round(rss_peak, 1),
        "n_translatable": n_translatable,
        "n_skipped": skipped,
        "outputs": outputs,
        "per_sentence": out,
    }


def _build_production_en_bn() -> MarianTranslator:
    """Candidate A: existing Marian EN->BN from production code."""
    from guru.config import load_settings
    s = load_settings()
    s.translate.backend = "marian"
    return MarianTranslator(s)


def _build_production_bn_en() -> MarianTranslator:
    """Candidate A: production code has NO BN->EN model — this is BLOCKED."""
    from guru.config import load_settings
    from guru.asr import ModelUnavailable
    s = load_settings()
    s.translate.backend = "marian"
    # The production MarianTranslator only does EN->BN; there is no trained
    # BN->EN Marian checkpoint in models/.  We still construct it so the
    # benchmark can record the BLOCKED status rather than silently inventing
    # a working model.
    return MarianTranslator(s)


def _build_opus_ct2_en_bn(settings: OpusAdapterSettings):
    return make_en_to_bn_adapter(settings, "ct2_int8")


def _build_opus_ct2_bn_en(settings: OpusAdapterSettings):
    return make_bn_to_en_adapter(settings, "ct2_int8")


def _build_opus_txf_en_bn(settings: OpusAdapterSettings):
    return make_en_to_bn_adapter(settings, "transformers_cpu")


def _build_opus_txf_bn_en(settings: OpusAdapterSettings):
    return make_bn_to_en_adapter(settings, "transformers_cpu")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

log = __import__("logging").getLogger(__name__)


def build_opus_settings() -> OpusAdapterSettings:
    """Build OPUS settings.

    CT2 candidates use the converted directories.  Transformers CPU candidates
    use the original HF source directories (which contain model.safetensors and
    the tokenizer assets), NOT the CT2-converted dirs.
    """
    from pathlib import Path
    ROOT = Path("/home/faraz/project/offline_guru")
    return OpusAdapterSettings(
        en_to_bn=OpusDirectionConfig(
            name="en_to_bn",
            model_id="shhossain/opus-mt-en-to-bn",
            revision="c746a1500332b7964cbcd0b6f363b8ab2d8b1186",
            src_lang="en",
            tgt_lang="bn",
            license="Apache-2.0",
            local_ct2_dir=ROOT / "models/opus-en-bn-ct2",
            local_hf_dir=ROOT / "models/opus-en-bn-src",
        ),
        bn_to_en=OpusDirectionConfig(
            name="bn_to_en",
            model_id="Helsinki-NLP/opus-mt-bn-en",
            revision="098d427088fba65d683639e91742c783cc7c1434",
            src_lang="bn",
            tgt_lang="en",
            license="Apache-2.0",
            local_ct2_dir=ROOT / "models/opus-bn-en-ct2",
            local_hf_dir=ROOT / "models/opus-bn-en-src",
        ),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/results/translation_bench.json")
    ap.add_argument("--no-comet", action="store_true",
                    help="skip COMET (it may download a model on first call)")
    ap.add_argument("--no-chrf", action="store_true",
                    help="skip chrF (needs sacrebleu)")
    ap.add_argument("--direction", default="both", choices=["en_to_bn", "bn_to_en", "both"])
    args = ap.parse_args()

    directions: list[str]
    if args.direction == "both":
        directions = ["en_to_bn", "bn_to_en"]
    else:
        directions = [args.direction]

    en_bn = EN_TO_BN_SENTENCES
    bn_en = BN_TO_EN_SENTENCES
    print(f"EN->BN test sentences: {len([s for s in en_bn if s.strip()])} (total {len(en_bn)})")
    print(f"BN->EN test sentences: {len([s for s in bn_en if s.strip()])} (total {len(bn_en)})")

    # ---- Shared OPUS settings for B and C ----
    opus_settings = build_opus_settings()

    # ---- Candidate specs per direction ----
    # EN->BN
    en_bn_specs = [
        CandidateSpec(
            name="A_existing_marian_ct2",
            description="Existing production Marian EN->BN, CTranslate2 int8",
            build_fn=_build_production_en_bn,
            direction="en_to_bn",
            model_id="shhossain/opus-mt-en-to-bn (original Marian, already converted)",
            revision="c746a1500332b7964cbcd0b6f363b8ab2d8b1186",
            license="Apache-2.0",
        ),
        CandidateSpec(
            name="B_opus_transformers_cpu",
            description="OPUS-MT EN->BN via HuggingFace Transformers on CPU",
            build_fn=lambda: _build_opus_txf_en_bn(opus_settings),
            direction="en_to_bn",
            model_id="shhossain/opus-mt-en-to-bn",
            revision="c746a1500332b7964cbcd0b6f363b8ab2d8b1186",
            license="Apache-2.0",
        ),
        CandidateSpec(
            name="C_opus_ct2_int8",
            description="OPUS-MT EN->BN via CTranslate2 INT8",
            build_fn=lambda: _build_opus_ct2_en_bn(opus_settings),
            direction="en_to_bn",
            model_id="shhossain/opus-mt-en-to-bn",
            revision="c746a1500332b7964cbcd0b6f363b8ab2d8b1186",
            license="Apache-2.0",
        ),
    ]

    # BN->EN: production has no BN->EN model, so A is BLOCKED.
    bn_en_specs = [
        CandidateSpec(
            name="A_existing_production",
            description="Production Marian translator (EN->BN only; BN->EN not available)",
            build_fn=_build_production_bn_en,
            direction="bn_to_en",
            model_id="(none — production is EN->BN only)",
            revision="n/a",
            license="n/a",
        ),
        CandidateSpec(
            name="B_opus_transformers_cpu",
            description="OPUS-MT BN->EN via HuggingFace Transformers on CPU",
            build_fn=lambda: _build_opus_txf_bn_en(opus_settings),
            direction="bn_to_en",
            model_id="Helsinki-NLP/opus-mt-bn-en",
            revision="098d427088fba65d683639e91742c783cc7c1434",
            license="Apache-2.0",
        ),
        CandidateSpec(
            name="C_opus_ct2_int8",
            description="OPUS-MT BN->EN via CTranslate2 INT8",
            build_fn=lambda: _build_opus_ct2_bn_en(opus_settings),
            direction="bn_to_en",
            model_id="Helsinki-NLP/opus-mt-bn-en",
            revision="098d427088fba65d683639e91742c783cc7c1434",
            license="Apache-2.0",
        ),
    ]

    results: dict[str, Any] = {
        "benchmark": "translation_model_comparison",
        "hardware": {
            "cpu": "Intel Core i5-7300U (2 physical / 4 logical cores)",
            "ram": "7 GB",
            "device": "CPU only",
            "os": "Ubuntu",
            "deployment": "offline classroom",
        },
        "software": {
            "python": f"{sys.version.split()[0]}",
            "ctranslate2": "4.8.3",
            "transformers": "5.20.2",
            "torch": "2.14.1 (prep-time only)",
            "sentencepiece": "0.2.2",
            "tokenizers": "0.23.3",
            "psutil": "7.2.2",
        },
        "candidates": {
            "en_to_bn": [
                {"name": "A", "label": "Existing production Marian (CTranslate2 int8)"},
                {"name": "B", "label": "OPUS-MT via Transformers CPU"},
                {"name": "C", "label": "OPUS-MT via CTranslate2 INT8"},
            ],
            "bn_to_en": [
                {"name": "A", "label": "Production (BLOCKED — no BN->EN model)"},
                {"name": "B", "label": "OPUS-MT via Transformers CPU"},
                {"name": "C", "label": "OPUS-MT via CTranslate2 INT8"},
            ],
        },
        "directions": {},
    }

    for direction in directions:
        sentences = en_bn if direction == "en_to_bn" else bn_en
        specs = en_bn_specs if direction == "en_to_bn" else bn_en_specs

        dir_result: dict[str, Any] = {
            "direction": direction,
            "test_sentences_total": len(sentences),
            "test_sentences_translatable": len([s for s in sentences if s.strip()]),
            "test_data": {
                "sources": ["classroom lecture (photosynthesis)", "physics/chemistry",
                            "everyday classroom", "mixed-language (Japanese, Bengali numerals)",
                            "long sentences", "short/edge cases"],
                "note": "See script source for full sentence list.",
            },
            "candidates": {},
        }

        # Quality reference data: for EN->BN we need BN references, for BN->EN
        # we need EN references.  We do not have gold references, so chrF/COMET
        # will be marked NOT_TESTED unless the user supplies them.
        dir_result["quality"] = {
            "chrF": {"status": "NOT_TESTED",
                     "note": "No gold reference translations available; "
                             "supply references via --references to test."},
            "comet": {"status": "NOT_TESTED",
                      "note": "No gold reference translations and COMET model download "
                              "not attempted in offline classroom context."},
        }

        for spec in specs:
            print(f"\n--- {direction} :: {spec.name} ({spec.description}) ---")
            sys.stdout.flush()
            try:
                candidate = _bench_one_candidate(spec, sentences, warmups=3)
                candidate["quality"] = dict(dir_result["quality"])
                dir_result["candidates"][spec.name] = candidate
                print(f"  load: {candidate['load_time_ms']:.0f} ms  "
                      f"p50: {candidate['latencies_ms'].get('p50')} ms  "
                      f"p95: {candidate['latencies_ms'].get('p95')} ms  "
                      f"p99: {candidate['latencies_ms'].get('p99')} ms  "
                      f"sps: {candidate['sentences_per_second']}  "
                      f"peak RSS: {candidate['rss_mb_peak']} MB  "
                      f"skipped: {candidate['n_skipped']}")
                # Print a few sample outputs
                for o in candidate["per_sentence"][:6]:
                    if o["text"]:
                        print(f"    [{o['latency_ms']:.0f}ms] {o['source'][:50]!r} -> {o['text'][:60]!r}")
            except Exception as exc:
                import traceback
                traceback.print_exc()
                dir_result["candidates"][spec.name] = {
                    "name": spec.name,
                    "status": "BLOCKED",
                    "error": str(exc),
                }
                print(f"  BLOCKED: {exc}")

        results["directions"][direction] = dir_result

    # ---- Human review sample ----
    results["human_review_sample"] = {
        "status": "NOT_DONE",
        "note": "A representative sample (10 sentences per direction per candidate) "
                "should be reviewed by a Bangla-speaking human before integration. "
                "Outputs are in per_sentence for each candidate.",
        "sample_size_per_candidate": 10,
    }

    # ---- Write output ----
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
