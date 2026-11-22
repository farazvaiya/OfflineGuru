# Agent Runtime Guide

**Date**: 2026-11-14
**Module**: `engineering_team/harness/agent_runtime.py` (Phase 5)

## What it is

A real subprocess execution adapter for agent labour. It replaces the
previous `pending_external`-only behaviour with actual execution, while
keeping every safety rule from the master prompt.

## Configuration

| Control | How | Default |
|---|---|---|
| executable | `AgentExecution(executable=...)` or env `TEAM_AGENT_RUNTIME` | `claude` (refused if it resolves to Claude — see below) |
| timeout | ctor `timeout_s` | 120 s |
| CPU limit | ctor `cpu_limit_s` (RLIMIT_CPU) | 300 s |
| memory limit | ctor `mem_limit_mb` (RLIMIT_AS) | 2048 MB |
| allowed roots | ctor `allowed_roots` | current working directory |

## Usage

```python
from engineering_team.harness.db import Database
from engineering_team.harness.agent_runtime import AgentExecution

db = Database("state/engineering_team.db")
rt = AgentExecution(db, executable="python3", timeout_s=60,
                    cpu_limit_s=120, mem_limit_mb=1024,
                    allowed_roots=["/home/faraz/project/offline_guru"])

av = rt.available()                 # availability check + version probe
assert av["available"]

res = rt.run("asr-agent",
             task={"id": "T3"}, problem_id="p1",
             command=["-c", "print('inspect only')"])
# res: status completed|failed|timeout|pending_external, exit_code,
#      stdout_tail, stderr_tail, elapsed_s, argv, run_id
```

## Safety rules (enforced, tested)

1. **No recursive Claude**: any executable whose name contains `claude` is
   refused at construction.
2. **argv only**: no shell; no string injection.
3. **Path containment**: path arguments must resolve inside `allowed_roots`
   or the run is refused (recorded as `failed` with the reason).
4. **Bounded capture**: stdout/stderr capped at 64 KiB; a hung child is
   killed at `timeout_s`.
5. **Honest status**: `pending_external` only when no command was given;
   `available()` must be checked before claiming an agent ran.
6. **Resource limits**: RLIMIT_CPU / RLIMIT_AS on POSIX; best-effort elsewhere.

## What agents may do

- Read the repository, tests, logs, reports.
- Run the existing test suite.
- Write inside their isolated candidate workspace (Phase 6).
- Produce findings, hypotheses, benchmark records.

## What agents may NOT do

- Approve, integrate, merge, or modify production code.
- Fabricate results (benchmark engine refuses missing raw artifacts).
- Launch further Claude instances.
- Touch anything outside `allowed_roots`.

## Offline note

The runtime itself is offline. Whether an *agent* needs internet depends on
the executable chosen; record it in the run's evidence and label it. The
default `claude` executable is refused, so an offline-safe runtime must be
configured explicitly.
