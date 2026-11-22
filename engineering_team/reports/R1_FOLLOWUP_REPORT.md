# R-1 Follow-up: Unsynchronized SpeechWindow Access (Capture vs ASR Thread)

**Date**: 2026-11-14
**Type**: residual risk follow-up (identified during F-03 verification)
**Status**: DOCUMENTED — no fix implemented (by design of this task)
**Related**: F-03 (fixed via frontier rebase), F-01 (capture path)

---

## 1. The Risk

`SpeechWindow` is written by two threads without a lock:

| Thread | Entry point | Methods called | Fields mutated |
|---|---|---|---|
| Capture thread (`guru-wav-replay` via `ctx.pipeline.submit_audio`, or mic thread via `MicCapture._run`) | `submit_audio()` → `_submit_stream()` (pipeline.py:289-319) | `feed()` (realtime.py:290) | `_buf`, `_pending`, `_clock`, `_preroll`, `_in_speech`, `_silence_run`, `_speech_start`, `_total_frames`(Segmenter path), `_noise_floor`, `dropped_frames` |
| ASR thread (`guru-asr`, pipeline.py:208) | ASR loop | `trim_to()` (realtime.py:358), `rebase()` (realtime.py:379), `take_ended()`, `end_time`/`start_time` reads | `_buf`, `_buf_start`, `_in_speech`, `_silence_run`, `_ended`, `_preroll` |

No `threading.Lock` or `RLock` exists anywhere in `guru/pipeline.py` for the
window (grep-verified) or in `guru/realtime.py`.

## 2. Possible Interleavings

1. **feed() concatenates while rebase() slices**: `feed` builds
   `np.concatenate([self._buf, frame])` (realtime.py:336) holding a reference
   to `self._buf`; `rebase` concurrently assigns `self._buf = self._buf[n:]`
   (or `.copy()`). The concatenate either sees the old object (fine) or the
   new one (fine) — **but** the frame counter `_clock` (advanced in `feed`)
   is independent of `_buf_start` (advanced by `rebase`):
   - if `rebase` runs between `feed`'s `concatenate` and the `_buf` assignment,
     `feed` writes the *old* (pre-trim) array plus the frame — **resurrecting
     trimmed audio** (duplicates committed words in the next decode), or
   - `feed` writes the new array built from the pre-trim slice — **silently
     dropping the trim** (memory grows, clock desync).
2. **`trim_to` mid-iteration**: same pattern, one frame behind instead of
   one segment — drops up to one 20 ms frame that the ASR thread believes
   was removed.
3. **Timestamp effects**: `rebase` sets `_buf_start = cut`; `feed` uses
   `_clock` for frame timestamps. If `rebase` re-anchors `_buf_start`
   *after* `feed` computed `frame_start = self._clock` but *before* the frame
   lands in `_buf`, the new frame's timestamp is correct (it uses `_clock`)
   but the buffer composition is stale — the window may hold audio whose
   timestamps are inconsistent with `_buf_start`, producing overlapping or
   gapped timestamps downstream (Whisper decode receives audio + offset;
   double-commit risk rises because `last_committed_time` comparisons shift).
4. **`_pending` corruption**: `feed` mutates `_pending` across multiple
   branches of the frame loop; a `rebase` during the loop cannot reorder
   `_pending`, but a second `feed` call (callback reentrancy) interleaving on
   the same thread-local is impossible (GIL); worst case remains the
   `_buf`-slice race above.

## 3. Potential Impact

- **Frame loss**: at most one 20 ms frame per race (bounded).
- **Frame duplication**: trimmed audio can reappear in the buffer (see #1);
  without a re-check against `last_committed_time`, re-committed words →
  duplicate captions (the exact f03b regression, now only reachable via race).
- **Timestamp corruption**: `end_time`/`start_time` may transiently
  under/over-estimate by one frame; `flush_all` timestamps for carried words
  may be off by ≤ 20 ms. No crash expected.
- **Severity justification**: REAL but BOUNDED. Requires the ASR thread to
  run `trim_to`/`rebase` while the capture thread is inside `feed` — i.e.,
  exactly the F-03 flush window, which is precisely when the race was
  introduced into the pipeline's hot path. The race predates the fix via
  `trim_to`, so this is not a regression introduced by F-03, but the fix
  widens the surface slightly (rebase copies). Impact is caption
  duplication/rare frame loss — correctness degradation, not data loss, no
  crash, no security impact. **Severity: P2 (concurrency correctness), not P0.**

## 4. Reproduction Strategy

1. Deterministic unit reproduction (no threads): simulate by calling
   `feed()` partially (monkeypatch `_feed_frame` to pause after
   `concatenate`), then `rebase()`, then `feed()` resumes and writes the
   stale concatenated array. Assert `_buf` contains trimmed bytes.
2. Stress reproduction (real threads): capture thread feeds tone() at
   real-time pace; ASR thread loops `rebase(frontier)` at 10 Hz; count
   hash of `_buf` pre/post each `rebase`; assert monotonic total frames fed
   minus trimmed equals buffer length (allow small slack only with lock).
3. Detect duplication: run pipeline for 60 s replay; compare `offset` passed
   to `transcribe_words` against `window.start_time` across a flush; any
   decrease indicates rebase ran mid-feed.

## 5. Required Concurrency Regression Tests

- T-1: paused-concatenation repro (monkeypatched) asserts trimmed audio is
  not resurrected — **would FAIL today without a lock**.
- T-2: threaded stress (10 s replay, 100 flushes) asserts frame-count
  conservation: `frames_fed == frames_trimmed + frames_in_window` within
  ±1 frame — **would FAIL intermittently today**.
- T-3: timestamp monotonicity across a flush boundary under concurrent feed
  (window.end_time non-decreasing within one decode step).
- T-4: `_pending` remainder survives interleaved `rebase` calls.
- T-5: after implementing a fix, T-1..T-4 must pass with the race removed
  (regression protection).

## 6. Fix Sketch (NOT IMPLEMENTED — for the approver)

Add `self._buf_lock = threading.Lock()` to `SpeechWindow`; hold it around
`feed()`'s buffer writes and around `rebase()`/`trim_to()`/`_cap_window()`.
Cost: one lock acquisition per 20 ms frame; negligible contention.
Alternative: single-writer principle — move the window mutation onto the ASR
thread and queue frames via `queue.Queue`; larger change, same effect.

## 7. Decision Needed From Human

1. Accept as documented residual risk (bounded, P2), or
2. Commission the fix (requires modifying `guru/realtime.py` — explicitly
   out of scope for F-03/F-04 work), or
3. Fold into the next scheduled reliability sprint.
