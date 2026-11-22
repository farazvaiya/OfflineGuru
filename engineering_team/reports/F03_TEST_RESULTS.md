# F-03 Test Results: Streaming ASR Audio Loss

**Date**: 2026-11-14
**Target**: `guru/realtime.py` `SpeechWindow.rebase()` + `guru/pipeline.py`
`_flush_stream_tail()` frontier-anchored flush (F-03)

**Constraint honoured**: no production files modified, no F-01 files touched,
no synthetic results presented as benchmarks. All runs below are synthetic
audio (no microphone, no real arecord, no ASR model) and are labelled as such.

## 1. Existing Regression Suite (this investigation's runs)

`pytest tests/test_realtime.py -k "rebase or flush or carry or commit"` →
**9 passed in 0.07 s**. `pytest tests/test_realtime.py tests/test_pipeline.py`
→ **43 passed**.

Key existing tests (all pass):

| Test | Property |
|---|---|
| `test_speech_window_rebase_keeps_audio_after_frontier` | F-03 core: audio after frontier survives |
| `test_speech_window_rebase_keeps_everything_when_frontier_is_old` | f03b failure mode closed |
| `test_speech_window_rebase_at_end_clears_audio` | end-of-window clears |
| `test_stream_flush_does_not_recommit_the_retained_context` | no duplicate captions |
| `test_stream_flush_carries_audio_that_arrived_during_the_decode` | `audio_carried_s > 1.0` |

## 2. New Scenario Verification (run this investigation)

Nine scenarios from the task's Phase 4 list, executed against the current
tree (script run inline, not committed; results reproducible from the
commands in §5):

| # | Scenario | Result | Evidence |
|---|---|---|---|
| 1 | Audio arriving during final decode | PASS | kept=4.50 s, end_time unchanged |
| 2 | Audio after committed frontier preserved | PASS | dur=4.00 s of 4.00 s fed |
| 3 | No duplicated/missing frames across 5 flushes | PASS | held+trimmed = 5.00 s = fed |
| 4 | Monotonically increasing timestamps | PASS | [0.5, 1.0, …, 3.0] strictly increasing |
| 5 | Repeated flush operations | PASS | kept converges to 2.00 s each time, no accumulation |
| 6 | Empty window flush | PASS | kept=0, dur=0, no exception |
| 6b | Short (0.1 s) tail flush | PASS | audio kept, no exception |
| 7 | Concurrent feed()+rebase() from two threads | PASS | no crash, no exception |
| 8 | Memory bounds (20 s fed, 5 s cap) | PASS | dur=5.00 s ≤ cap |

## 3. Archived Probe Evidence (real-pipe, frozen artifacts)

| Metric | Pre-fix `stream_flush.json` | Post-fix `stream_flush_after_fix.json` |
|---|---|---|
| window_resets | 14 | **0** |
| window_rebases | — | 11 |
| audio_discarded_at_reset_s | 22.2 | **0** |
| resets_while_speech_active | 13 | 0 |
| audio_carried_s | — | 3.08 |
| segments_published | 20 | 21 |

Full-pipeline runs on `data/demo/lecture.wav` (same input/conditions):

| Artifact | WER | Coverage | Note |
|---|---|---|---|
| `pipeline_baseline_streaming.json` | 0.541 | 0.528 | pre-fix |
| `pipeline_streaming_after_f03.json` | 0.5205 | 0.615 | fix v1 |
| `pipeline_streaming_after_f03b.json` | 1.2103 | 1.821 | regression: over-commit |
| `pipeline_streaming_after_f03c.json` | 1.0769 | 1.631 | regression: duplicates |
| current tree | — | — | frontier-rebase + no-recommit test closes the f03b/c regressions |

These are the recorded artifacts; the current tree was not re-benchmarked
here (no model inference run performed in this investigation).

## 4. Test Coverage Gaps

- G-1: No automated test asserts the drainer-style **thread race** between
  `feed()` (capture thread) and `rebase()` (ASR thread) beyond "does not
  crash". Scenario 7 checks crash-freedom only; exact frame accounting under
  interleaving is not asserted (residual risk R-1).
- G-2: No test for rebase with `keep_context=False`.
- G-3: Physical-microphone validation remains outstanding (see
  F01_FINAL_REVIEW.md §4 for the shared hardware list).

## 5. Reproduction Commands

```bash
cd /home/faraz/project/offline_guru
.venv/bin/python -m pytest tests/test_realtime.py -v -k "rebase or flush or carry or commit"
.venv/bin/python -m pytest tests/test_realtime.py tests/test_pipeline.py -q
# scenario suite: inline script in this investigation; deterministic, synthetic
```

## 6. Summary

The F-03 defect mechanism (unconditional `reset()` at flush) is **not present**
in the current tree; the frontier-anchored `rebase()` + frontier-anchored
`agreement.reset()` path passes all existing and new scenario checks, and the
archived probe artifacts show 0 s discarded vs 22.2 s pre-fix. The findings
record still says `"open"` — bookkeeping lag, documented in the decision
report.
