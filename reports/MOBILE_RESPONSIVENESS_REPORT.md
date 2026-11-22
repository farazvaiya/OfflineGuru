# Mobile responsiveness report

**Objective:** make the teacher console and student client usable on the phones
students actually bring, and verify the narrow layout instead of assuming it.

## What was changed

`guru/server/static/style.css` gained one small-screen pass and one accessibility
pass (no framework, no new dependency, same palette):

| Change | Why |
|---|---|
| `@media (max-width: 640px)` block | the previous only breakpoint was 900 px; below that the dense desktop grid stayed dense |
| `.wrap` padding 1rem → .75rem, `.card` padding 1rem → .8rem | reclaims ~10 % horizontal space on a 390 px screen |
| body font 17px → 16px, h1 1.5rem → 1.28rem | keeps two-column rows from wrapping mid-word |
| `.qr` 190px → 132px | the QR and join code were pushing the card wider than the viewport |
| `#transcript` max-height 62vh → 55vh | leaves room for the audio/tab controls without scrolling past them |
| `.metrics` min 120px → 92px | four metrics fit on one row at 360 px |
| toast width `calc(100vw - 1.6rem)` and centred text | a long Bengali message used to overflow off-screen |
| table font/padding reduced | the student list fitted without horizontal scroll |

## Verification (real browser, real CSS viewport)

Method: `scripts/browser_qa.py` renders each page inside a 390/360 px iframe —
a genuine CSS viewport, so the media query applies — and compares
`documentElement.scrollWidth` with `clientWidth`. Horizontal overflow would show
as `scrollWidth > clientWidth`.

| Page | Viewport | scrollWidth | Result |
|---|---|---|---|
| teacher | 1366 × 768 | 1366 | PASS |
| teacher | 768 × 1024 | 768 | PASS |
| teacher | **390 × 844** | 390 | PASS |
| teacher | **360 × 800** | 360 | PASS |
| student (join) | 390 × 844 | 390 | PASS |
| student (join) | 360 × 800 | 360 | PASS |
| student (classroom) | 768 × 1024 | 768 | PASS |
| student (classroom) | 1366 × 768 | 1366 | PASS |

Screenshots inspected visually: `reports/screenshots/teacher-phone-390.png`,
`student-phone-390.png` (join), `teacher-desktop-1366-room.png`. Cards stack in one
column, Bengali headings do not clip, the primary button is full-width on the
student page, and every control clears the 44 px touch-target minimum that the
stylesheet already set.

## Limits — do not read this as phone certification

1. **No physical phone was used.** This is CSS-viewport emulation in Firefox on a
   desktop. Font metrics, `viewport-fit=cover` notches, Android keyboard
   behaviour, and pinch-zoom are not exercised.
2. **No touch input, no rotation, no on-screen keyboard.**
3. **Only Firefox.** Chrome/Android WebView could differ on `backdrop-filter` and
   Bengali font fallback.
4. **Audio/video elements** were not exercised at phone size because the QA
   session had no live audio stream.
