# F-04 Track A: Custom Solution

**Date**: 2026-11-14
**Status**: PROPOSAL ONLY — no production code modified.

## 1. Design

Three small, independent changes, all in the existing Offline Guru
architecture (no new components, no dependencies):

### A-1: Empty-buffer bypass (correctness fix for zero cost)

```python
def _worth_final_decode(self, window):
    if window.duration_s <= 0:
        return False
    un_decoded = window.end_time - self._agreement.last_committed_time
    if un_decoded <= 0:
        return False
    if not self._agreement.committed:
        # flush_all() on an empty agreement commits nothing; skipping a
        # decode here loses the tail unconditionally.
        return True
    return un_decoded >= self.settings.asr.tail_decode_min_s  # A-2
```

### A-2: Expose the threshold as a config knob

Add `stream_tail_decode_min_s: float = 0.35` to `AsrConfig`
(`guru/config.py` AsrConfig, default preserves current behaviour) and
`asr.stream_tail_decode_min_s` to `config/default.toml`. No default change
→ zero-risk rollback, operator decides the trade.

### A-3: Unconditional final decode at pipeline stop

The final-flush call site (`pipeline.py:395`) runs when the pipeline stops;
there is no "subsequent speech" that will ever recover the tail. Change:

```python
if self._window is not None and self._window.duration_s > 0:
    self._stream_decode(True)
else:
    self._flush_stream_tail()
```

i.e., at stop, any non-empty buffer gets one final decode regardless of the
threshold. Cost: exactly one extra ~1 s decode per session.

## 2. Algorithm / Analysis

- End-of-speech skip keeps its CPU guard for non-empty agreements (hot path).
- The empty-agreement case decodes (otherwise guaranteed loss).
- Pipeline stop decodes any residue (one call per session).
- The window is capped at 15 s; the tail contribution to one decode is
  ≤ `max_window_s` — no unbounded cost.

## 3. Committed Frontier / Concurrency

- Frontier semantics unchanged (rebase remains authoritative).
- No new shared-state access; the bypass reads `self._agreement.committed`
  (already read on the ASR thread only).
- R-1 (feed/rebase race) is untouched — see R1_FOLLOWUP_REPORT.md; Track A
  neither worsens nor fixes it.

## 4. Memory / CPU Impact

- CPU: 0 extra mid-stream if agreement non-empty (unchanged); +~1 s per
  utterance only when agreement is empty; +~1 s per session at stop.
- Measured decode cost (this host, whisper-base int8, 3 threads): ~1.0 s flat
  regardless of tail length (0.35/0.6/1.5 s all ≈ 1 s).
- Memory: unchanged (window cap still 15 s, ~480 KB float32).

## 5. Failure / Recovery

- If the final decode raises at stop, existing `except` publishes
  `pipeline.error` and the flush proceeds with `flush_all()` — same safe
  default as today.
- A misconfigured tiny threshold is safe: worst case is more decodes, never
  less correctness.

## 6. Regression Test Plan (all synthetic)

- T-A1: empty agreement + tail audio → `_worth_final_decode` returns True.
- T-A2: non-empty agreement + 0.20 s un-decoded → returns False (unchanged).
- T-A3: non-empty agreement + 0.36 s un-decoded → returns True.
- T-A4: pipeline stop with non-empty window calls `_stream_decode` exactly
  once even when agreement empty (mock ASR; assert call count).
- T-A5: config knob honoured: `stream_tail_decode_min_s=0.05` → 0.10 s tail
  decodes.
- T-A6: no double-commit: tail decode followed by flush → committed words
  appear exactly once (reuse `test_stream_flush_does_not_recommit...`).

## 7. Rollback

Delete the AsrConfig field + revert `_worth_final_decode`/final-flush call
site — one small revert; default behaviour unchanged until operator opted in.
