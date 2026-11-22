# Translation Latency Benchmark — Offline Guru

**Date:** 2026-11-02
**Hardware:** Intel Core i5-7300U (2P/4T), 7 GB RAM, CPU only, Ubuntu
**Benchmark script:** `scripts/bench_translation.py`
**Raw data:** `reports/results/translation_bench.json`
**Status:** Complete. Production model unchanged.

---

## 1. Methodology

Each candidate was benchmarked as follows:

1. **Cold-start load time:** time from adapter construction to `adapter.load()` returning, measured with `time.perf_counter()`.
2. **Warm-up:** 3 passes over the first 3 non-empty sentences, beam_size=4.
3. **Full run:** each non-empty sentence translated individually with `adapter.translate(text)`, beam_size=4. Per-sentence latency recorded as wall-clock time for that single call.
4. **CPU/RSS sampling:** `psutil.Process.memory_info().rss` and `psutil.cpu_percent()` sampled every ~10% of the sentence count.
5. **Statistics:** p50/p95/p99 computed from per-sentence latencies via nearest-rank method.

All inference was on CPU. No GPU was available. The laptop was otherwise idle during benchmarking.

**Test data:** 50 EN→BN sentences (49 non-empty), 44 BN→EN sentences (43 non-empty). See `scripts/bench_translation.py` for the full list. The dataset includes classroom lecture vocabulary, technical terms, natural Bangla, mixed-language input, and long sentences.

**Beam size:** 4 for all candidates (configurable; production uses 4, and we matched it).

**Important caveat:** Per-sentence latency here is measured as a **single call to `translate()`**, not as it would behave inside the live pipeline where translation runs on a background thread with a bounded queue. The Phase 5 contention test addresses end-to-end pipeline behavior.

---

## 2. EN→BN latency

| Metric | A: Production Marian (CT2 int8) | B: OPUS-MT Transformers CPU | C: OPUS-MT CT2 INT8 |
|---|---|---|---|
| Cold-start load | 2336 ms | 6613 ms | 407 ms |
| Warm-up p50 | — | — | — |
| Warm-up mean | — | — | — |
| p50 | 239 ms | 837 ms | 237 ms |
| p95 | 697 ms | 2743 ms | 732 ms |
| p99 | 1583 ms | 4228 ms | 1152 ms |
| mean | — | — | — |
| min | — | — | — |
| max | — | — | — |
| Sentences/sec | 3.22 | 0.92 | 3.42 |
| Peak RSS | 243 MB | 791 MB | 593 MB |
| CPU (mean sample) | — | — | — |
| Skipped (empty/no-letter) | 1 | 1 | 1 |

**Interpretation:**

- **A vs C (EN→BN):** These are essentially the same model family (OPUS-MT Marian EN→BN). A is the older conversion that has been in the project; C is the freshly converted int8. Latency is nearly identical (p50 239 vs 237 ms; p99 1583 vs 1152 ms — C is slightly better on tail). Throughput is equivalent (~3.2-3.4 sps).
- **B is 3-4× slower** than A/C across all percentiles. p99 of 4.2 s is well outside the 1-3 s classroom budget.
- **Cold-start:** C is dramatically faster to load (407 ms vs 2336 ms for A) because the production Marian dir contains duplicate weight files.

---

## 3. BN→EN latency

| Metric | A: Production (BLOCKED) | B: OPUS-MT Transformers CPU | C: OPUS-MT CT2 INT8 |
|---|---|---|---|
| Cold-start load | 354 ms | 1603 ms | 310 ms |
| p50 | 147 ms | 798 ms | 200 ms |
| p95 | 712 ms | 2541 ms | 700 ms |
| p99 | 1633 ms | 2784 ms | 869 ms |
| Sentences/sec | 3.94 | 1.03 | 4.13 |
| Peak RSS | 604 MB | 819 MB | 622 MB |
| Skipped | 1 | 1 | 1 |

**Interpretation:**

- **Candidate A produces garbage for BN→EN** (the EN→BN Marian model cannot translate Bangla→English). The latency numbers above are real (it runs), but the outputs are meaningless ("উন্মাদনা" = "madness" repeatedly). **This candidate is BLOCKED for BN→EN**, regardless of latency.
- **C is the only viable BN→EN option on this hardware.** p99 869 ms is within budget; p95 700 ms is good.
- **B is too slow** (p99 2784 ms) and uses too much memory (819 MB peak).

---

## 4. Cold-start comparison

| Candidate | EN→BN load | BN→EN load |
|---|---|---|
| A (production Marian) | 2336 ms | 354 ms (loads but produces garbage) |
| B (Transformers CPU) | 6613 ms | 1603 ms |
| C (CT2 INT8) | 407 ms | 310 ms |

Candidate C loads in under half a second for both directions. This matters for the classroom scenario: if the teacher restarts the pipeline mid-lecture (e.g. after a crash), 400 ms is acceptable, 2.3 s is noticeable, 6.6 s is disruptive.

---

## 5. Memory comparison

| Candidate | EN→BN peak RSS | BN→EN peak RSS |
|---|---|---|
| A (production Marian) | 243 MB | 604 MB |
| B (Transformers CPU) | 791 MB | 819 MB |
| C (CT2 INT8) | 593 MB | 622 MB |

- **A is the lightest** for EN→BN (243 MB) because the Marian model is smaller than OPUS-MT.
- **C adds ~350 MB over A for EN→BN** (593 vs 243 MB) because OPUS-MT is a larger model than the Marian EN→BN variant already in the project.
- **B is the heaviest** in both directions (791-819 MB) and is not viable on a 7 GB laptop that also runs ASR (Whisper base ~145 MB) and TTS (Piper ~100 MB).

On a 7 GB laptop, the realistic headroom budget for a live session (ASR + translation + TTS + FastAPI server + OS) is roughly:

```
OS + desktop:     ~1500 MB
Whisper base:     ~145 MB
Piper TTS:        ~100 MB
FastAPI/uvicorn:  ~50 MB
-----------------------------
 available for translation: ~5000 MB (very rough)
```

All three candidates fit in absolute memory terms, but B at 819 MB spikes close to 1 GB for translation alone, leaving less headroom for the rest of the pipeline under load. C at 593-622 MB is comfortable. A at 243 MB is excellent but only works for EN→BN.

---

## 6. Throughput (sentences per second)

| Candidate | EN→BN sps | BN→EN sps |
|---|---|---|
| A | 3.22 | (garbage — not meaningful) |
| B | 0.92 | 1.03 |
| C | 3.42 | 4.13 |

A classroom lecture produces roughly 2-4 sentences per minute of speech (after segmentation). Even B's 0.92 sps is technically sufficient for a single speaker, but the latency tail (p99 4.2 s) means a slow sentence would block the enrichment queue and cause backpressure. C's 3.4-4.1 sps provides comfortable headroom.

---

## 7. Test classification

| Test | Result |
|---|---|
| EN→BN A: p50/p95/p99 | PASS (p99 1583 ms, within budget) |
| EN→BN B: p50/p95/p99 | FAIL (p99 4228 ms > 3 s) |
| EN→BN C: p50/p95/p99 | PASS (p99 1152 ms) |
| BN→EN A: availability | FAIL — BLOCKED (no BN→EN model) |
| BN→EN B: p50/p95/p99 | FAIL (p99 2784 ms) |
| BN→EN C: p50/p95/p99 | PASS (p99 869 ms) |
| Cold-start C | PASS (<500 ms both directions) |
| Cold-start A (EN→BN) | PARTIAL (2.3 s — acceptable but slow) |
| Cold-start B | FAIL (6.6 s) |
| Memory A | PASS |
| Memory C | PASS (593-622 MB, comfortable) |
| Memory B | FAIL (791-819 MB, too high) |

---

## 8. Notes and caveats

- **No gold references** → chrF and COMET were NOT TESTED. Latency is measured, quality is not automatically scored.
- **Single-threaded per-sentence measurement** — the pipeline runs translation on a dedicated background thread with a bounded queue, so real end-to-end latency also depends on queue wait time. Phase 5 tests this.
- **Production Marian (A) for EN→BN uses the project's existing conversion**, which may have been done with a different ctranslate2 version or settings than C. The similar latency suggests the conversions are comparable.
- **BN→EN quality is weak for both B and C** on scientific terms (see `TRANSLATION_QUALITY_EVALUATION.md`). Latency numbers for BN→EN are only meaningful if the quality is acceptable, which is not yet established.

---

*Next: TRANSLATION_QUALITY_EVALUATION.md, TRANSLATION_INTEGRATION.md*
