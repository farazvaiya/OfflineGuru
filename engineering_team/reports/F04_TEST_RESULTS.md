# F-04 Test Results: Final-Decode Threshold

**Date**: 2026-11-14
**Scope**: verification of the F-04 mechanism (current code) + Track A design
verification. **No production code was changed**, so "Track A results" are
design-level simulations, not executed implementation results.

## 1. Existing Suite

`pytest tests/test_realtime.py tests/test_pipeline.py` → **43 passed**.
No existing test references `_worth_final_decode` or the 0.35 value (verified
by grep) — the threshold is currently untested behavior.

## 2. Mechanism Verification (this investigation)

| Check | Method | Result |
|---|---|---|
| Threshold still present | `guru/pipeline.py:409` — `if un_decoded >= 0.35: return True` | CONFIRMED |
| Threshold not a config knob | `config/default.toml` + `guru/config.py:65-74` — no field | CONFIRMED |
| Empty agreement + skipped decode loses tail | simulation (synthetic): `flush_all()` on empty buffer commits [] | CONFIRMED — words for the tail are unrecoverable without a decode |
| Skipped tail audio retained (not destroyed) | simulation: `rebase(frontier)` keeps tail | CONFIRMED |
| Recovery on subsequent speech | simulation: tail audio survives; included in next `_stream_decode` | CONFIRMED (if speech resumes) |
| End-of-lecture tail lost | simulation: final flush (line 395) skips the last sub-0.35 s tail; no further decode occurs | CONFIRMED |
| Real decode cost | whisper-base int8, 3 threads, this host | 0.35 s → 1002 ms; 0.6 s → 957 ms; 1.5 s → 1020 ms (flat ~1 s per call) |

All simulations use synthetic `tone()` audio and synthetic `Word` timestamps;
no microphone, no arecord, no lecture audio was transcribed for these checks.

## 3. Track A Design Verification (synthetic, not executed against code)

| Track A test | Expected | Status |
|---|---|---|
| T-A1 empty agreement + tail → decode | True | Verified by simulation (empty `committed` list) |
| T-A2 agreement + 0.20 s un-decoded → skip | False | Verified by simulation (matches current behaviour) |
| T-A3 agreement + 0.36 s un-decoded → decode | True | Verified by simulation |
| T-A4 stop with non-empty window → exactly one decode | call count 1 | NOT EXECUTED (no implementation yet) |
| T-A5 knob honoured at 0.05 | decode for 0.10 s tail | NOT EXECUTED |
| T-A6 no double-commit after tail decode | single emission | NOT EXECUTED |
| T-B latency/CPU benchmark | measured | NOT EXECUTED — do not fabricate |

## 4. Track B

No implementation exists to test; decision is by inspection. No benchmarks
run. Marked **NOT EXECUTED** by design.

## 5. Regression Risk Assessment

| Change | Risk | Mitigation |
|---|---|---|
| A-1 empty-agreement bypass | decodes more often on first utterances | bounded: one extra ~1 s call; smoke-test pipeline stop path |
| A-2 config knob default 0.35 | none (invisible until operator sets it) | default preserves current behaviour |
| A-3 final decode at stop | one extra decode per session at pipeline stop | wrapped in existing try/except (pipeline.py:394-400) |

## 6. Hardware Notes

All timing measurements were taken on this host (not the i5-7300U target).
The flat ~1 s per decode is consistent with the archived baselines
(`asr_ms` p50 ≈ 1104 ms, p95 ≈ 1283 ms in `pipeline_baseline_streaming.json`).

## 7. Summary

F-04 is real, bounded by F-03 (audio retained), and untested by the suite.
Track A is low-risk and cheap; Track B offers no superior mechanism. No
benchmark claims are made for an implementation that does not exist yet.
