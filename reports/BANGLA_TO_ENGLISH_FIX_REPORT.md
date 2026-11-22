# Bangla → English — Fix Report

Date: 2026-11-17
Companion: `reports/BANGLA_TO_ENGLISH_ROOT_CAUSE.md` (why it was broken)
Result files: `reports/results/bangla_asr_eval.json`,
`reports/results/bangla_pipeline_*.json`

---

## 1. What changed

Language-conditioned model selection: Bengali decodes go to a Bengali-trained
CTranslate2 model, English keeps the existing model byte-for-byte. Every change
below is additive; no existing interface, event name, or default direction was
removed.

| File | Change |
|---|---|
| `models/whisper-base-bn-ct2/` | **New asset** (77 MB, int8): `Faiza07/whisper-base-bengali` converted with `ct2-transformers-converter`; vocab is identical to `models/whisper-base` (50258/50258 ids) so the existing tokenizer files are reused. Provenance/licence in `guru_meta.json`. |
| `guru/asr.py` | Lazy **per-directory** model cache (`_models` + lock), `_dir_for(language)` routing `bn`/`ben` to `bn_model_dir`, `load_for(language)`, `bn_available`, and `detect_language()` (probe used only when the caller asked for `auto`). Exposes `last_detected_language` / `last_detection_prob`. A missing Bengali model logs one warning and falls back to the base model instead of failing the session. |
| `guru/config.py`, `config/default.toml` | `asr.bn_model_dir = "models/whisper-base-bn-ct2"`, `asr.auto_probe_interval_s = 20.0`. |
| `guru/bilingual.py` | `TurnLanguageRouter.decode_language()` (single source of the decode language) and `observe_detected(language, prob)` — a recognizer probe may route an `auto` turn before any text is committed. |
| `guru/pipeline.py` | `prepare_direction()` preloads the route's ASR model; `_decode_language()` with a 20 s probe cache in auto mode; `_process_final`, `_process_partial` and `_stream_decode` all use it; `_note_detection()` records the route. Custom ASR adapters without a `language` kwarg still work (signature check). |
| `scripts/prepare_models.py` | `fetch_bn_asr()` (`--only bn-asr`) downloads + converts the Bengali model; manifest row and licence note. |
| `scripts/eval_bn_asr.py` | **New**: WER / CER / script / RTF / RSS evaluation over the repository's Bengali pairs. |
| `scripts/bench_bangla_pipeline.py` | **New**: real-time end-to-end drive of `ClassroomPipeline` (segment & streaming, `bn`/`en`/`auto`, or any WAV). |
| `scripts/pipeline_cli.py` | `--language en|bn|auto`, plus captions labelled by their actual source/target language instead of hard-coded EN/BN. |
| `tests/conftest.py` | `FakeAsr` accepts `language`, records it, and can emulate a language probe. |
| `tests/test_bangla_asr_routing.py` | **13 new regression tests** (model selection, fallback, probe routing, partial/final plumbing, English regression). |
| `models/MANIFEST.md` | New asset row, licence caveat, re-provision command. |

## 2. Behaviour, before and after

6 Bengali clips (34.5 s of speech) fed through the real pipeline at real-time pace,
real Marian/OPUS translation models, TTS disabled (`NullBidirectionalTts`) so the
numbers are transcription/translation only.

| Run | Segments | Captions with English | Bengali-script ratio | Untranslated echoes | First caption |
|---|---|---|---|---|---|
| segment, `bn`, **before** (base model) | 6 | 5 | **0.00** | 1 | n/a (segment mode) |
| segment, `bn`, **after** | 6 | **6** | **1.00** | **0** | n/a (segment mode) |
| streaming, `bn`, **before** | 6 | 4 | **0.00** | 2 | 6.56 s |
| streaming, `bn`, **after** | 7 | **7** | **1.00** | **0** | **2.64 s** |
| segment, `auto`, **after** | 6 | 6 | **1.00** | 0 | 1 turn switch (probe → bn) |
| streaming, `en`, **after** (English→Bangla regression) | 5 | 5 Bangla captions | 0.00 (correct) | 0 | 1.80 s |

Failure rate on the Bengali runs (captions whose "English" was an untranslated
echo): 1/6 (segment) and 2/6 (streaming) before, **0** after.

Streaming decodes also dropped from 16 to 11 for the same audio, because the
Bengali model's first hypothesis is already stable (the base model needed extra
agrees before committing).

After the fix, real output:

```
BN: আলোকসং করানটি হলো এই প্রকরীয়ে যার মাধুমে শবুঝ উধিট সুশস্ত্বের মাধুমে নিজ্যেদের খাঁধ্য তড়ি করে
EN: The light lamp is in this chamber where the wiring wiring of self-demons in the form of a supthesis.
```

It is *Bengali in, English out* — the acceptance criterion. It is not yet
lecture-grade translation; see §5.

English → Bangla after the fix (Emma Watson excerpt, 25 s):

```
EN: I was appointed six months ago.
BN: আমাকে ছয় মাস আগে নিযুক্ত করা হয়েছিল।
EN: And the more I've spoken about feminism.
BN: আর নারীবাদ সম্পর্কে আমি যত বেশি কথা বলেছি.
```

## 3. Accuracy measurements (ASR only)

`scripts/eval_bn_asr.py` over 54 clips (one database row excluded: its reference
is a run of "m", which would swamp a mean). Piper `bn_BD` TTS speech, beam 2,
`language="bn"`.

| Metric | `whisper-base` (before) | `whisper-base-bn-ct2` (after) |
|---|---|---|
| WER mean / median | 1.37 / 1.00 | **0.65 / 0.50** |
| CER mean | 1.14 | **0.39** |
| Clips with Bengali script | 0 / 54 | **54 / 54** |
| Clips with WER ≤ 0.30 | 0 / 54 | **22 / 54** |
| Character completeness (hyp/ref) | 0.98 | 1.19 |
| RTF (batch, short clips) | 0.58 | 1.03 |
| Model load | 0.66 s | 0.18 s |
| Extra RSS | +156 MB | **+93 MB** |

The WER of the fine-tune is still high. Its errors are systematic and phonetic —
`শোভাযাত্রার` → `শুভাজাটার`, `কার্বান ডাই অক্সাইডে` → `কাড়বান দাই অসাইড়ে`. A base-sized
model fine-tuned on Bengali simply is not a strong Bengali recogniser; it is,
however, the first configuration in this repository that produces Bengali at all.

## 4. Cost on the target hardware

Intel i5-7300U class CPU, `compute_type=int8`, `cpu_threads=3`:

- Bengali model: +93 MB RSS, loads in 0.2 s, 77 MB on disk.
- Batch RTF 1.03 for 1-8 s clips (short clips pay Whisper's fixed 30 s padding).
- Streaming: 6 of 11 decodes exceeded real time (`stream_backlog`), with window
  RTF up to 2.8 when the window grew to 5.7 s. Bengali streaming therefore runs
  **at the edge of real time** on this CPU, unlike English. Mitigations that
  already exist: `asr.stream_max_window_s`, `cpu_threads`, and segment mode.
- Auto mode adds one language probe per `auto_probe_interval_s` (default 20 s):
  ~1.2 s each, cached in between. Explicit `bn`/`en` sessions pay nothing.

## 5. Known gaps

1. **Lexical accuracy.** WER 0.65 (median 0.50) on synthetic Bengali is not
   lecture-grade. The failure is in the acoustic model, not the plumbing: the
   English translations are now real translations of what was transcribed, and
   they inherit every transcription error.
2. **Short-clip hallucination.** Clips under ~1 s occasionally loop
   ("পেঁপি পেঁপি পেঁপি…"), and a trailing silence can produce a repeated-character
   segment. Existing guards (`no_speech_prob > 0.9`, VAD, `min_segment_s = 0.8`)
   reduce but do not eliminate this.
3. **Unrelated-script echoes.** `guru/translate.py` only sends text containing
   Latin or Bengali letters to Marian; anything else is echoed. This is what made
   the pre-fix Arabic-script hallucinations appear "translated". It is left as-is
   deliberately — the correct place to fix hallucinated script is the recognizer.
4. **Auto-mode switching granularity.** The route is sticky per turn and re-probes
   at most every 20 s; a mid-sentence language switch is noticed at the next
   probe, not instantly.
5. **Licence.** `Faiza07/whisper-base-bengali` declares no licence. Flagged in
   `models/MANIFEST.md` and `guru_meta.json`. Replacing it is a one-line config
   change (`asr.bn_model_dir`).
6. **No human-speech or microphone validation.** See §6.

## 6. Verification performed

- `pytest tests -q` → **345 tests collected, all passed, exit status 0**
  (332 pre-existing + 13 new).
- `ruff check` on every touched file: clean; the three remaining findings in
  `guru/bilingual.py` / `guru/pipeline.py` are pre-existing lines that were not
  modified.
- ASR evaluation over 54 Bengali clips (before/after table in §3).
- End-to-end pipeline runs in segment, streaming and auto mode, plus the
  English→Bangla regression run (§2).
- `scripts/prepare_models.py` was edited but **not re-run end-to-end** (it needs
  a network download and torch conversion); the asset it produces is the one
  already committed, created with the same converter command.

### Physical microphone validation — not executed

No microphone was available in this environment, so no physical-microphone claim
is made. To validate on the target laptop:

```bash
# 1. record 20 s of Bengali speech from the built-in mic (16 kHz mono)
arecord -f S16_LE -r 16000 -c 1 -d 20 /tmp/mic_bn.wav

# 2. run it through the real pipeline (Bengali session, Bengali ASR model)
.venv/bin/python scripts/pipeline_cli.py /tmp/mic_bn.wav --language bn --no-tts

# expected: "[  x.xs] BN <Bengali script>" lines followed by "EN <English>".
# if the log says "ASR loaded ... models/whisper-base-bn-ct2", the fix is active.
```

For the classroom flow, start the server (`scripts/serve.sh`), create a session
with source language **Bengali**, and speak; `pipeline.health()` reports
`language_turn_switches` and `stream_backlog` for the session.

## 7. Rollback

Set `asr.bn_model_dir = ""` (or delete the directory). The runtime falls back to
`asr.model_dir`, logs a warning, and behaves exactly as before this change —
English → Bangla is unaffected either way.

## 8. Acceptance criteria

| Requirement | Status |
|---|---|
| Bengali speech transcribed | ✅ 54/54 clips in Bengali script (was 0/54) |
| Transcripts reach English translation | ✅ 6/6 segment, 7/7 streaming captions |
| English actually produced | ✅ (lexically rough — §5.1) |
| English → Bangla still works | ✅ 5/5 Bangla captions, unchanged model path |
| Streaming behaviour preserved | ✅ 7 phrases, first caption 2.64 s (was 6.56 s) |
| No critical regressions | ✅ 345/345 tests pass; no API/protocol change |
| Remaining gaps documented | ✅ §5 |
| Physical-microphone validation | ⚠️ not possible here — commands in §6 |
