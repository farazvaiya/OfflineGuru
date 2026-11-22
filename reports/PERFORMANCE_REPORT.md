# Performance report

**Objective:** measure the real pipeline with the real models on this machine, and
state plainly what each number does and does not prove.

**Environment:** 4-thread CPU, 7 GB RAM, no GPU, Ubuntu, Python 3.14.
**Input:** `data/demo/lecture_45s.wav` replayed at real time by `WavSource`
(16 kHz mono). This is a **file-based** benchmark — a real microphone adds capture
latency that is not measured here.

## 1. End-to-end latency (real ASR + Marian + Piper)

`.venv/bin/python scripts/bench_latency.py --wav data/demo/lecture_45s.wav`

10 segments, 41 s of speech, 47.4 s wall, **0 ASR drops**.

| Stage | n | p50 | p95 | p99 | max |
|---|---|---|---|---|---|
| ASR (Whisper base int8) | 10 | 1355.6 ms | 1741.6 ms | 1741.6 ms | 1741.6 ms |
| Translation (Marian CT2 int8) | 10 | 179.9 ms | 421.8 ms | 421.8 ms | 421.8 ms |
| TTS (Piper, disk-cached) | 10 | 266.7 ms | 862.0 ms | 862.0 ms | 862.0 ms |
| **Text end-to-end** (speech end → caption published) | 10 | **1532.0 ms** | 3897.1 ms | 3897.1 ms | 3897.1 ms |

Raw: `reports/results/latency.json`.

Interpretation: text captions land ~1.5 s after the teacher stops a phrase
(p50), inside the project's 1–3 s target. ASR dominates; translation is ~12 % of
the total. The p95 outlier is a single long segment, not a systematic stall.

## 2. CPU and memory during a live replay

Sampled every 500 ms in-process during the same run.

| Metric | Value |
|---|---|
| CPU mean | **44.8 %** (of the whole machine) |
| CPU max | 82.0 % |
| RSS at start (after model load) | 45 MB |
| RSS at end | **785 MB** |
| RSS peak | **878 MB** |
| Samples | 93 |

The ~800 MB figure matches the earlier class estimate. Two thirds of the RAM is
the three models; the process does not grow monotonically over a 45 s lecture.

## 3. ASR under document upload — replaced the proxy measurement

The earlier report measured +4 % on a synthetic CPU loop. This is the real thing:
Whisper transcribing 6 s of audio while a 150-slide PPTX is extracted and chunked
on a worker thread, three repeats.

`.venv/bin/python scripts/bench_asr_upload.py --repeats 3`

| Measurement | Result |
|---|---|
| Extraction alone (150 slides) | mean 21.2 ms (p95 29.3) |
| ASR alone | mean **1308.4 ms** (p50 1238.5) |
| ASR while extraction runs | mean **1261.9 ms** (p50 1255.6) |
| Extraction while ASR runs | mean 20.5 ms |
| **ASR slowdown** | **−3.6 %** (no measurable penalty; within run-to-run noise) |
| RSS | 263 MB → 273 MB |

Raw: `reports/results/asr_upload_contention.json`.

Honest reading: at the supported caps (150 slides ≈ 56 kB of XML), extraction is
~20 ms of work, so it cannot meaningfully disturb ASR. The upload admission gate
(409 while live) is therefore justifiable as protection against pathological
files and unpredictable teacher workflow, **not** as a fix for a measured
regression. A 400-page scanned-text PDF is the worst case and was not exercised.

## 4. Retrieval cost

| Operation | Cost |
|---|---|
| Lexical index, 6 pages | ~0 ms |
| Lexical query | 0.5 ms |
| Bilingual indexing (translate 6 pages, en→bn) | 3645.8 ms (~608 ms/page) |
| Query with bilingual chunks | 1.0 ms |
| Lexical index/query at 300 chunks (earlier) | 32 ms / 35.9 ms |

Bilingual indexing is a one-off, upload-time cost, bounded in code at 60 s and
120 000 characters per upload so a large deck cannot become an unbounded job.

## 5. What is not measured

- Physical microphone capture latency and human speech (synthetic TTS audio only).
- 45-minute thermal soak; `sensors` and `/sys/class/thermal` exist but no
  sustained real lecture was run.
- End-to-end latency with 30 physical phones on a hotspot radio.
- Behaviour with a 400-page PDF during a live lecture.
