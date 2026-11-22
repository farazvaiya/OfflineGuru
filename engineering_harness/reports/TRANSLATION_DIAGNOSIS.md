# Translation Diagnosis

**Inspection only in Phase 1 — no translation change is proposed.**
**Baseline:** `baselines/translation_quality_baseline.json`
**Reproduce:** `.venv/bin/python engineering_harness/run_bench.py translation --label baseline`

---

## 1. Backends available

| Backend | Model | Size | Role |
|---|---|---|---|
| `marian` (**default**) | `marian-en-bn-ct2` int8 | 80 MB | ~0.1–0.4 s/sentence on this CPU |
| `nllb` | `nllb-600m-ct2-int8` | 618 MB | better Bangla, ~4× slower |
| `opus` | `opus-en-bn-ct2` | 659 MB | experimental, bidirectional pair present |
| `passthrough` | — | — | used when `translate.enabled=false` |

`guru/translate.py` also enforces **number preservation**: every digit group in the source must appear in the output (ASCII or Bengali numerals), with the missing numbers logged as a warning.

## 2. Measured baseline (Marian, author-written reference set)

| Metric | Value |
|---|---|
| chrF mean | **0.5923** |
| Sentences | 26 |
| Numbers preserved | all (see `numbers_preserved` in the artifact) |
| Latency p50 | **135.3 ms** |
| Latency max | 401.1 ms |
| Backend | marian (int8, intra_threads=2, beam 4) |

The project's own evaluation script is honest about its limits: the reference translations were written by the engineer who built the pipeline, not a native-speaker panel. chrF against them is a **regression signal**, not a quality claim. That framing is correct and is carried forward unchanged into the acceptance criteria (`translation_metric_absolute_regression: 0.02`).

## 3. Where translation sits in the latency budget

From the full-pipeline run (`baselines/latency_baseline.json`, 45 s file, 14 phrases):

| Stage | p50 | p95 | max |
|---|---|---|---|
| translate_ms | 92.6 | 448.9 | 493.0 |
| tts_ms | 58.5 | 556.4 | 615.6 |
| e2e_text_ms | 2169.0 | 4725.3 | 5421.9 |

Translation is **not** the bottleneck in the default configuration: p50 is 93 ms against a 2.17 s end-to-end caption. The p95 (449 ms) comes from long phrases hitting the 160-character cap, and is dwarfed by the 1.1 s ASR decode that precedes it.

## 4. Chunk boundaries — the real quality risk

Translation input is a `Phrase` from `PhraseBuilder`, closed on:

* sentence punctuation `. ! ? । ؟`,
* 160 characters (split at the last clause boundary or conjunction),
* 8.0 s of speech,
* 1.5 s since the phrase opened with ≥12 characters.

Consequences worth recording:

1. **Mid-sentence starts are deliberate** (the 1.5 s age rule) and improve perceived latency, but they deny the model its sentence subject. Bangla verb-final structure makes a subject-less clause more damaging than it would be in English. This is a genuine accuracy/latency trade-off, not a bug.
2. **The 160-char split** keeps within the model's comfortable input length; `translate.split_sentences` handles longer inputs by splitting on punctuation.
3. **No cross-phrase context** is passed to the translator. `max_input_chars=1200` allows it, but the pipeline never does. For terminology consistency across a lecture this matters; for latency it does not.

Because F-03 (streaming audio loss) deletes the ends of sentences, the *current* translation input is often a truncated fragment. Fixing F-03 will change what the translator sees, so the chrF baseline must be re-measured after Phase 2 rather than assumed stable.

## 5. Failure behaviour (already sound)

* A translation exception does **not** lose the English transcript: `_enrich` publishes the segment with an empty `bn` and a `pipeline.error` event, then continues.
* An enrichment-queue overflow drops the translation but keeps the caption in English and increments `enrichment_queue_drops`.
* `numbers_preserved=False` is logged and carried in the segment payload, so the teacher UI can flag it.

## 6. What Phase 3 may and may not change

Allowed: batching, chunk boundaries, context passing, thread count, backend selection — each measured against chrF **and** latency, with the same 26-sentence reference set.

Not allowed: swapping the reference set, switching backend between baseline and final (the gate rejects it), or accepting a chrF drop larger than 0.02 to buy speed.

## 7. Conclusion

Translation is in good shape and is not implicated in the reported missing-word problem. It should be perturbed as little as possible in Phase 2, and re-measured **only** because fixing F-03 changes the text it receives.
