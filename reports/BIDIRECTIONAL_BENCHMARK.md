# Bidirectional benchmark summary

## Run conditions

- Artifacts: `reports/results/bidirectional_benchmark.json` (one measured pass) and `reports/results/bidirectional_benchmark_repeat2.json` (two measured invocations per phrase), generated 2026-11-12.
- Dataset: 26 author-written EN/BN parallel phrases, not independent human verification and not held-out.
- Directions: EN→BN and BN→EN reported separately.
- Runtime: CPU int8. Target i5-7300U was not confirmed as this run host.
- Warmup: one unmeasured call per phrase/model/voice. The second artifact has N=52 measured invocations per model/voice.
- Host contention: pre-existing local Uvicorn process used ~800 MB RSS and about 150% CPU during part of benchmarking. Available RAM was around 1.8–1.9 GB, system swap used ~2.1 GB and sampled CPU frequently hit 100%. The metrics are contaminated and not clean target-device acceptance evidence.
- NLLB comparison was not completed. Its model card license is CC-BY-NC-4.0 (non-commercial only).

## Measured automatic MT and Piper

| Configuration | Quality proxy | p50 / p95 ms, first pass (N=26) | p50 / p95 ms, repeat (N=52) |
|---|---:|---:|---:|
| Marian EN→BN | chrF2 0.5923 | 335.73 / 1047.04 | 378.78 / 1837.97 |
| OPUS BN→EN | chrF2 0.6377 | 239.74 / 495.19 | 224.09 / 414.20 |
| Piper English | — | 622.33 / 1569.74; mean RTF .247 | 603.63 / 2588.18; mean RTF .270 |
| Piper Bengali | — | 980.58 / 1902.78; mean RTF .290 | 783.34 / 2406.73; mean RTF .266 |

Translation quality uses author-written text references; Bengali source for BN→EN is the reference side, not natural recorded speech. TTS is per-phrase synthesis time. Neither number is first-caption or end-of-phrase-to-first-audio latency for ASR→MT→TTS. The repeat runs show long-tail variation and preclude a stable latency conclusion.

## Resource and delivery evidence

The JSON files include per-sample process RSS, system swap and CPU. No target laptop peak-RSS run, swap-growth isolation, 30-student bilingual audio-order test, dropped-finalized-segment load test, or thermal run was completed. Historical English-only loopback load evidence is not a bidirectional result.

## Acceptance matrix

| Proposed goal | Status | Reason |
|---|---|---|
| WER <=20%, both directions | BLOCKED | No human Bengali speech reference; English reference not independently verified and no fresh comparable streaming WER run. |
| Translation human rating >=4/5 | BLOCKED | No independent bilingual review panel. |
| First audio p50 <=2s / p95 <=4s | NOT MEASURED | No compatible end-to-end streaming benchmark. MT/TTS microbenchmark is not first audio. |
| Zero dropped finalized segments | Unit-tested bounded queue only; load acceptance NOT MEASURED | No concurrent target-host run. |
| Bounded queues | Code and regression tests PASS; sustained load NOT MEASURED | Dynamic load absent. |
| Memory within target | NOT ESTABLISHED | Host contended; target hardware not confirmed. |
| No severe regression in either direction | NOT ESTABLISHED | No independent held-out two-direction references. |

## Next benchmark

Run on the declared laptop (or clearly identify actual CPU), with sufficient isolated memory headroom, both directions, Base/Small and 0.5/0.75/1/1.5/2-second chunking after warmup and repeated runs. Pin each run's model, chunk/overlap, VAD, reference split, threads and voice. Record Bengali-aware WER/CER, blind human ratings, first-caption/audio and phrase-end latency, true RTF, CPU/RSS/swap, queue depth/drops and delivery order. Do not mix incompatible cells.