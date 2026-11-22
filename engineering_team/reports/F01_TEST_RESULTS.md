# F-01 Test Results: Audio Capture Deadlock Fix

**Test Date**: 2026-11-14
**Target**: `guru/audio.py` `MicCapture` stderr deadlock (F-01, P0)

## 1. Existing Test Coverage

| Test File | Tests | Coverage |
|---|---|---|
| `tests/test_audio.py` | 7 tests | Segmenter VAD logic only — no MicCapture tests |
| `tests/conftest.py` | fixtures | `silence()`, `tone()` — synthetic audio generators |
| **Before this work** | 0 | The repository had zero unit tests for `MicCapture.start()` / `_run()` / `stop()`. |

## 2. Reproduction of Original Deadlock

**Procedure** (without fix):
1. Start a lecture capture session with microphone input (`capture=mic`).
2. arecord writes to stderr, but stderr is never read during capture.
3. After ~2 seconds, the 64 KiB kernel stderr buffer fills.
4. arecord blocks on `write(2)` to stderr → no more stdout data.
5. Reader thread blocks on `stdout.read()` → silent capture.
6. Result: ~2.2 s of audio from 10 s lecture (WER audit: 0.22 ratio).

**Verification**: Reproduced via the findings in `state/findings.json` (F-01), which recorded:
- `audio_delivered_s=2.2 of run_seconds=10.01`
- "The stall coincides with the 64 KiB stderr buffer filling (22 chunks x ~2.9 KB)."

The pre-fix behaviour is also reproduced in `test_pre_fix_behaviour_stderr_only_read_at_eof`
(`tests/test_audio_stderr.py`), which runs the original `_run` logic inline and asserts
stderr was read exactly once, at EOF, leaving a 1000-chunk flood unread.

## 3. Implemented Fix (Track A)

`guru/audio.py` `MicCapture`:
- Added `_stderr_drainer()` daemon thread method — continuously reads stderr,
  keeps a bounded 4 KiB tail in `self._stderr_tail` for diagnostics.
- `start()` now launches both the reader thread and the drainer thread.
- `_run()` prefers `_stderr_tail` for the post-EOF diagnostic read (the drainer
  may have already consumed the pipe).
- `stop()` unchanged in behaviour; drainer is a daemon thread.

## 4. Regression Tests Created

New file: `tests/test_audio_stderr.py` — 11 tests, all using fake in-memory
pipes (`_FakeProc` / `_FakePipe`) via `unittest.mock.patch` of
`guru.audio.subprocess.Popen`. No real arecord or microphone is used.

| # | Test | Verifies | Result |
|---|---|---|---|
| 1 | `test_stderr_drainer_reads_stderr_continuously` | Drainer reads stderr >= 20 times during capture (not just at EOF) | PASS |
| 2 | `test_large_stderr_output_cannot_block_stdout` | ~104 KiB stderr flood (1.6x kernel buffer) does not stall stdout; >= 1000 audio frames delivered | PASS |
| 3 | `test_stderr_tail_retained_for_diagnostics` | `_stderr_tail` is bounded (<= 4 KiB) and contains the last warning | PASS |
| 4 | `test_stop_does_not_hang` | `stop()` returns < 5 s even with a never-EOF stderr | PASS |
| 5 | `test_existing_audio_behavior_preserved` | Frames are float32, shape (1600,), |x| <= 1.0 — existing behaviour intact | PASS |
| 6 | `test_drainer_is_daemon_thread` | Drainer is a daemon thread (won't block process exit) | PASS |
| 7-9 | `test_empty_and_small_stderr[0/1/100]` | Zero/tiny stderr volumes don't break capture | PASS |
| 10 | `test_reader_thread_exits_on_stdout_eof` | Reader returns and records `last_error` on arecord exit | PASS |
| 11 | `test_pre_fix_behaviour_stderr_only_read_at_eof` | Documents the original bug: pre-fix logic reads stderr exactly once, at EOF | PASS |

**Full run**: `pytest tests/test_audio_stderr.py -v` → **11 passed in 0.15s**.

## 5. Full Suite Regression Check

`pytest -q` → **272 passed** (261 pre-existing + 11 new) in ~14 s.
No existing test broke. No ASR, VAD, translation, TTS, or playback logic
was modified.

## 6. Smoke Test (real arecord path)

`MicCapture(device='default')` + `start()` + `stop()` on this container
(no microphone): capture starts, drainer thread is created and daemon,
stop returns cleanly. arecord exits immediately (no device), reader
records `last_error="arecord exited"` as before. This confirms the
lifecycle, not physical capture.

## 7. Test Evidence Summary

- **Verified facts**: drainer thread drains stderr continuously; stdout
  delivery survives a >64 KiB stderr flood; `_stderr_tail` is bounded;
  `stop()` does not hang; existing frame format is unchanged; full suite
  passes.
- **Hypotheses**: none remaining — the drainer prevents the pipe-fill
  deadlock by construction (the pipe is never allowed to fill).
- **Reproduced failures**: the pre-fix single-read-at-EOF behaviour is
  reproduced and asserted in test 11.
- **Remaining physical tests** (cannot be done in this container):
  1. Real microphone lecture capture on the target i5-7300U laptop,
     verifying no silence after 2 s.
  2. Real ALSA stderr flood (overrun warnings) under load, verifying
     the drainer keeps arecord unblocked.
  3. 45-minute sustained capture, verifying drainer memory stays bounded
     (`_stderr_tail` <= 4 KiB) and no thread leak.

## 8. Status

- Implementation: **done** in `guru/audio.py` (Track A).
- Tests: **done** in `tests/test_audio_stderr.py` (11/11 pass).
- Integration/approval: **pending human approval** per Phase 5 constraint.
  This change is staged in the working tree only; no merge, no commit, no
  checkpoint integration has been performed.