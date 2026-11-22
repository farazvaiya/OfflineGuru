# F-01 Track B: Alternative Robust Subprocess Streaming Pattern

**Approach**: Use `subprocess` with `select` for concurrent stdout/stderr drainage, or restructure to use `asyncio` subprocess.

## 1. Candidate Pattern: `select`-based concurrent I/O

### Design:

Use Python's `select.select()` to monitor both stdout and stderr file descriptors simultaneously, reading from each as data becomes available. This avoids the need for separate threads while ensuring neither pipe blocks.

```python
import select
import os

def _run_select_based(self, on_frames):
    self._proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0
    )
    
    stdout_fd = self._proc.stdout.fileno()
    stderr_fd = self._proc.stderr.fileno()
    
    while not self._stop.is_set():
        # Wait for data on either fd with timeout for stop check
        ready, _, _ = select.select(
            [stdout_fd, stderr_fd],
            [], [],
            0.1  # 100ms timeout allows periodic stop check
        )
        
        for fd in ready:
            if fd == stdout_fd:
                raw = self._proc.stdout.read(FRAME_SAMPLES * 2)
                if not raw:
                    # stdout closed
                    self._handle_process_exit()
                    return
                audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
                on_frames(audio)
            elif fd == stderr_fd:
                # Drain stderr to prevent pipe fill
                try:
                    self._proc.stderr.read1(4096)
                except Exception:
                    pass
    
    # Cleanup
    self._proc.wait()
```

### Correctness:
- Both pipes are monitored concurrently — no pipe can fill because data is read as soon as it's available.
- The 100ms timeout ensures the stop event is checked frequently.
- `read1(n)` reads up to n bytes without blocking for the full buffer.

### Deadlock Prevention:
- `select()` monitors both FDs; as soon as data is written to either pipe, it's read immediately.
- The 64 KiB stderr buffer never fills because data is consumed asynchronously.

### Shutdown Behavior:
- The loop checks `self._stop.is_set()` on each iteration.
- After the loop, `self._proc.wait()` reaps the subprocess.
- `stop()` sets `self._stop` and calls `self._proc.terminate()`.

### Memory Usage:
- No extra threads — just the existing single-threaded loop.
- Buffer for select timeout: minimal (0.1s of processing time).

### CPU Overhead:
- `select()` with 100ms timeout: ~1% CPU on idle, negligible under load.
- One `read1()` call per select iteration when stderr has data.
- Comparable to the current single-threaded approach.

### Error Visibility:
- stderr is drained in the same loop iteration audio is read.
- `last_error` can be updated on process exit as before.
- No regression in error reporting.

### Maintainability:
- Uses standard library only (`select`, `os`).
- Well-understood pattern for concurrent subprocess I/O.
- More code than the threaded approach but no thread-safety concerns.
- Easier to test since there's no thread coordination.

## 2. Candidate Pattern: `asyncio` subprocess

### Design:

Replace the threading model entirely with `asyncio` subprocess, which natively supports concurrent reading of stdout and stderr.

```python
import asyncio

class MicCapture:
    def __init__(self, ...):
        super().__init__(...)
        self._proc = None
        self._stop = asyncio.Event()
    
    async def start(self, on_frames):
        cmd = [...]
        self._proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.PIPE, stderr=asyncio.PIPE
        )
        self._thread = asyncio.create_task(self._run_async(on_frames))
    
    async def _run_async(self, on_frames):
        while not self._stop.is_set():
            # Read stdout and stderr concurrently
            stdout_data = await self._proc.stdout.read(FRAME_SAMPLES * 2)
            stderr_data = await self._proc.stderr.read(4096)
            
            if stdout_data:
                audio = np.frombuffer(stdout_data, dtype="<i2").astype(np.float32) / 32768.0
                on_frames(audio)
            
            if stderr_data:
                # Drain stderr to prevent pipe fill
                self.last_error = stderr_data.decode("utf-8", "replace") or "arecord exited"
            
            if not stdout_data and not stderr_data:
                # Process may have exited
                break
        
        self._proc.close()
```

### Correctness:
- `asyncio` inherently handles concurrent I/O on both pipes.
- `read()` calls are non-blocking at the event loop level — they return when data is available or the pipe is closed.

### Deadlock Prevention:
- Same as `select`-based: both pipes are read in each iteration, preventing buffer fill.

### Shutdown Behavior:
- `self._stop` event signals the loop to exit.
- `await self._proc.wait()` reaps the subprocess.
- Clean integration with the existing async FastAPI architecture.

### Memory Usage:
- Similar to current approach — one coroutine instead of one thread.
- No stack overhead from threading.

### CPU Overhead:
- Event loop overhead: minimal (~0.1% on typical workloads).
- `await` yields control back to the event loop, allowing other tasks to run.

### Error Visibility:
- stderr data is captured in each iteration.
- `last_error` updated on each stderr read.
- Same error-reporting behavior as current code.

### Maintainability:
- Requires `asyncio` integration — the rest of `app.py` is already async.
- Fits naturally with the existing FastAPI/asyncio architecture.
- Fewer moving parts than the threaded approach (no thread synchronization).
- But requires restructuring `MicCapture` from threading to asyncio, which is a larger change.

## 3. Comparison: Track A vs Track B

| Aspect | Track A (daemon drainer thread) | Track B `select`-based | Track B `asyncio` |
|---|---|---|---|
| Code change size | ~15 lines + thread | ~30 lines + select import | ~40 lines + asyncio refactor |
| Dependencies | None (std threading) | None (std select) | None (std asyncio) but requires async refactor |
| Thread safety | Two daemon threads | Single-threaded, no locks | Single-threaded event loop |
| Deadlock prevention | ✅ Continuous drain | ✅ Concurrent monitoring | ✅ Concurrent monitoring |
| Shutdown | ✅ Daemon threads exit | ✅ Stop event + wait | ✅ asyncio event + wait |
| Memory overhead | ~4 KiB buffer | Minimal | Minimal |
| CPU overhead | Negligible | Negligible (~1% with 100ms timeout) | Negligible |
| Error visibility | ✅ Continuous drain | ✅ Same iteration | ✅ Same iteration |
| Maintainability | ✅ Simple, familiar pattern | ✅ Standard library, no threads | ✅ Fits async architecture, but larger change |
| Test complexity | ✅ Existing test patterns | ✅ Straightforward | ✅ Requires async test patterns |
| Risk of regression | Low (minimal change) | Low (structured change) | Medium (restructures MicCapture) |
| Fit for current codebase | ✅ Works with sync `MicCapture` used from sync `start()` | ✅ Works with sync code | ✅ Requires changing `start()` signature and callers |

## 4. Recommended Choice

**Track A (daemon drainer thread)** is the recommended approach for this environment and codebase because:

1. **Minimal risk**: The change is localized to `guru/audio.py` — no restructuring of `MicCapture` or its callers.
2. **Compatible with current API**: `MicCapture.start()` and `MicCapture.stop()` keep their current signatures; the caller in `app.py:499` does not need to change.
3. **Same-thread audio processing**: The existing reader thread handles audio; adding a second daemon thread for stderr is a predictable pattern.
4. **Proven in similar projects**: Many subprocess wrappers use a secondary thread for stderr drainage — it's an established pattern.
5. **Lower immediate cost**: Can be implemented and verified in a single coding session without affecting other components.

**Track B `select`-based** is a strong alternative if the team prefers threadless code, but it requires reworking the `_run()` method from a simple loop to a select-based concurrent reader, which changes the control flow more significantly.

**Track B `asyncio`** offers the tightest integration with the async FastAPI server but at the cost of restructuring `MicCapture` from a threading model to an asyncio model — this risks breaking the `/api/teacher/start` endpoint and other callers. Not recommended for a P0 fix unless the team is already planning an asyncio migration.

## 5. Implementation Summary (Track A)

The Track A fix adds one daemon thread that continuously reads and discards stderr data. This is the minimal, lowest-risk fix for the verified root cause (stderr pipe blockage). It prevents `arecord` from blocking on its own stderr writes, ensuring continuous audio capture for the lifetime of the lecture.

The implementation:
- Adds `_stderr_drainer()` method to `MicCapture`
- Starts the thread in `start()` 
- Removes the inline stderr read from `_run()` (or reduces it to post-exit diagnostic)
- `stop()` continues to work as before (daemon threads clean up with the process)