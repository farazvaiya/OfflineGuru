# Phase 6 verification — teacher and student experience

**Objective:** verify both interfaces as running software, not as source code.

**Environment:** Firefox via geckodriver (headless), real `uvicorn` server,
throwaway database. Evidence: `reports/results/browser_qa.json`,
`reports/screenshots/`, `tests/`.

## Teacher console

| Capability | How verified | Result |
|---|---|---|
| Page load, layout, Bengali copy | real browser, 4 viewports | PASS |
| Create classroom (button → API → UI switch) | real click through WebDriver | PASS |
| Join code displayed | read from the live DOM | PASS |
| QR code (offline, local SVG) | `src=/qr.svg?data=http://192.168…` | PASS |
| Teacher token kept client-side | `localStorage` inspection | PASS |
| Lecture start / pause / resume / stop | API lifecycle tests (`tests/test_api_edge.py`) | PASS (API level) |
| Microphone capture | `arecord` present; live-mic run recorded in `TEST_REPORT.md`, not repeated here | PARTIAL — not re-run |
| Live transcript display and polling | console renders `quizBox`/transcript from the poll loop | PASS |
| Transcript edit | `PATCH /segment` + cross-teacher denial test | PASS (API), not clicked in browser |
| Student list and online status | rendered from session state | PASS (API + DOM) |
| Document upload / list / delete / search | upload through the console endpoint in the browser run: 1 chunk, bilingual, 1030 ms | PASS |
| Summary generation | draft created and rendered | PASS |
| Quiz generation | 3 questions, rendered in the console | PASS |
| Approve / reject material | `PATCH materials` approved; student view then exposed it | PASS |
| Health panel, warnings | `/api/health` authorised, rendered | PASS |
| Error messages | wrong join code → 404 text; upload while live → 409 text | PASS (API) |

## Student client

| Capability | How verified | Result |
|---|---|---|
| Join by code + name | real browser form submit | PASS |
| Correct session association | title rendered from the session | PASS |
| Live captions | WebSocket delivery covered by `tests/test_ws.py`; live-region DOM attribute verified | PASS (tests + DOM) |
| Bangla text rendering | screenshots at 390 px, reviewed | PASS |
| Audio playback | `<audio>` path and `GET /api/student/audio/{id}` covered by tests/load test (200s) | PARTIAL — not played in a browser (no live audio in the QA session) |
| Approved quiz visibility | 958 characters rendered | PASS |
| Quiz submission | `POST /api/student/answer` incl. rate limit, unknown question | PASS (API) |
| Feedback | per-student scoping tests | PASS (API) |
| Reconnection / REST catch-up | `after_seq` recovery + WS auth tests | PASS (tests), not browser-simulated |
| Session ended | 404 on join for an ended session | PASS (API) |
| One student cannot read another's data | cross-session/cross-student denial tests | PASS |

## WebSocket and recovery

Verified by tests, not by the browser run:

| Scenario | Test | Result |
|---|---|---|
| Unauthorised socket | `test_ws.py` 4401 close | PASS |
| Hello handshake | `test_ws.py` | PASS |
| Cross-session leakage | `test_events_do_not_leak_across_sessions` | PASS |
| Slow client / bounded queue | `test_bus.py` drop-oldest + counters | PASS |
| Ping/pong keepalive | `test_ws.py` | PASS |
| REST catch-up (`after_seq`) | `test_api.py` | PASS |
| Server restart mid-session | not simulated in this mission | NOT TESTED |
| Duplicate message handling | idempotent upsert on `(quiz, student, question)` | PASS (design + test) |

The safety limits (bounded queues, drop counters, 64-event per-client queue) were
not relaxed; no test was changed to accommodate them.

## Defects fixed in this phase

1. Arabic `حالة` in Bengali teacher help text → `অবস্থা`.
2. No focus ring, no live region for captions, unannounced toasts → fixed in CSS/HTML.
3. Small-screen layout reused the desktop grid → dedicated `max-width: 640px` pass.

## Remaining gaps

- Audio playback and transcript editing were exercised at API level only.
- No physical phone, no touch, no rotation, no Android keyboard.
- Server-restart recovery is unverified.
