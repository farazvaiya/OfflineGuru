# F-03 Decision Report: Streaming ASR Audio Loss

**Date**: 2026-11-14
**Finding**: F-03 (P0) — Streaming ASR audio loss
**Final status**: **CONFIRMED** (defect mechanism real, already fixed in tree;
bookkeeping lags)

---

## 1. Was the audit hypothesis correct?

The hypothesis — "`_flush_stream_tail()` calls `SpeechWindow.reset()`
unconditionally; audio arriving during the final decode is discarded" —
**accurately described the original defect** but is **stale for the current
tree**. Verification:

- `guru/pipeline.py:481-518` uses `self._window.rebase(frontier)` and
  `self._agreement.reset(frontier)` — frontier-anchored, not zero-anchored.
- `SpeechWindow.reset()` is no longer called anywhere in the pipeline
  (grep-verified; only tests call it).
- Archived probe: pre-fix discarded 22.2 s of 59.6 s fed audio across 14
  resets (13 during active speech); post-fix discards 0 s and carries 3.08 s.

The defect is **CONFIRMED as historically real and currently fixed**.

## 2. Track comparison (same input, same conditions)

| Criterion | Track A (in-tree frontier rebase) | Track B (open-source adoption) |
|---|---|---|
| Frame loss | 0 s discarded (probe); scenario 1-3 pass | WhisperLiveKit same pattern (`chunk_at` frontier trim) — equivalent |
| Duplicate frames | closed by frontier-anchored `agreement.reset` + `test_..._does_not_recommit` | same mechanism upstream (`pop_committed`) |
| Timestamp correctness | monotonic clock verified (scenario 4) | same design family |
| Caption latency | carried audio wakes ASR immediately (`_asr_wake.set()`) | not measurable without adoption |
| CPU/RAM | unchanged: window capped 15 s; one O(kept) slice per flush | WhisperLiveKit is a full server replacement — heavier |
| Reliability | 9/9 existing + 9/9 new scenario checks pass | untested in this environment |
| Existing regression suite | 272/272 (F-01 suite included) | N/A |
| License | n/a (own code) | Apache-2.0 (WhisperLiveKit) — compatible but whole-library adoption |
| Integration complexity | none (already in tree) | HIGH: replaces the whole streaming layer |

**Decision**: keep Track A. Track B's reference implementation
(WhisperLiveKit) corroborates the design — it trims at the committed frontier
and resets only on speaker change / ≥5 s silence, never at end-of-utterance —
but offers no mechanism worth adopting, and adoption cost is disproportionate.
faster-whisper is batch-only (not applicable); WhisperStreaming/WhisperLive
unverified from source (recorded as limitation, not recommended).

## 3. Remaining items

1. **Bookkeeping**: `engineering_harness/state/findings.json` still lists F-03
   `"status": "open"`. Human should set it to resolved (with the probe
   artifacts as evidence) — this report does not mutate state.
2. **Residual risk R-1**: `SpeechWindow._buf` is written by `feed()` on the
   capture thread and by `rebase()`/`trim_to()` on the ASR thread without a
   lock. Bounded worst case (one lost/duplicated frame at a boundary; GIL +
   fresh-array concatenate prevent corruption). Pre-existing, separate from
   F-03; recommend a follow-up finding if the team wants it closed.
3. **Physical validation**: real-microphone lecture on the i5-7300U target,
   confirming no silence onset and no WER regression (shared hardware list in
   F01_FINAL_REVIEW.md §4).
4. **F-04 (related, still open)**: `_worth_final_decode()`'s 0.35 s threshold
   still skips short tails; F-03's retained window bounds the loss, but the
   threshold change itself was never made.

## 4. Recommendation

**Do not integrate anything for F-03 — there is nothing to integrate.** The
fix is already in the working tree, tested, and verified. The required human
actions are: (a) close the findings-record entry, (b) run the physical
microphone validation, (c) decide whether to open R-1 as its own finding.

## 5. Evidence index

- `guru/pipeline.py:481-518` — frontier-anchored flush
- `guru/realtime.py:371-415` — `reset()` (unused in pipeline) and `rebase()`
- `engineering_harness/artifacts/stream_flush.json` — pre-fix 22.2 s discarded
- `engineering_harness/artifacts/stream_flush_after_fix.json` — post-fix 0 s
- `engineering_harness/artifacts/pipeline_streaming_after_f03{,b,c}.json` —
  fix iterations incl. over-commitment regressions
- `tests/test_realtime.py:153-228` — five dedicated regression tests
- `engineering_team/reports/F03_ROOT_CAUSE_ANALYSIS.md` — full analysis
- `engineering_team/reports/F03_TRACK_A.md`, `F03_TRACK_B.md` — track records
- `engineering_team/reports/F03_TEST_RESULTS.md` — test evidence

## 6. Constraints compliance

- No production files modified (incl. `guru/pipeline.py`, `guru/realtime.py`).
- F-01 files untouched.
- No commits, merges, or integration performed.
- No synthetic results presented as benchmarks; all new numbers are from
  synthetic-audio scenario checks and are labelled as such.
- Track A and Track B kept independent; Track B inspected real upstream
  source where obtainable (WhisperLiveKit 0.2.19 from PyPI, faster-whisper
  1.2.1 installed) and records non-obtainable sources as unverified.
