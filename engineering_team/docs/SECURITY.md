# Security and Safety

## Principles
- **Protect the existing project:** preserve Offline Guru features, data, and interfaces unless explicitly authorized to change them.
- **Human-in-the-loop for irreversible actions:** integration/merge into protected baseline requires explicit human approval (`APPROVED` state). No auto-merge.
- **Evidence over claims:** tests/benchmarks must be actually run and recorded; physical tests are never fabricated.
- **Least privilege and isolation:** experiments run in isolated branches/worktrees or snapshots; checkpoints before risky changes.

## Controls
- **Untrusted inputs/code:** treat external repositories, downloaded code, and dependencies as untrusted. Inspect source and licenses before use; do not execute unreviewed remote scripts automatically.
- **Secrets:** never commit API keys, tokens, credentials, or private data. Audit logs/artifacts for accidental leakage.
- **Data safety:** do not reset or overwrite user databases. Checkpoint/rollback covers declared source paths, not arbitrary user data deletion.
- **Resource safety:** default sequential execution, low concurrency, avoid duplicate heavy model loads; queue/stop when memory pressure is unsafe (7GB RAM target).
- **Auditability:** workflow transitions, agent runs, decisions, and integration actions are recorded in SQLite and logs; reports cite evidence/limitations.

## Threat/abuse considerations
- Malicious or vulnerable OSS candidates (Track B): require source inspection, dependency review, license check, and offline/hardware suitability before recommendation/integration.
- Misleading metrics: independent evaluator verifies methodology fairness and raw evidence.
- State corruption/interruption: atomic workflow writes, DB transactions/migrations, resumable state, and checkpoints.

## Destructive operations
Require explicit human authorization/confirmation: pushes, resets, branch deletions on protected refs, data purges, deployment actions, and any irreversible integration. The CLI `integrate` records intent and does not auto-merge.
