# Third-party licenses

Offline Guru itself is **GPL-3.0-or-later** (see `LICENSE`). GPL-3.0 was chosen
because the Piper TTS runtime is GPL-3.0; linking it into the distributed
application requires a GPL-compatible license for the whole work.

| Component | Version | License | Notes |
|---|---|---|---|
| FastAPI / Starlette / Uvicorn | 0.149 / 1.9 / 0.54 | MIT / BSD-3 / BSD-3 | web server |
| faster-whisper | 1.2.1 | MIT | ASR runtime |
| CTranslate2 | 4.8.3 | MIT | inference engine |
| Whisper `base` weights (Systran/faster-whisper-base) | — | MIT | ASR model |
| transformers / tokenizers | 5.20.2 / 0.23 | Apache-2.0 | tokenizer only (no torch at runtime) |
| sentencepiece | 0.2.2 | Apache-2.0 | SentencePiece models |
| sacremoses | 0.2.0 | MIT | Marian tokenizer pre-processing |
| Marian `shhossain/opus-mt-en-to-bn` | — | Apache-2.0 | EN→BN translation model |
| Piper (`piper-tts`) | 1.8.0 | **GPL-3.0-or-later** | TTS runtime |
| Piper voice `bn_BD-google-medium` | — | runtime GPL-3.0; voice data OpenSLR-37 **CC-BY-SA-4.0** + CMU Indic license | see `models/piper/MODEL_CARD` |
| Piper voice `en_US-lessac-medium` | — | prep/demo only | English demo audio |
| qrcode / Pillow | 8.2 / 13.0 | BSD / MIT-CMU | offline QR generation |
| numpy / soundfile / psutil | — | BSD / BSD / BSD | utilities |
| httpx | 0.28.1 | BSD | development only (`requirements-dev.txt`): load test and translator comparison scripts |
| **Optional** NLLB-200-distilled-600M | — | **CC-BY-NC-4.0** | non-commercial only; not downloaded by default |
| **Optional** PyTorch (prep only) | 2.14.1 | BSD-3 | used once to convert Marian to CTranslate2 |

## Asset notices

* OpenSLR-37 (Bengali speech corpus) is CC-BY-SA-4.0. The Piper voice model
  derived from it inherits attribution/share-alike obligations for the model
  itself. It is shipped as a separate data file under `models/`.
* Whisper weights: MIT, © OpenAI.
* Marian checkpoint: Apache-2.0, © the model authors (Helsinki-NLP recipe,
  community fine-tune by `shhossain`).

If you redistribute a classroom image, keep `LICENSES.md`, `LICENSE`, and
`models/MANIFEST.md` (which records provenance and sizes) together with the app.
