# Evaluator Decision — Phase 1

**Evaluator:** evaluator-agent
**Date:** 2026-11-10
**Basis:** `reports/GATE_PHASE1.json`, `state/findings.json`, `reports/QA_PHASE1.md`, artifacts under `engineering_harness/artifacts/`

---

## 1. Criteria evaluation

| # | Criterion | Evidence | Verdict |
|---|---|---|---|
| 1 | Architecture inspected | `PHASE1_REPOSITORY_AUDIT.md`, `ARCHITECTURE_MAP.md`; every core module named; 6761 LOC inventoried | PASS |
| 2 | Existing tests and benchmarks evaluated | 239 tests pass; WER, latency, translation, TTS, 30-client loopback all reproduced and stored in `baselines/` | PASS |
| 3 | Major problems have evidence | 9 findings, 100 % of cited artifact paths exist | PASS |
| 4 | Root causes identified or explicitly unresolved | 8 root-caused; F-09 measured with the optimum left open on purpose | PASS |
| 5 | Competing hypotheses tested | 4 hypotheses rejected with data: GIL starvation, VAD false rejection, LocalAgreement withholding, cross-speaker generalisation | PASS |
| 6 | Prioritised repair plan | P0/P1/P2 in `PHASE1_FINAL_REPORT.md`, each item tied to a finding id | PASS |
| 7 | QA verified independently | `QA_PHASE1.md`: numbers re-derived from artifacts, probes re-run, one error caught | PASS |
| 8 | Evaluator approval | this document | PASS |

## 2. Judgement on the headline claim

The brief asked whether cross-speaker WER could be explained "simply by a different speaker" and warned against assuming so. The evaluator confirms the stronger conclusion: the 95.6 % figure measured the wrong reference, and the wrongness is reproducible to three decimals. An explanation was published for a number that measured something else. Withdrawing it is the correct action; replacing it with a fabricated true WER would be worse, and the reports do not do that.

The evaluator also notes that the *in-domain* 5.6 % WER was accurate but **unrepresentative**: it was measured on a code path the classroom does not use. That is a measurement-validity issue, not a lie, and it is now documented in the architecture map and in F-03.

## 3. Disagreements and conditions

1. **F-09 severity.** Recorded as P2. Acceptable, because it is a cost/latency issue, not a correctness one — but it must not be used in Phase 3 to "improve latency" by decoding less audio than the accuracy gate allows. Condition: Phase 3 must report WER alongside any cadence change.
2. **Baseline representativeness.** The latency baseline is a 45 s file with 15 phrases. p95 at n=15 is effectively the maximum. Condition: Phase 3 must repeat each latency measurement at least twice and report spread, which the acceptance criteria already require (`repeat_spread_p95_pct: 25`).
3. **Emma absolute WER.** Not established and must not be published. Condition: any future accuracy claim on that clip requires a human-verified transcript stored in the repository.

## 4. Risk assessment for Phase 2

| Risk | Mitigation |
|---|---|
| Fixing F-03 changes every downstream measurement | Re-baseline latency/translation/TTS **after** the fix and record them as Phase 3 baselines; never compare pre-fix latency to post-fix accuracy |
| The F-03 fix changes `LocalAgreement` reset semantics and could duplicate words | Regression test must assert no duplicated words at a pause boundary, not just no lost audio |
| Capture restart logic could spawn duplicate arecord processes | Test asserts at most one live child after repeated failures |
| Widening the window retention could grow memory | Gate on peak RSS ≤ 1400 MB; the window is already capped at 15 s |
| Changing `eval_asr.py` could hide the historical number | Keep `data/eval/asr.json` untouched as the historical record; add the corrected evaluation beside it |

## 5. Decision

**PHASE 1 APPROVED.**

Reason: all eight exit criteria are met with machine-readable evidence, the QA review independently re-derived the numbers and corrected one, and the limits of the environment are documented rather than papered over. The three P0 findings are sufficient justification to proceed to repair.

Approval applies to the source digest recorded in `state/signoffs.json`. If application code changes for any reason other than the approved Phase 2 fixes, this approval must be invalidated and Phase 1 re-run — the orchestrator enforces this by invalidating downstream signoffs on a rollback.
