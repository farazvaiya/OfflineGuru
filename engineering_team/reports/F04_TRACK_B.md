# F-04 Track B: Open-Source Research

**Date**: 2026-11-14
**Status**: PROPOSAL ONLY — no code adopted, no dependencies added.

## 1. Research Question

Does any open-source streaming-ASR implementation solve the "un-decoded tail
below a threshold" problem differently from Offline Guru's own skip-if-short
pattern, in a way worth adopting?

## 2. Candidates Inspected (source-level)

### WhisperLiveKit 0.2.19 (PyPI package, Apache-2.0)
- Source: `whisperlivekit/local_agreement/online_asr.py` (downloaded, read).
- Tail handling: **no tail threshold at all**. Its `end_silence()` pads the
  gap with zeros and `process_iter()` decodes; the only full buffer reset
  (`init(offset)`) happens on speaker change or ≥ 5 s silence, always with
  the frontier offset preserved.
- Committing: `flush()` commits longest common prefix (same LocalAgreement-2
  Offline Guru adapted); `pop_committed(time)` trims at the committed-time
  frontier.
- License: Apache-2.0 (dist-info METADATA). Compatible with GPL-3.0 project.
- Maintenance: active (0.2.19, many patch releases).
- Offline: yes. CPU/RAM: full server footprint — heavier than Offline Guru's
  ~800 MB.
- **Solves F-04?** Partially — it never skips a decode for being short, so
  no tail is lost by threshold; but it has no equivalent of the CPU guard,
  so it pays ~1 s per end-of-speech on any hardware. It demonstrates the
  "always decode" alternative, not a mechanism to import.

### faster-whisper 1.2.1 (installed in this venv, MIT)
- `faster_whisper/transcribe.py`: batch only — no incremental append, no
  rolling window, no end-of-speech flush. Not applicable to F-04.

### WhisperStreaming / WhisperLive (Collabora)
- Not verifiable from this environment (no PyPI package; repo fetch
  unavailable). Descriptions indicate sliding-window chunking with token
  prefix overlap removal — same family, no evidence of a tail-threshold
  mechanism. **Recorded as unverified; not recommended.**

## 3. Comparison

| Criterion | Track A (custom) | Track B (adopt WhisperLiveKit pattern) |
|---|---|---|
| Tail loss | eliminated via A-1/A-3, guarded via A-2 | eliminated (no threshold) |
| CPU cost | bounded: +~1 s only when agreement empty or at stop | +~1 s per end-of-speech, always |
| Integration | ~15 lines in existing `_worth_final_decode` + config field | replace streaming layer (server, threading, VAD, token types) |
| License | own code | Apache-2.0 (compatible) |
| Offline | yes | yes |
| Test burden | 6 synthetic tests | re-validate entire streaming path |
| Fit with F-03 fix | reuses frontier rebase | orthogonal but requires re-porting |

## 4. Verdict

No open-source mechanism solves F-04 better than the Track A design. The
upstream evidence (WhisperLiveKit) corroborates that *never skipping a short
decode* is safe — but it pays the CPU cost unconditionally. Track A gets the
same correctness for a bounded, configurable cost by decoding only when the
agreement buffer is empty or at pipeline stop. **Adopt Track A; do not
integrate any external library for F-04.**

## 5. License Note

No third-party code was copied or integrated. WhisperLiveKit was inspected
for design evidence only; Offline Guru's pre-existing Apache-2.
LocalAgreement adaptation (attribution header in `guru/realtime.py`) is
unchanged by this investigation.
