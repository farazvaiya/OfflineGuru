# Offline Guru

**A teacher's laptop becomes an offline AI classroom.** Spoken English is
transcribed locally, translated into Bangla, and streamed to students' browsers
over the laptop's own Wi-Fi hotspot — with no Internet, no cloud API and no
student app install.

Built and measured on a 2017 i5 / 8 GB laptop with no GPU:
**text end-to-end 1.50 s median, 1.88 s p95**, **30 simulated students, zero
dropped events**, ~800 MB RAM.

```
teacher speaks ─► mic ─► Whisper (local) ─► Marian EN→BN (local) ─► Bangla caption
                                                        │
                                                        ├─► Piper Bangla TTS (local)
                                                        └─► summary + quiz + grading
                                                               (grounded, teacher-approved)
students ── Wi-Fi hotspot ──► browser: live captions, headphones audio,
                              summary, practice quiz, private feedback
```

---

## What it does

**Teacher console** (`/teacher`)

* Create a class, get a 6-character join code, join URL and locally generated QR.
* Start / pause / resume / stop lecture capture from the microphone (or replay a
  WAV for demos and evaluation).
* Enter today's vocabulary — measured to fix domain-term recognition.
* Read the live transcript, correct recognition errors; corrections push to
  students immediately.
* Generate and **approve** a Bangla summary and a quiz with evidence links.
* See connected students, health, measured latency, RAM and network state.
* Review answers, override an automated grade, export JSON, delete everything.

**Student page** (`/`)

* Join with code + name; no app, no account, no Internet.
* Live Bangla captions with the English source underneath, plus the segment's
  measured latency.
* Optional per-student audio through their own headphones (tap once to satisfy
  browser autoplay rules).
* Bangla summary, practice quiz, instant feedback with the lecture timestamp to
  review, and topic-level progress — only once there is enough evidence.
* Automatic reconnect and recovery of missed captions.

**Education workflow** — deterministic and evidence-linked by design:

* summaries are selected from the stored transcript, then translated (no
  generated facts),
* quiz questions come from detected definitions and numeric statements, each
  carrying segment id, timestamp and quote; answer keys never reach the student
  before grading,
* objective answers are graded exactly; short answers use a rubric with an
  explicit "teacher should review" band instead of guessing,
* a quiz is invisible to students until the teacher approves it.

## Quick start (development machine with Internet)

```bash
cd offline_guru
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -r requirements-prep.txt \
    --extra-index-url https://download.pytorch.org/whl/cpu

.venv/bin/python scripts/prepare_models.py --with-en-demo-voice   # ~1.2 GB, once
.venv/bin/python scripts/make_demo_lecture.py                     # 160 s demo lecture

.venv/bin/pytest -q
./scripts/serve.sh
```

Open <http://127.0.0.1:8000/teacher>, create a class, press either **🎤 শুরু করুন**
or **▶ ফাইল চালান** with `data/demo/lecture.wav`.

For a real classroom: `sudo ./scripts/hotspot.sh start`, then `./scripts/serve.sh`,
then students open the printed join URL on the hotspot.

Full deployment: [docs/OFFLINE_SETUP.md](docs/OFFLINE_SETUP.md).

## Measured performance

Hardware: i5-7300U (2C/4T), 7.1 GB RAM, no GPU, Ubuntu 26.04, int8 models.
Test material: 160 s English science lecture, 37 segments. Full method and raw
data: [docs/EVALUATION.md](docs/EVALUATION.md).

| Metric | Result |
|---|---|
| ASR (Whisper base) WER, clean synthetic speech | **3.8%** |
| ASR latency p50 / p95 | 1.30 s / 1.42 s per segment |
| Translation p50 / p95 | 0.20 s / 0.34 s |
| TTS p50 (cold cache) | 0.31 s |
| **Text end-to-end p50 / p95** | **1.50 s / 1.88 s** |
| 30 clients: segments delivered | 10/10 to all 30, **0 drops** |
| 30 clients: fan-out spread p95 | 6.1 ms |
| Runtime RAM | ~800 MB |
| Offline verification | **zero external connections** |

Model choice was measured, not assumed: `small` has 3.1% WER but RTF 1.09, which
pushed end-to-end latency to ~19 s on this CPU, so `base` is the default. See
[docs/MODEL_SELECTION.md](docs/MODEL_SELECTION.md).

## Real-time streaming

The teacher does not have to finish a sentence for students to hear it. The
pipeline decodes a rolling audio window continuously, commits only the words two
consecutive hypotheses agree on (LocalAgreement-2), groups them into phrases, and
translates and voices each phrase as soon as it closes — on punctuation, at a
character cap, after 8 s, or after 1.5 s with at least 12 characters.

* `transcript.partial` — unstable English, shown live, never translated.
* `transcript.stable` — words that are now committed.
* `transcript.final` / `segment` — the Bangla caption for a closed phrase.
* `audio.ready` — the Bangla audio for that phrase, with its `sequence_number`.

Students play audio strictly in sequence, never twice, and resume at the live
edge after a reconnect instead of replaying the lecture. Captions and audio are
independent: a slow or failing voice engine never delays a caption.

Set `asr.streaming = false` to return to the original one-decode-per-pause
behaviour. Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Architecture in one paragraph

One Python process, three worker threads (ASR → translate → TTS) fed by a
rolling window (streaming) or a voice-activity segmenter (fallback), with bounded
queues at every stage. Text is published to students immediately after
translation; audio follows on its own thread, so a slow voice engine never delays
a caption. An in-process event bus fans out to WebSockets with per-client bounded
queues that drop oldest events instead of stalling the lecture. SQLite (WAL)
stores sessions, segments, materials, answers and latency metrics; REST is the
recovery path when a student's WebSocket drops. Details and failure behaviour:
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Privacy and safety

* No cloud calls, no telemetry, no accounts. The only optional network probe is
  an informational Internet check that can be disabled.
* Raw microphone audio is never written to disk. Only the synthesised Bangla WAV
  per segment is cached, and it is deleted with the session.
* Student tokens are scoped: a student can only read their own session's
  transcript, approved materials and their own answers.
* Student text is treated as data: rendered with `textContent`, never executed,
  never interpreted as instructions, capped at 2 KB, rate-limited.
* Feedback never comments on a student's ability, and short answers are flagged
  for teacher review rather than marked wrong when they are ambiguous.
* Teacher approval is required before students see a quiz; summaries are drafts
  until approved.

## Repository layout

```
guru/
  audio.py          mic capture (arecord) + VAD segmenter
  asr.py            faster-whisper adapter (local only)
  translate.py      Marian CT2 adapter + optional NLLB backend
  tts.py            Piper Bangla voice + disk cache
  pipeline.py       streaming orchestration, queues, metrics
  realtime.py       LocalAgreement, rolling window, phrase builder, latency
  bus.py            bounded per-client pub/sub
  storage.py        SQLite schema and queries
  education/        transcript analysis, summary, quiz, grading, feedback
  server/app.py     FastAPI REST + WebSockets + static UI
  server/static/    teacher.html/js, student.html/js, style.css
  net.py            hotspot/interface discovery, QR
config/default.toml runtime configuration (env overrides: GURU_*)
scripts/            model prep, demo audio, eval, latency/contention/load benchmarks,
                    browser QA, offline verify, hotspot
tests/              239 unit + integration tests
docs/               architecture, model selection, evaluation, roadmap, networking,
                    setup, troubleshooting, demo script, measurement artifacts
data/               SQLite DB, audio cache, demo/eval artifacts (gitignored)
models/             local model assets (gitignored; see MANIFEST.md)
```

## Commands

```bash
make test            # pytest
make run             # classroom server
make verify-offline  # socket-guarded proof of no network use
make loadtest        # 30 WebSocket students against a running server
make demo            # regenerate the demo lecture
``` 

Improvement harness: `python3 -m engineering_team status` drives the human-gated
engineering workflow described in [engineering_team/README.md](engineering_team/README.md).

Diagnostics: `curl -s localhost:8000/api/health | python3 -m json.tool`.
Guides: [INSTALLATION_GUIDE.md](INSTALLATION_GUIDE.md),
[TEACHER_GUIDE.md](TEACHER_GUIDE.md), [STUDENT_GUIDE.md](STUDENT_GUIDE.md).
Verification evidence (browser QA, latency, contention, load, retrieval, grading):
[reports/](reports/) — start with
[reports/FINAL_VERIFICATION_REPORT.md](reports/FINAL_VERIFICATION_REPORT.md).
Phased plan and what is still open: [PROJECT_ROADMAP.md](PROJECT_ROADMAP.md).
Troubleshooting: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).
Hotspot modes and OS quirks: [docs/NETWORKING.md](docs/NETWORKING.md).

## Limitations (honest list)

1. **Not tried in a real classroom.** No consenting teacher/students, no real
   accents or noise; WER above is on clean synthetic speech and is optimistic.
2. **Translation is imperfect.** A 77M-parameter model is fast but makes lexical
   errors (sunlight → "সূর্যাস্ত", respiration → "পুনর্বাসন"). NLLB is better but
   ~10× slower and CC-BY-NC (non-commercial). Teacher correction is the mitigation.
3. **Bangla TTS is intelligible, not beautiful** — a single medium-quality voice.
4. **30 students was measured at the application layer on one laptop**, not 30
   physical phones over the radio. Wi-Fi capacity must be tested on site.
5. **Hotspot script was not executed during development** (no root/AP-capable
   environment). The `nmcli` commands are explicit and reversible.
6. **Live speech into the microphone** was validated only as capture + DSP +
   file replay, not by a human speaking into this machine.
7. **English→Bangla only** is wired end-to-end.
8. **Long lessons** (45 min) were not tested for thermal throttling; the health
   endpoint exposes the signals to detect it.
9. **Bangla search against English slides** is now indexed (top-1 0.167 → 0.667),
   but English search against Bangla slides remains weak (0.167).
10. **Grading has no synonym knowledge** — a correct paraphrase sharing no rubric
    term is marked wrong and needs a teacher override.

## Licenses

Project: **GPL-3.0-or-later** (required by the Piper TTS runtime). Models:
Whisper MIT, Marian Apache-2.0, Piper voice data CC-BY-SA-4.0. Full inventory:
[LICENSES.md](LICENSES.md).
