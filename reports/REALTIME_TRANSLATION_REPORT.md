# Real-Time Speech Translation — Implementation Report

**Date:** 2026-11-08
**Scope:** Phases 3–15 of the real-time upgrade.
**Companion documents:** `REALTIME_TRANSLATION_AUDIT.md`,
`OPEN_SOURCE_PROJECT_COMPARISON.md`, `OPEN_SOURCE_INTEGRATION_PLAN.md`,
`REALTIME_TRANSLATION_BENCHMARK.md`, `TRANSLATION_QUALITY.md`.

---

## 1. What changed

The pipeline used to wait for a pause before it decoded anything. It now decodes
continuously and translates stable phrases while the teacher is still speaking.

| Area | Before | After |
|---|---|---|
| ASR | one batch decode per VAD segment | continuous decode of a rolling window, word timestamps, LocalAgreement |
| Transcript states | `partial` (English, off by default), `final` | `partial` (unstable), `stable` (committed), `final` (phrase closed) |
| Translation | after the segment is finalized | per stable phrase, mid-sentence |
| TTS | after translation, per segment | per phrase, still decoupled from captions |
| Playback | client queue, drops above 3, no order guarantee | strict `sequence_number` order, dedupe, gap skip, live-edge resume |
| Events | `transcript.partial/final`, `segment`, `audio.ready`, `classroom` | adds `transcript.stable`, `pipeline.status`, `pipeline.error`; `segment` carries the playback contract |
| Metrics | per-stage ms | adds queue wait, ASR RTF, backlog, first-caption, first-audio |
| Config | segment sizes only | streaming knobs + phrase flush policy |

### Files

| File | Change |
|---|---|
| `guru/realtime.py` | **new** — LocalAgreement, SpeechWindow, PhraseBuilder, LatencyRecorder |
| `guru/pipeline.py` | streaming mode, ordered segment emission, richer payloads, failure isolation |
| `guru/asr.py` | `transcribe_words()` with word timestamps and probabilities |
| `guru/config.py` | `AsrConfig.streaming*`, `commit_confidence`, `TranslateConfig.phrase_*` |
| `config/default.toml` | matching defaults |
| `guru/storage.py` | `audio_status`, `segment_status`, `translation_at` + migration |
| `guru/server/app.py` | `_public_segment` playback contract |
| `guru/server/static/student.js` | ordered playback queue, dedupe, stale suppression |
| `scripts/bench_realtime.py` | **new** — chunk sweep, latency, RSS |
| `scripts/eval_translation_quality.py` | **new** — chrF, numbers, terms |
| `scripts/loadtest.py` | new events + mid-lecture reconnection |
| `tests/test_realtime.py` | **new** — 28 tests |
| `tests/conftest.py` | pins the original suite to segment mode; adds `streaming_settings` |
| `LICENSES.md` | **new** — attribution and licence inventory |

---

## 2. How the streaming path works

```
arecord ──► SpeechWindow ──wake──► ASR thread
                                     │  decode window (word timestamps)
                                     ▼
                              LocalAgreement.commit()
                                     │  stable words only
                                     ▼
                              PhraseBuilder (punctuation / chars / age / duration)
                                     │
                                     ├─► transcript.final + storage.add_segment
                                     │
                                     └─► [queue 12] translate ──► segment (caption)
                                                                      │
                                                                [queue 16] TTS
                                                                      │
                                                                 audio.ready
```

**Why only stable words are translated.** Translating an unstable hypothesis
means translating the same words twice and voicing them twice. LocalAgreement
commits a word when two consecutive hypotheses agree; a word with model
probability ≥ `commit_confidence` (0.9) is committed immediately, because on a
2-core CPU waiting for a second decode costs a full extra decode (~1.3 s).

**Why the window is trimmed.** After a commit, audio before
`committed_end - stream_context_s` is dropped. Without this the window grows and
every decode re-processes the whole lecture — the Realtime-translation failure
mode.

**Why a phrase closes on age.** A teacher can speak a 20-second sentence with no
punctuation. Waiting for the sentence end would defeat the purpose, so a phrase
that has been open for `phrase_flush_age_s` (1.5 s) with at least
`phrase_min_chars` (12) is translated as a clause. The character cap splits at
the last comma/semicolon/conjunction so a clause is not cut in half.

**Why the final decode is skipped when the tail is short.** Whisper pads every
input to 30 s, so decoding a 0.3 s tail costs the same as decoding 10 s. If less
than 350 ms of audio remains un-decoded at end of speech, the pipeline commits
what it already has instead of paying for a full call.

---

## 3. Failure behaviour

| Failure | Behaviour |
|---|---|
| ASR slower than real time | one decode in flight; `stream_backlog` increments; audio is never dropped for this reason |
| ASR exception | logged, `pipeline.error` published, loop continues |
| Translation exception | English transcript kept, `pipeline.error` published, segment still delivered with empty Bangla |
| TTS exception / empty audio | `audio_status = failed`, `segment.updated` published, captions unaffected |
| Metric write failure | logged at WARNING, class continues |
| Enrichment queue full | segment stored and published, translation skipped, counter incremented |
| TTS queue full | caption delivered, audio skipped, counter incremented |
| Student WebSocket drop | REST `/api/student/state` recovery; playback resumes at the live edge |
| Missing audio for a sequence | client waits 2.5 s then skips the gap |
| Pause | audio ignored, no decode |
| Stop | ASR flushes the tail, sentinels drain translate → TTS, threads joined |

---

## 4. What is verified and what is not

**Verified by automated tests (239 total, all passing):**
streaming primitives, phrase closing rules, ordered segment emission, no
duplicate sequence numbers, translation failure isolation, TTS failure marking,
metric-write failure isolation, pause, idempotent stop, playback payload
contract, plus the original 211 tests unchanged.

**Verified by measurement:** see `REALTIME_TRANSLATION_BENCHMARK.md` (chunk
sweep, first-caption/first-audio latency, RTF, peak RSS) and
`TRANSLATION_QUALITY.md` (chrF, number preservation, term coverage).

**Not verified:**
* No native-speaker evaluation of Bangla quality. The reference set was written
  by the engineer; the report says so and leaves a review column empty.
* No 30-student run on real phones over the hotspot in this session. The
  application-level load test exists and is documented; the radio is not
  measured here.
* No 45-minute thermal soak. The health endpoint exposes the signals; the soak
  is a deployment check, not a code claim.

---

## 5. Known limitations

1. **ASR throughput is the ceiling.** Whisper base costs ~1.3–1.9 s per decode on
   this CPU regardless of window length. First-caption latency is therefore
   bounded by roughly one decode plus translation, not by the chunk size.
2. **Committed text can differ from a full-segment decode.** LocalAgreement
   commits the agreed prefix; a later full decode might have chosen a different
   word. The segment path remains available for accuracy-critical use.
3. **Clause-level translation loses cross-clause context.** Marian has no
   context mechanism; the buffer is bounded so a clause is translated whole, but
   a pronoun referring to the previous clause can be ambiguous.
4. **No lip synchronisation.** The synchronisation provided is between the
   original speech timeline, the phrase sequence, captions and audio playback.
5. **Browser autoplay policy.** Audio starts only after the student taps once;
   captions are unaffected.
6. **NLLB is optional and non-commercial.** Its weights are CC-BY-NC-4.0; Marian
   is the default.
