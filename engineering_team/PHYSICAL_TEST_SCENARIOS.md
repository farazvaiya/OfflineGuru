# Physical Test Scenarios (Library)

**Date**: 2026-11-14
**How to use**: each scenario lists expected conditions, what the monitoring
system records, and what only a human can confirm. `NOT_EXECUTED` is the
default until a human runs the scenario on target hardware.

Legend: 🧑 = human required · 🤖 = monitoring can verify · 🚫 = impossible server-side

---

## Scenario A — Microphone

| Check | Type | Evidence |
|---|---|---|
| mic startup under `capture=mic` | 🧑🤖 | session RUNNING; capture inference `active` |
| continuous capture > 5 min | 🧑🤖 | no `last_error` transitions; `stream_decodes` rises |
| short utterance (< 2 s) | 🧑🤖 | `segments_finalized` increments |
| long monologue (> 12 s) | 🧑🤖 | force-cut segments observed; `audio_carried_s > 0` |
| silence rejection | 🧑🤖 | `segments_empty` stable while teacher silent |
| capture interruption (mic unplug) | 🧑🤖 | `last_error` appears; F-02 signal |
| capture recovery | 🧑🤖 | restart resumes segments *after fix F-02 lands* |
| audible Bangla through student headphones | 🧑🚫 | student ack or operator observation only |

## Scenario B — Bidirectional Translation

| Check | Type | Evidence |
|---|---|---|
| EN→BN captions | 🤖 | segments carry `bn` text |
| BN→EN captions | 🤖 | segments carry `en` text |
| short utterances | 🧑🤖 | teacher speaks; WER vs reference |
| long utterances | 🧑🤖 | force-cut + carry works |
| pauses mid-lecture | 🧑🤖 | resume without dropped captions |
| **final words at lecture end** | 🧑🤖 | F-04 symptom check; requires F-04 disposition |
| Bangla-to-Bangla (no translation) | 🤖 | `source==target` segments |

## Scenario C — Playback

| Check | Type | Evidence |
|---|---|---|
| audio delivery events | 🤖 | bus subscribers delivered>0 |
| delivery drop counts | 🤖 | bus dropped==0 expected |
| playback started (student audible) | 🧑🚫 | student report/ack |
| playback ended events | 🧑🚫 | student report/ack |
| playback errors | 🧑 | console error reported |
| queue growth | 🤖 | `tts_queue_drops`, subscriber `queued` |
| delayed playback | 🧑🤖 | student reports delay; `e2e_ms` latency |
| playback order errors | 🤖 | `playback_order_errors` stat |

## Scenario D — Student Load

Expected students: 1 · 2 · 5 · 10 · 15 · 30.

| Check | Type | Evidence |
|---|---|---|
| connection success | 🧑🤖 | subscriber count ≥ expected |
| delivery to all students | 🤖 | per-subscriber delivered>0 |
| zero dropped segments | 🤖 | bus dropped==0 |
| latency under load | 🤖 | `e2e_ms` p95 vs baseline |
| reconnection recovery | 🧑🤖 | student drops+rejoins; buffered events delivered |
| failure of one student | 🧑🤖 | other students unaffected |
| **30 students actual** | 🧑 | loopback simulation ≠ physical radio validation |

Scenario D passes ONLY when actually executed; a simulated load is not a
physical pass.

## Scenario E — Environmental Conditions

| Condition | Type | Notes |
|---|---|---|
| quiet room | 🧑 | baseline |
| background noise (fans/chatter) | 🧑🤖 | `room_noise=noisy` recorded; WER delta measured |
| microphone distance 1m / 3m | 🧑🤖 | `device_distances` recorded |
| fast speaking pace | 🧑🤖 | `speaking_pace=fast` |
| slow speaking pace | 🧑🤖 | `speaking_pace=slow` |
| 15-minute continuous lecture | 🧑🤖 | 30-min and 45-min variants below |

## Scenario F — Long-Duration Stability

| Session length | Type | Evidence |
|---|---|---|
| 15 minutes | 🧑🤖 | CPU/RAM trend; RSS growth < 10 % |
| 30 minutes | 🧑🤖 | same; queue stability |
| 45 minutes | 🧑🤖 | same; error count; thermal if sensors exist |
| temperature trend | 🤖 or unavailable | `temperature_c`; unavailable on hosts without sensors |

## Transcript reference pairs

- WER requires `reference_transcript` — engineer-written references only;
  WER is a regression signal, never a quality claim.
- chrF requires `reference_translation` — same caveat.
- No reference → outcome stays `INCONCLUSIVE`/`NOT_EXECUTED`.

## Human confirmation rule

Every physical scenario ends with `operator confirmation` recorded via
`physical-test feedback`. Without it, the session evidence export is
incomplete and the scenario does NOT count as passed.
