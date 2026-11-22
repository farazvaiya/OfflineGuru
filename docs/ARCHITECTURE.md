# Architecture

Offline Guru is a single Python process on the teacher's laptop. It owns the
microphone, the AI models, the classroom web server and the local database.
Students use a browser — nothing to install, no app store, no Internet.

```
                          TEACHER LAPTOP (Ubuntu, no Internet required)
 ┌───────────────────────────────────────────────────────────────────────────┐
 │                                                                            │
 │  microphone ──► arecord ──► Segmenter (VAD) ──► ASR worker ──► transcript  │
 │   (ALSA)       16 kHz        silence/pause        Whisper       (SQLite)   │
 │                                │                    │                      │
 │                                │ partial (opt.)     ▼                      │
 │                                └──────────────► Translation worker         │
 │                                                    Marian/CT2              │
 │                                                       │                    │
 │                                          ┌────────────┴───────────┐        │
 │                                          ▼                        ▼        │
 │                                   WebSocket event           TTS worker     │
 │                                   (Bangla caption)          Piper (BN)     │
 │                                          │                        │        │
 │                                          │                  WAV cache     │
 │                                          │                   data/audio   │
 │  ┌───────────────────────────────────────┴────────────────────────────┐   │
 │  │ FastAPI + WebSockets                                                │   │
 │  │  /teacher  console (token)      /  student page (join code)         │   │
 │  │  REST: sessions, segments, materials, answers, audio, export        │   │
 │  └───────────────┬─────────────────────────────────┬───────────────────┘   │
 │                  │                                 │                       │
 │            Education service                 Event bus (bounded queues)    │
 │    extractive summary + template quiz         per-student backpressure     │
 │    rubric grading + feedback                                               │
 │                  │                                                         │
 │             SQLite (WAL)  data/guru.db  +  data/audio/<session>/*.wav      │
 └──────────────────┬──────────────────────────────────┬──────────────────────┘
                    │                                  │
              Wi-Fi hotspot                      Wi-Fi hotspot
                    │                                  │
            ┌───────▼────────┐                 ┌───────▼────────┐
            │ student phone  │   … up to 30 …  │ student tablet │
            │ browser        │                 │ browser        │
            │ captions+audio │                 │ captions+audio │
            └────────────────┘                 └────────────────┘
```

## Module map

| Module | Responsibility | Key design decision |
|---|---|---|
| `guru/audio.py` | mic capture + voice-activity segmentation | pure segmenter logic, unit-testable; `arecord` avoids PortAudio |
| `guru/asr.py` | faster-whisper wrapper | local dir only, `local_files_only=True`, no cloud fallback |
| `guru/translate.py` | Marian EN→BN via CTranslate2 | dedicated small NMT model; number-preservation check |
| `guru/tts.py` | Piper Bangla voice | disk cache keyed by text; one synthesis for 30 listeners |
| `guru/pipeline.py` | orchestration, queues, metrics | three workers + bounded queues; text published before audio; streaming and segment modes |
| `guru/realtime.py` | streaming primitives | LocalAgreement-2, rolling window, phrase builder, latency recorder; no models, no threads |
| `guru/bus.py` | in-process pub/sub | per-client bounded queue, drop-oldest, counters |
| `guru/storage.py` | SQLite persistence | WAL, one connection + lock, scoped by `session_id` |
| `guru/education/*` | summary, quiz, grading, feedback | deterministic, evidence-linked, no fabrication |
| `guru/education/documents.py` | PDF/PPTX/TXT ingestion, page-tagged chunks, local retrieval | pypdf for PDF, stdlib zip+XML for PPTX; hashed lexical vectors, no model download |
| `guru/server/app.py` | REST + WebSocket + static UI | teacher token, per-student tokens, approval gates |
| `guru/net.py` | hotspot/interface discovery, QR | read-only system inspection |

## Threading model (why it stays real-time)

```
capture thread ── final audio ──► [queue 8]  ──► ASR thread  ──► [queue 12] ──► translate thread
      │                                                                              │
      └── partial audio ──► [queue 1] (optional, tail only)                          ▼
                                                                          publish caption (WS)
                                                                                     │
                                                                              [queue 16]
                                                                                     ▼
                                                                            TTS thread
                                                                                     │
                                                                          publish audio.ready
```

* The **capture thread never blocks** on a model. If ASR falls behind, the oldest
  unprocessed segment is dropped and counted (`asr_queue_drops`) instead of
  growing memory without bound.
* **Text is published immediately after translation.** TTS runs on its own
  thread, so a slow voice engine cannot delay the Bangla caption. Measured
  effect: TTS takes ~0.7 s per sentence; keeping it off the caption path saves
  that from every student's perceived latency.
* **Partial captions are optional and off by default.** On a 4-core laptop they
  cost ~70% of a core and roughly double end-to-end latency (see
  `docs/EVALUATION.md`).
* CPU budget is explicitly split: Whisper 3 threads, Marian 2 threads, Piper 1.
  Over-subscription was measurable in testing, so the defaults stay conservative.

## Streaming mode (default)

The segment path above waits for a pause before it decodes anything. Streaming
mode decodes continuously instead, so a Bangla caption can appear while the
teacher is still talking.

```
capture ──► SpeechWindow (rolling, timestamped, VAD-gated)
                 │  wake
                 ▼
            ASR thread: decode window ──► LocalAgreement ──► stable words
                 │                                              │
                 │                                     PhraseBuilder (closes on
                 │                                      punctuation / chars / age)
                 │                                              │
                 └── transcript.partial (unstable, English)      ▼
                                                        transcript.final + segment
                                                               │
                                                      [queue 12] ──► translate ──► caption
                                                                                    │
                                                                              [queue 16] ──► TTS
```

* **Only stable words are translated.** `LocalAgreement` commits a word when two
  consecutive hypotheses agree, or immediately when the model's word probability
  is at or above `asr.commit_confidence` (default 0.9). The second rule is what
  keeps first-caption latency at one decode instead of two on a 2-core CPU.
* **The window is trimmed at the commit point**, keeping `stream_context_s` of
  already-committed audio so the next decode has context. It never grows without
  bound (`stream_max_window_s`, default 15 s).
* **A phrase closes** on sentence punctuation, at `phrase_max_chars`, after
  `phrase_max_s` seconds, or when it has been open for `phrase_flush_age_s` with
  at least `phrase_min_chars`. The last rule is what makes translation start
  mid-sentence; the char cap splits at the last clause boundary so a clause is
  not cut in half.
* **One decode in flight.** The ASR thread never queues a second decode; if a
  decode is slower than the audio it covers, the loop simply runs one decode
  behind and `stream_backlog` increments. Audio is never dropped for this reason.
* **Segment mode remains available** (`asr.streaming = false`) and is what the
  original test suite exercises.

## Event protocol (WebSocket)

| Event | Sent to | Payload |
|---|---|---|
| `hello` | both | role, session/student identity, `live` flag |
| `transcript.partial` | both | `en`, `start_s` — unstable, English only, never translated |
| `transcript.stable` | both | newly committed words, `start_s`/`end_s` |
| `transcript.final` | both | segment id/seq/times/English text |
| `segment` | both | English + Bangla + timings + playback contract (no audio yet) |
| `audio.ready` | both | `segment_id`, `sequence_number`, `audio_url`, `tts_ms` |
| `pipeline.status` | both | running / paused / stopped / speech |
| `pipeline.error` | both | stage (`asr`/`translate`/`tts`) and message |
| `segment.updated` | both | teacher correction |
| `materials.updated` | both | summary/quiz status change |
| `answer.submitted` | teacher | student id (never the answer text) |
| `classroom` | both | running / paused / stopped |
| `ping` / `pong` | both | keepalive for flaky Wi-Fi |

Students recover missed events by re-fetching `/api/student/state` on reconnect
and merging by segment id — the WebSocket is an optimisation, not the source of
truth. REST is the recovery path.

`segment` is the wire name for the translated segment (the "translation.final"
event in the real-time contract). It carries both the legacy keys (`id`, `seq`,
`en`, `bn`, `audio_url`) and the explicit playback fields
(`sequence_number`, `source_text`, `translated_text`, `source_start_time`,
`source_end_time`, `translation_timestamp`, `audio_status`, `audio_reference`,
`segment_status`), so an older cached client keeps working.

### Ordered playback

The student client plays strictly in `sequence_number` order:

* a segment is never played twice (played set),
* a segment older than `nextPlaySeq` is ignored (stale after reconnect),
* a missing segment is waited for 2.5 s and then skipped, so one failed
  synthesis cannot stall the rest of the lecture,
* on reconnect or unmute, playback resumes at the live edge instead of replaying
  the backlog.

## Data model

```
sessions(id, code, title, teacher_token_hash, status, source_lang, target_lang, …)
students(… token_hash …)   -- credentials are stored as SHA-256 digests, never plaintext
segments(id, session_id, seq, start_s, end_s, en, bn, confidence,
         asr_ms, translate_ms, tts_ms, total_ms, numbers_preserved, audio_path)
materials(id, session_id, kind['summary'|'quiz'], content_json, status)
students(id, session_id, name, token, joined_at, last_seen)
answers(id, quiz_id, student_id, question_id, answer, is_correct, score,
        feedback, needs_review, teacher_override)
topic_stats(session_id, student_id, topic, attempts, correct)
metrics(session_id, kind, value, meta_json)   -- latency measurements only
documents(id, session_id, name, kind, pages, chars, sha256, created_at)
doc_chunks(id, document_id, session_id, page, text)
```

`metrics` stores numbers, never student text. `doc_chunks.page` is the 1-based page
or slide number from the uploaded file and is what generated questions cite.

## Privacy boundaries

* **Teacher** token → all sessions owned by that token; can read every student's
  answers for the session.
* **Student** token → only their own session's transcript, approved materials,
  and their own answers. Audio URLs are scoped to the student's session.
* `/api/health` exposes model errors, interfaces and latency, so it requires the
  session teacher token; with no session yet it answers loopback only. `/healthz`
  stays open and carries nothing but liveness.
* Student-submitted text is stored verbatim but always rendered with
  `textContent` (never `innerHTML`) and is never interpreted as instructions.
* Uploaded documents are teacher-owned. Students only ever see a `{document, page}`
  citation inside material the teacher approved; the file itself is never served.
  Document text is data: it is stored, chunked and quoted, never executed and never
  treated as an instruction (there is no LLM in the path).
* Raw microphone audio is **not stored**; only the synthesised Bangla WAV per
  segment is cached (and can be disabled). Session purge deletes rows + audio.
* One student's answers are never included in another student's REST/WS payload.

## Failure behaviour

| Failure | Behaviour |
|---|---|
| ASR model missing | health shows `asr: unavailable`; start returns 503 (no silent fallback) |
| Translation model fails | English transcript still delivered; `bn` empty, flagged |
| TTS fails / disabled | captions remain; audio button has nothing to play |
| CPU saturated | oldest queued segment dropped and counted, memory bounded |
| Student network drops | client reconnects with backoff and re-fetches state |
| Server restart | sessions/materials survive in SQLite; an interrupted lecture is not resumed automatically |
| RAM < 400 MB free | `/api/health` warns; model loading failures are reported, not hidden |
