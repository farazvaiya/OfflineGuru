# F-03 Track B: Open-Source Research

**Question**: does an existing open-source streaming-ASR buffer pattern solve
F-03 better than Offline Guru's own frontier-rebase?

**Method**: inspect actual source of the candidates (not READMEs). License,
maintenance, offline fit, CPU/RAM, and — decisively — whether the pattern
keeps audio that arrives during a decode.

## Candidate 1: WhisperLiveKit (QuentinFuxa/WhisperLiveKit)

- **Source inspected**: PyPI `whisperlivekit==0.2.19`, file
  `whisperlivekit/local_agreement/online_asr.py` (downloaded and read
  directly).
- **License**: Apache-2.0 (dist-info METADATA). Compatible with Offline Guru's
  GPL-3.0-or-later? Apache-2.0 is permissive; including Apache-2.0 code in a
  GPL project is permitted. (Offline Guru already adapts LocalAgreement-2 from
  this project — see `guru/realtime.py` attribution header.)
- **Maintenance**: active (0.2.19 published; many patch releases).
- **Offline**: yes — local Whisper, no cloud.
- **The relevant pattern** (`chunk_at`, `init`, `end_silence`):
  - `chunk_at(time)` trims the audio buffer at the committed-time frontier:
    `audio_buffer = audio_buffer[int(cut_seconds * SR):]`,
    `buffer_time_offset = time` — a frontier rebase, identical in spirit to
    Offline Guru's `rebase()`.
  - `init(offset)` (full reset) is called **only** on speaker change or
    ≥ 5 s silence — never at every end-of-utterance flush.
  - `pop_committed(time)` removes committed tokens from the hypothesis buffer
    at the same frontier.
- **Does it solve F-03?** Yes — it never discards un-decoded audio at a flush;
  it trims at the committed frontier and carries the tail.
- **Integration complexity**: HIGH. WhisperLiveKit is a full server
  (WebSocket, its own threading, its own VAD iterator, token types). Adopting
  it would replace Offline Guru's entire streaming layer, not just the flush
  logic. CPU/RAM: heavier than the current ~800 MB footprint.

## Candidate 2: SYSTRAN/faster-whisper

- **Source inspected**: `faster_whisper/transcribe.py` (installed v1.2.1).
- **License**: MIT. Maintenance: very active. Offline: yes.
- **Finding**: `transcribe()` is **batch** — audio fully loaded, sliced into
  chunks via VAD, no incremental append API, no rolling window between
  decodes. The "arrived during decode" case does not exist in its design;
  continuity is handled only via the `prompt` token list.
- **Does it solve F-03?** No — not applicable. It has no streaming buffer to
  fix or borrow; the streaming layer is the caller's responsibility.
- **Integration complexity**: N/A (already in use for the segment path).

## Candidate 3: Collabora/WhisperStreaming and WhisperLive

- Not inspected line-by-line (network fetch of the repos failed/404 from this
  environment; PyPI has no installable package for either under those names).
- Published descriptions indicate sliding-window chunking with overlap removal
  by token prefix matching — the same family of frontier-anchored trimming.
- **Limitation recorded**: unverified against source; not recommended on
  evidence. (Documented as unverified rather than assumed.)

## Track B Verdict

The open-source evidence **corroborates** the Track A design. WhisperLiveKit —
the upstream Offline Guru's LocalAgreement was adapted from — uses the same
frontier-anchored trim (`chunk_at` / `pop_committed` / `init(offset)`) and
never resets the audio buffer to zero at an end-of-speech flush. There is no
materially different open-source pattern that solves F-03 better, and no
library that can be dropped in to replace just the flush logic.

**Do not adopt an external library for F-03.** The existing in-tree fix is the
same pattern as the upstream reference implementation, with less integration
risk and a smaller footprint.

## License Note

No third-party code was copied in this investigation. WhisperLiveKit's
Apache-2.0 LocalAgreement adaptation already present in `guru/realtime.py`
carries its attribution header; that predates F-03 and is unchanged.
