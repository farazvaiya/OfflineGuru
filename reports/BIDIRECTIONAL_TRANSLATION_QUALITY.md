# Bidirectional translation quality

**Status: automatic proxy results recorded; human quality acceptance not established.**

## Dataset and comparability

`data/eval/en_bn_quality.json` contains 26 paired phrases across normal speech, academic lecture, technical terms, long sentences, numbers, proper nouns and streaming fragments. Its own note says references are engineer-written, not a native-speaker panel. It is neither independent human verification nor a held-out final test split. Bengali source strings are the Bengali references paired with the English originals; this can smoke-test the reverse OPUS route but can overstate quality because evaluation reuses references as source. Scores below are thus diagnostic/regression figures only.

Run profile: CPU int8, current host (not verified to be the target i5-7300U), one model loaded at a time, 26 entries, one untimed warmup invocation per phrase and one measured pass per phrase. Heavy unrelated local services were active; resource captures show CPU often at 100% and system swap around 2.09–2.20 GB. Do not compare these latencies against historical results without matching hardware, configuration, warmup, and repeat procedure.

## Results

| Direction | Model/backend | chrF2 mean | Invocation latency p50 / p95 | Human rating |
|---|---|---:|---:|---|
| English→Bengali | Marian EN→BN CT2 int8, Apache-2.0 model | **0.5923** | **335.73 / 1047.04 ms** | Not collected |
| Bengali→English | Helsinki-NLP OPUS BN→EN CT2 int8, Apache-2.0 model | **0.6377** | **239.74 / 495.19 ms** | Not collected |
| English→Bengali | NLLB-200 distilled 600M | Not run | Not run | Not collected |
| Bengali→English | NLLB-200 distilled 600M | Not run | Not run | Not collected |

The 26 outputs are retained in `reports/results/bidirectional_benchmark.json`. Its estimated per-direction WER/CER columns compare MT hypotheses with paired strings under a shared character/word normalizer; these are not ASR WER/CER and are not human translation judgments. NLLB is CC-BY-NC-4.0 (non-commercial use only); it was not loaded during the run because the long comparison would increase memory/CPU pressure and is not a distributable/commercial default.

## Acceptance assessment

The proposed human translation rating >=4/5 is **NOT MEASURED**. No bilingual reviewer panel or human rating sheet was returned. The acceptance goal cannot be claimed from chrF2 on author-written references. No severe regression claim can be made on an independent final test split because none was available.

## Human review protocol to complete

Have at least two qualified Bengali/English reviewers, blind to model/backend, independently rate adequacy and fluency on 1–5 for both directions, with a third adjudicator for disagreements. Preserve source, reference, output, direction, context and reviewer IDs in an append-only sheet. Report per-direction mean, median, confidence interval/inter-rater agreement and errors; use no training/tuning phrases in the final set.