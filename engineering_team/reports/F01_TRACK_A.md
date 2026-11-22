# F-01 Track A: Minimal Python Subprocess I/O Fix

**Approach**: Modify `MicCapture._run()` to drain stderr continuously on a daemon thread, keeping the pipe bounded.

## 1. Design

Add a second daemon thread whose sole purpose is to read stderr from the `arecord` process and discard it (or keep a bounded history). This ensures the stderr pipe never fills, preventing `arecord` from blocking.

### Thread design:

```python
def _stderr_drainer(self) -> None:
    """Continuously drain stderr so arecord never blocks on its own output."""
    while not self._stop.is_set():
        try:
            chunk = self._proc.stderr.read(4096)
            if not chunk:
                break  # stderr closed — process is ending
            self._stderr_tail = (self._stderr_tail + chunk)[-4096:]
        except Exception:
            break  # best-effort; drainer dying is not fatal
    try:
        remaining = self._proc.stderr.read()
        if remaining:
            self._stderr_tail = (self._stderr_tail + remaining)[-4096:]
    except Exception:
        pass
```

## 2. Correctness

- **Deadlock prevention**: By continuously consuming stderr, the 64 KiB kernel pipe buffer never fills. `arecord` can always write its status/errors without blocking.
- **Correctness**: No audio data is lost; stdout reading pattern is unchanged.
- **Shutdown**: The daemon thread terminates when `self._stop` is set (same as the reader thread). The main `stop()` method does not need to join this thread since it's a daemon.

## 3. Deadlock Prevention Mechanism

| Vector | Before | After |
|---|---|---|
| stderr pipe fill | 64 KiB kernel buffer fills → `arecord` blocks → no stdout | Continuously drained → buffer never fills → `arecord` produces audio indefinitely |
| Reader thread block | Blocks on `stdout.read()` after stderr kills stdout | Unaffected — stdout reader unchanged |

## 4. Shutdown Behavior

- The drainer thread is a daemon thread (`daemon=True` in `threading.Thread`).
- When `stop()` is called, `self._stop.set()` signals both the reader and drainer threads to stop.
- Both threads will exit their loops on the next iteration.
- `self._proc.terminate()` + `self._proc.wait(timeout=3)` ensures the subprocess is killed if it doesn't exit cleanly.
- Since the drainer is a daemon, the process is cleaned up by the OS if the main thread exits before it.

## 5. Memory Usage

- Drainer buffer: constant ~4 KiB (configurable).
- No significant increase over the existing reader thread.
- The bounded buffer (last 2 KiB of stderr text) is sufficient for diagnostics.

## 6. CPU Overhead

- One additional thread with a `read()` call that blocks until data is available.
- No polling — the thread blocks on `stderr.read()`, consuming ~0% CPU when no data is written.
- Negligible overhead on the i5-7300U target.

## 7. Error Visibility

- **Before**: stderr was only read after stdout EOF → last errors were captured but too late to prevent deadlock.
- **After**: stderr is drained continuously → diagnostics are available in near real-time.
- A bounded history (last 4 KiB of stderr text, `_stderr_tail`) is kept for the `last_error` field, preserving the existing error-reporting behavior. The reader prefers `_stderr_tail` over a post-EOF pipe read, since the drainer may already have consumed the pipe.

## 8. Maintainability

- Minimal code change: add ~15 lines + a new thread.
- The drainer function is self-contained and testable in isolation.
- No changes to the audio processing pipeline or VAD logic.
- Clear separation of concerns: reader thread handles audio, drainer thread handles stderr.

## 9. Implementation Plan

1. Add `_stderr_drainer()` method to `MicCapture`.
2. Start the drainer thread in `start()` alongside the existing reader thread.
3. Remove the inline stderr read from `_run()` (or reduce it to a best-effort diagnostic after the thread exits).
4. Ensure `stop()` works correctly with the additional thread.

## 10. Risks

- If the drainer thread somehow blocks (unlikely with blocking `read()`), it could prevent the reader thread from seeing process exit. Mitigation: use a timeout on `read()` or run in a separate thread with its own stop event.
- Daemon thread semantics: if the main process exits abnormally, the drainer may not finish draining. Mitigation: this is acceptable — the alternative (deadlock) is worse.

## 11. Comparison with Track B

Track A is the minimal fix — it addresses the root cause (stderr pipe blockage) with minimal code change and no new dependencies. Track B would explore alternative subprocess patterns (e.g., using `subprocess.PIPE` with `select`, or `asyncio` subprocess). Track A is preferred for this P0 finding because it fixes the verified root cause with the least risk of introducing new bugs.