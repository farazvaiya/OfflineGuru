# Open-Source Project Comparison — Real-Time Speech Translation

**Date:** 2026-11-08
**Method:** each repository was cloned at its default branch and its source read directly.
Every "verified" cell below is backed by a file path in that clone. Where a repository
does not contain the claimed feature, the cell says so.

Clones inspected (shallow, default branch):

| Repo | Commit date | Files | License |
|---|---|---|---|
| PolyTalkIO/polytalk | 2026 | 116 tracked files, ~17k Python LOC | **AGPL-3.0** (`LICENSE`, `COPYRIGHT`) |
| QuentinFuxa/WhisperLiveKit | 2026 | 271 tracked files | **Apache-2.0** (`LICENSE`, `pyproject.toml:11`) |
| CrazyCyberbug/Realtime-translation | 2026 | 6 files, 456 Python LOC | **none** — no LICENSE file in the repository |
| rohitsux/sunlo-core | 2026 | **1 file: README.md** | README claims MIT; no LICENSE file |

---

## 1. Verification results

### A. PolyTalk — `PolyTalkIO/polytalk`

**What it actually is.** A FastAPI app (`app/main.py`) plus two sidecar services:
`stt/` (faster-whisper over WebSocket) and `tts/` (Piper over HTTP), wired by
`docker-compose.yml`. The browser captures audio and streams raw 16 kHz mono int16 PCM
to `/api/ws/translate`.

**Verified capabilities**

| Claim | Verdict | Evidence |
|---|---|---|
| Streaming ASR | **Yes, windowed** — not true streaming. Fixed-size windows with overlap, re-decoded per window | `stt/app/main.py:56-57` (`STREAM_CHUNK_SECONDS`, `CHUNK_OVERLAP_SECONDS`), `:561-627` |
| Incremental transcript | **Yes** — cumulative transcript, delta extraction, repeated-word collapse | `stt/app/main.py:375-488` |
| Incremental translation | **Yes** — buffer flushed on sentence boundary / char count / age | `app/services/pipeline_service.py:1018-1053` |
| Chunk management | **Yes** — preroll, pause flush, idle flush, ordered results | `stt/app/main.py:539-753, 820-979` |
| Bangla support | **Translation only** — `bn` is in the supported target list | `README.md:452` |
| Bangla TTS | **No** — the shipped voice list has no `bn` voice | `tts/setup-voices.sh:18-34` |
| CPU support | **Yes** — `STT_COMPUTE_TYPE=int8`, CPU default | `stt/app/main.py:92-95` |
| Offline | **Conditional** — STT/TTS are local, but translation defaults to an OpenAI-compatible HTTP API. Offline only if pointed at a local Ollama/vLLM server | `README.md:102-106`, `config/config.yaml.example` |
| WebSocket | **Yes** | `app/routers/api.py`, `stt/app/main.py:507` |
| Latency instrumentation | **Yes, excellent** — queue wait, inference, emit delay, backpressure per stage | `stt/app/main.py:330-352`, `pipeline_service.py:631-666` |

**Why it cannot be dropped in.** Translation is an LLM chat completion. On this CPU a
useful local LLM (qwen3-8b as configured) is impossible; the pipeline would need a
different translation backend anyway. It is also a multi-service Docker deployment, while
Offline Guru is a single process on the teacher's laptop.

**License consequence.** AGPL-3.0 is copyleft over the network. Copying its code into
Offline Guru (GPL-3.0-or-later) would force the whole project to AGPL-3.0. **No code is
copied.** The *algorithms* (window overlap, pause flush, delta extraction, translation
flush policy, per-stage metrics) are standard streaming techniques and are re-implemented
independently in `guru/realtime.py`, with attribution in the module docstring.

---

### B. WhisperLiveKit — `QuentinFuxa/WhisperLiveKit`

**What it actually is.** A mature streaming ASR server with several policies
(SimulStreaming/AlignAtt, LocalAgreement, Voxtral, Qwen3-ASR), optional diarisation, and
optional NLLB translation.

**Verified capabilities**

| Claim | Verdict | Evidence |
|---|---|---|
| Streaming ASR | **Yes** — two real policies | `whisperlivekit/local_agreement/online_asr.py`, `whisperlivekit/simul_whisper/` |
| LocalAgreement | **Yes** — canonical `HypothesisBuffer` (committed / buffer / new) | `local_agreement/online_asr.py:11-93` |
| Incremental transcription | **Yes** — commit longest common prefix, trim audio at commit | `online_asr.py:219-266, 339-353` |
| Partial transcripts | **Yes** — `get_buffer()` returns the unvalidated tail | `online_asr.py:212-216` |
| CPU inference | **Yes** — `FasterWhisperASR` with `device="auto"`, `compute_type="auto"` | `local_agreement/backends.py:101-155` |
| CTranslate2 | **Yes** — faster-whisper is the default non-MLX backend | `backends.py:105-127` |
| NLLB translation | **Yes** — in-process via the `nllw` package, token-level | `translation.py:36-52` |
| Bangla ASR | **Yes** — Whisper supports `bn` | `whisper_online.py:15` |
| Bangla TTS | **No** — the project has no TTS at all | (absent from the tree) |
| Timestamps | **Yes** — word-level `ASRToken(start, end, text, probability)` | `timed_objects.py`, `backends.py:141-149` |
| WebSocket | **Yes** | `whisperlivekit/basic_server.py` |
| Offline | **Yes** for ASR; NLLB weights must be present locally | `translation.py` |

**Why it cannot be dropped in.** It is a complete server with its own session model,
its own WebSocket protocol, its own frontend, and a large dependency surface
(`nllw`, `mosestokenizer`/`wtpsplit`, optional `torch`). Replacing Offline Guru's
FastAPI app with it would discard the teacher console, education services, auth and
storage. Its default policy (SimulStreaming) needs PyTorch and alignment heads — far too
heavy for 2 cores.

**What is reused.** The **LocalAgreement-2 hypothesis buffer** and the
**commit-then-trim** window management. Apache-2.0 is compatible with GPL-3.0, so the
algorithm is adapted into `guru/realtime.py` with an explicit attribution header. The
adaptation is not a copy: it operates on Offline Guru's own `Word` dataclass, uses
normalised comparison, and adds an explicit `flush_all()` for end-of-speech.

---

### C. Realtime-translation — `CrazyCyberbug/Realtime-translation`

**What it actually is.** A 456-line Gradio demo intended to run in Google Colab.

**Verified capabilities**

| Claim | Verdict | Evidence |
|---|---|---|
| Continuous capture | **Yes** — Gradio streaming microphone | `GradioApp.py:184-187` |
| Audio chunking | **Naive** — appends to a buffer, re-transcribes the whole buffer every 2 s | `GradioApp.py:79-101` |
| Incremental ASR | **No** — full re-decode, then string concatenation of transcripts | `GradioApp.py:91-92` |
| Partial translation | **Yes** — translates each 2 s chunk | `GradioApp.py:88` |
| Streaming communication | **No WebSocket** — Gradio's own streaming UI | `GradioApp.py:234-244` |
| Bangla | **Yes** — IndicTrans2 `ben_Beng` | `GradioApp.py:44-52`, `Translator.py:7-9` |
| Bangla TTS | **No** — no TTS stage at all | (absent) |
| CPU | **Technically yes, practically no** — `torch.float16` and `whisper-large-v3-turbo` by default | `Transcriber.py:58-74` |
| Offline | **No** — models are pulled from the Hugging Face Hub at import time | `Transcriber.py:12-13, 66-74` |
| License | **None** — no LICENSE file, so all rights reserved by default | — |

**Reusable value.** Only as a negative example: it demonstrates exactly the failure mode
to avoid (re-transcribing a growing buffer and concatenating the results produces
duplicated text and unbounded latency). No code is reused, and none legally could be.

---

### D. Sunlo Core — `rohitsux/sunlo-core`

**What it actually is.** **A README only.** The repository contains exactly one tracked
file, `README.md` (182 lines). The directory tree it documents (`asr/`, `translate/`,
`tts/`, `server/`, `benchmarks/`, `tests/`, `docker/`, `requirements.txt`) does not
exist. Its own roadmap marks every module as unchecked:

```
- [x] Repo structure + README
- [ ] ASR module with streaming support
- [ ] NMT module with CT2 inference
- [ ] TTS module with first-chunk streaming
- [ ] VAD integration
- [ ] FastAPI WebSocket server
- [ ] Docker deployment
- [ ] Benchmark suite
```
(`README.md:142-153`)

| Claim | Verdict |
|---|---|
| Faster-Whisper integration | **Not present** |
| IndicTrans2 | **Not present** |
| IndicTTS | **Not present** |
| Streaming processing | **Not present** |
| CPU vs GPU | README says "CPU inference supported (higher latency)"; no code, no measurement |
| Memory requirements | README lists model sizes; no measurement |
| Bengali support | Claimed via IndicTrans2; unverifiable |
| Benchmarks | README's own table is empty ("Work in progress") |
| License | README says MIT; no LICENSE file |

**Conclusion.** Nothing can be reused. The "<300 ms on GPU" headline is a target, not a
measurement, and does not apply to a CPU-only laptop in any case.

---

## 2. Comparison table

| Criteria | PolyTalk | WhisperLiveKit | Realtime-translation | Sunlo Core | **Offline Guru (existing)** |
|---|---|---|---|---|---|
| Streaming ASR | Windowed re-decode | **True (LocalAgreement / AlignAtt)** | Naive full re-decode | none | Segment-based batch |
| Incremental translation | Yes (buffer flush) | Yes (token-level NLLB) | Yes (per chunk) | none | **No** |
| Chunk management | **Excellent** (overlap, preroll, pause flush) | **Excellent** (commit + trim) | Poor | none | Basic (VAD segments) |
| Bangla ASR | via Whisper | via Whisper | English only | claimed | **Yes** (Whisper) |
| Bangla translation | via LLM API | via NLLB | IndicTrans2 | claimed | **Yes** (Marian CT2) |
| Bangla TTS | **No voice shipped** | **No TTS** | **No TTS** | claimed | **Yes** (Piper bn_BD) |
| CPU support | Yes (int8) | Yes (faster-whisper) | Poor (fp16, large model) | unknown | **Yes (measured)** |
| RAM (measured here) | multi-container, not measured | not measured | not measured | n/a | **~800 MB** |
| Offline | Conditional (needs local LLM) | Yes | No (HF download) | n/a | **Yes (verified)** |
| WebSocket | Yes | Yes | No | claimed | **Yes** |
| License | AGPL-3.0 | Apache-2.0 | **none** | MIT (claimed, no file) | GPL-3.0-or-later |
| Code reusable here | **No** (AGPL) | **Yes** (Apache-2.0) | **No** (no license) | **No** (no code) | — |

---

## 3. Component selection

| Responsibility | Selected | Source | Rationale |
|---|---|---|---|
| Audio streaming | Existing `MicCapture` + new `SpeechWindow` | Offline Guru + PolyTalk *ideas* | arecord path is proven; window/preroll/pause-flush policy adapted from PolyTalk's design |
| ASR engine | Existing faster-whisper + CTranslate2 int8 | Offline Guru | Already measured on this CPU; no new dependency |
| Transcript stabilisation | **LocalAgreement-2** | WhisperLiveKit (Apache-2.0), adapted | Only policy that is cheap enough for 2 cores; SimulStreaming needs PyTorch |
| Translation | Existing Marian CT2 int8 | Offline Guru | 0.20 s/sentence vs NLLB 2.1 s; NLLB stays selectable |
| Translation buffering | New `TranslationBuffer` | PolyTalk *ideas*, original code | Sentence/char/age/pause flush policy is exactly what incremental translation needs |
| TTS | Existing Piper `bn_BD-google-medium` | Offline Guru | Only verified Bangla voice available offline |
| Audio buffering / ordering | New server-side sequence + client ordered queue | Original | Neither repo solves multi-student ordered playback |
| WebSocket delivery | Existing `EventBus` + `_pump` | Offline Guru | Already handles 30 clients with zero drops |
| Latency measurement | New `StageTimer` + metrics | PolyTalk *ideas* | Per-stage queue wait + RTF + first-caption/first-audio |

**Nothing is copied wholesale.** The only adapted code is the LocalAgreement hypothesis
buffer (Apache-2.0, attributed). Everything else is written for this repository.

---

## 4. What the four repositories do *not* provide

1. **Bangla TTS.** Only Offline Guru has a working Bangla voice. PolyTalk ships 15 Piper
   voices and none is Bengali; WhisperLiveKit and Realtime-translation have no TTS.
2. **Multi-student ordered playback.** All four are single-listener tools.
3. **A CPU budget for 2 cores.** WhisperLiveKit's defaults assume a GPU; PolyTalk's
   translation assumes an LLM server.
4. **Offline-by-construction.** Only Offline Guru verifies zero external connections.

This is why the result is a hybrid: the streaming *policy* comes from WhisperLiveKit,
the *buffering discipline* from PolyTalk, and the models, TTS, storage, auth and fan-out
stay Offline Guru's own.
