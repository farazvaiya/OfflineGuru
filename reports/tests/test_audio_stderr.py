"""Regression tests for F-01: arecord stderr pipe deadlock.

Root cause: ``MicCapture._run()`` read arecord stderr only after stdout EOF,
so a busy stderr filled the 64 KiB kernel pipe buffer, arecord blocked in
write(2), and stdout went silent. Fix: a daemon drainer thread consumes
stderr continuously.

These tests use fake in-memory pipes, not a real arecord or microphone. They
prove the drainage property, not physical capture behaviour.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from unittest.mock import patch

import numpy as np
import pytest

import guru.audio as audio_mod
from guru.audio import FRAME_SAMPLES, MicCapture


class _FakePipe:
    """A blocking pipe that yields canned chunks, like arecord's stdout/stderr."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self._lock = threading.Lock()
        self._reads = 0

    def read(self, size: int = -1) -> bytes:
        with self._lock:
            self._reads += 1
            if self._chunks:
                return self._chunks.pop(0)
            return b""

    @property
    def reads(self) -> int:
        with self._lock:
            return self._reads


class _FakeProc:
    def __init__(self, stdout_chunks: list[bytes], stderr_chunks: list[bytes]) -> None:
        self.stdout = _FakePipe(stdout_chunks)
        self.stderr = _FakePipe(stderr_chunks)
        self.returncode: int | None = None
        self._terminated = threading.Event()

    def terminate(self) -> None:
        self._terminated.set()

    def wait(self, timeout: float | None = None) -> int:
        self._terminated.wait(timeout)
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self._terminated.set()


def _audio_chunk(n_frames: int = FRAME_SAMPLES) -> bytes:
    return b"\x00\x01" * n_frames


def _start(cap: MicCapture, proc: _FakeProc, on_frames) -> None:
    """Start a MicCapture backed by a fake process."""
    with patch.object(audio_mod.subprocess, "Popen", return_value=proc):
        cap.start(on_frames)


def _join(cap: MicCapture, timeout: float = 5.0) -> None:
    if cap._thread is not None:
        cap._thread.join(timeout=timeout)
    if cap._stderr_thread is not None:
        cap._stderr_thread.join(timeout=timeout)


def test_stderr_drainer_reads_stderr_continuously():
    """The drainer thread consumes stderr while stdout is still flowing.

    Without the fix, stderr is read only after stdout EOF (one read, post
    exit). With the fix, stderr is read continuously during capture.
    """
    stderr_chunks = [b"ALSA warning: period underrun\n" for _ in range(30)]
    stdout_chunks = [_audio_chunk() for _ in range(30)] + [b""]
    proc = _FakeProc(stdout_chunks, stderr_chunks)

    cap = MicCapture(device="default")
    frames: list[np.ndarray] = []
    _start(cap, proc, frames.append)
    _join(cap)
    cap.stop()

    assert proc.stderr.reads >= 20, (
        f"drainer read stderr only {proc.stderr.reads} times; "
        "stderr is not being drained continuously"
    )
    assert len(frames) >= 20, f"expected audio frames, got {len(frames)}"


def test_large_stderr_output_cannot_block_stdout():
    """A stderr flood larger than the 64 KiB kernel buffer must not stall stdout.

    Simulates arecord writing ~104 KiB of stderr (1.6x the kernel pipe buffer)
    while still producing audio. The drainer keeps the pipe empty, so the
    reader thread keeps getting audio frames.
    """
    stderr_chunks = [b"ALSA warning: period underrun\n" * 3 for _ in range(1200)]
    stdout_chunks = [_audio_chunk() for _ in range(1200)] + [b""]
    proc = _FakeProc(stdout_chunks, stderr_chunks)

    cap = MicCapture(device="default")
    frames: list[np.ndarray] = []
    _start(cap, proc, frames.append)
    _join(cap, timeout=15)
    cap.stop()

    assert len(frames) >= 1000, (
        f"stdout stalled: only {len(frames)} audio frames from 1200 available; "
        "stderr pipe fill blocked arecord"
    )
    assert proc.stderr.reads >= 1000, (
        f"drainer fell behind: only {proc.stderr.reads} stderr reads for 1200 chunks"
    )


def test_stderr_tail_retained_for_diagnostics():
    """The drainer keeps a bounded tail of stderr for shutdown diagnostics."""
    stderr_chunks = [f"warning {i}\n".encode() for i in range(50)]
    stdout_chunks = [_audio_chunk() for _ in range(5)] + [b""]
    proc = _FakeProc(stdout_chunks, stderr_chunks)

    cap = MicCapture(device="default")
    _start(cap, proc, lambda audio: None)
    _join(cap)
    cap.stop()

    assert cap._stderr_tail, "drainer retained no stderr tail"
    assert len(cap._stderr_tail) <= 4096, "stderr tail is unbounded"
    assert b"warning 49" in cap._stderr_tail, "tail does not contain the last warning"


def test_stop_does_not_hang():
    """stop() must return promptly even with the drainer thread running."""
    # stderr never EOFs; the drainer would block forever if stop() did not
    # signal it. The stop event + daemon status must break the loop.
    stderr_chunks = [b"ALSA warning\n"] * 10_000
    stdout_chunks = [_audio_chunk() for _ in range(10_000)]
    proc = _FakeProc(stdout_chunks, stderr_chunks)

    cap = MicCapture(device="default")
    _start(cap, proc, lambda audio: None)
    time.sleep(0.05)  # let both threads enter their read loops

    t0 = time.monotonic()
    cap.stop()
    elapsed = time.monotonic() - t0

    assert elapsed < 5.0, f"stop() took {elapsed:.1f}s; shutdown hangs"
    assert cap._stop.is_set()


def test_existing_audio_behavior_preserved():
    """Audio frames are still float32 mono at 16 kHz, handed to the callback."""
    stdout_chunks = [_audio_chunk(FRAME_SAMPLES) for _ in range(10)] + [b""]
    proc = _FakeProc(stdout_chunks, [])

    cap = MicCapture(device="default")
    frames: list[np.ndarray] = []
    _start(cap, proc, frames.append)
    _join(cap)
    cap.stop()

    assert len(frames) == 10, f"expected 10 frames, got {len(frames)}"
    for f in frames:
        assert f.dtype == np.float32
        assert f.shape == (FRAME_SAMPLES,)
        assert np.all(np.abs(f) <= 1.0)


def test_drainer_is_daemon_thread():
    """The drainer must not prevent process exit if the reader never stops."""
    cap = MicCapture(device="default")
    proc = _FakeProc([_audio_chunk(), b""], [b"err\n"])
    _start(cap, proc, lambda audio: None)
    try:
        assert cap._stderr_thread is not None
        assert cap._stderr_thread.daemon, "drainer must be a daemon thread"
    finally:
        cap.stop()


@pytest.mark.parametrize("chunks", [0, 1, 100])
def test_empty_and_small_stderr(chunks: int):
    """Zero or tiny stderr volumes must not break capture."""
    stderr_chunks = [b"err\n" for _ in range(chunks)]
    stdout_chunks = [_audio_chunk() for _ in range(10)] + [b""]
    proc = _FakeProc(stdout_chunks, stderr_chunks)

    cap = MicCapture(device="default")
    frames: list[np.ndarray] = []
    _start(cap, proc, frames.append)
    _join(cap)
    cap.stop()

    assert len(frames) == 10


def test_reader_thread_exits_on_stdout_eof():
    """When arecord exits (stdout EOF), the reader returns and records an error."""
    proc = _FakeProc([b""], [b"arecord: device vanished\n"])
    cap = MicCapture(device="default")
    _start(cap, proc, lambda audio: None)
    _join(cap)
    cap.stop()

    assert cap.last_error is not None, "reader did not record arecord exit"
    assert "arecord" in cap.last_error or cap._stderr_tail != b""


def test_pre_fix_behaviour_stderr_only_read_at_eof():
    """Document the pre-fix bug: stderr was read only after stdout EOF.

    Reproduces the ORIGINAL ``_run`` logic inline (no drainer). It must show
    exactly one stderr read, at EOF, while a 64 KiB-scale stderr flood would
    have already blocked arecord. This is the baseline the fix improves on.
    """
    stderr_chunks = [b"ALSA warning: period underrun\n" for _ in range(1000)]
    stdout_chunks = [_audio_chunk() for _ in range(10)] + [b""]
    proc = _FakeProc(stdout_chunks, stderr_chunks)

    # Inline copy of the ORIGINAL _run (pre-fix): no drainer thread.
    def old_run(on_frames):
        assert proc.stdout is not None
        bytes_per_frame = 2
        block_frames = FRAME_SAMPLES * 5
        while True:
            raw = proc.stdout.read(block_frames * bytes_per_frame)
            if not raw:
                err = b""
                if proc.stderr is not None:
                    err = proc.stderr.read(400) or b""
                cap.last_error = err.decode("utf-8", "replace") or "arecord exited"
                return
            on_frames(np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0)

    cap = MicCapture(device="default")
    frames: list[np.ndarray] = []
    old_run(frames.append)

    # Pre-fix: stderr read exactly once, only after stdout EOF.
    assert proc.stderr.reads == 1, (
        f"pre-fix should read stderr once at EOF, got {proc.stderr.reads}"
    )
    # And it would have read at most 400 bytes of the 1000-chunk flood,
    # leaving ~99.9% of stderr unread — the pipe would have filled and
    # arecord blocked long before this point in a real run.
    assert len(cap.last_error) <= 400