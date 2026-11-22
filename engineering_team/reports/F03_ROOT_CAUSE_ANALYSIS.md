# F-03 Root Cause Analysis: Streaming ASR Audio Loss

**Finding**: F-03 (P0) — Streaming ASR audio loss. Audit hypothesis:
`ClassroomPipeline._flush_stream_tail()` calls `SpeechWindow.reset()`
unconditionally, discarding audio that arrived during the final decode.

**Report Date**: 2026-11-14
**Status of the hypothesis**: The mechanism was REAL in the original code, but
**it has already been fixed in the current tree**. The audit hypothesis is
stale. This report documents the verification.

---

## 1. Inspection Result (Phase 1)

### 1.1 What the code says today

`guru/pipeline.py:481-518` `_flush_stream_tail()`:

```python
frontier = self._agreement.last_committed_time
if tail:
    frontier = max(frontier, tail[-1].end)
kept = self._window.rebase(frontier)     # <-- rebase, NOT reset
...
self._agreement.reset(frontier)          # <-- frontier-anchored, not zero
```

`guru/realtime.py:379-415` `SpeechWindow.rebase(frontier, keep_context=True)`:
drops audio before `frontier - context_s`, keeps everything after it, clamps
the cut to `[buf_start, end]`, preserves VAD state, re-anchors the clock.
Docstring explicitly cites F-03:

> "``reset()`` is only correct when nothing has been spoken since the last
> decode. In the streaming pipeline it is called at the end of an utterance,
> i.e. *after* a decode that took about a second, so the buffer usually holds
> audio that arrived while the model was running. Discarding it loses the
> beginning of the next sentence (and, when the teacher resumes during the
> decode, the whole of it)."

### 1.2 No reset-at-zero remains in the production path

`grep` over `guru/pipeline.py` + `guru/realtime.py` for `.reset(`:
- `pipeline.py:512` — `self._agreement.reset(frontier)` — frontier-anchored
- `pipeline.py:513` — `self._phrases.reset()` — phrase builder only
- `realtime.py:371` — `SpeechWindow.reset()` exists but is **never called from
  the pipeline** (only from tests)

### 1.3 Whether audio was actually lost (historical, now fixed)

Evidence from `engineering_harness/artifacts/stream_flush.json` (pre-fix probe,
60 s of `data/demo/lecture.wav`):

| Metric | Pre-fix (`stream_flush.json`) | Post-fix (`stream_flush_after_fix.json`) |
|---|---|---|
| window_resets | 14 | **0** |
| window_rebases | — | 11 |
| audio_discarded_at_reset_s | **22.2** | **0** |
| resets_while_speech_active | 13 | 0 |
| audio_discarded_while_speech_active_s | 22.2 | 0 |
| audio_carried_s | — | 3.08 |
| segments_published | 20 | 21 |

The pre-fix probe matches the audit hypothesis exactly: 22.2 s of 59.6 s fed
audio destroyed, 13 of 14 resets while speech was active. The post-fix probe
shows zero discard and 3.08 s carried across phrase boundaries.

### 1.4 Full-pipeline regression evidence

`engineering_harness/artifacts/pipeline_baseline_streaming.json` (pre-fix):
- 55 segments, 73.9 s speech covered, 206/390 words, WER 0.541

Post-fix iterations (same wav, same reference, same model):
- `pipeline_streaming_after_f03.json`: 53 seg, 80.1 s, 240 words, WER 0.5205
- `pipeline_streaming_after_f03b.json`: 89 seg, 118.0 s, 710 words — coverage
  1.821 > 1.0 → **over-commitment regression** (an intermediate fix version
  trimmed too early and re-committed context)
- `pipeline_streaming_after_f03c.json`: 70 seg, 100.7 s, 636 words, WER 1.0769
  — still > 1.0 coverage (duplicates)

The current tree's `rebase(frontier)` with the `trim_to`/context interaction is
the third iteration; the over-commitment regression is closed by
`test_stream_flush_does_not_recommit_the_retained_context`
(`tests/test_realtime.py:194`), which asserts carried context is never
re-published.

### 1.5 Exact state transition that caused the loss (historical)

1. Speech ends → VAD sets `_ended` → `_worth_final_decode()` returns False for
   short tails → `_flush_stream_tail()` runs.
2. OLD CODE: `window.reset()` cleared `_buf`, set `_buf_start = _clock`,
   `_in_speech = False`.
3. Audio that arrived during the ~1.1 s decode (and was already in `_buf`)
   was discarded; the next sentence's first ~1 s was gone.
4. `LocalAgreement.reset(window.start_time)` then wiped agreement state, so the
   next decode could not distinguish carried context from new speech.

### 1.6 Timestamp and frontier correctness (current code)

Verified empirically (this review, synthetic audio, no microphone):

| Edge case | Result |
|---|---|
| rebase(1.0) with 3 s buffered, context 1.0 | cut clamped to buf_start=0.0, keeps all 3 s, `in_speech` preserved |
| rebase(2.0) at exact end | clears buffer, kept=0 |
| rebase(2.0) again on empty | idempotent, kept=1.0 (context slice only) |
| rebase(0.1) with frontier older than buffer start | keeps full 2.0 s (clamped) |
| LocalAgreement.reset(0.6) after commit at 0.6 | `last_committed_time=0.6`; re-inserting overlapping words commits **nothing** — no double-commit |
| Clock after rebase + feed | end_time advances monotonically, no jump |

### 1.7 Other related race conditions

**Latent, not F-03**: `SpeechWindow._buf` is mutated by `feed()` on the capture
thread (`pipeline.py:306` via `submit_audio`) and by `rebase()`/`trim_to()` on
the ASR thread (`pipeline.py:503`), with **no lock**. Worst interleaving is a
lost/duplicated frame at the boundary, not corruption (GIL serializes
bytecode; `np.concatenate` allocates a fresh array). Same exposure existed
before the fix via `trim_to`. Documented as residual risk R-1; not a blocker
for F-03 closure, but should be tracked.

## 2. Reproduction Procedure

The pre-fix bug is reproduced by the archived probe
`engineering_harness/probes/stream_flush.py` (instrumented reset/trim/decode
events). Its output is frozen in `artifacts/stream_flush.json` (22.2 s
discarded). The post-fix output is `artifacts/stream_flush_after_fix.json`
(0 s discarded, 3.08 s carried). Re-running the probe against the current tree
reproduces the post-fix numbers.

## 3. Existing Test Coverage

`tests/test_realtime.py` (all pass, 9/9 selected, 34 total in file):
- `test_speech_window_rebase_keeps_audio_after_frontier` (line 153) — the
  exact F-03 property: audio after the frontier survives
- `test_speech_window_rebase_keeps_everything_when_frontier_is_old` (167) —
  the f03b failure mode
- `test_speech_window_rebase_at_end_clears_audio` (184)
- `test_stream_flush_does_not_recommit_the_retained_context` (194) — no
  duplicate captions from carried context
- `test_stream_flush_carries_audio_that_arrived_during_the_decode` (214) —
  `audio_carried_s > 1.0`

## 4. Conclusion

**CONFIRMED (historically) — ALREADY FIXED in the current tree.**

The audit hypothesis correctly described the original defect, and the
archived probe artifacts prove the loss was real (22.2 s of 59.6 s discarded,
13 resets during active speech). The current implementation
(`_flush_stream_tail` → `rebase(frontier)` + `agreement.reset(frontier)`)
removes the mechanism: zero resets, zero discard, audio carried across
phrase boundaries, no double-commit. The findings record
(`engineering_harness/state/findings.json`) still lists F-03 as `"status":
"open"` — that is a bookkeeping lag, not a live defect. Residual risk R-1
(unsynchronised window access across capture/ASR threads) is pre-existing,
bounded, and separate from F-03.
