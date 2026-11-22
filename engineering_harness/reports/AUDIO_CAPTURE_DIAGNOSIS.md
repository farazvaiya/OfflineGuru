# Audio Capture Diagnosis

**Findings:** F-01 (P0, stderr-pipe deadlock), F-02 (P0, unsupervised capture)
**Evidence:** `artifacts/capture_stall.json`
**Reproduce:** `.venv/bin/python engineering_harness/probes/capture_stall.py`

---

## 1. How capture works today

`guru/audio.py::MicCapture`

```python
cmd = ["arecord", "-q", "-t", "raw", "-f", "S16_LE",
       "-r", "16000", "-c", "1", "-D", device]
self._proc = subprocess.Popen(cmd, stdout=PIPE, stderr=PIPE, bufsize=0)
threading.Thread(target=self._run, args=(on_frames,), daemon=True).start()

# _run:
while not self._stop.is_set():
    raw = self._proc.stdout.read(3200)        # 100 ms
    if not raw:                                # only here is stderr touched
        err = self._proc.stderr.read(400)
        self.last_error = ...
        return
    audio = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    on_frames(audio)
```

Two properties matter:

1. **`stderr=PIPE` with no reader.** arecord writes ALSA warnings (`underrun occurred`, `Unknown PCM`, overrun notices) continuously on a busy laptop. A pipe buffer is 64 KiB. When it fills, the child blocks in `write(2)` — before it ever writes more PCM.
2. **No supervision.** If `read()` returns EOF (child died), `_run` sets `last_error` and returns. `app.py` starts capture once, at session start, and never looks at it again. `/api/health` does not include any capture field.

## 2. Experiment

A shimmed `arecord` is placed first on `PATH`. It emits 3200 bytes of S16_LE PCM every 100 ms (real-time pace) and, in the "overrun" scenario, ~2 KB of ALSA-style stderr per chunk. This is a faithful stand-in for a laptop with a busy USB audio device, and it is the only way to exercise the path on a host with no microphone.

| Scenario | Run | Audio delivered | Ratio | Silent for | arecord alive | Reader thread | `last_error` |
|---|---|---|---|---|---|---|---|
| `stderr_overrun_deadlock` | 10.01 s | **2.20 s** | **0.22** | **7.29 s** | yes (blocked) | alive (blocked in read) | `null` |
| `device_exits_silently` | 8.00 s | 3.00 s | 0.375 | 4.16 s | no | **dead** | `"arecord exited"` |

The stall begins after 22 chunks: 22 × ~2.9 KB ≈ **64 KiB**, exactly the default pipe capacity. That coincidence is the causal fingerprint.

## 3. Hypothesis table

| Hypothesis | Test | Verdict |
|---|---|---|
| stderr pipe fills and blocks arecord | shim with 2 KB stderr/chunk | **accepted** — stall at 64 KiB |
| GIL starvation of the reader thread | inspect thread state during stall | rejected — thread is in `read(2)`, which releases the GIL |
| ALSA device underruns drop samples | no device available | **untested** — recorded as a limitation, not a cause |
| Short reads corrupt the stream | 3200-byte reads on a pipe are legal but not exact | separate issue: `Segmenter` drops the remainder (F-05) |

## 4. Why this explains the classroom symptom

The brief reports a live lecture with missing words, incomplete segments and delayed output. In production this failure mode produces:

* a transcript that runs normally for the first seconds of a session and then **stops entirely**;
* no error in the teacher console, because nothing raised and `last_error` stays `None`;
* the pipeline still reporting `status: running`, still heartbeating, still accepting students;
* `pipeline.status` showing `speech` events while nothing arrives.

The audio is not degraded — it stops. That is distinguishable from packet loss and matches the description of a lecture that "misses words" progressively rather than uniformly.

## 5. Secondary capture defects

* **No `start_new_session`**: `arecord` inherits the server's process group; a crashed server can leave it holding the device, and the next session fails to open it.
* **`stop()` timing**: `proc.terminate()` then `wait(3)`; if arecord ignores SIGTERM the thread stays blocked until process exit (harmless for a session stop, relevant for restart logic).
* **Odd-length reads**: `np.frombuffer(raw, "<i2")` raises `ValueError` on an odd byte count, which kills the reader thread exactly like an exit. A pipe read of an odd number of bytes is possible if the writer is killed mid-sample.
* **`-q` is not enough**: `-q` suppresses arecord's own banner, not ALSA library diagnostics, which come from libasound.

## 6. Fix direction (Phase 2)

1. Drain stderr on a dedicated daemon thread into a bounded ring buffer (last ~50 lines). Never let the child block on its diagnostics.
2. Accept and carry partial reads with an odd-byte carry.
3. Add `MicCapture.alive` / `.state` and a supervisor with bounded restart + exponential backoff (max 5 attempts, then fail loudly).
4. Surface capture state in `/api/health` and publish a `pipeline.error{stage:"capture"}` event so the teacher sees it.
5. `start_new_session=True` so the child cannot outlive the server.

Regression tests required: a stderr flood must not stop PCM delivery; an exit must be detected and reported; odd-length reads must not kill the reader.

## 7. Confidence

High for both findings. The mechanism is reproduced deterministically, the 64 KiB coincidence is measured, and the code path is short enough to read end-to-end. The only untested part is real ALSA behaviour, which cannot be exercised on this host.
