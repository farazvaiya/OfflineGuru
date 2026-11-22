# F-03 Closure Report: Streaming ASR Audio Loss

**Date**: 2026-11-14
**Finding**: F-03 (P0)
**Status**: `CONFIRMED_FIXED — READY_FOR_HUMAN_CLOSURE`
**Authority for closure**: human reviewer only. This report does NOT close the
finding; `engineering_harness/state/findings.json` carries the new status and
a human must flip it to closed.

---

## 1. Confirmed Fix

`guru/pipeline.py:481-518` `_flush_stream_tail()` (current tree):

```python
frontier = self._agreement.last_committed_time
if tail:
    frontier = max(frontier, tail[-1].end)
kept = self._window.rebase(frontier)      # NOT reset()
...
self._agreement.reset(frontier)           # frontier-anchored, NOT zero
```

`guru/realtime.py:379-415` `SpeechWindow.rebase()`: drops audio before
`frontier - context_s`, clamps the cut to `[buf_start, end_time]`, retains
everything after the frontier, preserves VAD state, re-anchors the clock.

## 2. Historical Evidence

### Pre-fix (`engineering_harness/artifacts/stream_flush.json`, 60 s replay of lecture.wav)
- window_resets: **14**, 13 while speech active
- audio_discarded_at_reset_s: **22.2** of 59.6 s fed (37 %)
- 11 ended-decodes started while teacher had already resumed (in_speech=True)

### Post-fix (`engineering_harness/artifacts/stream_flush_after_fix.json`)
- window_resets: **0**, window_rebases: 11
- audio_discarded_at_reset_s: **0**
- audio_carried_s: 3.08 (kept across phrase boundaries)
- segments_published: 21 (vs 20 pre-fix)

### Fix iterations (same wav / reference / model)
`pipeline_streaming_after_f03.json` (WER 0.5205, coverage 0.615) →
`after_f03b.json` (coverage 1.821 — over-commitment regression) →
`after_f03c.json` (coverage 1.631 — duplicates) → current tree (frontier
rebase + `test_stream_flush_does_not_recommit_the_retained_context` closes
both).

### Test evidence (2026-11-14, this investigation)
- `tests/test_realtime.py:153-228`: 5 dedicated regression tests — all pass
- Expanded scenario suite: 9/9 pass (audio during decode survives; frontier
  preserved; no frame loss/duplication over 5 flushes; monotonic timestamps;
  repeated flush converges; empty/short tails; concurrent feed+rebase
  crash-free; memory bounded by max_window_s)
- Full suite: **272/272** pass

## 3. Remaining Validation Limitations

1. **No physical-microphone validation** performed; capture path verified via
   fake arecord only (see F-01 review §4 for the shared hardware list).
2. **No new full-pipeline benchmark** for this closure; archived artifacts are
   the evidence. A fresh end-to-end WER comparison on the target i5-7300U
   hardware remains advisable.
3. **R-1 residual risk** (unsynchronized `feed()`/`rebase()` across threads)
   documented separately; see `R1_FOLLOWUP_REPORT.md`.
4. **Bookkeeping lag**: findings.json said `"open"` until this task; the
   record now carries the closure metadata. Human must confirm closure.

## 4. Evidence Index

`guru/pipeline.py:481-518`, `guru/realtime.py:371-415`;
`engineering_harness/artifacts/stream_flush{,_after_fix}.json`;
`engineering_harness/artifacts/pipeline_streaming_after_f03{,b,c}.json`;
`tests/test_realtime.py:153-228`; reports: `F03_ROOT_CAUSE_ANALYSIS.md`,
`F03_TRACK_A.md`, `F03_TRACK_B.md`, `F03_TEST_RESULTS.md`,
`F03_DECISION_REPORT.md`.

## 5. Out of Scope

No production code modified; no F-01 changes; no benchmark claims beyond the
frozen artifacts. F-04 tracked separately (see `F04_*.md`).
