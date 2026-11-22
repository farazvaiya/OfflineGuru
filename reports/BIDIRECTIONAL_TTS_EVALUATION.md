# Bidirectional offline TTS evaluation

## Verified local voices

Both voice pairs exist and were actually used to synthesize valid WAVs.

| Target language | Voice | Local files and license note | Sample results |
|---|---|---|---|
| English | Piper `en_US-lessac-medium` | ONNX + JSON present. Voice-specific redistribution terms not located in this checkout; confirm before redistribution. | One load: 9966.5 ms. Two warm uncached sample utterances: 1045.2 and 758.1 ms; RTF .413/.317. Phrase-set (26 phrases, one timed invocation after per-phrase warmup) p50/p95 622.33/1569.74 ms; RTF mean .247. Repeat run (52 samples) p50/p95 603.63/2588.18 ms; RTF mean .270. |
| Bengali | Piper `bn_BD-google-medium` | ONNX + JSON + model card present. Bengali Bangladesh/India; 16 speakers; 22,050 Hz. Source data OpenSLR-37 CC-BY-SA-4.0 plus CMU Indic terms; preserve notices and assess share-alike obligations. | One load: 5618.7 ms. Two warm uncached samples: 798.7 and 791.8 ms; RTF .201/.193. Phrase-set p50/p95 980.58/1902.78 ms; RTF mean .290. Repeat run (52 samples) p50/p95 783.34/2406.73 ms; RTF mean .266. |

The two-sentence measurements are not representative p50/p95. The phrase runs are descriptively useful, but host-contended and have one or two measured invocations per phrase. The long tails shift between repetitions, so acceptance cannot be concluded.

## Runtime path and resource behavior

The app maintains voice-specific Piper instances/cache keys. It loads the selected target voice before capture and synthesizes on the TTS worker, delivering audio in the existing sequenced segment events. Both voice routes use actual local model paths. One sample loading both voices in one process reached about 408 MB RSS; it is not a peak-resource measurement with ASR + MT + both voices. The application should load only the selected target voice per session/route.

The MT/TTS benchmark ran during high host contention with significant swap use. The current host is not confirmed to be the declared i5-7300U.

## Acceptance status

The user's first translated audio p50<=2s / p95<=4s and resource limits require a comparable end-to-end streaming benchmark, not isolated synthesis. No target-laptop long run, thermal test, voice intelligibility/naturalness panel or 30-student bilingual ordering/drop test was completed. These goals remain **NOT ESTABLISHED**.