# Bidirectional model comparison

**Run:** local Offline Guru host, 2026-11-12. CPU int8; one inference backend loaded at a time. Dataset: `data/eval/en_bn_quality.json` (26 parallel phrases). It is engineer-written, not an independently reviewed/held-out test set. One warmup then timed invocations per phrase. Earlier single pass showed very high outliers under contention; an additional repeat run is recorded separately. p50/p95 are descriptive, not acceptance-grade or target-laptop numbers.

## Translation — 1 timed invocation/phrase

| Direction | Backend/model | License | chrF2 mean | p50 / p95 latency ms | Human rating |
|---|---|---|---:|---:|---|
| English→Bengali | Marian EN→BN CT2 int8 | Apache-2.0 | 0.5923 | 335.73 / 1047.04 | Not collected |
| Bengali→English | Helsinki-NLP OPUS BN→EN CT2 int8 | Apache-2.0 | 0.6377 | 239.74 / 495.19 | Not collected |

## Translation — 2 timed invocations/phrase

| Direction | Model | chrF2 mean | p50 / p95 ms (N=52) |
|---|---|---:|---:|
| English→Bengali | Marian EN→BN CT2 int8 | 0.5923 | 378.78 / 1837.97 |
| Bengali→English | Helsinki-NLP OPUS BN→EN CT2 int8 | 0.6377 | 224.09 / 414.20 |

`reports/results/bidirectional_benchmark_repeat2.json` has the separate per-utterance samples. Direction medians/upper tails differ substantially from the first pass due to contention/noise; do not collapse runs into a single stable service latency claim. Models load from local CT2 paths and no model is switched in a phrase's timed loop.

The same 26 pairs supply both sides. Bengali source strings are the author-written English→Bengali references. This is development/proxy data, not recorded Bengali speech, an independent set, or a bilingual human-rating panel. Human rating >=4/5 is untested. NLLB-200 distilled 600M (CC-BY-NC-4.0, non-commercial only) was not run and is not the default.

## ASR selection and actual evidence

| Candidate | Local assets | Decision/evidence |
|---|---|---|
| faster-whisper Base multilingual CPU int8 | Present (142 MB) | Historical English project baseline WER 5.64% on a separate VAD/segment configuration; Bengali human-speech WER unavailable. |
| faster-whisper Small multilingual CPU int8 | Present (464 MB) | No completed target-device bilingual streaming benchmark. |
| Existing LocalAgreement-2 | Present in `guru/realtime.py`; Apache-2.0 attribution | Kept; language-turn and bounded-queue regression tests pass. |
| WhisperLiveKit SimulStreaming | Upstream Apache-2.0, Torch-heavy and Python constraint differs | Not integrated; no evidence it fits target CPU/memory better. |

## TTS selection and actual voice assets

Both ONNX/config pairs exist and actual valid WAV synthesis was exercised.

| Target voice | Model | Load ms (one run) | Two warm uncached samples ms | RTF |
|---|---|---:|---:|---:|
| English | `en_US-lessac-medium` | 9966.5 | 1045.2, 758.1 | 0.413, 0.317 |
| Bengali | `bn_BD-google-medium` (bn_BD/bn_IN card) | 5618.7 | 798.7, 791.8 | 0.201, 0.193 |

Two-invocation phrase-set results (N=52) from the repeat2 artifact: English p50/p95 603.63/2588.18 ms, mean RTF .270; Bengali 783.34/2406.73 ms, mean RTF .266. The 26-phrase single-pass run is separately recorded in its benchmark JSON. All measurements were under a busy current host and may be contaminated. They are not target i5-7300U, thermal or memory acceptance results. Bengali voice OpenSLR/CMU terms must be retained. Confirm English voice-specific model redistribution terms before shipping.

## Recommendation

Load only the selected direction and target voice before capture, and reuse them. Avoid concurrent NLLB or both voices on constrained hardware without isolated memory tests. No figures here combine ASR, MT, TTS, target hardware, chunk size or different run conditions.