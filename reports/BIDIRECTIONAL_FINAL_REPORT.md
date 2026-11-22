# Final report: bidirectional English–Bengali speech translation

## Outcome

**Implementation complete and regression-tested. Acceptance validation remains blocked.** Offline Guru now routes finalized speech in either Bengali↔English direction to the matching local translation and Piper voice models, with turn-stable auto language routing, direction-aware captions, legacy client compatibility and bounded work queues. This does not establish the requested real-world simultaneous bilingual accuracy/latency targets on the i5-7300U.

## Implementation

- Direction mapping supports explicit `en`, `bn` and optional `auto`; auto route locks after stable script evidence for a VAD turn.
- Whisper receives per-decode language selection; translation dispatches to Marian EN→BN or OPUS BN→EN; Piper selects matching English or Bengali target voice.
- Canonical source/translation/language fields are additive. Existing `en`/`bn` segment and client/event payload shapes remain compatible.
- Teacher/student UIs show source text, target translation and target-language audio.
- Existing session auth/scope, classroom WebSocket delivery and queues are retained.
- Six integration regressions (capture validation order/availability and TTS cache API compatibility) were repaired; targeted tests passed.
- Both directional translators and local Piper voices were actually loaded and used offline. NLLB was not used as default and no network inference was introduced.

## Tests

Fresh full suite: **250 passed; 0 failures/errors/skips** (`engineering_harness/artifacts/pytest_final.json`, source digest `61097712830c018a`). New direction/router/pipeline tests and existing HTTP/auth/storage/document/WebSocket tests are included.

## Measurements available (diagnostic only)

| Component | English→Bengali | Bengali→English | Limitation |
|---|---|---|---|
| Translation, 1 timed invocation/phrase | Marian CT2 int8; chrF2 .5923; p50/p95 335.73/1047.04 ms | OPUS CT2 int8; chrF2 .6377; p50/p95 239.74/495.19 ms | 26 author-written reference pairs; no independent human ratings; host contention. |
| Translation, 2 timed invocations/phrase | chrF2 .5923; p50/p95 378.78/1837.97 ms | chrF2 .6377; p50/p95 224.09/414.20 ms | N=52 per route; high variability under contention. |
| Piper phrase synthesis, 1 invocation/phrase | p50/p95 622.33/1569.74 ms; mean RTF .247 | p50/p95 980.58/1902.78 ms; mean RTF .290 | Not first audio; voice-only, not e2e. |
| Piper phrase synthesis, 2 invocations/phrase | p50/p95 603.63/2588.18 ms; mean RTF .270 | p50/p95 783.34/2406.73 ms; mean RTF .266 | N=52; outlier tails shift between runs. |
| ASR | Historical English Base WER .0564 on project VAD path | No human Bengali speech/reference | Historical English WER is not the new bidirectional streaming integration. |

The existing text references are author-written rather than native-speaker reviewed. Benchmark ran while a local server used about 800 MB RSS/~150% CPU; sampled CPU often saturated and swap was about 2.1 GB. The host is not confirmed to be an i5-7300U. Metrics are contaminated and cannot support target-device acceptance.

## Acceptance checklist

| Requirement | Status | Finding |
|---|---|---|
| WER <20% in both directions | **BLOCKED** | No Bengali human-speech references; English references not independently verified; no fresh comparable streaming WER. |
| Translation rating >=4/5 | **BLOCKED** | No independent bilingual reviewer panel. chrF2 is only a proxy metric. |
| First translated audio p50<2s, p95<4s | **NOT MEASURED** | No compatible end-to-end streaming benchmark. |
| No finalized-segment loss | **UNIT TESTED; sustained load not measured** | Queue regression tests pass; no bilingual high-concurrency target-host test. |
| Bounded queues | **CODE + REGRESSION TEST PASS** | Dynamic sustained-load memory/queue growth not measured. |
| Memory/resources fit target | **NOT ESTABLISHED** | Host contention/swap and hardware mismatch. |
| No severe regression both directions | **BLOCKED** | No independent held-out two-direction speech set. |
| Model/repository/license research | **DOCUMENTED** | NLLB is non-commercial; Piper voice-data terms must be observed/confirmed. |

## Harness status

- Phase 2: **FAIL**—legacy P0 findings F-01, F-02, F-03 and F-07 remain open; no independent phase-2 signoff.
- Phase 3: **FAIL/BLOCKED**—standardized baseline-comparable ASR/translation/latency/resource/load/reproducibility artifacts and signoffs are missing.
- Gates were not weakened and unsupported QA/evaluator approval was not recorded.

## To complete acceptance

1. Supply authorized, disjoint, independently verified 20-minute Bengali + 20-minute English + 10-minute code-switch speech plus noisy/fast examples, human translations and blind bilingual review.
2. Run both directions on the declared i5-7300U (or identify actual hardware) under adequate isolated memory headroom. Compare Base/Small and 0.5/0.75/1/1.5/2-second chunks, pinned overlap/VAD/thread settings, repeated after warmup.
3. Record per-direction Bengali-aware WER/CER, blind adequacy/fluency, ASR/MT/TTS and end-of-phrase→first-audio latency, RTF, peak RSS/swap/CPU, queue depth/drops, ordering errors and 30-student delivery.
4. Resolve legacy harness P0 findings; obtain independent QA/evaluator review before claiming gate pass.

## Reports and data

- `reports/BIDIRECTIONAL_REPOSITORY_RESEARCH.md`
- `reports/BIDIRECTIONAL_MODEL_COMPARISON.md`
- `reports/BIDIRECTIONAL_ASR_EVALUATION.md`
- `reports/BIDIRECTIONAL_TRANSLATION_QUALITY.md`
- `reports/BIDIRECTIONAL_TTS_EVALUATION.md`
- `reports/BIDIRECTIONAL_BENCHMARK.md`
- `reports/BIDIRECTIONAL_INTEGRATION_REPORT.md`
- `reports/BIDIRECTIONAL_HARNESS_STATUS.md`
- `reports/results/bidirectional_benchmark.json`
- `reports/results/bidirectional_benchmark_repeat2.json`
- `reports/results/bidirectional_asr_evaluation.json`
