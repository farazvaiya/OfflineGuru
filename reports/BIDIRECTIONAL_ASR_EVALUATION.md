# Bidirectional ASR evaluation

**Result: Bengali and mixed-language human-speech acceptance is blocked; do not claim both directions passed.**

## Available evidence

The only paired speech/reference asset in this checkout is `data/demo/lecture.wav` / `data/demo/lecture.txt` (English lecture, 160.0 s file; existing project reference). The harness's historical, stamped evaluation used faster-whisper Base CPU int8, energy-VAD segment mode: WER **0.0564**, 37 segments, 144.4 s detected speech, RTF mean **0.28**, segment ASR p50/p95 **1089.8/1156.1 ms**. The reference was not independently human-verified during this task, and this is not the newly integrated bidirectional streaming configuration or confirmed i5-7300U hardware.

The historical baseline records Whisper Base / Small transcript agreement at 0.975 on a separate English clip, but agreement is not WER and is not Bengali evidence.

## Directional status

| Direction | Language pin / model candidates | Reference evidence | WER / CER acceptance |
|---|---|---|---|
| English speech → Bengali | Whisper Base/Small CPU int8; explicit `en` route | Existing English lecture file/transcript, reference verification unknown | **Not re-measured in the current streaming integration**; historical Base result above is not a comparable acceptance run. |
| Bengali speech → English | Whisper Base/Small CPU int8; explicit `bn` route | No human Bengali speech recording + verified transcript | **BLOCKED** |
| Mixed English/Bengali | Turn-level script routing is unit-tested; a confident stable script locks direction until VAD turn boundary | No mixed speech recording/reference | **BLOCKED**; code-switch unit tests do not constitute ASR accuracy results. |

The benchmark script attempted to run both sizes on the existing long English lecture and make a Piper-synthesized Bengali proxy, but the ASR job was cancelled due to resource contention and yielded no valid ASR artifact. It is not counted as a run. Piper-generated Bengali speech, if collected later, can be a supplementary synthetic diagnostic only; it cannot replace a human-speech test set.

## Why acceptance is not asserted

No available set provides the requested 20-minute Bengali + 20-minute English + 10-minute mixed + noisy/fast clips, human-verified transcripts, translations, or separate tuning/test partitions. Therefore independent Bengali WER/CER, code-switch ASR accuracy, and no-severe-regression in both directions cannot be determined. Do not convert the old English segment-path WER into a claim about the new bidirectional live path.

## Needed to unblock

Collect authorized 16 kHz mono human speech with independently verified Bengali and English references, bilingual review and disjoint tuning/test splits. Include mixed/code-switch clips as a separately scored category. On the target i5-7300U, evaluate Base and Small under the same chunk/overlap/streaming configuration, repeated after warmup, then calculate Bengali-aware WER and CER with the same normalizer and versioned references.

Machine-readable details: `reports/results/bidirectional_asr_evaluation.json`.