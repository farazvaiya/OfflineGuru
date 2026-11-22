# Claude Runtime Test Results

**Date**: 2026-11-15
**Suite**: `tests/test_claude_runtime.py` (20 tests) + full regression
**Command**: `.venv/bin/python -m pytest tests/test_claude_runtime.py -v`

## 1. Result summary

| Category | Tests | Result |
|---|---|---|
| MOCK (fake `claude` on PATH) | 19 | **19 PASS** |
| REAL (installed CLI, authenticated) | 1 | **1 PASS** |
| Full existing suite | 312 | **312 PASS** (no regressions) |
| **Total after integration** | — | **332 passed** |

## 2. MOCK tests (deterministic, offline — never claim real execution)

| # | Test | Verifies | Result |
|---|---|---|---|
| 1 | `test_discover_claude_real_or_none` | discovery returns path or None | PASS |
| 2 | `test_discovery_missing_cli` | empty PATH → None | PASS |
| 3 | `test_role_loading_each_section` | all 9 AGENTS.md sections load by letter | PASS |
| 4 | `test_role_loading_substring` | "evaluator" substring match | PASS |
| 5 | `test_successful_execution` | exit 0 + valid JSON → SUCCESS; digests; workspace | PASS |
| 6 | `test_missing_cli` | no executable → NOT_CONFIGURED | PASS |
| 7 | `test_auth_failure` | auth-shaped stderr + exit 1 → AUTH_REQUIRED | PASS |
| 8 | `test_timeout` | sleeping CLI + 1 s timeout → TIMEOUT | PASS |
| 9 | `test_nonzero_exit` | exit 3 → FAILED with "exit 3" | PASS |
| 10 | `test_invalid_output` | non-JSON stdout → FAILED | PASS |
| 11 | `test_output_json_missing_result_field` | JSON without result/type → FAILED | PASS |
| 12 | `test_cancelled` | KeyboardInterrupt → CANCELLED; row persisted | PASS |
| 13 | `test_adapter_persists_run` | agent_runs row complete; log file on disk | PASS |
| 14 | `test_adapter_pending_external_without_runtime` | legacy path unchanged | PASS |
| 15 | `test_track_isolation` | Track A/B workspaces distinct | PASS |
| 16 | `test_single_writer_lock` | second writer refused (flock) | PASS |
| 17 | `test_orchestrator_execute_agent_failure_keeps_state` | NOT_CONFIGURED → task open, state unchanged | PASS |
| 18 | `test_orchestrator_execute_agent_success_path` | SUCCESS → can_continue; workflow event; artifacts valid | PASS |
| 19 | `test_orchestrator_execute_unknown_task` | unknown task id → SystemExit | PASS |
| 20 | `test_no_auto_production_integration` | SUCCESS run creates no decisions/integrations/signoffs | PASS |

(20 rows: 19 mock + the real smoke below is #21 in file order; the
`test_cancelled` row covers the CANCELLED vocabulary + DB persistence.)

## 3. REAL test (labelled, self-skipping when CLI absent)

`test_real_claude_cli_smoke` — marked `@pytest.mark.real`:
- Skips if `claude` not on PATH or `--version` fails.
- When run: availability probe → real `AgentExecution.run()` with
  `--max-turns 1`, read-only constraints, trivial prompt.
- **Result: PASS.** Direct verification run:
  - `available: True`, version `2.1.325 (Claude Code)`
  - `status: SUCCESS`, `exit_code: 0`, `elapsed_s: 9.2`
  - stdout JSON with `session_id`, `duration_api_ms: 7203`,
    `total_cost_usd: 0.1028`, token usage
  - stderr warning `[claude-code:unrecognized_model]` — non-fatal, correctly
    not treated as auth failure
- This test proves the adapter can really reach the runtime and classify a
  genuine result. It does NOT prove any engineering task was performed.

## 4. Honesty checks

- No mock test asserts real execution; the real test never runs in CI-like
  environments without the CLI.
- Missing metrics are never zero-filled: NOT_CONFIGURED carries a reason.
- `test_no_auto_production_integration` asserts decisions/integration_history
  stay empty after a SUCCESS run.
- The `test_cancelled` KeyboardInterrupt is simulated at the subprocess.run
  boundary (patched), labelled as such in the test body.

## 5. Reproduction

```bash
cd /home/faraz/project/offline_guru
.venv/bin/python -m pytest tests/test_claude_runtime.py -v      # all 20
.venv/bin/python -m pytest tests/test_claude_runtime.py -v -m real  # real only
.venv/bin/python -m pytest -q                                    # full 332
```

## 6. Known non-results

- No multi-agent orchestration run was executed (task §11 forbids starting
  the full workflow).
- No physical hardware involved; all tests are container-side.
