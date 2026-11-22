# Accessibility audit

**Objective:** find and fix practical accessibility defects in both consoles,
without adding complexity or a dependency. Automated/static findings are kept
separate from assistive-technology testing, because no screen reader was run here.

## Fixes made

| Defect | Fix | File |
|---|---|---|
| No visible focus indicator in a dark theme; keyboard users lost position | `:focus-visible { outline: 3px solid #79c0ff; outline-offset: 2px; }` | `style.css` |
| New captions were silent to a screen reader (content injected by JS) | `role="log" aria-live="polite" aria-label="…"` on the transcript in both pages | `student.html`, `teacher.html` |
| Toast messages (errors, confirmations) were never announced | `role="status" aria-live="polite"` on the toast | both pages |
| Empty-state and health text changed without announcement | `role="status"` on the empty-transcript line, `aria-live="polite"` on the health box | both pages |
| Arabic word `حالة` in Bengali help text (wrong language, wrong meaning) | replaced with `অবস্থা` | `teacher.html` |
| Animations could not be suppressed | `@media (prefers-reduced-motion: reduce)` disables animation/transition | `style.css` |
| Long toast could overflow a phone screen | max-width + centred wrapping | `style.css` |

## Static review of the existing markup

| Criterion | Finding |
|---|---|
| Language | `<html lang="bn">` with Bengali UI copy — correct |
| Semantic structure | `header`, `main`, `section`, `table` with `thead`/`tbody`; headings are ordered h1 → h2 → h3 |
| Form labels | every `input`/`select` on both pages is bound with `<label for="…">`; the file input is labelled too |
| Buttons | all are `<button>` or `<a>`, not clickable `div`s; text labels are present |
| Images | QR `<img>` carries an `alt`; no other raster images |
| Icons in text | emoji (🎤 ▶ ⏸ ⏹ 🔈) are always adjacent to a text label, so they are not the sole carrier of meaning |
| Colour contrast | body `#e6edf3` on `#0d1117` ≈ 15:1; muted `#97a3b3` on `#161b22` ≈ 7:1; the accent-green button uses white on `#2ea043` ≈ 3.7:1 — **large-text only**; flagged below |
| Live regions | added (above) |
| Error messages | rendered as text next to the control (`#joinErr`, `#setupErr`), not colour-only |
| Captions | font-size 1.12rem, line-height 1.6, high contrast |
| Audio controls | the student audio toggle is a real `<button>` with `aria-pressed` (already present) and a visible label |

## Verified in a real browser

`scripts/browser_qa.py` asserts on the live DOM after joining a class:

```
caption updates are announced to assistive tech:
  aria-live=polite controls=21 unlabeled=9
```

The `unlabeled=9` figure comes from a deliberately crude heuristic (no text, no
`aria-label`, no `id`, no placeholder). The nine are the quiz option radio inputs,
which are wrapped in a `<label class="option">` created at
`guru/server/static/student.js:426` — they are labelled by their wrapping element,
which the heuristic does not check. No control is genuinely unlabelled.

## Not done — stated plainly

1. **No screen reader was run.** NVDA, Orca, VoiceOver and TalkBack are not
   installed on this host. "Screen-reader-friendly" here means the semantics and
   live regions are present, not that a screen-reader session was observed.
2. **No keyboard-only walkthrough in the browser.** Focus order was reviewed in
   source; it was not tabbed through end to end after the CSS change.
3. **Contrast of the green primary button** (white on `#2ea043`, ≈3.7:1) is below
   the 4.5:1 AA threshold for normal text. It passes only as large text (≥18.66px
   bold). Darkening the green to ~`#1f883d` would fix it, at the cost of the
   current brand colour — not changed unilaterally.
4. **No automated accessibility scanner** (axe-core, pa11y) is installed, and
   installing one offline was not possible.
5. **No cognitive-load or dyslexia review**, and no Bengali screen-reader
   pronunciation check.
