# Bangla → English Speech Recognition Failure — Root Cause

Date: 2026-11-17
Scope: offline Bengali speech → English captions (English → Bangla is unaffected)
Verdict: **the ASR model cannot write Bengali script; the pipeline then fed that
non-Bengali text to an English→Bangla translator.** Secondary pipeline defects
made it worse. Audio capture, resampling, VAD thresholds and the translation
models are not the cause.

---

## 1. Symptom

A session created in Bengali (`language_pair = "bn"`) produced captions that
were **not Bengali at all** — Latin transliteration, Arabic script, Devanagari,
Korean, or degenerate character loops — and the corresponding "English" caption
was either the same transliteration echoed back or unrelated boilerplate
("Thank you for watching", "The last.").

English → Bangla, by contrast, was correct on the same machine, same server,
same models directory.

## 2. How it was reproduced

Real (synthetic) Bengali speech was already in the repository: `data/guru.db`
holds 55 rows with Bengali reference text, and each row's audio was rendered with
Piper `bn_BD` and stored at `data/audio/<session>/<segment_id>.wav`.

```bash
# 55 pairs, ~54 usable (one row's reference is a run of "m")
.venv/bin/python scripts/bench_bangla_pipeline.py --mode segment --language bn --clips 6 \
    --bn-model-dir ""   # "before" behaviour: base model only
```

Reproduced output (segment mode, `whisper-base`, `language="bn"`):

```
BN: 'شبائشو شاکل آت کے آمر چھو بھی سنیتی شامпور کے جانتے جاتھے'
EN: 'شبائشو شاکل آت کے آمر چھو بھی سنیتی شامпور کے جانتے جاتھے'   ← echoed, untranslated
BN: 'alokshon kranti hulu ehi prokrie jarmatume shabuji udhit shushastr madhumi ...'
EN: 'Mankshron kunti houary girmeushi'
```

Measured over the corpus (`scripts/eval_bn_asr.py`, 54 clips):

| Model used for Bengali | WER (mean / median) | CER | Clips containing Bengali script |
|---|---|---|---|
| `models/whisper-base` (stock) | 1.37 / 1.00 | 1.14 | **0 / 54** |
| `models/whisper-base-bn-ct2` (Bengali fine-tune) | 0.65 / 0.50 | 0.39 | **54 / 54** |

Zero clips contained a single Bengali character before the fix, although the
*audio* was clean Bengali and the *reference* was Bengali text.

## 3. Evidence chain — hypotheses tested and eliminated

| Hypothesis | Test | Result |
|---|---|---|
| Audio path / resampling corrupts speech | Decoded the same clip with FFT band-limited resampling, linear interpolation, and the file served at 16 kHz | Identical output in all three cases → not the cause |
| Tokenizer missing Bengali | Loaded `models/whisper-base/tokenizer.json`; encoded/decoded Bengali strings; token id `` = 50302; round-trip perfect | Tokenizer is fine → not the cause |
| int8 quantization destroys Bengali | Same decode with `compute_type=float32` | Same (wrong) output → not the cause |
| VAD / silence thresholds cutting speech | Fed whole clips with no segmentation at all | Same output → not the cause |
| Translation model at fault | Fed reference Bengali text straight to the bn→en model | Correct English ("Chlorophyll uses a green color for energy.") → translation is fine |
| Decoder task configured wrongly | Tried `task="translate"` on the same audio | Hallucinated English boilerplate ("Thank you for watching") → not the cause |
| Model too small | Repeated with `whisper-small` | Also failed to produce Bengali script (Devanagari + loops) → not size |
| Language detection misrouting | Probed `detect_language` on the clip with the base model | `ko` at 0.27 on a 1.6 s clip, `bn` at 0.88-0.99 on 7 s clips: detection is *not* the blocker |
| Decoder head of the base model cannot emit Bengali | Prompted with a Bengali `initial_prompt`, forced `language="bn"`, raised beam size and fallback temperatures | Bengali characters appeared but degenerated into one-character loops ("ৈৈৈৈৈৈ", "সবাল। সবাল।…") → the decoder has no usable Bengali output distribution |

**Conclusion:** `Systran/faster-whisper-base` was trained on data that map Bengali
speech onto Latin/Arabic/Devanagari surface forms. It can *classify* the language
but cannot *transcribe* it. No decoding parameter fixes a missing output
distribution; only a different acoustic model does.

## 4. Secondary pipeline defects (independent of the model)

These would have degraded Bengali even with a capable model:

1. **Auto mode was pinned to English.** `_process_final` called
   `asr.transcribe(seg.audio)` whenever the turn source was `"en"`, and
   `WhisperAsr` defaults `language=None` to `cfg.language` (`"en"`). Whisper's
   own detection was never reached, so an `auto` session decoded Bengali with an
   English-pinned decoder.
2. **Provisional captions had no language at all.** `_process_partial` called
   `transcribe(buf)` → English default, even in a Bengali session.
3. **Auto routing only counted scripts.** `TurnLanguageRouter.observe_stable`
   compares Bengali vs Latin character counts. A model that romanises Bengali
   emits only Latin, so the ratio never crossed the Bengali threshold and the
   turn stayed on the English route forever. Romanised Bengali was structurally
   unroutable.
4. **Streaming prompt feedback.** `_stream_decode` passes the committed text back
   as `initial_prompt`; once the first phrase was romanised, the prompt
   reinforced Latin script for the rest of the turn.

## 5. Why English → Bangla kept working

`models/whisper-base` is English-competent, so English audio decoded correctly,
and the en→bn Marian model is independent of the ASR failure. Both directions
shared the same broken model *selection* code, but only Bengali hit the
capability wall.

## 6. Model census (why this fix and not another)

Constraints: Intel i5-7300U, 4 cores, 7 GB RAM, CPU-only, offline, no model
larger than the existing `whisper-small` (486 MB) budget.

| Candidate | Size | Verdict |
|---|---|---|
| stock `whisper-base` / `whisper-small` | 148 / 486 MB | Cannot emit Bengali script — disqualified |
| `Faiza07/whisper-base-bengali` → int8 CT2 | 77 MB on disk, +93 MB RSS | **Chosen.** Same architecture as base, vocab byte-identical to the existing tokenizer (50258/50258 ids), decodes English acceptably, RTF 0.6-1.1 |
| `lucius-40/ct2-bangla-whisper` | 6.17 GB | Far beyond the RAM/disk budget |
| `Eusha22/faster-whisper-bangla-lora` | 3.09 GB | Too large for 7 GB RAM with translation models loaded |
| `ashrafulparan/whisper-small-bangla` (Apache-2.0) | ~486 MB, RTF > 1 | Documented fallback if the chosen model's undeclared licence is a blocker |

## 7. Limits of this analysis

- **No human Bengali speech was available.** All measurements use Piper `bn_BD`
  synthetic speech. Absolute WER is therefore optimistic and not comparable to
  published Bengali benchmarks; the before/after comparison is like-for-like and
  valid.
- **No physical microphone was available.** Microphone-path validation is
  described in `BANGLA_TO_ENGLISH_FIX_REPORT.md` §7 but was *not* executed here.
- **Licence:** the chosen fine-tune declares no licence. It is documented as such
  in `models/MANIFEST.md` and `models/whisper-base-bn-ct2/guru_meta.json`, and it
  ships inside `models/`, so nothing is downloaded at runtime.
