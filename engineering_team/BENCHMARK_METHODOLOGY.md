# Benchmark Methodology

**Date**: 2026-11-14
**Module**: `engineering_team/physical/benchmark.py` (Phase 7)

## Equivalent-conditions rule

Two runs are comparable ONLY when:
1. `dataset_digest` is identical (same raw artifact file, sha256-verified), and
2. `config` is identical (model, speed, knobs).

The gate `assert_equivalent()` returns `equivalent: false` otherwise, and any
comparison across non-equivalent runs must be reported as invalid — never as a
regression or improvement.

## Provenance recorded per run

- dataset name + digest, full config, environment (platform, machine,
  processor, python, cpu_count), results dict, raw artifact path, created_at.
- A run whose raw artifact does not exist is REFUSED (no fiction).

## Comparison

- `compare()` computes deltas for numeric keys present on both sides.
- Non-numeric keys are skipped, not faked.
- Direction (better/worse) depends on the metric; the caller declares
  lower-is-better (e.g. `*_ms`, `wer`, `cer`, `drops`, `rss_mb`).

## Metrics used in this project

| Metric | Definition | Reference |
|---|---|---|
| WER | Levenshtein over words vs reference transcript | regression signal only |
| CER | same over characters | regression signal only |
| chrF | character n-gram F-score (orders 1..6, beta=2) | regression signal only |
| asr_ms / translate_ms / tts_ms / e2e_ms | from app `metrics` table | measured |
| drops/delivered | from EventBus stats | measured |

## What a benchmark does NOT prove

- Reference metrics do not determine semantic quality (master prompt rule).
- A loopback load test is not a 30-phone RF validation.
- Clean synthetic speech WER is optimistic vs classroom acoustics.

## Reproducibility

Re-run with the same dataset digest + config; if either differs, the engine
marks the comparison invalid instead of computing a misleading delta.
