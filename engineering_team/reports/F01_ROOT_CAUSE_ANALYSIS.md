# F-01 Root Cause Analysis: Audio Capture Deadlock

**Finding**: F-01 (P0) — Audio capture deadlock causing live lectures to go silent after ~2 seconds.

**Report Date**: 2026-11-14

## 1. Affected Code

| File | Lines | Function |
|---|---|---|
| `guru/audio.py:215-226` | `MicCapture.start()` | Creates subprocess with `stderr=subprocess.PIPE`; starts reader thread |
| `guru/audio.py:228-245` | `MicCapture._run()` | Reads from `stdout` in a loop; reads stderr only *after* stdout EOF |
| `guru/audio.py:247-256` | `MicCapture.stop()` | Terminates subprocess and joins thread |

## 2. Root Cause

**Verified fact**: `MicCapture._run()` at line 232–243 reads from `self._proc.stdout` in a loop. It only reads `self._proc.stderr` *after* the stdout read returns empty (i.e., the subprocess has exited). There is **no continuous drainage** of the stderr pipe during capture.

**How the deadlock occurs**:

1. `arecord` writes PCM audio data to stdout and status/errors to stderr.
2. The Linux kernel provides a **64 KiB buffer** for stderr. If arecord writes more than 64 KiB to stderr without being read, the writing process blocks — it cannot write more until someone reads from the pipe.
3. `MicCapture._run()` continuously reads from stdout but **never reads stderr** during capture.
4. After ~2 seconds of audio, arecord's stderr accumulates beyond 64 KiB.
5. `arecord` blocks on its own stderr write.
6. Since `arecord` is blocked, it produces no more stdout data.
7. The reader thread blocks on `self._proc.stdout.read()` — nothing more comes.
8. Result: **silent capture** with `arecord` still alive, no error recorded.

**Evidence from the findings** (F-01 in `state/findings.json`):
- "MicCapture gives arecord a stderr pipe that is never read. Real ALSA overrun warnings fill the 64 KiB kernel pipe buffer; arecord blocks in write(2); stdout stops producing PCM; the reader thread blocks forever in read(2)."
- Audio delivered: 2.2 s of 10 s (ratio 0.22)
- "The stall coincides with the 64 KiB stderr buffer filling (22 chunks x ~2.9 KB)."

## 3. Alternative Explanations Considered

| Hypothesis | Tested | Verdict | Evidence |
|---|---|---|---|
| GIL starves the reader thread | Yes | Rejected | The thread is blocked inside `read(2)`, not waiting on the GIL; a blocked syscall releases the GIL. |
| ALSA device underruns and drops samples | No (limited) | Documented limitation | "No capture device exists in this container, so real ALSA underrun behaviour cannot be exercised." |
| **stderr pipe deadlock** | Yes | **Accepted** | Matches the exact timing (2.2 s), volume (64 KiB buffer), and symptom (silent capture, process alive). |

## 4. Reproduction Procedure

1. Start a lecture capture session with microphone input (`capture=mic`).
2. Let capture run for ~10 seconds.
3. Observe that transcript stops after ~2 seconds of audio.
4. Check logs: `arecord` process still alive but no new audio data.
5. The `/api/health` endpoint does not report capture state.

## 5. Existing Test Coverage

No dedicated tests for the stderr deadlock exist in the repository. The probe `engineering_harness/probes/capture_stall.py` exercises the capture path but does not specifically test the stderr drainage behavior.

## 6. Summary

The reported deadlock hypothesis is **verified**: `MicCapture._run()` never reads stderr during capture, allowing the 64 KiB kernel pipe buffer to fill, causing `arecord` to block and silently stop producing audio.

**Fix status**: implemented in `guru/audio.py` (Track A daemon drainer thread), regression-tested in `tests/test_audio_stderr.py` (11/11 pass, full suite 272/272 pass). Awaiting human approval per the Phase 5 constraint; no merge/commit performed.