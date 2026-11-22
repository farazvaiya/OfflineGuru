# Open-Source Research

**Question:** do the four repositories named in the brief offer anything this project should adopt, given CPU-only, 2 cores / 4 threads, ~2.3 GB headroom, offline, GPL-3.0?

**Method:** each repository was checked for existence, default branch, licence, commit count, release activity and what its code actually contains — not what its README claims. Checked 2026-11-10. Claims below are limited to what could be seen.

---

## 1. QuentinFuxa/WhisperLiveKit — **relevant, already partly adopted**

| Property | Finding |
|---|---|
| Exists | yes, `main` |
| Licence | **Apache-2.0** (LICENSE verified) |
| Activity | 922 commits; latest release v0.2.26, 2026-08-29 |
| Stack | faster-whisper, SimulStreaming/AlignAtt, LocalAgreement, Sortformer diarization, NLLB translation, FastAPI + WebSocket |
| CPU fit | designed for GPU in practice; AlignAtt/SimulStreaming adds an attention-alignment model on top of Whisper |

**This project already adapts its LocalAgreement** (`guru/realtime.py` header credits WhisperLiveKit's `online_asr.py`, Apache-2.0, and states the adaptation: own `Word` dataclass, case/punctuation-insensitive comparison, added `flush_all()`). That attribution is correct and the licence is compatible.

**Most useful finding** — v0.2.26 release notes include:

> "LocalAgreement consumes its pending buffer at end of input instead of losing the final deferred audio (#411)."

An upstream project independently hit the same class of bug as this repo's F-04 (and the same family as F-03). This is corroboration that end-of-stream handling is the fragile part of LocalAgreement-style streaming, not evidence that copying their code is the fix.

**Adoption recommendation:** do **not** vendor the repository (GPU-oriented, large dependency surface). Do read its buffer-consumption fix when implementing F-03/F-04. Keep the existing attribution.

## 2. PolyTalkIO/polytalk — **not suitable**

| Property | Finding |
|---|---|
| Exists | yes, `main` |
| Licence | **AGPL-3.0**, dual-licensed with a commercial edition |
| Activity | created 2026-05-27, **19 commits** — very new |
| Architecture | FastAPI + service-oriented; translation via **OpenAI-compatible chat completions** endpoints, STT/TTS via provider services; Docker Compose, GPU-ready |
| Offline fit | local models possible but the design assumes you point it at provider endpoints |

Two blockers:

1. **Licence.** This project is GPL-3.0-or-later (forced by the Piper runtime). Absorbing AGPL-3.0 code into a GPL-3.0 work is not permitted in that direction; it would force the whole classroom product to AGPL.
2. **Design mismatch.** PolyTalk's translation layer targets an OpenAI-compatible HTTP provider — a cloud/LLM dependency the brief explicitly forbids at runtime. Its useful ideas (provider abstraction, `BaseTranscriptionService` contracts) are irrelevant here, where models are local CTranslate2 files.

**Adoption recommendation:** none. Note it in `LICENSES.md` as reviewed-and-rejected for licence reasons.

## 3. CrazyCyberbug/Realtime-translation — **not suitable**

| Property | Finding |
|---|---|
| Exists | yes, `main`; described as "A colab backed implementation for streaming ASR" |
| Content | `Transcriber.py` (110 lines) using `torch`/`torchaudio`; a Colab notebook demo |
| Licence | no licence file visible in what was checked |
| Maintenance | demo-scale, not a library |

It is a single-file Colab demonstration built on PyTorch. This project deliberately does not ship Torch at runtime (`requirements.txt` says so explicitly, Torch only appears in `requirements-prep.txt` for one-off model conversion). There is no maintainable component to adopt, and no licence to rely on.

**Adoption recommendation:** none.

## 4. rohitsux/sunlo-core — **not usable as a source**

| Property | Finding |
|---|---|
| Exists | yes, `main`; 3 stars, **1 commit**, created 2026-03-11 |
| Content | the repository's only top-level file is **`README.md`**. The README describes a full `asr/ translator/ tts/ server/ benchmarks/` tree, but that tree is not present |
| Claim | "<300 ms latency", "Built on AI4Bharat models" |
| Licence | none visible |
| Relevance if it existed | Indic language pair support (IndicTrans2, IndicTTS) would be interesting for Bangla |

A one-commit, README-only repository cannot be evaluated for latency, correctness or licence. The <300 ms claim is unverifiable and, for a full ASR→MT→TTS chain on CPU, implausible.

**Adoption recommendation:** none. Do not cite its latency figure anywhere.

## 5. What the research changes in this engagement

| Question | Answer |
|---|---|
| Replace the pipeline with an upstream project? | No. This project's architecture (3 threads, bounded queues, per-session isolation, classroom auth) is already appropriate; the defects are local and fixable. |
| Adopt AlignAtt/SimulStreaming for lower latency? | Not now. It adds a model and GPU-oriented assumptions; Phase 3's cadence experiment is a cheaper first step with measurable WER guardrails. |
| Adopt IndicTrans2 for Bangla quality? | Worth a future experiment (Bangla↔English is exactly its target), but it needs a model download, a licence review and a quality comparison against Marian. Out of scope for this engagement, recorded as a follow-up. |
| Anything to learn from upstream? | Yes: upstream's LocalAgreement end-of-input bug (#411) validates the F-03/F-04 direction, and their "pending buffer must be consumed" rule is the design principle to implement. |

## 6. Licence compliance note

`LICENSES.md` already records Piper (GPL-3.0) and the WhisperLiveKit LocalAgreement adaptation (Apache-2.0). No new code was copied in this engagement, so no new notice is required. PolyTalk was reviewed and rejected; that should be recorded so the next engineer does not repeat the evaluation.
