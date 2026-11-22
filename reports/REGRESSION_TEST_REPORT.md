# Regression test report

**Objective:** prove the suite is green after the Phase 6 / Phase 8 changes, with
the commands, exit codes and counts recorded rather than asserted.

**Environment:** Python 3.14.7 in `.venv`, 4-thread CPU, no network required.

## Commands and results (final run)

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `.venv/bin/python -m pytest -q` | 0 | **211 passed**, 0 failed |
| 2 | `.venv/bin/ruff check --no-cache guru scripts tests` | 0 | All checks passed |
| 3 | `.venv/bin/python -m py_compile guru/*.py guru/education/*.py guru/server/*.py scripts/*.py` | 0 | OK |
| 4 | `node --check guru/server/static/teacher.js` | 0 | OK |
| 5 | `node --check guru/server/static/student.js` | 0 | OK |
| 6 | `.venv/bin/python scripts/verify_offline.py --seconds 15` | 0 | PASS — models loaded, pipeline ran, **zero external connections** |
| 7 | `scripts/browser_qa.py --server http://127.0.0.1:8011` | 0 | 22 checks passed |
| 8 | `scripts/loadtest.py --clients 1,2,5,10,20,30` | 0 | all green, 0 drops |

Baseline before this mission was **187 passing**. Final is **211**: +24 tests,
none deleted, none weakened.

## New coverage added in this mission

| File | Tests | What it pins |
|---|---|---|
| `tests/test_retention.py` | 7 | expired ended sessions purged, active kept, retention disabled honoured, background loop purges without restart, loop cancellable, full data purge (segments/documents/chunks) |
| `tests/test_bilingual_retrieval.py` | 7 | Bangla query hits an English slide, citation still quotes English, English path unchanged, chunks without a Bangla side behave as before, empty query/corpus, SQLite round-trip |
| `tests/test_grading_eval.py` | 10 | correct answers EN/BN, word order, Bangla inflection, empty answer not marked wrong, too-short → review, unrelated → wrong, paraphrase limitation pinned |

No existing test was modified except where behaviour deliberately changed (none
was required this time). No test was skipped or marked xfail.

## Failures encountered during the work, and how they were resolved

1. **Syntax error in `guru/server/app.py`** after inserting the upload
   re-indexing block (two statements merged onto one line). Caught by pytest
   collection, fixed, suite re-run — this is exactly why the checks were re-run
   after each edit.
2. **`matched_terms` reported the English rubric term when the Bangla variant
   matched.** Fixed in `grading.py`: the term that actually matched is the one
   recorded.
3. **Two scaffolded expectations were wrong, not the code.** The DPR viewport
   emulation silently did nothing (CSS viewport stayed 780 px), and an assumption
   that a pure paraphrase would land in `needs_review` was incorrect — it is
   scored wrong by design. Both were corrected to state the measured truth
   instead of forcing a pass.

## Not covered by the suite

Physical devices, radio behaviour, real classroom audio, thermal soak, and other
browsers. These are listed as BLOCKED in the final verification report and are not
claimed as tested.
