# F-01 Final Review: Audio Capture Deadlock Fix

**Review Date**: 2026-11-14
**Reviewer**: Autonomous review (no code modified during this review)
**Scope**: `guru/audio.py`, `tests/test_audio_stderr.py`, and the four F-01 reports

---

## 1. Verified Facts

Each fact below was independently verified by inspection or execution during this review.

| # | Claim | Verification method | Result |
|---|---|---|---|
| V1 | stderr is drained continuously during capture | `guru/audio.py:241-271` `_stderr_drainer()` loops `read(4096)` on a daemon thread started in `start()` (line 238-239) | VERIFIED |
| V2 | Drainage does not block stdout | Real-pipe A/B test: identical fake-arecord (66 KiB+ stderr flood, 60 audio blocks) → 16/60 frames without fix, **60/60 frames with fix** | VERIFIED |
| V3 | Diagnostic tail is bounded to 4 KiB | `guru/audio.py:260` `(self._stderr_tail + chunk)[-4096:]`; measured `len(cap._stderr_tail) == 4096` in the real-pipe run | VERIFIED |
| V4 | Shutdown cannot hang | Worst-case stress (subprocess ignores SIGTERM + floods stderr): `stop()` returned in **3.00 s** exactly — the `terminate()` → `wait(timeout=3)` → `kill()` path in `guru/audio.py:295-307` | VERIFIED |
| V5 | Existing audio behaviour unchanged | `tests/test_audio.py` 7/7 pass; new `tests/test_audio_stderr.py` 11/11 pass; full suite **272 passed** | VERIFIED |
| V6 | Reader prefers retained tail for exit diagnostics | `guru/audio.py:283` reads `self._stderr_tail[-400:]` before falling back to the pipe; smoke test showed `last_error` carrying a real traceback | VERIFIED |
| V7 | Pre-fix behaviour documented, not just asserted | `tests/test_audio_stderr.py:226-263` runs the ORIGINAL `_run` logic inline and asserts stderr is read exactly once, at EOF | VERIFIED |
| V8 | No production code was modified by this review | `diff -q` against the pre-review backup: RESTORE CLEAN | VERIFIED |

---

## 2. Potential Defects

None blocking. Three observations, ranked by severity:

### O-1 (low): `stop()` does not join the drainer thread
`guru/audio.py:305-307` deliberately skips `self._stderr_thread.join()`. The comment justifies it (daemon thread; joining a possibly-unstarted thread can hang). **Assessment**: acceptable. The drainer breaks its loop on `self._stop` or on stderr EOF; worst case it lingers until process exit, which the daemon flag already permits. A hung `join()` in a request handler would be worse than the thread's brief post-stop lifetime. Not a defect — a documented trade-off.

### O-2 (low): `_stderr_tail` write is not lock-guarded
`guru/audio.py:260` writes `self._stderr_tail` from the drainer thread; `guru/audio.py:283` reads it from the reader thread. CPython's GIL makes the slice-and-assign atomic in practice; a torn read is not possible for a single attribute assignment. **Assessment**: acceptable for diagnostics-only data. If `_stderr_tail` were ever used for correctness (it is not — only `last_error` derives from it), a lock would be required.

### O-3 (informational): drainer's final `read()` after loop exit
`guru/audio.py:266` `self._proc.stderr.read()` (no size cap) runs once after the loop. If arecord closed stderr, this returns immediately; if it somehow had not, this could block. It runs on the daemon thread only, so it cannot hang `stop()`. **Assessment**: acceptable, consistent with O-1.

---

## 3. Test Coverage Gaps

| Gap | Impact | Suggested follow-up (not required for approval) |
|---|---|---|
| G-1: No test asserts the **drainer thread exits** after `stop()` | Low — daemon status covers the lifecycle risk | Add `assert not cap._stderr_thread.is_alive()` after a post-stop sleep in `test_stop_does_not_hang` |
| G-2: No test for `start()` called twice on one instance | Low — pre-existing behaviour, unchanged by this fix | Out of scope for F-01 |
| G-3: Mock tests use `_FakePipe.read()` that never blocks | Low — real-pipe A/B test (V2) covers the blocking case empirically | The real-pipe A/B result should be recorded in CI or as a scripted probe |
| G-4: No test for `_stderr_tail` content when stderr exceeds 4 KiB *before* first read | Low — slicing `[-4096:]` is provably bounded | Covered implicitly by V3 measurement |

---

## 4. Physical Microphone Validation Requirements

These cannot be done in this container (no microphone, no real ALSA). They are **required before the fix can be called production-verified**, per the original finding's caveat:

1. **Live mic lecture on the target i5-7300U laptop** — capture ≥ 5 minutes; confirm no silence onset after ~2 s (the original symptom) and no WER degradation vs the segment path.
2. **Real ALSA overrun flood under load** — run capture while the CPU is saturated; confirm the drainer keeps arecord unblocked (compare audio-delivered ratio against the 0.22 pre-fix baseline from `state/findings.json` F-01).
3. **45-minute sustained capture** — confirm `_stderr_tail` stays ≤ 4 KiB, RSS does not grow, and no thread leak (`threading.active_count()` stable across the run).
4. **Re-run `engineering_harness/probes/capture_stall.py`** against the fixed code — the `overrun` scenario should now show `audio_delivered_s ≈ run_seconds` and `arecord_process_still_alive` reaching exit cleanly instead of stalling at 2.2 s.

---

## 5. Cross-Report Consistency Check

| Report | Status after review |
|---|---|
| `F01_ROOT_CAUSE_ANALYSIS.md` | Consistent with code; fix-status line updated to reflect implementation |
| `F01_TRACK_A.md` | Consistent; code snippet matches `guru/audio.py:241-271` (tail retained, 4 KiB bound) |
| `F01_TRACK_B.md` | Consistent; correctly describes alternatives not taken |
| `F01_TEST_RESULTS.md` | Consistent; 11/11 + 272/272 confirmed by execution in this review |

One correction to my own earlier tick note: the "Track A doc claims diagnostics are kept but implementation discards them" discrepancy was real at that time and is now **resolved** — `_stderr_tail` is retained and consumed by `_run()` (V6).

---

## 6. Final Recommendation

# **APPROVE**

The fix is correct, minimal, and empirically verified on real OS pipes against a
reproduction of the original deadlock (16/60 → 60/60 frames). Shutdown is bounded
(3.00 s worst case). All 272 tests pass. No blocking defects found.

**Conditions attached to this approval** (per the task's Phase 5 constraint, no
integration has occurred and none should occur without the human owner):

1. The production change in `guru/audio.py` remains **staged in the working tree
   only** — no commit, no merge, no checkpoint integration.
2. Physical-microphone validation items 1-4 in §4 must pass before the finding
   can be marked `resolved` in `state/findings.json`.
3. Gap G-1 (drainer exit assertion) should be added in a follow-up; it is not a
   blocker.

**Verification evidence summary**:
- `pytest tests/test_audio_stderr.py -v` → 11 passed
- `pytest -q` → 272 passed
- Real-pipe A/B (fix vs no-fix, same fake-arecord): 60/60 vs 16/60 frames
- Worst-case shutdown (SIGTERM-ignoring flooder): 3.00 s, no hang
- `_stderr_tail` measured at exactly 4096 bytes under flood
