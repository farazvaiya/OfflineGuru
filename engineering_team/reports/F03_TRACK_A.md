# F-03 Track A: Custom Solution (existing Offline Guru architecture)

**Status**: The fix described below is **already implemented** in the current
tree. This document is the design record for the approach the code took,
written so the dual-track comparison can judge it on its merits. No new
production change was made for F-03.

## 1. Proposed Algorithm

Anchor every end-of-utterance flush to the **committed word frontier**, not to
the window start:

1. `LocalAgreement.flush_all()` commits the unstable tail (end-of-speech).
2. `frontier = max(agreement.last_committed_time, tail[-1].end)` — the last
   instant of committed speech.
3. `window.rebase(frontier)` — drop audio before `frontier - context_s`, keep
   everything after it, clamp the cut to `[buf_start, end_time]`.
4. `agreement.reset(frontier)` — reset the hypothesis buffer with
   `last_committed_time = frontier`, so carried audio cannot be re-committed.
5. `phrases.reset()`, `last_partial_text = ""`, and if audio was carried
   (`kept > 0`), wake the ASR loop immediately rather than waiting for the
   next capture callback.

Reference: `guru/pipeline.py:481-518`, `guru/realtime.py:379-415`.

## 2. Audio Buffer Lifecycle

```
capture thread                     ASR thread
     |                                |
feed(samples) ──► _pending           |
     |           frames → _buf      |
     |           (VAD: start/end)   |
     |              ┌───────────────┘
     |              │ decode(window.audio())
     |              │ commit() → trim_to(last_committed.end)
     |              │ (end of speech) _flush_stream_tail()
     |              │   rebase(frontier)  ◄── cut at frontier-context, keep tail
     |              │   agreement.reset(frontier)
     |              │   if kept>0: wake
     └──────────────┘  next feed() appends to surviving _buf
```

- Audio is **never discarded wholesale** mid-stream.
- Carried audio keeps VAD in speech state (`rebase` sets
  `_in_speech = bool(buf.size)`), so the next sentence does not need a fresh
  trigger.
- `_cap_window()` bounds total memory at `max_window_s` (default 15 s).

## 3. Committed Frontier Handling

- The frontier is the **end timestamp of the last committed word**, not the
  window start, not zero.
- `rebase` clamps `cut = max(frontier - context_s, buf_start)` so a frontier
  older than the buffer (the f03b failure mode) cannot wipe the window.
- `agreement.reset(frontier)` makes `insert()` drop words that end before
  `last_committed_time - 0.1` (`realtime.py:122`), so re-decoding carried
  context cannot double-commit.

## 4. Concurrency Implications

- Reader/ASR work happens on the ASR thread; `feed()` on the capture thread.
- The flush path is entirely on the ASR thread — no cross-thread state
  mutation during `rebase`.
- **Residual risk R-1**: `feed()` (capture thread) and `rebase()`/`trim_to()`
  (ASR thread) both mutate `SpeechWindow._buf` without a lock. Worst case is
  one lost or duplicated frame at a boundary, not corruption (GIL + fresh-array
  `np.concatenate`). Pre-existing exposure; tracked separately from F-03.

## 5. Memory and CPU Impact

- Memory: unchanged asymptotic. `_stderr_tail`-style bounded tail does not
  exist here; the window is already capped at `max_window_s` (15 s ≈ 480 KB
  float32). `rebase` copies the surviving slice once per flush (`_buf[n:].copy()`),
  O(kept) — at most one extra 15 s buffer transiently.
- CPU: one extra slice+copy per phrase boundary (~every 8 s of speech).
  Negligible against a ~1.1 s Whisper decode.
- Latency: `kept > 0` sets `_asr_wake` immediately, so carried audio is
  decoded without waiting for the next 0.5 s step — caption latency for the
  next sentence improves, not degrades.

## 6. Failure and Recovery Behavior

- If `flush_all()` returns an empty tail and the frontier is stale, `rebase`
  clamps and keeps the buffer — no data loss, no crash.
- If the model dies mid-decode, the window retains everything after the last
  committed frontier; the next decode resumes from carried audio.
- If `rebase` throws, the pipeline's `except` in `_stream_decode`/`_run`
  publishes `pipeline.error` and the loop continues; the window is left
  un-flushed (safe default: keep audio).

## 7. Verification

- `tests/test_realtime.py:153-228` — five dedicated regression tests, all pass.
- Real-pipe A/B (this investigation): pre-fix probe discarded 22.2 s of 59.6 s;
  post-fix discards 0 s and carries 3.08 s.
- Full streaming pipeline: coverage 0.528 → 0.615 (after_f03) with no duplicate
  captions (the f03b over-commitment regression was caught by
  `test_stream_flush_does_not_recommit_the_retained_context` and fixed by the
  frontier-anchored reset).
