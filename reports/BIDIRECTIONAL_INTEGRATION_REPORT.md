# Bidirectional integration report

## Change summary

Implemented within Offline Guru's existing FastAPI/auth/WebSocket/session stack; no application rewrite and no network inference added.

- Added `guru/bilingual.py`: fixed English/Bengali route mapping; optional auto detection from stable script evidence, which locks the route for a VAD turn; lazy directional Marian/OPUS instances; lazy Bengali/English Piper voices.
- Extended `WhisperAsr` to accept per-call language selection, including true Whisper auto detection.
- Added configurable Bengali→English OPUS model/tokenizer and English Piper model/config paths. Reverse model tokenizer comes from cached source assets.
- Added additive SQLite fields `source_language`, `target_language`, `source_text`, `translated_text`; old `en`/`bn` fields remain supported and represent the corresponding-language text.
- Pipeline routes stable phrases to direction-specific MT and queues target-language TTS independently of ASR. Capture=none remains available to existing lifecycle/testing; invalid WAV is checked before model loading. Finalized MT/TTS handoffs use bounded worker backpressure instead of silently discarding phrase work.
- Updated teacher/student UI to render canonical source/translated fields and permit audio playback in either target language.
- Added `tests/test_bilingual.py` for route pairing, stable turn/code-switch behavior, local model/voice assets and both-direction event/TTS dispatch.

## Regression evidence

- Targeted integration set: `tests/test_api_edge.py`, `tests/test_documents.py`, `tests/test_bilingual.py`: passed after repairs.
- Full suite: **253 passed** (fresh run after integration).
- Model warm-load smoke: both directional translators loaded offline and returned outputs; two actual Piper voice files loaded and produced valid WAVs.
- No Git metadata exists at project root. Known-good harness source snapshot: `engineering_harness/checkpoints/20261112-005540-ba54f6`.

## Existing features and limitations

Existing REST/WebSocket authentication, session scope, lecture/document flows and storage behavior remain covered by the passing full suite. The existing student/event contract is retained with additive canonical fields.

This report does **not** certify two-direction speech-to-speech acceptance. Bengali human-speech WER, mixed-script ASR, blind translation ratings, first translated audio p50/p95 under the requested chunk sweep, end-to-end target-host resources, classroom load delivery/order and severe-regression comparison remain unverified. Benchmark host had an active Uvicorn workload, high CPU contention and approximately 2.1 GB swap used. The user's acceptance criteria remain open.

## Harness note

The pre-existing three-phase harness is `PHASE2_BLOCKED` and its prior P0 findings concern capture stderr supervision, capture restart, the historical stream flush loss, and a wrong-reference WER result. This bidirectional feature work does not resolve all of those findings or provide QA/evaluator signoffs. Fresh test evidence is written, but a phase gate must not be marked passed without all required criteria, independently reviewed accuracy data, and signoff.
