# ASR Diagnosis

**Findings:** F-03 (P0, streaming audio loss), F-04 (P1, tail skip), F-07 (P0, wrong-reference WER), F-09 (P2, decode cadence)
**Evidence:** `artifacts/pipeline_baseline_streaming.json`, `artifacts/pipeline_baseline_segment.json`, `artifacts/stream_flush.json`, `artifacts/asr_wer_emma_reproduction.json`, `artifacts/asr_independent_baseline.json`, `baselines/asr_wer_baseline.json`
**Reproduce:** `run_pipeline_eval.py`, `run_asr_eval.py`, `probes/stream_flush.py`

---

## 1. Headline

Whisper `base` (int8, beam 2 in segment mode / 1 in streaming) is **not** the accuracy problem. On identical audio:

| Path | Segments | Recognised speech | Words | WER vs lecture.txt |
|---|---|---|---|---|
| Segment mode (`streaming=false`) | 37 | 144.4 s | 391 / 390 | **0.056** |
| Streaming mode (`streaming=true`, **default**) | 55 | **73.9 s** | **206 / 390** | **0.541** |

The same model, the same file, the same VAD thresholds. The default path loses **47 % of the words**. Every historical accuracy figure was produced with `eval_asr.py`, which exercises the segment path, so the shipped configuration's regression was never measured.

## 2. F-03 — where the words go

Instrumented run (`probes/stream_flush.py`, 60 s at real-time pace):

| Counter | Value |
|---|---|
| Decodes | 47 |
| Decodes that started with `ended=True` **while speech was already active** | **11** |
| `window.reset()` calls | 14 |
| Audio discarded at those resets | **22.2 s of 59.6 s fed** |
| Resets where `in_speech=True` at the moment of reset | **13 / 14** |
| Audio discarded while speech was active | **22.2 s (100 % of the loss)** |

Event trace excerpt (absolute seconds):

```
decode_start t=4.43  ended=True  in_speech=False  dur=2.60  end=4.32
flush_tail   t=5.52                in_speech=True   win_end=5.50  last_committed=2.72  uncommitted=1.18
window.reset t=5.52  in_speech=True  win_start=4.32  win_end=5.50  dur=1.18   ← 1.18 s of live speech deleted
```

Mechanism:

1. VAD declares end-of-speech; `_ended` is set **without a timestamp**.
2. The ASR thread begins the final decode of the window. On this CPU that takes **1.09 s**.
3. The teacher resumes speaking during that second. `SpeechWindow` correctly appends the new audio and reports `in_speech=True`.
4. The decode returns; `_stream_decode(ended=True)` calls `_flush_stream_tail()`, which calls `SpeechWindow.reset()` and `LocalAgreement.reset(window.start_time)` **unconditionally**.
5. Everything that arrived during step 2–3 is gone. It was never decoded, so it never reaches storage, the caption stream, or translation.

The pipeline knows speech is happening (`in_speech=True`) and deletes it anyway. That is why the loss is proportional to how quickly the teacher resumes — a normal lecture cadence loses 30–40 % of the audio; a lecture with long pauses loses almost none. It also explains the "incomplete segments": phrases close on punctuation, but the tail of the sentence was deleted mid-word.

### Competing hypotheses, tested

| Hypothesis | Verdict | Evidence |
|---|---|---|
| `reset()` discards audio that arrived during the final decode | **accepted** | 22.2 s lost, 13/14 resets during `in_speech=True` |
| `LocalAgreement` withholds committed words | rejected | `stream_commits` rises monotonically; buffer tail is 0–6 words; words are never decoded at all |
| VAD never sees the missing speech | rejected | the discarded buffers contain 1.2–2.2 s of detected speech |
| `trim_to()` over-cuts | rejected | trims only remove audio before the committed frontier minus 1 s context |

## 3. F-04 — the final-decode threshold

```python
def _worth_final_decode(self, window):
    un_decoded = window.end_time - self._agreement.last_committed_time
    return un_decoded >= 0.35
```

When fewer than 0.35 s remain, the pipeline skips the decode and calls `flush_all()`, which commits the **previous** hypothesis. The last fraction of a second of every sentence is simply not transcribed. With ~37 utterances per lecture the worst case is ~13 s of speech; typical loss is smaller. Fixing F-03 makes most of this recoverable (the audio stays in the window), but the threshold should be lowered to ~0.15 s and the residual documented.

**External corroboration:** WhisperLiveKit v0.2.26 shipped exactly this class of fix — "LocalAgreement consumes its pending buffer at end of input instead of losing the final deferred audio (#411)". The upstream project hit the same defect in its own LocalAgreement implementation.

## 4. F-07 — the 95.6 % cross-speaker WER is an artifact

### What was claimed

`reports/EMMA_WATSON_ASR_EVALUATION.md`: WER 95.6 %, explained as "a different speaker… the model was trained on the demo lecture corpus". Whisper base is a general pretrained model; that explanation is false on its face.

### What actually happened

`scripts/eval_asr.py` has:

```python
ap.add_argument("--wav", default="data/demo/lecture.wav")
ap.add_argument("--text", default="data/demo/lecture.txt")   # independent default
```

The report's reproduction command passes only `--wav /tmp/emma_16k.wav`. The reference therefore stayed `lecture.txt`.

### Reproduction

| Quantity | Value |
|---|---|
| `lecture.txt` word count | **390** |
| Stored JSON `reference_words` for the Emma run | **390** |
| My Emma hypothesis word count | **353** |
| Stored JSON `words` | **353** |
| WER of my Emma hypothesis against `lecture.txt` | **0.9564** |
| Stored WER | **0.956** |

Exact reproduction, including the hypothesis word count. The Emma audio was scored against the photosynthesis lecture.

### Is cross-speaker recognition actually bad?

No. The transcript is coherent and follows the speech:

> "I was appointed six months ago and the more I've spoken about feminism… fighting for women's rights has too often become synonymous with man-hating… Feminism by definition is the belief that men and women should have equal rights and opportunities."

Coverage: 353 words over 220.1 s (1.60 words/s), all 23 VAD segments containing speech, with only the intro/outro non-speech segments empty (a 9.2 s music/applause segment → "I", a 1.5 s segment → empty, an 8.8 s outro → "you").

Independent-sample check (no human reference exists for this clip): **base vs small agree on 97.5 % of words** (353 vs 355 words, same 23 segments, 1 empty each). Two different model sizes hear essentially the same content; a genuine 95.6 % error rate would require both to be wrong in the same way.

**What we can and cannot say:** the absolute WER on the Emma clip is **unknown** because there is no human-verified transcript for it in the repository. What is proven is that the 95.6 % number measures the wrong thing, and that the recognition is coherent, complete and corroborated by a second model.

### Fix

Make `--text` required, record the `(wav, text)` pair in the output, and refuse to report WER when reference and hypothesis word counts differ by more than 2× (a guard that would have caught this immediately). Correct the report.

## 5. F-09 — decode cadence wastes CPU and inflates the tail

| Window length | asr_ms | RTF |
|---|---|---|
| 1–12 s (segment mode) | ~1.1 s | **0.28** |
| 0.3–1.0 s (streaming) | 0.5–2.0 s | **1.1–3.4** |

Whisper pads every input to 30 s; a 0.6 s window costs almost as much as a 6 s window. With `stream_step_s=0.5` and `stream_min_audio_s=0.6`, the loop spends most of its time on windows that cannot amortise the call: `asr_rtf` p50 0.70, p95 2.0, max 5.1; `stream_backlog` 27 of 125 decodes.

This is a throughput/latency problem, not an accuracy problem — and it is Phase 3's main optimisation lever, to be validated against WER so that no words are traded for speed.

## 6. Segment-mode accuracy detail (baseline, 5.64 % WER)

| Error type | Count |
|---|---|
| Correct | 372 |
| Substitutions | 15 |
| Deletions | 3 |
| Insertions | 4 |
| Total errors | 22 / 390 words |

Deletions are the category that matters for the classroom: only 3 words vanished. In the streaming path, the equivalent number is **184 missing words**. Same model, same audio — the difference is entirely the pipeline.

## 7. Confidence

* F-03: **high** — measured, instrumented, deterministic, and reproduced by a second run.
* F-04: **medium** — code-level certainty, magnitude bounded by the 0.35 s threshold.
* F-07: **high** — exact numeric reproduction of the stored artifact, plus a positive test (coherent transcript, 97.5 % inter-model agreement).
* F-09: **medium** — measured RTF, but the optimum is a Phase 3 experiment, not a known constant.
