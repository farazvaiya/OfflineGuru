# Open-source repository research: speech-to-speech translation

**Date:** 2026-11-12T01:46:14+06:00  
**Scope:** code/dependency/license/audio/streaming and published-benchmark review of the three requested projects. No upstream source was copied into this change.

| Project | License / code examined | Dependencies / real-time design | Published claims and suitability |
|---|---|---|---|
| `gebrilfradj/realtime-speech-translator` | MIT license; README, pyproject and pipeline/realtime/ASR/MT/segmentation/TTS sources. | faster-whisper, Transformers/Torch, SentencePiece, NumPy/soundfile; optional sounddevice, Piper, pyttsx3, Gradio. Its mic/VAD creates utterances, bounded queue, worker cascade and separate playback; queue policy discards oldest input when full. It is utterance-based, not stable partial-token simultaneous translation; model download may occur on first use. | README advertises many language codes, but that does not prove Bengali speech quality or paired Bengali TTS coverage. Reported CPU comparison is English→Spanish, 4.5-second clip, five warm runs; not comparable to this target hardware/directions. No code adopted. |
| `QuentinFuxa/WhisperLiveKit` | Apache-2.0; README, pyproject, audio input, processing queue, LocalAgreement, SimulWhisper/AlignAtt sources. Existing Guru LocalAgreement attribution retained. | FastAPI/websockets, faster-whisper, librosa/soundfile, optional Torch/torchaudio and inference backends; streamed audio/VAD enters server processing queues; LocalAgreement commits stable hypotheses. SimulWhisper is separate AlignAtt implementation with extra model/runtime. Upstream currently constrains Python below this project's Python 3.14 environment. | Streaming ASR library, not the complete offline bidirectional classroom product. Its general streaming claims do not establish BN↔EN accuracy or target CPU latency. No code copied/integrated. |
| `SYSTRAN/faster-whisper` | MIT; README, transcribe/audio implementation. | CTranslate2, NumPy, tokenizers, PyAV/tqdm. Audio decode/resample is provided; capture and streaming hypothesis policy are caller responsibilities. Whisper `transcribe` language/timestamps/VAD controls; CPU int8. | README benchmark uses 13-minute English audio on i7-12700K / 8 threads (Small int8 1m42s, 1477 MB). Not i5-7300U, not Bengali WER, and not an online translation benchmark. Guru continues using its installed local faster-whisper path. |

## Model/data license notes

- Cached OPUS/Marian conversion metadata records source repository and revision. The BN→EN Helsinki-NLP and EN→BN community model cards state Apache-2.0; conversion to CT2 int8 does not replace those conditions.
- NLLB-200 distilled 600M model card is CC-BY-NC-4.0, non-commercial only. Not selected for the default path and not run in the local comparison.
- Bengali Piper `bn_BD-google-medium` card names OpenSLR-37 CC-BY-SA-4.0 plus CMU Indic terms. Keep voice model attribution and comply with source/model distribution terms.
- English Piper `en_US-lessac-medium` ONNX+JSON is present, but its voice-data license was not located in this checkout. Confirm before redistribution.
- Piper runtime is GPL-3.0-or-later as already recorded in `LICENSES.md`.

## Decision

Retain Guru's VAD, LocalAgreement, bounded queues and authenticated classroom UI. Add direction-specific offline MT and target-language Piper selection without bringing in a GPU-/Torch-heavy replacement framework. No upstream performance/language claim substitutes for local evidence.

## Evidence boundary

The local benchmark results are separate and labeled in `reports/BIDIRECTIONAL_BENCHMARK.md`. There is no human-verified Bengali or code-switch speech-reference data in this checkout, so those quality results are blocked rather than inferred from repository claims.

**Detailed notes:** `reports/BIDIRECTIONAL_REPOSITORY_RESEARCH.md`. **Harness review source:** `engineering_harness/reports/OPEN_SOURCE_RESEARCH.md` (existing). Recorded files:
- `engineering_harness/reports/OPEN_SOURCE_RESEARCH.md` — SHA-256 `b8ba14e3622a06ad61eee6236ef9022de7f74701e7efbce7433ca24e05944d63`, 5855 bytes.
- `reports/BIDIRECTIONAL_REPOSITORY_RESEARCH.md` — SHA-256 `40bb1a0e8f48043078067259584693b4cd4f42cd3e45b43597c890c8ea715426`, 5942 bytes.
