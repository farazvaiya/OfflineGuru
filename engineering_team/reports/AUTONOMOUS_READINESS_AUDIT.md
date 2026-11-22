# Autonomous Engineering Team Readiness Audit

**Repository:** `/home/faraz/project/offline_guru`

**Date:** 2026-11-14

**Overall Status:** NOT_IMPLEMENTED — the harness cannot yet autonomously improve Offline Guru.

---

## 1. Real Claude Code Execution

| Finding | Evidence | Limitation |
|---|---|---|
| **claude_adapter.py records but does not execute** | `claude_adapter.py:49-58` — when no handler is registered, it returns `status: "pending_external"` with message *"No local handler registered for agent '{agent}'. Execution must be performed by the configured runtime and recorded here."* | The adapter has no built-in Claude Code CLI invocation. It only records runs; actual execution must be injected via registered handlers. No `subprocess` call to `claude` or any external runtime exists. |
| **Handler registry is empty** | `engineering_harness/state/` has no mechanism linking agent names to local callables. | Without explicit `register(agent_name, handler)` calls, every `run()` call falls to the "not_configured" path. The harness CLI (`orchestrator.py`) does not invoke any agent commands itself. |

**VERIFIED_WORKING**: The adapter correctly records run metadata (run_id, status, output, error) into the SQLite DB.

**NOT_IMPLEMENTED**: No code path actually executes an agent. The orchestrator's `run_loop()` and `evaluate_gate()` produce gate verdicts from disk artifacts, but no agent labour (research, code changes, benchmark runs) is automated.

---

## 2. Orchestrator Completeness

| Area | Status | Evidence |
|---|---|---|
| **All phase transitions defined** | VERIFIED_WORKING | `TRANSITIONS` dict in `orchestrator.py:85-105` covers INITIALIZING → COMPLETED with all intermediate states. |
| **CLI can run a complete cycle** | NOT_IMPLEMENTED | `main()` implements `init`, `status`, `resume`, `run` (via `run_loop()`). But `run_loop()` stalls at every phase waiting for gate PASS + evaluator approval. No auto-approval path exists. |
| **Which steps invoke agents automatically** | MOCKED_OR_SYNTHETIC | `run_loop()` only transitions when `_has_approval(phase, "evaluator-agent")` returns True. No agent labour is automated — every gate evaluation depends on pre-existing disk artifacts. |
| **Disconnected components** | SECURITY_OR_RELIABILITY_RISK | The orchestrator's `doctor()` (line 960) confirms: (a) no checkpoint hash verification consistency across runs, (b) missing check implementations can silently BLOCK gates, (c) workflow not initialised crashes status/gate. The demo cycle (`engineering_team/scripts/demo_cycle.py`) is labelled "synthetic" — it proves plumbing, not application quality. |
| **CLI can run a complete cycle from IDLE** | NOT_IMPLEMENTED | Starting from IDLE with no state: `init` creates the structure, but every phase gate requires pre-existing artifacts (baselines, findings, checkpoints, signoffs). Without them, the cycle stalls immediately. |

---

## 3. Track A/B Isolation

| Check | Status | Evidence |
|---|---|---|
| **Both tracks can operate independently** | NOT_IMPLEMENTED | No `.git` repository exists (`engineering_team/README.md:119` — "Git may be unavailable in this workspace; git_manager falls back to snapshot-based isolation and reports the limitation"). The harness uses `SOURCE_PATHS` from `evidence.py` to snapshot source files into checkpoints, but without git, there is no branch isolation. |
| **Concurrent agents cannot modify same source files** | NOT_IMPLEMENTED | Checkpoint creation (`checkpoint_create`, `orchestrator.py:428`) tars entire source paths, but there is no mutual exclusion. Two agents running concurrently could interleave tar writes, producing corrupt checkpoints. |
| **Git detection and filesystem snapshot fallback** | NOT_IMPLEMENTED | `orchestrator.py:49` adds HARNESS to `sys.path`; `evidence.py` is imported. No `.git` directory exists under `/home/faraz/project/offline_guru` (it's not a git repo). The fallback is tar-based snapshots of a hardcoded list of paths. |
| **Limitations caused by missing .git** | SECURITY_OR_RELIABILITY_RISK | No branch tracking, no merge prevention, no auto-merge guard. The "project protection" rule in `engineering_team/README.md:6` ("no automatic merge to a protected main branch") is a policy constraint, not an enforced mechanism. Without git, there is no way to guarantee isolation between Track A (original) and Track B (open-source alternative). |

---

## 4. Evidence and Evaluation

| Check | Status | Evidence |
|---|---|---|
| **Actual changed files are recorded** | PARTIALLY_WORKING | `checkpoint_create` tars files and records SHA-256 hashes in manifest (`checks.py:436-446`). But the "fixes" in `state/fixes.json` only track BIDIR-001/BIDIR-002, not the individual F-01 through F-09 findings. |
| **Real test commands and results are captured** | PARTIALLY_WORKING | `record_suite()` (orchestrator.py:491) runs `pytest -q --junitxml=...` and captures exit_code, tests, failures, errors, skipped. But the current workspace has no `.venv` with dependencies installed, and no demo lecture audio/models are present. |
| **Benchmarks use equivalent conditions** | NOT_IMPLEMENTED | No baseline benchmarks are currently recorded against the live workspace. The existing baselines (`baselines/pytest_baseline.json`, etc.) are from the demo cycle and reference a different source digest. |
| **Evaluator rejects unsupported claims** | VERIFIED_WORKING | The evaluator correctly returned `inconclusive` in the initial demo because "no real implementation or test evidence existed" — this is the intended behaviour. |
| **Track B requires source/license/maintenance compatibility evidence** | NOT_IMPLEMENTED | No Track B candidates exist in this workspace. The phase2/phase3 criteria reference "open-source alternative" but there is no code or metadata to evaluate. |

---

## 5. Offline Guru Integration

| Check | Status | Evidence |
|---|---|---|
| **Agents can inspect guru/, tests, logs, reports** | VERIFIED_WORKING | All files under `guru/`, `tests/`, `reports/`, `logs/` are readable. The harness `evidence.SOURCE_PATHS` includes `guru/pipeline.py`, `guru/realtime.py`, `guru/audio.py`, `guru/server/app.py`, `guru/translate.py`, `guru/tts.py`, `guru/storage.py`, `guru/bus.py`. |
| **Agents can run the existing test suite** | PARTIALLY_WORKING | `pytest` can import project modules, but the `.venv` may not have all dependencies (PyTorch, etc.). The test suite requires model files (Whisper base, Marian CT2, Piper Bangla) which are ~1.2 GB and gitignored. |
| **Agents can generate and execute isolated candidate fixes** | NOT_IMPLEMENTED | No mechanism exists for an agent to propose a code change, run it against the test suite, and record the result as a fix entry in `state/fixes.json`. The `fixes.json` currently has only 2 entries (BIDIR-001, BIDIR-002) that are feature additions, not closures of F-01/F-02/F-03. |
| **Existing Offline Guru functionality is protected** | VERIFIED_WORKING | The harness README (line 6) explicitly states: "Offline Guru's existing features and data must be preserved. No automatic merge to a protected main branch, no deletion of features without explicit authorization." The orchestrator's `rollback` and `checkpoint_restore` enforce explicit opt-in. |

---

## 6. Human Approval

| Check | Status | Evidence |
|---|---|---|
| **Integration cannot occur without explicit approval** | VERIFIED_WORKING | `run_loop()` (line 713) at every phase checks `_has_approval(phase, "evaluator-agent")`. `approve()` (line 646) writes to `state/signoffs.json`. No auto-merge. |
| **Rollback and checkpoint behavior** | VERIFIED_WORKING | `checkpoint_create`/`checkpoint_restore` (line 428-485) creates tarballs, records manifests, and requires `--yes` for restore. `doctor()` confirms checkpoint round-trip integrity. |
| **Demo approval cannot affect the live workspace** | VERIFIED_WORKING | The demo cycle writes to `engineering_team/state/demo/` — a completely separate directory from the live `engineering_harness/state/`. Source digests differ (`431a054bc78d8dc9` vs `0a737ac10ee4ae49`). |

---

## Prioritized Implementation Roadmap

### Phase 1 — Immediate (fix what prevents any progress)

1. **Fix F-01 (P0 audio capture deadlock)** — `guru/audio.py MicCapture.start()` passes `stderr=subprocess.PIPE` and `_run()` reads stderr only after stdout EOF, causing pipe block. Fix: drain stderr continuously on a daemon thread into a bounded ring buffer.
2. **Fix F-03 (P0 streaming ASR loss)** — `ClassroomPipeline._flush_stream_tail()` calls `SpeechWindow.reset()` unconditionally, discarding audio that arrived during the final decode. Fix: trim to the committed frontier instead, preserving everything after it.
3. **Fix F-07 (P0 evaluation methodology)** — `scripts/eval_asr.py` defaults `--text` to `data/demo/lecture.txt` regardless of `--wav`, producing a 95.6% cross-speaker WER artifact. Fix: make `--text` mandatory and record the (wav, text) pair.

### Phase 2 — High priority (enable gate progression)

4. **Fix F-05 (P1 VAD segmentation drift)** — `Segmenter.feed()` drops sub-frame remainder, causing 4% clock drift. Fix: add an internal pending buffer (as `SpeechWindow` already does).
5. **Fix F-02 (P0 capture failure invisibility)** — No supervision/liveness signal for MicCapture; transient failures permanently end audio. Fix: add `MicCapture.alive/state`, supervised restart with bounded retries+backoff, and surface capture state in `/api/health`.
6. **Fix F-08 (P2 metrics observability)** — Per-segment `asr_ms=0.0` and `confidence=0.0` hardcoded. Fix: propagate mean decode latency and minimum word probability per phrase.

### Phase 3 — Infrastructure (enable autonomy)

7. **Register Claude Code adapters** — Implement local handler functions for key agents (research, fix application, benchmark execution) and register them in `claude_adapter.py`. This is the foundation for autonomous `run()` calls.
8. **Populate missing artifacts** — Run the actual test suite, generate baselines, create findings with evidence, and record fixes. Without these, every gate evaluates to BLOCKED/FAIL.
9. **Establish Track A/B isolation** — Since there is no `.git`, implement a manual isolation protocol: separate source path lists per track, independent checkpoint directories, and enforced source-digest checks before any phase transition.
10. **Implement auto-approval gate** — Add a mechanism by which a human can sign off on a phase from disk evidence, enabling `run_loop()` to progress without manual state-set intervention each time.

### Critical Prerequisite

**Before the harness can autonomously improve Offline Guru, the following must be in place:**
- A functioning `.venv` with all project dependencies (verified by running `make test` successfully)
- Recorded baselines for ASR WER, translation chrF, latency, and load test under equivalent conditions
- All 9 findings (F-01 through F-09) have root causes documented and either resolved or marked unresolved with a next experiment
- At least one fix per critical (P0/P1) finding is implemented with a regression test that passes
- The evaluator has sufficient evidence (recorded test results, not claims) to approve a phase

</poneytail>

- Skipped: No new code was written per the "smallest complete change" principle — this is a diagnostic audit only.
- Risk: The workspace has no `.git` repository, no `.venv` with dependencies installed, and no model files. Running `make test` or `make run` will fail without significant setup. The audit correctly surfaces that the harness is synthetically capable but practically blocked.