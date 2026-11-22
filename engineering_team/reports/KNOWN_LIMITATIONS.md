# Known Limitations and Future Improvements

## Known limitations

1. **End-to-end autonomous closure not fully demonstrated:** The harness implements the required workflow, persistence, gating, and reporting, but a complete cycle (discover a new real issue → RCA+baseline → Track A & B implementations → equivalent benchmarks → independent evaluation → decision report → human approval → merge approved candidate → regression → new baseline) was not executed to completion in this pass. External agent/runtime labor and measurable candidate work are needed per problem.
2. **No git repository:** This workspace is not a git repo; `GitManager` cannot create real branches/worktrees. Isolation relies on filesystem checkpoint snapshots (implemented) and recorded metadata. Future: operate in a git clone to enable true worktree isolation.
3. **Execution adapter is pluggable:** `ClaudeCodeAdapter` records runs and supports registered local handlers but cannot autonomously perform deep coding/research without a connected execution runtime. "Pending external" runs are recorded honestly.
4. **Hardware/physical validation constraints:** Host is not the target classroom laptop and lacks microphone/30 physical devices/RF environment. 30-student results, thermal 45-min behavior, live mic accuracy, and real hotspot capacity require physical/human testing and must not be fabricated.
5. **Benchmark comparability depends on datasets:** Reproducible, equivalent-condition benchmarks (ASR WER/CER, translation quality, TTS, e2e latency p50/p95, resources, reliability, load) require curated datasets and controlled runs (e.g. demo WAVs, simulated clients). Some metrics need human-verified references or on-device measurement.
6. **Track B research depth:** OSS discovery/evaluation (license, maintenance, CVEs, CPU/RAM, offline fit) must be performed with source-level inspection; automated web research is not embedded as a guaranteed capability in this core package.
7. **Integration is guarded/manual:** To protect the baseline, `integrate` records intent/checkpoints and does not auto-merge diffs; applying and verifying an approved candidate remains an explicit, auditable step.

## Future improvements

- **True worktree-per-experiment flow:** integrate git worktree lifecycle (create/run/compare/cleanup) when running inside a git repo; enforce "same baseline" diffs.
- **Richer agent runtime integration:** wire handlers/adapters to an approved coding/research runtime for automated discovery, RCA, implementation, and test execution with captured logs/artifacts.
- **Standard benchmark suite:** packaged datasets and scripts for ASR/translation/TTS/e2e/load (simulated) with fixed configs, producing artifacts directly consumable by `BenchmarkEngine`/evaluator.
- **Automated regression gate:** run targeted/full pytest and selected benchmarks on integration and block completion on regressions vs recorded baseline.
- **Report/UI enhancements:** richer HTML/markdown dashboards, diff viewers, and structured physical-test submission forms linked to problems/experiments.
- **Policy hardening:** secret scanning on artifacts, dependency/license allowlists for Track B, and stricter protected-path rules.
- **Resilience:** periodic auto-checkpoints, disk-space/resource pressure guards, and clearer recovery commands.
- **Multi-problem scheduling:** priority queue, concurrency limits per resource profile, and conflict detection across overlapping fixes.
