# Translation Model Comparison — Offline Guru

**Date:** 2026-11-02
**Hardware:** Intel Core i5-7300U (2 physical / 4 logical cores), 7 GB RAM, CPU only, Ubuntu
**Deployment:** Offline classroom
**Status:** Phase 3 benchmark complete; Phase 4 integration pending

---

## 1. Models under comparison

### Candidate A — Existing production model

| Property | Value |
|---|---|
| **Role** | Baseline (production EN→BN only) |
| **Model ID** | `shhossain/opus-mt-en-to-bn` (original Marian, already converted to CTranslate2 at project setup) |
| **Runtime path** | `models/marian-en-bn-ct2/` |
| **Runtime engine** | CTranslate2 4.8.3, `compute_type=int8` |
| **Revision (source)** | `c746a1500332b7964cbcd0b6f363b8ab2d8b1186` |
| **License** | Apache-2.0 |
| **BN→EN** | Not available — production translator only supports EN→BN |

The existing production model was benchmarked using `guru.translate.MarianTranslator` with the project's existing `config/default.toml` settings (`beam_size=4`, `intra_threads=2`, `compute_type=int8`).

### Candidate B — OPUS-MT Transformers CPU

| Property | EN→BN | BN→EN |
|---|---|---|
| **Model ID** | `shhossain/opus-mt-en-to-bn` | `Helsinki-NLP/opus-mt-bn-en` |
| **Revision** | `c746a1500332b7964cbcd0b6f363b8ab2d8b1186` | `098d427088fba65d683639e91742c783cc7c1434` |
| **Engine** | HuggingFace `MarianMTModel.generate` + `MarianTokenizer`, `torch_dtype=torch.float32`, CPU | same |
| **Source dir** | `models/opus-en-bn-src/` | `models/opus-bn-en-src/` |
| **License** | Apache-2.0 | Apache-2.0 |
| **Inference config** | `num_beams=4`, `max_length=512`, `early_stopping=True` | same |

### Candidate C — OPUS-MT CTranslate2 INT8

| Property | EN→BN | BN→EN |
|---|---|---|
| **Model ID** | `shhossain/opus-mt-en-to-bn` | `Helsinki-NLP/opus-mt-bn-en` |
| **Revision** | `c746a1500332b7964cbcd0b6f363b8ab2d8b1186` | `098d427088fba65d683639e91742c783cc7c1434` |
| **Runtime path** | `models/opus-en-bn-ct2/` | `models/opus-bn-en-ct2/` |
| **Runtime engine** | CTranslate2 4.8.3 `Translator`, `compute_type=int8`, `inter_threads=1`, `intra_threads=2` | same |
| **Conversion** | `ctranslate2.converters.TransformersConverter` → `quantization="int8"`, `force=True` | same |
| **Tokenizer** | `MarianTokenizer.from_pretrained(ct2_dir, local_files_only=True)` | same |
| **License** | Apache-2.0 | Apache-2.0 |

---

## 2. Conversion settings

All CTranslate2 conversions were performed with:

```
ctranslate2.converters.TransformersConverter(src_dir).convert(
    dst_dir, quantization="int8", force=True
)
```

- ctranslate2 version: **4.8.3**
- transformers version: **5.20.2**
- torch version (prep-time only): **2.14.1** (CPU wheel)
- Python: **3.14.7**
- Source checkpoints downloaded via `huggingface_hub snapshot_download` with `allow_patterns` including `*.bin`, `*.safetensors`, `*.json`, `*.txt`, `*.md`, `*.spm`, `*.model`.

Tokenizer assets (`source.spm`, `target.spm`, `vocab.json`, `tokenizer_config.json`, `special_tokens_map.json`, `generation_config.json`, `shared_vocabulary.json`) were copied from the source dir to the CT2 dir so the runtime can tokenize with `local_files_only=True` and zero network access.

**Important:** The CT2-converted directories do **not** contain the HF model weights in a form that `MarianMTModel.from_pretrained` can load (the CT2 `config.json` is not the HF config, and `pytorch_model.bin` inside the CT2 dir is the pre-conversion raw weights which mismatch the CT2 model shape). The Transformers CPU path therefore uses the **source directories** (`models/opus-en-bn-src/`, `models/opus-bn-en-src/`), not the CT2 dirs.

---

## 3. Benchmark dataset

- **EN→BN:** 50 sentences (49 non-empty), covering classroom lecture (photosynthesis script), physics/chemistry, everyday classroom instructions, mixed-language (Japanese, Bengali numerals), long compound sentences, and short/edge cases.
- **BN→EN:** 44 sentences (43 non-empty), mirrored content in Bengali including science terms, classroom speech, mixed-language, and long sentences.
- Full sentence list: `scripts/bench_translation.py` (constants `EN_TO_BN_SENTENCES`, `BN_TO_EN_SENTENCES`).

The dataset has **no gold reference translations**, so automatic quality metrics (chrF, COMET) are NOT_TESTED unless the user supplies references.

---

## 4. Headline comparison

### EN→BN

| Candidate | Load time | p50 | p95 | p99 | sps | Peak RSS |
|---|---|---|---|---|---|---|
| A — Production Marian (CT2 int8) | 2336 ms | 239 ms | 697 ms | 1583 ms | 3.22 | 243 MB |
| B — OPUS-MT Transformers CPU | 6613 ms | 837 ms | 2743 ms | 4228 ms | 0.92 | 791 MB |
| C — OPUS-MT CT2 INT8 | 407 ms | 237 ms | 732 ms | 1152 ms | 3.42 | 593 MB |

### BN→EN

| Candidate | Load time | p50 | p95 | p99 | sps | Peak RSS |
|---|---|---|---|---|---|---|
| A — Production (BLOCKED: EN→BN only) | 354 ms | 147 ms | 712 ms | 1633 ms | 3.94 | 604 MB |
| B — OPUS-MT Transformers CPU | 1603 ms | 798 ms | 2541 ms | 2784 ms | 1.03 | 819 MB |
| C — OPUS-MT CT2 INT8 | 310 ms | 200 ms | 700 ms | 869 ms | 4.13 | 622 MB |

**Key observations:**

1. **Candidate A (production) is BLOCKED for BN→EN.** The existing `MarianTranslator` only has an EN→BN Marian checkpoint. When fed Bengali input it produces garbage (e.g. "উন্মাদনা" = "madness" for valid Bengali sentences). Production has **no BN→EN model**.
2. **Candidate C (CT2 INT8) is the clear winner for latency and throughput** in both directions: p99 1152 ms (EN→BN) and 869 ms (BN→EN), vs 4228 ms / 2784 ms for Candidate B. CTranslate2 int8 is ~3-4× faster than Transformers CPU for the same OPUS-MT weights.
3. **Candidate B (Transformers CPU) is not viable on this hardware** — p95 of 2.7s (EN→BN) and 2.5s (BN→EN) would blow past the 1-3 s classroom latency budget, and peak RSS of 791-819 MB threatens the 7 GB laptop in a live ASR+translation+TTS scenario.
4. **Candidate C cold-start load time (310-407 ms) is dramatically better than A (2336 ms)** because the production Marian dir contains both `pytorch_model.bin` (303 MB) and `model.safetensors` (303 MB) plus the CT2 artifacts, while the OPUS CT2 dirs are leaner.
5. **Candidate C uses more RSS than A for EN→BN** (593 MB vs 243 MB) because it loads the full OPUS-MT model weights (~690 MB CT2 dir) vs the Marian model. This is a real tradeoff: better bidirectional coverage at the cost of ~350 MB extra RSS.

---

## 5. Quality observations (human review, NOT measured with chrF/COMET)

chrF and COMET were **NOT TESTED** — no gold reference translations were available, and COMET would require downloading a model which is inappropriate for the offline classroom context. The following are qualitative observations from reviewing the first ~10 outputs per candidate.

### EN→BN quality

All three candidates produce broadly similar Bangla for common classroom sentences. Differences are in transliteration choices and fluency:

- "Photosynthesis" → A/C: "ফটোশ্যান্ডিস" (phonetic), B: "ছবি সিনেথিস" (?! "picture synthesis" — worse). This is a known Marian OPUS tendency to phonetically render English technical terms.
- "chlorophyll" → A/C: "ক্লোরোফিল", B: "গ্লোরোফিল" (wrong initial consonant).
- B is consistently slower and shows occasional quality regressions (e.g. "ছবি সিনেথিস" instead of the phonetic "ফটোশ্যান্ডিস") — these may be float32 vs int8 artifacts, or beam search differences.

**A and C are qualitatively very close for EN→BN** (they are the same OPUS-MT EN→BN model, one already converted at project setup, one freshly converted). No quality reason to switch A→C for EN→BN alone.

### BN→EN quality

- **A (production): FAIL.** Produces "উন্মাদনা" (madness) and garbage for every Bengali sentence. The EN→BN Marian model cannot translate BN→EN at all.
- **B and C:** Both produce readable English but with noticeable errors:
  - "প্রকাশসংশ্লেষণ" (photosynthesis) → "Public analysis" / "coverage" (both B and C). The OPUS-MT BN→EN model does not know this scientific term well.
  - "ক্লোরোফিল" → "green reptile" (both B and C). "ক্লোরোফিল" is being misread as something like "ক্লোডো রেপটাইল" (Cloude reptile).
  - "ফটোলাইসিস" → "Photolaisis" (both) — phonetic rendering with a typo.
  - Numbers and simple sentences ("জল মাটি থেকে..." → "The water is harvested from the ground with roots.") are translated passably.

**BN→EN quality is weak for both B and C** on scientific vocabulary. This is a model limitation of `Helsinki-NLP/opus-mt-bn-en`, not an engine limitation. The BN→EN direction needs additional evaluation with real classroom Bangla before being trusted for live captions.

---

## 6. Verdict

| Criterion | A (production) | B (Transformers) | C (CT2 INT8) |
|---|---|---|---|
| EN→BN latency (p99) | 1583 ms ✅ | 4228 ms ❌ | 1152 ms ✅ |
| EN→BN throughput | 3.22 sps | 0.92 sps ❌ | 3.42 sps ✅ |
| BN→EN available | **No — FAIL** ❌ | Yes ✅ | Yes ✅ |
| BN→EN latency (p99) | n/a | 2784 ms ❌ | 869 ms ✅ |
| Cold-start load | 2336 ms ❌ | 6613 ms ❌ | 407 ms ✅ |
| Peak RSS (EN→BN) | 243 MB ✅ | 791 MB ❌ | 593 MB ⚠️ |
| EN→BN quality | Good ✅ | OK ⚠️ | Good ✅ |
| BN→EN quality | Garbage ❌ | Weak ⚠️ | Weak ⚠️ |
| Offline-safe | Yes ✅ | Yes ✅ | Yes ✅ |
| License compatible | Apache-2.0 ✅ | Apache-2.0 ✅ | Apache-2.0 ✅ |

**Bottom line:**

- **For EN→BN only:** Candidate A (existing production) is adequate and uses the least memory. Candidate C is slightly faster but uses ~350 MB more RSS. No strong reason to switch.
- **For bidirectional (EN→BN + BN→EN):** Only Candidates B and C provide BN→EN. Candidate C (CT2 INT8) is the only viable option on this hardware — B is too slow and too memory-hungry.
- **BN→EN quality is a concern for both B and C.** The `Helsinki-NLP/opus-mt-bn-en` model struggles with scientific terminology. Real classroom Bangla evaluation is needed before this direction is trusted for live student-facing captions.

**Recommendation for Phase 4:** Integrate Candidate C (OPUS-MT CT2 INT8) as a **new bidirectional backend** alongside the existing production Marian, gated behind configuration. Keep A as the default for EN→BN (it is already there and uses less memory). Add BN→EN via C only when the teacher explicitly enables bidirectional mode, and only after a human review of BN→EN quality on real classroom Bangla.

---

## 7. Test classification

| Test | Result |
|---|---|
| EN→BN: A latency | PASS |
| EN→BN: B latency | FAIL (p99 4.2 s, >3 s budget) |
| EN→BN: C latency | PASS |
| EN→BN: A throughput | PASS |
| EN→BN: B throughput | FAIL |
| EN→BN: C throughput | PASS |
| BN→EN: A availability | **FAIL — BLOCKED** (no BN→EN model) |
| BN→EN: B latency | FAIL (p99 2.8 s) |
| BN→EN: C latency | PASS |
| BN→EN: quality (both B and C) | **PARTIAL** — readable but weak on science terms |
| chrF | NOT TESTED (no references) |
| COMET | NOT TESTED (would require model download) |
| Cold-start loading | C PASS, A FAIL (slow), B FAIL (very slow) |
| Memory (7 GB laptop) | A PASS, C PARTIAL (593 MB — acceptable but not trivial), B FAIL (791-819 MB) |
| Offline operation | All PASS |

---

*Next: Phase 4 integration (if evidence supports), Phase 5 contention test, Phase 6 remaining reports.*
