# Accuracy regression: root-cause report

Status: **investigation complete, no production code modified.** Two defects are
proven with measurements; the suspected causes are listed separately and are not
counted as findings.

Date: 2026-11-13. Host: 4-core CPU, 7.2 GB RAM, 4 GB swap (2.3 GB in use).
Investigated against `models/whisper-base` (ASR) and `models/marian-en-bn-ct2`
(translation), the shipped defaults for `config/default.toml`.

---

## 1. Executive summary

ASR and translation models, weights, decoding parameters and on-disk
configuration did **not** change. Measured on the project's own reference audio:

- The **segment path** (one decode per VAD segment) still reproduces the stamped
  2026-11-09 baseline *exactly*: WER **0.0564**, coverage **1.003**, 37 segments,
  144.4 s of speech from a 160 s file.
- The **streaming path**, which is the shipped default (`asr.streaming = true`),
  delivers WER **0.4923** and coverage **0.556**: it finalises 38 captions from
  only 82.7 s of the 144.4 s of speech. **43 % of the lecture never becomes a
  caption.**
- Translation quality on identical sources is *unchanged*: chrF **0.5923**
  (26 entries), identical per-category scores to the stamped baseline
  (`engineering_harness/baselines/translation_quality_baseline.json`).

Two defects explain the damage:

1. **R1 — the streaming decoder cannot keep up, and the overflow is silently
   discarded.** Each rolling-window decode costs 2.1 s p50 / 4.6 s p95 while the
   loop asks for a new decode every 0.5 s of new audio. Measured RTF p50 1.1,
   p95 4.3. The 15 s rolling window then hits its cap and `SpeechWindow._cap_window()`
   drops the **oldest un-decoded** audio. `SpeechWindow.dropped_frames` exists but
   is never incremented, so the loss is invisible in `pipeline.health()` and logs.
2. **R2 — Whisper prompt feedback loop.** `LocalAgreement.prompt()` feeds the last
   committed words back as `initial_prompt`, and `asr.commit_confidence = 0.9`
   commits echo tokens immediately. Reproduced on 2 s of noise:
   with no prompt → **0 words**; with prompt `"hey, hey, …"` → **112 words**, all
   `"Hey, hey, …"`, timestamps `(0.0, 1.98)` then `(1.98, 1.98)` repeated — i.e.
   zero-length spans, which become zero-duration duplicate captions. The user's
   latest real session contains exactly this pattern.

**The automatic-playback change did not cause the regression.** It touched only
`guru/server/static/student.js` and `student.html` (audio queue, labels, status
line). The only server change made after the harness checkpoint is an optional
body on `POST …/start`. Evidence for the timeline is in §4 and §5.

The most plausible reason the degradation became *noticeable* after the playback
work: students now hear every caption automatically, and the R2 echo loop makes
11 consecutive identical captions audible and burns 93 % of translation compute
(37.5 s of 40.2 s in the recorded session), which compounds R1.

---

## 2. Previous vs current configuration

No git metadata exists in this checkout (`fatal: not a git repository`), so the
comparison uses the harness snapshot
`engineering_harness/checkpoints/20261112-005540-ba54f6` (label: *pre-bidirectional-streaming:
verified existing three-phase baseline*) and its SHA-256 manifest. 14 of the
manifest's files differ from the working tree today; the accuracy-relevant ones
are `guru/asr.py`, `guru/pipeline.py`, `guru/translate.py`, `guru/config.py`,
`config/default.toml`, `guru/storage.py`, `guru/server/app.py`.

| Setting | Snapshot (baseline) | Working tree now | Changed? |
|---|---|---|---|
| ASR model | `models/whisper-base` | same | no |
| `asr.compute_type` | int8 | int8 | no |
| `asr.beam_size` (segment) | 2 | 2 | no |
| `asr.stream_beam_size` | 1 | 1 | no |
| `asr.language` | `en` | `en` (explicit sessions pin it; `auto` passes `None`) | no |
| `asr.streaming` | **true** | **true** | no |
| `asr.stream_step_s` / `stream_min_audio_s` | 0.5 / 0.6 | 0.5 / 0.6 | no |
| `asr.stream_max_window_s` | 15.0 | 15.0 | no |
| `asr.commit_confidence` | 0.9 | 0.9 | no |
| `asr.vad_threshold` | 0.006 | 0.006 | no |
| `asr.partial_enabled` | false | false | no |
| `translate.backend` | marian | marian | no |
| `translate.model_dir` | `models/marian-en-bn-ct2` | same | no |
| `translate.beam_size` | 4 | 4 | no |
| `translate` new keys | — | `bn_en_model_dir`, `bn_en_tokenizer_dir` | additive only |
| `tts` new keys | — | `english_voice_onnx`, `english_voice_config` | additive only |

`config/default.toml` and `guru/config.py` diffs contain **only** the additive
bilingual/TTS keys; no accuracy knob changed. `guru/asr.py` added an optional
`language=` argument (identical behaviour when called without it) and nothing
else.

The snapshot's `models/marian-en-bn-ct2/guru_meta.json` contains no
`tokenizer_dir` override, so `MarianTranslator` still tokenises EN→BN with the
model's own `source.spm`/`vocab.json` — the new tokenizer lookup cannot have
changed EN→BN input.

---

## 3. Files and functions involved

| Area | Location |
|---|---|
| Rolling window, silent drop | `guru/realtime.py:216` `SpeechWindow`, `feed()` 296, `_cap_window()` 350, unused `dropped_frames` 250 |
| Prompt feedback | `guru/realtime.py:196` `LocalAgreement.prompt()`; used at `guru/pipeline.py:421` |
| Streaming decode cadence | `guru/pipeline.py:356` `_stream_asr_loop`, `_stream_decode` 414, backlog warning 437 |
| Phrase finalisation | `guru/pipeline.py:520` `_finalize_phrase` (no zero-duration / duplicate guard) |
| Enrichment handoff | `guru/pipeline.py:577` and 700 (`self._enrich_q.put(job)`, blocking, post-snapshot change) |
| ASR call | `guru/asr.py:70` `transcribe`, `:103` `transcribe_words` |
| Translation | `guru/translate.py:130` `MarianTranslator`, `guru/bilingual.py:105` `BidirectionalTranslator` |
| Playback-only changes | `guru/server/static/student.js`, `student.html` (no server ASR/TTS involvement) |

---

## 4. Proven root causes

### R1 — streaming decode is over budget and its overflow is dropped silently

Measured on `data/demo/lecture.wav` (160 s, reference `data/demo/lecture.txt`)
replayed at real-time pace through the real pipeline:

| Run | Mode | WER | Coverage | Segments | Speech in captions | RTF p50 | Backlog decodes |
|---|---|---|---|---|---|---|---|
| `pipeline_baseline_segment` (2026-11-09) | segment | 0.0564 | 1.003 | 37 | 144.4 s | — | 0 |
| `pipeline_regress_now_segment` (today) | segment | **0.0564** | **1.003** | 37 | 144.4 s | — | 0 |
| `pipeline_baseline_streaming` (2026-11-10) | streaming | 0.5410 | 0.528 | 55 | 73.9 s | 0.7 | 27 |
| `pipeline_regress_now_streaming` (today) | streaming | **0.4923** | **0.556** | 38 | **82.7 s** | **1.1** | **31** |

Artifacts:
`engineering_harness/artifacts/pipeline_regress_now_{streaming,segment}.json`,
`pipeline_baseline_{streaming,segment}.json`,
`engineering_harness/baselines/asr_wer_baseline.json`.

Decode latency today, streaming: p50 **2140 ms**, p95 **4574 ms**, max **10 096 ms**
per decode; 59 decodes in 160 s. The loop triggers a decode every
`stream_step_s = 0.5 s` of new audio and at every speech end, so demand exceeds
supply roughly 3–8×. While the loop is behind, `SpeechWindow._cap_window()` keeps
only the newest 15 s and advances `_buf_start` past the discarded audio
(`guru/realtime.py:350-357`). That audio is never decoded, so its words never
reach `LocalAgreement` and never become captions: this is the missing 61.7 s of
speech and the 43 % coverage loss. `SpeechWindow.dropped_frames` (`:250`) is
never incremented and never reported, so the pipeline reports healthy while
dropping audio.

The streaming path was already in this state on 2026-11-10 (WER 0.541,
coverage 0.528, 27 backlog decodes) — before the bidirectional work (2026-11-12)
and before the playback change (2026-11-13). Today's run is marginally better,
not worse.

### R2 — committed text is fed back as a Whisper prompt, and the echo commits

`_stream_decode()` sets `prompt = self._agreement.prompt()` (`pipeline.py:421`),
which returns the last ≤200 chars of committed words (`realtime.py:196`), and
passes it as `initial_prompt`. With `commit_confidence = 0.9`, echoed tokens have
high probability and are committed on the *first* hypothesis, so no second decode
is needed to confirm them.

Reproduction (`.venv/bin/python`, Whisper Base, int8, `language="en"`,
`beam_size=1`, 2 s of low-level noise):

```
no prompt        -> 0 words
prompt "hey, hey, hey, hey, hey, hey, hey, hey, hey, hey"
                 -> 112 words: "Hey, hey, hey, ..." 
                    times: (0.0, 1.98), (1.98, 1.98), (1.98, 1.98), ...
```

Zero-length spans are passed to `PhraseBuilder` and then to `_finalize_phrase`,
which has no guard for `end - start == 0` or for identical consecutive text, so
they become real captions with `start_s == end_s`, are translated, synthesised
and (after the playback fix) **spoken**. The prompt then grows with the echo,
sustaining the loop.

Evidence in the user's latest real session, `data/guru.db` session
`f3c03bad313c` (created 2026-11-13 13:29, i.e. **before** the playback change at
23:13), en→bn:

- 23 segments, 6 with zero duration (`seq` 14, 15, 16, 18, 19, 22);
- 5 consecutive duplicate pairs (13/14, 14/15, 17/18, 18/19, 21/22), all
  `"hey, hey, hey, …"`;
- segments 13–23: 306 of 375 words (**81.6 %** of the transcript) from **2.1 s**
  of audio;
- translation time spent on the echo: **37.5 s of 40.2 s total** (max single
  segment **7117 ms**, versus a 228 ms standalone p50);
- `asr_ms = 0.0` on every row: the streaming path produced them.

That session alone accounts for the reported "ASR accuracy decreased": for most
of it the transcript was an echo, and every echoed caption was translated and
synthesised, so the visible/audible output degraded in both languages.

---

## 5. Suspected causes not yet proven

| Suspicion | Status | Why not proven |
|---|---|---|
| Blocking `self._enrich_q.put(job)` (`pipeline.py:577`, `:700`, new since the checkpoint) starves the ASR worker | **Not the cause on this hardware.** Contention probe with real Marian + Piper on a 60 s clip: enrichment queue peak **2 / 12**, TTS peak **3 / 16**, so the blocking put never engaged | Queue never filled in a clean run; it *could* engage during an R2 echo burst (see §6) |
| Browser audio playback now adds CPU load to the same laptop, pushing ASR RTF past 1.0 | Plausible, unmeasured | Host has multiple Brave renderers (~2.2 GB RSS) and 2.3 GB swap in use; no browser audio session was measurable here |
| `auto` source language (`language=None`) degrades decoding | Not observed | 2 s English clip decoded identically with `language='en'` and auto-detect; no Bengali/mixed human audio available |
| `BidirectionalTranslator` changed EN→BN quality | Ruled out | chrF identical on identical sources (§7); `guru_meta.json` has no tokenizer override |

---

## 6. Resource / performance evidence

- Streaming run: CPU mean **84.0 %**, max 100 %, process RSS peak **372.5 MB**,
  ASR p95 **4574 ms**, e2e p95 **5900 ms**, first caption **2174 ms**.
- Segment run: CPU mean 61.7 %, e2e p95 4664 ms.
- Baseline streaming run (2026-11-10): CPU mean 60.4 %, RTF p50 0.7 — already
  behind real time on this class of host.
- Contention probe (60 s, translation + TTS live): `translate_ms` p50 228 ms,
  p95 642 ms, max 913 ms; `tts_ms` p50 312 ms, max 1107 ms; enrichment peak 2/12,
  TTS peak 3/16; `stream_backlog` 13 decodes flagged RTF > 1 (p50 1.5).
- Host at investigation time: 7.2 GB RAM, 5.9 GB used, **1.3 GB available**,
  **2.3 GB swap used**; browser renderers ~2.2 GB RSS combined.

In the recorded class session the echo loop inflated translation work 18×
(37.5 s for 2.1 s of audio), which is exactly the kind of load that worsens R1:
while the enrichment/TTS workers saturate the CPU, streaming decodes slow further
and more window audio is discarded. The clean-run probe shows the queue itself
does not overflow without that amplification.

---

## 7. ASR and translation comparison detail

### ASR (identical audio, identical model, different delivery path)

```
segment   WER 0.0564  coverage 1.003  hypothesis 391/390 words
streaming WER 0.4923  coverage 0.556  hypothesis 217/390 words
```

Error character of the streaming run: missing words dominate (only 217 of 390
reference words delivered), with correct prefixes per caption — consistent with
*audio never reaching the decoder*, not with a weaker model. Example captions are
clean ("Good morning photosynthesis", "their own food using sunlight."), then
content is simply absent.

### Translation (identical source sentences, `data/eval/en_bn_quality.json`)

| Run | chrF | numbers preserved | entries |
|---|---|---|---|
| Stamped baseline 2026-11-10 | 0.5923 | 26/26 | 26 |
| Today, current code | **0.5923** | 26/26 | 26 |

Per-category scores are identical to four decimals (academic_lecture 0.5873,
long_sentence 0.7401, technical_terms 0.4628, …). Artifact:
`engineering_harness/artifacts/translation_quality_now.json`.

Live translation therefore only *looks* worse because its input is wrong
(echo/missing sentences). Independently of that, the user-visible duplication
(11 identical captions, then 11 identical translations) is an R2 artefact.

---

## 8. Impact on automatic audio playback

- No playback code path touches ASR or translation. The diff against the
  checkpoint for `student.js` / `student.html` is confined to the client audio
  queue (`queueAudio`, `drainAudio`, `playAudio`, status line, Bangla labels) and
  contains no server calls beyond the existing `/api/student/audio/{id}` fetch.
- Playback remains functional and must stay functional. Nothing in this report
  recommends disabling it.
- Playback does not need to change to fix either root cause. It *amplifies* the
  symptom's audibility: 11 duplicate captions are now heard as 11 repeated
  phrases instead of being clicked one at a time.
- The single server change made in the playback session (optional `StartBody` on
  `POST …/start`, `guru/server/app.py:467`) cannot affect accuracy; segment-mode
  WER measured after it equals the baseline exactly.

---

## 9. Recommended minimal fixes (not implemented)

Ordered by evidence strength and blast radius; each needs the gate in §10.

1. **Break the prompt echo (R2).** Either stop passing `initial_prompt` by
   default (make it opt-in via `asr.stream_prompt = false`), or cap it to the last
   3–5 words *and* suppress it when the committed tail is a repeated token run.
   This is a few lines in `_stream_decode` / `LocalAgreement.prompt`.
2. **Reject empty/duplicate phrases (R2 guard).** In `_finalize_phrase`, skip a
   phrase whose duration is `< 0.05 s`, or whose normalised text equals the
   previous finalised segment and has no duration growth. Prevents zero-duration
   duplicates from reaching translation, TTS and students.
3. **Make streaming keep up, or stop shipping it as default (R1).** Two options:
   (a) until an adaptive scheduler exists, ship `asr.streaming = false` — the
   segment path measures WER 0.0564 today, with the same models; (b) raise
   `stream_step_s` to ≈ measured decode time (2.0 s), widen
   `stream_max_window_s` above the worst decode, and only then re-measure. Do not
   trade accuracy for latency; (a) is the accuracy-preserving default.
4. **Instrument the loss (R1).** Increment `SpeechWindow.dropped_frames` in
   `_cap_window()` and expose `dropped_audio_s` in `pipeline.health()` and the
   session summary, so coverage is observable during a real class.
5. **Keep the ASR thread non-blocking (latent).** Revert the enrichment handoff to
   `put_nowait` plus the existing drop counter, or gate the blocking put on free
   capacity. Measured queues never filled in a clean run, so this is hygiene, not
   a proven fix.

---

## 10. Tests required before implementation

1. `engineering_harness/run_pipeline_eval.py` on `data/demo/lecture.wav`, both
   modes, with gates: segment WER ≤ 0.06 and coverage ≥ 0.99 (regression lock at
   today's 0.0564/1.003); streaming WER ≤ 0.15 and coverage ≥ 0.95 before
   streaming may be the default again.
2. Prompt-echo unit test: `transcribe_words(2 s of noise, prompt="hey, hey, …")`
   must not produce committed words after the fix (today it yields 112).
3. Pipeline duplicate test: replay 60 s with a forced repeated hypothesis and
   assert no finalised segment with `end_s - start_s < 0.05` and no two
   consecutive segments with identical normalised `source_text`.
4. Translation lock: `scripts/eval_translation_quality.py` on
   `data/eval/en_bn_quality.json` must stay ≥ 0.5923 chrF.
5. Telemetry test: feeding a WAV faster than real time makes
   `health()["dropped_audio_s"] > 0` (proves the new counter works).
6. `scripts/browser_audio_qa.py` must stay 11/11 after any pipeline change, so the
   playback fix is not regressed by the accuracy fix.

---

## 11. Limitations

- **No git history.** There is no repository in this checkout, so "previous
  configuration" is the harness snapshot
  `20261112-005540-ba54f6` plus its stamped baseline artifacts. Changes made
  before that snapshot (notably the introduction of the streaming path, first
  measured 2026-11-10 at WER 0.541) cannot be diffed, only inferred from the
  artifacts.
- **One English human reference only.** `data/demo/lecture.wav`/`.txt` is the only
  paired speech/reference asset. Bengali and code-switched accuracy remain
  **BLOCKED** (no human Bengali speech + verified reference), as stated in
  `reports/BIDIRECTIONAL_ASR_EVALUATION.md`. WER figures apply to that file only.
- **The user's complaint is qualitative.** The recorded session in `data/guru.db`
  is the only post-change trace; no reference transcript exists for it.
- **No browser measurement.** Client-side CPU cost of continuous playback could
  not be measured (no browser audio device in this environment); that suspicion
  stays unproven.
- **Machine load varies.** These runs competed with the user's own long-running
  server and browser (~84 % CPU mean during the streaming run), which is
  representative of the target laptop but makes millisecond figures
  non-comparable to the 2026-11-09 baseline of 27 % CPU.
