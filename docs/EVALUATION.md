# Evaluation

Everything here was measured on the machine described below. Nothing is quoted
from a model card. The raw artifacts are checked in under `docs/results/` (see
its README for the command that produced each one); regenerated runs write to
`data/eval/` and `data/demo/`.

## Test hardware

| | |
|---|---|
| CPU | Intel Core i5-7300U, 2 cores / 4 threads @ 2.60 GHz |
| RAM | 7.1 GB total (≈2.5–3.2 GB available during tests) |
| GPU | none (Intel HD 620) |
| Disk | SSD, 89 GB free |
| OS | Ubuntu 26.04 LTS |
| Python | 3.14.7 |
| Runtime | faster-whisper 1.2.1, ctranslate2 4.8.3, piper-tts 1.8.0 |
| Quantization | int8 throughout |
| ASR threads | 3 |
| Input | 16 kHz mono |

This is a deliberately modest laptop: a 2017 ultrabook. Numbers on a modern
8-core machine will be better; numbers on a 2-core Celeron will be worse.

## Test material

A 160.8 s English science lecture ("photosynthesis", 37 sentences, 390 words),
synthesised with the Piper `en_US-lessac-medium` voice, 0.65 s pauses between
sentences, stored as `data/demo/lecture.wav` with a known transcript
(`lecture.txt`). Reproduce with `scripts/make_demo_lecture.py`.

**Important limitation:** this is clean, read, synthetic speech. It has no
classroom noise, no room reverberation, no student chatter, and no Bangladeshi
teacher accent. WER on real classroom speech will be **worse** than the numbers
below. The synthetic corpus was chosen because it is reproducible and contains no
identifiable speaker data; it is not a substitute for a field study.

Real-speech sanity check: the JFK sample (`samples/jfk.wav` from whisper.cpp)
transcribes correctly with `base` in 1.28 s for 11 s of audio (RTF 0.12,
short-file overhead lower than long segments).

## 1. ASR accuracy and speed (`scripts/eval_asr.py`)

37 VAD segments, transcribed one by one (streaming shape, not whole-file batch).

| Model | WER | p50 | p95 | RTF | RSS | Words out |
|---|---|---|---|---|---|---|
| tiny | 5.9% | 673 ms | 842 ms | 0.19 | 182 MB | 385 |
| **base** | **3.8%** | **1276 ms** | **1541 ms** | **0.36** | **330 MB** | 387 |
| small | 3.1% | 3915 ms | 4456 ms | 1.09 | 885 MB | 390 |

Errors observed with `base` are mostly domain terms, not grammar: *glucose* →
"Bluecoast", *chlorophyll* → "Coro fill", *stomata* → "stomata" (correct). Adding
per-lesson `hotwords` fixed the glucose case in a direct A/B test:

```
without hotwords: Bluecoast is a simple sugar that stores chemical energy.
with hotwords:    glucose is a simple sugar that stores chemical energy.
```

Decision: `base` is the default (3× faster than small, 0.7 pp worse WER, 555 MB
lighter). `small` is available via `GURU_ASR__MODEL_DIR=models/whisper-small`.

## 2. Translation speed (`scripts/compare_translators.py`)

| Backend | p50 / sentence | max | Notes |
|---|---|---|---|
| Marian `opus-mt-en-to-bn` int8 | **218 ms** | 454 ms | default |
| NLLB-200-distilled-600M int8 | 2152 ms | 3265 ms | better Bangla; non-commercial license |

## 3. End-to-end latency

Full 160 s lecture, real-time replay, TTS cache cold, partial captions off
(default). 37 segments, **0 ASR drops, 0 translation drops, 0 TTS drops**.

| Metric | mean | p50 | p95 | max |
|---|---|---|---|---|
| ASR (per segment) | 1299 ms | **1295 ms** | 1420 ms | 1738 ms |
| Translation | 214 ms | **202 ms** | 337 ms | 402 ms |
| TTS (cold cache) | 367 ms | 314 ms | 741 ms | 807 ms |
| **Text end-to-end** (speech end → Bangla caption) | 1573 ms | **1504 ms** | **1882 ms** | 3612 ms |

The 1–3 s target is met at p95 on this hardware. The 3.6 s maximum came from the
first segment after the final forced cut.

Audio is decoupled from text: `audio_e2e_ms` is measured separately and is
typically text latency + 0.3–0.7 s. Students see the caption first, then hear it.

### Cost of provisional captions

60 s lecture, same machine, two runs:

| Partials | Segments | ASR p50 | e2e p50 | e2e p95 |
|---|---|---|---|---|
| off (default) | 13 | 1344 ms | 1600 ms | 1950 ms |
| on (tail 5 s, every 2.5 s) | 13 | 1289 ms | 1610 ms | 1885 ms |

With the earlier `small` model, partials roughly doubled end-to-end latency and
pushed it to ~19 s. They are off by default because the cost depends heavily on
the CPU and model; enable with `GURU_ASR__PARTIAL_ENABLED=true`.

## 4. Client capacity (`scripts/loadtest.py`)

Real server, real models, 45 s lecture clip replayed in real time, N student
WebSocket clients + REST joins on the same laptop (loopback). This measures the
**application**, not the Wi-Fi radio.

| Clients | Segments received | Join p50/p95 | WS connect p50/p95 | Fan-out spread p50/p95 | e2e p50/p95 | Bus drops |
|---|---|---|---|---|---|---|
| 1 | 5/5 | 3 / 3 ms | 34 / 34 ms | 0 / 0 ms | 1491 / 1547 ms | 0 |
| 5 | 10/10 | 26 / 47 ms | 5 / 16 ms | 0.7 / 18.8 ms | 1434 / 1692 ms | 0 |
| 10 | 10/10 | 49 / 90 ms | 8 / 19 ms | 1.6 / 2.5 ms | 1446 / 3079 ms | 0 |
| 20 | 10/10 | 125 / 179 ms | 19 / 23 ms | 3.1 / 8.7 ms | 1544 / 2666 ms | 0 |
| 30 | 10/10 | 154 / 257 ms | 25 / 28 ms | 4.3 / 6.1 ms | 1463 / 1981 ms | 0 |

Every client received every segment at 30 clients; no subscriber queue overflowed.
Available RAM stayed at 2.8–2.9 GB during the 30-client run.

**Not measured:** 30 physical phones over a 2.4 GHz hotspot. That is a radio
limit (see `docs/NETWORKING.md`), not an application limit, and must be tested on
site. The same script works over the hotspot from a second device.

## 5. Offline verification (`scripts/verify_offline.py`)

A socket guard blocks and records every non-loopback `connect`, `connect_ex` and
`getaddrinfo` **before models are imported**. The script then loads ASR,
translation and TTS from disk, processes 20 s of the lecture, produces a summary
and a quiz.

Result: **PASS — zero external connections attempted.** The only optional network
call in the codebase is the informational Internet probe in `/api/health`, and it
is disabled for this test (`GURU_SERVER__ONLINE_PROBE=false`).

## 6. Functional checks

| Check | Result |
|---|---|
| 54 unit + integration tests | pass (`pytest -q`) |
| Teacher session create → join code → QR | pass (HTTP 200, QR is SVG generated locally) |
| Live WAV lecture → captions → audio | pass (5/5 and 37/37 segments delivered) |
| Real microphone capture via `arecord` | pass (3 s captured, RMS 0.056, pipeline start/stop clean) |
| **Speech spoken into the mic → captions** | **not tested end-to-end** (no way to play audio into this machine's mic in the test environment); file replay exercises the identical DSP path after capture |
| Summary generation from stored transcript | pass (7 sentences, 8 key terms, 1.6 s) |
| Quiz generation with evidence + answer keys | pass (5 questions, each linked to a segment/time; distractors are defined terms) |
| Quiz hidden until teacher approval | pass (automated test) |
| MCQ + short-answer grading, needs-review band | pass (automated tests) |
| Teacher override of a grade | pass (automated test) |
| One student cannot read another's answers | pass (automated test) |
| Audio URL scoped to student's session | pass (automated test) |
| Export JSON / purge session + audio | pass (automated test) |
| Hotspot creation (`scripts/hotspot.sh`) | **not executed here** — requires root and an AP-capable radio; commands are documented and reversible |

## 7. Known failures and limitations

1. **Translation quality.** A 77M-parameter model produces fluent but imperfect
   Bangla: *sunlight* → "সূর্যাস্ত" (sunset), *respiration* → "পুনর্বাসন"
   (rehabilitation), *absorbed* → "খনন" (excavation). NLLB is better but 10×
   slower and non-commercial. Teacher correction is implemented; a bigger model
   is not a substitute for review.
2. **ASR on domain terms.** Science vocabulary is misrecognised without
   hotwords; WER was measured on synthetic clean speech, so real classroom
   accuracy is unknown and likely lower.
3. **No real classroom trial.** No consenting teacher/students, no real accents,
   no noisy room. Capacity and latency are application measurements.
4. **Hotspot and phone testing.** Not performed (no root, no AP-capable
   environment, no physical student devices).
5. **Long-run thermal behaviour.** A 160 s run is not a 45-minute lesson.
   Throttling on thin laptops is plausible; the health endpoint exposes load and
   latency to detect it.
6. **Partial captions.** Off by default; measured cost varies from negligible
   (`base`) to catastrophic (`small`).
7. **Quiz quality is bounded by transcript quality.** If ASR writes "Bluecoast",
   the quiz will faithfully ask about "Bluecoast" — grounding prevents invention,
   not upstream error. Teacher review exists for this reason.
8. **Language coverage.** Only English→Bangla is wired end-to-end. The pipeline
   is language-agnostic in shape, but only `en` source and `bn` target models are
   shipped and tested.

## Reproducing

```bash
make models                 # one-time, needs Internet
make demo                   # synthesise the lecture
.venv/bin/python scripts/eval_asr.py --sizes tiny,base,small
.venv/bin/python scripts/compare_translators.py
.venv/bin/python scripts/pipeline_cli.py data/demo/lecture.wav --speed 1
./scripts/serve.sh &        # then:
.venv/bin/python scripts/loadtest.py --clients 30
.venv/bin/python scripts/verify_offline.py
```
