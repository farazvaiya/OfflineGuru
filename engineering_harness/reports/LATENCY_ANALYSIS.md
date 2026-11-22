# Latency Analysis

**Sources:** `baselines/latency_baseline.json` (full models, 45 s file), `artifacts/pipeline_baseline_streaming.json` (real-time streaming, 300 s file), `baselines/loadtest_30_baseline.json` (30 clients)
**Finding:** F-09 (decode cadence). No latency claim here was inferred from a file-replay throughput number.

---

## 1. Three different latency numbers, three different meanings

| Measurement | What it actually times | Value |
|---|---|---|
| `asr_ms` (segment mode) | one `transcribe()` call | p50 1090 ms, p95 1156 ms |
| `asr_ms` stored per streaming phrase | **hardcoded 0.0** (F-08) | 0.0 |
| `e2e_text_ms` | speech-end → translated caption published | p50 2169 ms, p95 4725 ms |
| `first_caption_ms` | first speech onset → first translated caption | **4402 ms** |
| load-test `e2e_ms` | same as e2e_text_ms, under 30 clients | p50 2791.6 ms, **p95 15289.2 ms** |

The historical report's "file-replay e2e p50 ≈ 2.95 s / p95 ≈ 16.02 s" is reproduced in kind: p50 2.17 s, p95 4.73 s on a short file, and p95 15.3 s under 30-client load. The p95 is **dominated by outliers and by small sample counts**, not by a systematically slow stage.

## 2. Stage decomposition (45 s lecture, 14 phrases)

```
capture ─────────────────────────────────────────────── 0 (in-process)
SpeechWindow: wait for ≥0.6 s audio ..................... up to 600 ms
decode (whisper-base int8, 0.6–8 s window) .............. ~700–1300 ms
LocalAgreement: wait for a second agreeing hypothesis ... 1 extra decode for low-confidence words
PhraseBuilder: wait for punctuation / 12 chars / 1.5 s ... up to 1500 ms
translate .............................................. p50 93 / p95 449 ms
publish over bus ....................................... ~4 ms fan-out spread p95 (30 clients measured 7.3 ms)
```

| Stage | p50 | p95 |
|---|---|---|
| translate_ms | 92.6 | 448.9 |
| tts_ms | 58.5 | 556.4 |
| e2e_text_ms | 2169.0 | 4725.3 |

Translation + TTS together account for ~7 % of p50 end-to-end. The caption path is dominated by **waiting**: for enough audio, for agreement, and for the phrase to close.

## 3. The first-caption case

`first_caption_ms = 4402` even though the first decode starts at 0.52 s.

| Event | t (s) |
|---|---|
| First decode starts | 0.52 |
| First decode returns, 0 words committed | 2.14 |
| 2 words committed | 3.27 |
| 10 words committed | 4.43 |
| Phrase closes on punctuation, caption published | 4.42 |

Three separate waits stack up: 0.6 s minimum audio, one decode for the hypothesis, a second decode for LocalAgreement, then up to 1.5 s for the phrase to age out or hit punctuation. The brief's "1–3 s target" is met only after the pipeline is warm; the first caption of a lecture takes ~4.4 s.

## 4. Decode cadence is the throughput defect (F-09)

| Window | asr_ms | RTF |
|---|---|---|
| 1–12 s | ~1.1 s | 0.28 |
| 0.3–1.0 s | 0.5–2.0 s | 1.1–3.4 |

Whisper pads to 30 s internally, so cost is nearly flat below ~5 s of audio. Decoding every 0.5 s of new audio therefore burns CPU without reducing caption latency: 47 decodes in 60 s, 27 of them flagged `stream_backlog`.

This is the clearest Phase 3 lever: **decode less often, on larger windows**, and measure whether first-caption latency and WER survive. Any change must be accepted only if WER does not regress — the whole point of Phase 2 is that accuracy and latency cannot be traded blindly here.

## 5. Tail latency under load

30-client loopback run, 45 s file:

| Metric | Value |
|---|---|
| e2e p50 / p95 | 2791.6 / **15289.2** ms |
| asr p95 / max | 2039.3 / 6485.9 ms |
| translate p95 / max | 928.2 / 7797.4 ms |
| tts p95 / max | 975.6 / 4051.9 ms |
| fan-out spread p95 | 7.3 ms |
| socket errors / drops | 0 / 0 |

The p95 is a single slow phrase: `translate` max 7.8 s on one segment drags the tail. Two structural reasons:

1. **Only 15 phrases** in the sample, so "p95" is effectively the maximum. Latency p95 from a 45 s file is not a stable statistic; it should be reported with `n`.
2. **Translation is serialised** behind one enrich thread; a long phrase blocks every phrase behind it, and their `e2e_ms` includes the wait. That is queue wait being counted as translation time — the brief explicitly asks to separate processing from waiting, and the current metric does not.

## 6. What was *not* measured

* Microphone-to-caption latency on real hardware (no device; capture is shimmed).
* Thermal throttling over a 45-minute lecture (target hardware not available).
* Wi-Fi/hotspot transmission delay to real phones (loopback only).

All three are listed as `not_testable_locally` in the acceptance criteria and will appear in the final report as BLOCKED rather than estimated.

## 7. Conclusion

The latency problem is real but is a *waiting* problem, not a *compute* problem: translation (93 ms) and TTS (59 ms) are cheap, ASR is 0.28 RTF on useful windows, and the caption appears 2–4 s later because the pipeline waits for audio, agreement and phrase closure. The p95 blowups come from (a) decoding windows too small to amortise a call and (b) one long phrase blocking a serialised translation queue.
