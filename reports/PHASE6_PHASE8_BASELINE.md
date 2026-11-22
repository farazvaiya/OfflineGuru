# Phase 6 / Phase 8 — baseline audit

**Date:** 2026-11-02
**Repository:** `offline_guru` (no `.git` directory present — nothing to preserve or branch from; all work is in the working tree)
**Purpose:** establish what existed before any change in this mission, so every later
claim can be compared against a recorded starting point.

## 1. Environment actually available

| Item | Value | Source |
|---|---|---|
| CPU | 4 threads | `nproc` |
| RAM | 7 GB total, ~2.0–2.9 GB available | `free -g` |
| Disk free | 84 GB | `df -h .` |
| Python | 3.14.7 in `.venv` | `.venv/bin/python -V` |
| Browser | **Firefox** (`/usr/bin/firefox`) + **geckodriver** (`/snap/bin/geckodriver`) | `which` |
| Playwright / Selenium | **not installed** | import check |
| Node | present (`/usr/local/bin/node`) | `which node` |
| Audio | `arecord`, `sensors`, `/sys/class/thermal/thermal_zone0` present | `which`, `ls` |
| Models | whisper tiny/base/small, marian-en-bn CT2, nllb-600m CT2, Piper bn_BD | `ls models/` |
| Physical student devices | **none** | not attachable from this host |
| Hotspot AP mode | not exercised (needs root + AP-capable radio) | previous reports |

**Consequence:** browser QA is possible (Firefox + geckodriver) even though
Playwright/Selenium are absent; physical-phone and radio testing are not.

## 2. Baseline results, re-run before changing anything

| Check | Command | Result |
|---|---|---|
| Test suite | `.venv/bin/python -m pytest -q` | **187 passed** (exit 0) |
| Lint | `.venv/bin/ruff check --no-cache guru scripts tests` | clean |
| Compile | `py_compile guru/… scripts/*.py` | OK |
| JS syntax | `node --check teacher.js`, `student.js` | OK |

## 3. Task matrix at baseline

Classification of every Phase 6 / Phase 8 item **as it stood before this
mission**. "Implemented but not verified" means code existed but no evidence
had been produced on this host.

| Area | Baseline classification | Note |
|---|---|---|
| Teacher dashboard | IMPLEMENTED BUT NOT VERIFIED | no browser execution had ever been run |
| Student dashboard | IMPLEMENTED BUT NOT VERIFIED | same |
| Browser QA | NOT IMPLEMENTED | no automation; previous report said BLOCKED |
| Mobile responsiveness | PARTIALLY IMPLEMENTED | CSS had one 900 px breakpoint, no small-screen pass |
| Accessibility | PARTIALLY IMPLEMENTED | semantic HTML and labels present; no focus ring, no live regions, no audit |
| WebSocket recovery | IMPLEMENTED AND VERIFIED | `tests/test_ws.py`, load-test reconnect not covered |
| Regression suite | IMPLEMENTED AND VERIFIED | 187 tests |
| End-to-end latency | IMPLEMENTED AND VERIFIED (file-based) | `scripts/pipeline_cli.py` numbers existed |
| CPU/RAM profiling | PARTIALLY IMPLEMENTED | only RSS after model load |
| ASR + upload contention | PARTIALLY IMPLEMENTED | proxy CPU loop only, not real ASR |
| Retention | PARTIALLY IMPLEMENTED | startup-only purge — the recorded limitation |
| Retrieval (bn→en) | IMPLEMENTED, KNOWN WEAK | measured top-1 0.167 |
| Grading | PARTIALLY IMPLEMENTED | literal substring match |
| Simulated load | IMPLEMENTED AND VERIFIED | 1–30 clients, earlier session |
| Physical hotspot | BLOCKED BY EXTERNAL REQUIREMENT | no AP radio / phones |
| 30 physical devices | BLOCKED BY EXTERNAL REQUIREMENT | no devices |
| Thermal stability | BLOCKED BY EXTERNAL REQUIREMENT | no sustained real workload |

## 4. Defects found during the audit

1. **Arabic word in Bengali UI copy.** `teacher.html` contained
   `(নিচে حالة দেখা যাবে)` — the Arabic word `حالة` instead of Bengali `অবস্থা`.
   Fixed.
2. **Retention never ran after startup.** Confirmed by reading `lifespan`: the
   purge was called once, then never again. Fixed with a supervised background
   loop.
3. **Bangla queries could not reach English slides at all.** Confirmed by the
   recorded benchmark (0.167 top-1) and by reading `search_chunks`: only the
   English text was indexed. Fixed by indexing the Bangla side at upload.
4. **No visible focus indicator** anywhere in the dark theme, and no live region
   for captions — a screen reader would not announce new captions. Fixed.

## 5. What this baseline deliberately did not touch

Authentication, token hashing, upload validation, queue limits, approval gates
and provenance were left alone: they were already implemented and covered, and
weakening them to make a test easier would have violated the mission rules.
