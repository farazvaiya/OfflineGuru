# Physical Test Acceptance Criteria

**Date**: 2026-11-14
**Binding rule**: a scenario passes ONLY when actually executed on the target
hardware with the required evidence. Simulation, stub data, or "the code
looks right" is never a pass. Missing evidence stays `NOT_EXECUTED`.

## General (all phases)

| ID | Criterion | Evidence |
|---|---|---|
| AC-0 | No production file (`guru/`, `config/`) modified by monitoring work | source digest unchanged |
| AC-1 | Session lifecycle works end-to-end via CLI | Phase 1 tests |
| AC-2 | Session data persists across process restart | Phase 1 tests |
| AC-3 | Invalid lifecycle transitions rejected | Phase 1 tests |
| AC-4 | Concurrent session operations safe | Phase 1 tests + DB locking |
| AC-5 | Telemetry collected read-only from real app | Phase 2 tests |
| AC-6 | Missing metrics render as unavailable, never zero | Phase 2/3 tests |
| AC-7 | Collector failure never crashes the app | Phase 2 loop tests |
| AC-8 | Dashboard live from telemetry DB; local-only | Phase 3 tests |
| AC-9 | Validator outcomes exactly PASS/FAIL/INCONCLUSIVE/NOT_EXECUTED | Phase 4 tests |
| AC-10 | No PASS without required evidence | Phase 4 tests |
| AC-11 | Agent runs execute via real configured runtime or are labelled pending | Phase 5 tests |
| AC-12 | No recursive Claude launches | Phase 5 refusal test |
| AC-13 | Track A/B candidates isolated; concurrent mutation detected | Phase 6 tests |
| AC-14 | Benchmarks only compared under equivalent conditions | Phase 7 gate |
| AC-15 | No fabricated raw artifacts | Phase 7 refusal test |
| AC-16 | Integration requires explicit human decision | Phase 8 gate |
| AC-17 | Rollback checkpoint exists before any integration | Phase 8 test |
| AC-18 | Historical comparison refuses materially different conditions | Phase 9 test |
| AC-19 | Next-test plan derives from evidence gaps, not ambition | Phase 9 test |
| AC-20 | Full existing suite green after all changes | regression run |

## Physical scenarios (require human execution)

| ID | Scenario | Pass condition |
|---|---|---|
| AC-P1 | A microphone startup/continuity | capture active > 5 min, no silent stall |
| AC-P2 | A interruption/recovery | error surfaced, restart recovers |
| AC-P3 | B bidirectional short utterances | WER vs reference within tolerance |
| AC-P4 | B final words at lecture end | last utterance fully transcribed |
| AC-P5 | C student audible playback | student ack recorded |
| AC-P6 | D 30 physical students over hotspot | zero drops on RF, not loopback |
| AC-P7 | E background noise | WER delta within tolerance |
| AC-P8 | F 45-minute session | CPU/RAM stable, no error accumulation |

All AC-P items are `NOT_EXECUTED` until a human runs them on the i5-7300U
target laptop with the hotspot and consenting participants.
