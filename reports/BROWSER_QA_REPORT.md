# Browser QA report

**Objective:** verify that the teacher console and student client actually work in
a real browser — rendering, JavaScript execution, API calls, session creation, join
flow — not merely that the HTML parses.

**Environment:** Firefox via geckodriver (headless), driven over the W3C WebDriver
HTTP protocol by `scripts/browser_qa.py` (standard library only; Playwright and
Selenium are not installed on this host). Server: real `uvicorn` on
`127.0.0.1:8011` with a throwaway database. Date: 2026-11-02.

**Procedure:**

```
GURU_STORAGE__DB_PATH=/tmp/qa/guru.db GURU_STORAGE__DATA_DIR=/tmp/qa \
  .venv/bin/python -m uvicorn guru.server.app:app --host 127.0.0.1 --port 8011
.venv/bin/python scripts/browser_qa.py --server http://127.0.0.1:8011
```

**Result: 22 checks passed, 0 failed, 0 skipped.** Raw output:
`reports/results/browser_qa.json`. Screenshots: `reports/screenshots/`.

| Check | Result | Evidence |
|---|---|---|
| Teacher page loads | PASS | `setupView` rendered; `teacher-desktop-1366-setup.png` |
| Create session by clicking the button | PASS | room view became visible |
| Join code displayed | PASS | code read from the DOM (e.g. `DR53DF`) |
| QR generated locally | PASS | `src=/qr.svg?data=…`, no external request |
| Teacher token kept client-side | PASS | `localStorage` keys `guru.teacher.token`, `guru.teacher.session` |
| Document upload | PASS | 1 chunk stored, `bilingual=1`, `index_ms≈1030` |
| Summary generation | PASS | draft returned |
| Quiz generation | PASS | 3 questions |
| Console renders generated quiz | PASS | `quizBox` populated; `teacher-desktop-1366-room.png` |
| Teacher at 768 px | PASS | `scrollWidth=768` |
| Teacher at 390 px CSS width | PASS | `viewport=390, scrollWidth=390` |
| Teacher at 360 px CSS width | PASS | `viewport=360, scrollWidth=360` |
| Student page loads | PASS | join view rendered |
| Student joins with the code | PASS | class view visible |
| Session title shown to the student | PASS | `title='QA lecture'` |
| Approved quiz renders | PASS | 958 characters in the quiz tab |
| Approved summary renders | PASS | 161 characters |
| Captions announced via live region | PASS | `aria-live=polite` |
| Student fits 768 / 390 / 360 / 1366 | PASS | `scrollWidth == viewport` in each case |
| Screenshots captured | PASS | 10 files in `reports/screenshots/` |

**Visual inspection (performed, not assumed):** `teacher-phone-390.png`,
`student-phone-390.png` and `teacher-desktop-1366-room.png` were opened and
reviewed. Bengali copy renders correctly, cards stack in a single column at
390 px, buttons meet the 44 px touch target, the QR and join code are legible, and
no element overflows horizontally.

## Limitations of this QA — stated, not hidden

1. **No console-log capture.** Firefox's WebDriver implementation exposes no
   console-log endpoint, so JavaScript errors are inferred from behaviour (the UI
   would not update if a handler threw) rather than read from a log.
2. **Narrow viewports are emulated with an iframe.** Headless Firefox refuses to
   make a window narrower than ~500 px, and `layout.css.devPixelsPerPx` was
   ignored by this build (the CSS viewport stayed 780 px). An iframe *is* a real
   viewport, so media queries evaluate against 390/360 px — that is how the mobile
   screenshots were produced. It is a genuine 390 px layout, but not a physical
   phone.
3. **Join URL port mismatch in the test harness.** The QR/join URL reports
   `:8000` because `settings.server.port` is the configured port while the test
   server was bound to 8011 by CLI. In the documented deployment
   (`scripts/serve.sh`) these are the same value; this is a harness artefact, not
   an application defect.
4. **One browser only** (Firefox). Chromium, Safari and Android WebView were not
   tested — none is installed.
5. **No touch, no on-screen keyboard, no rotation.** Those need a device.

**Status: PASS for browser functionality and layout at 1366/768/390/360 px in
Firefox. Physical-device behaviour remains unverified (see
`PHYSICAL_DEVICE_TEST_PLAN.md`).**
