# Security review

**Objective:** re-check the security posture after this mission's changes, and
confirm nothing added weakens it.

## Posture (unchanged areas re-verified by tests)

| Control | State | Evidence |
|---|---|---|
| Credentials at rest | only SHA-256 digests stored for teacher and student tokens; legacy rows migrated in place | `tests/test_security.py` |
| `/api/health` authorisation | teacher token required; loopback-only with no token; a supplied bad token always 403 | `tests/test_security.py` |
| Log redaction | tokens travelling as query params are scrubbed from every sink | `guru/logsafe.py`, `tests/test_logsafe.py` |
| Session/student isolation | cross-session audio, answers and documents denied | `tests/test_api.py`, `tests/test_documents.py` |
| Upload validation | extension allowlist, 25 MB, 400 pages, 400 000 chars, zip-bomb cap, encrypted/corrupt refused, basename-only filename, teacher token required, SHA-256 dedupe | `tests/test_documents.py` |
| Answer keys | never sent to the student before grading (`public_quiz`) | `tests/test_education_service.py` |
| Rate limiting | 30 submissions/minute/student | `tests/test_api_edge.py` |
| Approval gate | student sees a quiz only when `status='approved'` | `tests/test_education_service.py` |
| Document content as data | never executed, never interpreted, never sent to a model | by construction; `documents.py` has no eval/exec/network import |

## New surface introduced in this mission

| Change | Risk considered | Verdict |
|---|---|---|
| `doc_chunks.text_bn` (translated corpus stored in SQLite) | stores more derived text at rest, in the same file that already holds the source | acceptable: no new secret, deleted with the session and by retention |
| Machine translation at upload | could execute untrusted input? No — it is text-in/text-out through a local model, no shell, no network, no prompt | acceptable |
| Bounded translation budget (60 s / 120 000 chars) | prevents a pathological deck from pinning a worker | improves availability |
| Background retention loop | could it delete live data? Only `status != 'active'` older than `retention_days`; active sessions are excluded by SQL and covered by a test | safe |
| Retention loop shutdown | task cancelled in `finally`; tested as cancellable before the first sweep | safe |
| New upload response fields (`bilingual_chunks`, `index_ms`) | expose counts and timing to an authenticated teacher | acceptable |
| Browser QA script | drives a real browser against a local server; uses a throwaway DB | not part of the app; no credential is written to a report |

## Confirmed unchanged: still offline

`scripts/verify_offline.py --seconds 15` passed after all changes: ASR, translation
and TTS loaded from `models/`, the pipeline ran, and a socket guard blocked every
non-loopback connect and DNS lookup. No new import in `guru/` opens a socket.

## Residual risks (not fixed, stated)

1. **Unsalted SHA-256 for tokens.** Correct for 32-byte random tokens; would be
   wrong for passwords (none are used).
2. **Loopback health access without a token.** A local process can read health
   (no student text, no tokens). Deliberate so the pre-session setup path works.
3. **Audio endpoints take the token as a query parameter** (browser media elements
   cannot send headers). Mitigated by log redaction, not by headers.
4. **No fuzzing or adversarial file corpus.** Validation was tested with crafted
   malformed/oversized/encrypted cases, not a fuzzer.
5. **No network-layer security testing** on the hotspot (ARP, captive-portal
   behaviour, rogue DHCP). Requires hardware.
6. **XSS is prevented by using `textContent` throughout**, but there is no CSP
   header. A strict CSP would be a small, sensible hardening step and was not
   added because it can break the inline handlers in the current UI.
