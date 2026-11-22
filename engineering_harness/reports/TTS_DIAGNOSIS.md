# TTS Diagnosis

**No TTS defect found in Phase 1.** Measurement only; Phase 3 may tune, Phase 2 will not change TTS.

---

## 1. Implementation

`guru/tts.py` — Piper (ONNX, CPU, `onnxruntime`) with a disk cache keyed by `sha256(text|voice|length_scale)[:24]`. A `threading.Lock` serialises synthesis. `NullTts` is used when `tts.enabled=false`.

The docstring makes an important product statement: **students play audio on their own device; the teacher laptop never controls a student's headphone output**, and captions are always available as fallback. Ordering is therefore a client concern plus a sequence number, not a server-side playback queue.

## 2. Measured (Bangla voice `bn_BD-google-medium`)

| Text | Chars | Output | Audio length | Synth ms | RTF |
|---|---|---|---|---|---|
| "সবাই শুভ সকাল, আজকে আমরা ফটোসিন্থেসিস সম্পর্কে জানতে যাচ্ছি।" | 59 | 207,916 B @ 22,050 Hz | 4.71 s | 623.3 | 0.13 |
| "গাছপালা সূর্যের আলো ব্যবহার করে নিজের খাবার তৈরি করে।" | 53 | 145,452 B | 3.30 s | 357.3 | 0.11 |
| "এই সংজ্ঞাটি তোমার খাতায় লিখে রাখো।" | 35 | 97,324 B | 2.21 s | 237.1 | 0.11 |
| same text again (cache hit) | 59 | — | — | **0.0** (0.2 ms wall) | — |

Voice load: 1.73 s once per process.

RTF 0.11–0.13 means TTS comfortably outpaces real time. The cache makes repeated captions free, which matters when 30 students re-fetch the same segment.

## 3. Pipeline behaviour

* TTS runs on its own thread and its own bounded queue (16), **after** the caption is published. A slow voice engine cannot delay a Bangla caption. This is the right ordering and is verified by the load test: `first_caption_ms` 4137 ms vs `first_audio_ms` 4141 ms — 4 ms apart, so the caption is not waiting on synthesis.
* Synthesis failure marks `audio_status="failed"`, publishes `segment.updated`, and leaves the caption intact.
* Queue overflow drops audio only and increments `tts_queue_drops`.

## 4. Observed p95 cost in context

`baselines/latency_baseline.json`: tts_ms p50 58.5, p95 556.4, max 615.6. The p95 is high relative to p50 because long Bangla sentences produce 4–5 s of audio and synthesis cost scales with output length. It sits behind the caption so it does not affect caption latency; it does affect `audio_e2e_ms` and therefore perceived audio delay for students.

Load test (30 clients, 45 s lecture): tts_ms p50 101.7, p95 975.6, max 4051.9 — the max is a first-synthesis outlier under concurrency, still well inside a 15 s p95 budget.

## 5. Things worth watching in Phase 3 (not defects)

1. `length_scale=1.0` produces a fairly slow Bangla reading; students may prefer 0.95. Subjective — cannot be validated here.
2. Cache lives under `data/audio` and is swept by the retention job (`retention_days=90`). No size cap: a long-running school term could accumulate gigabytes. Not measured; flagged as a capacity risk for the maintainers.
3. Audio is 22,050 Hz mono WAV. At ~44 KB per second of speech this is heavier over a hotspot than Opus; the repo ships `scripts/prepare_opus_models.py` and `guru/opus_benchmark.py`, so an Opus path is clearly anticipated. Out of scope here.

## 6. Conclusion

TTS meets the latency requirement with margin (RTF ≈ 0.12), is correctly decoupled from captions, fails safely, and caches effectively. No Phase 2 change is justified. Bangla pronunciation quality cannot be assessed on this machine — it needs a native speaker, and that is recorded as a limitation, not scored.
