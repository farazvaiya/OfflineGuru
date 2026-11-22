# Phase 4 — document ingestion and local retrieval

Scope: uploaded PDF/PPTX/TXT/MD material is extracted locally, chunked with page
provenance, stored in SQLite and retrieved offline to ground summaries and quizzes.
No cloud call, no model download, no new service.

## Files

| File | Change |
|---|---|
| `guru/education/documents.py` | **new** — extraction, chunking, hashed embeddings, hybrid retrieval, upload validation |
| `guru/storage.py` | `documents` + `doc_chunks` tables, add/get/list/delete, chunks-with-document names, purge integration, export |
| `guru/education/quiz.py` | optional `passages`; page-cited slide questions; `source_documents`; `public_quiz` exposes the citation |
| `guru/education/summary.py` | optional `passages`; slide sentences appended with page; `sources` list |
| `guru/education/transcript_utils.py` | `Evidence.source` (document/page) |
| `guru/education/service.py` | `_passages` retrieval, `search_materials`, grounding flags |
| `guru/server/app.py` | upload (raw body), list, search, delete endpoints; documents in session state; quiz `use_documents` |
| `guru/server/static/teacher.html` / `teacher.js` | upload/list/delete card, material search, page citations in summary and quiz |
| `requirements.txt` | `pypdf==6.21.4` (pure-Python PDF text extraction) |
| `tests/test_documents.py` | **new** — 31 tests |

## Why these choices

- **pypdf for PDF, stdlib for PPTX.** A PPTX is a zip of XML; parsing `a:t` runs
  with `zipfile` + `ElementTree` is ~30 lines and adds no dependency. PDF text
  extraction is genuinely hard (cross-reference tables, content streams, encodings),
  so it uses a maintained pure-Python library that never touches the network.
- **Raw request body instead of multipart.** `python-multipart` is not installed;
  the console uploads one file at a time, so `request.body()` plus a `name` query
  parameter removes a dependency without losing anything.
- **Hashed n-gram vectors instead of a neural embedding model.** No embedding model
  ships in `models/`, `sentence-transformers` would pull torch (prep-only, ~2 GB),
  and on this hardware the classroom corpus is a few hundred pages. Signed
  BLAKE2b-hashed word/bigram buckets give deterministic, dependency-free scoring
  that blends 70 % cosine with 30 % term overlap. `shortcut: lexical vectors miss
  pure paraphrase; swap in a local embedding model if retrieval quality is the
  limiting factor.`
- **Chunking keeps pages; retrieval keeps `{document, page}`.** Page numbers are
  the index into the source file, never synthesised: a passage with no valid page
  is skipped rather than cited as page 0 (`test_quiz_never_invents_a_page`).

## API

| Method | Path | Notes |
|---|---|---|
| POST | `/api/teacher/session/{id}/documents?name=` | body = file bytes; 200 with `document`, `chunks`, `duplicate` |
| GET | `/api/teacher/session/{id}/documents` | list with page and chunk counts |
| GET | `/api/teacher/session/{id}/documents/search?q=&k=` | ranked hits with document, page, score, text |
| DELETE | `/api/teacher/session/{id}/documents/{doc_id}?confirm=true` | removes document and its chunks |

Grounding is on by default for summary and quiz generation; `{"use_documents": false}`
reproduces the previous transcript-only behaviour exactly.

## Grounding rules

- A question is only created from a sentence that exists in a stored page, and its
  evidence carries that page.
- Bangla question text comes from the translation model; the quote, term and page
  are copied verbatim.
- With no transcript yet, the opening pages are used (real page numbers, score 0)
  instead of producing an empty draft.
- Students only see citations inside approved material.

## Security

Extension allowlist; 25 MB body cap (header and body); 400-page cap; 400 000-character
extracted-text cap; PPTX declared-uncompressed-size cap against zip bombs; encrypted,
empty, corrupt and image-only PDFs rejected with 400 and a readable message; filename
reduced to its basename and truncated; teacher token required on every endpoint;
SHA-256 dedupe scoped per session; documents purged with the session and on retention.
Uploaded text is stored and quoted only — it is never executed, never interpreted as
instructions and never sent to an LLM.

## Tests actually executed

```
.venv/bin/python -m pytest -q                                  -> 170 passed (31 new)
.venv/bin/ruff check --no-cache guru scripts tests             -> All checks passed
.venv/bin/python -m py_compile guru/*.py guru/education/*.py guru/server/*.py scripts/*.py -> OK
node --check guru/server/static/teacher.js                     -> OK
.venv/bin/python scripts/verify_offline.py --seconds 20        -> PASS, zero external connections
```

New coverage: extraction (txt/pdf/pptx, page numbering), rejection (wrong type,
empty, oversize, image-only, corrupt), chunk bounds, embedding determinism, ranking,
search determinism, storage round-trip/dedupe/scoping/delete/purge, API auth and
isolation, deleted-document confirmation, grounding with page citations, "never invent
a page", transcript-only behaviour unchanged, merged-chunk regression, API-level
upload→quiz grounding.

Live server (throwaway data dir, models pointed at missing paths on purpose):

```
upload_http=200
questions 3 source_documents [{'document': 'biology.txt', 'page': 1}]
first_citation biology.txt page 1 | options 3
token_in_log=0
```

The one traceback in that log is the expected translation fallback
(`guru/education/quiz.py::_translate_map` → `translate.py`) because the smoke run had
no translation model; question text falls back to English.

## Measurements

| Measurement | Value |
|---|---|
| Index build, 300 chunks | 32 ms |
| Query + rank, 300 chunks | 35.9 ms |
| Single-machine, single run | not a benchmark |

## Limitations

1. Lexical retrieval: paraphrase and cross-language queries will miss. Bangla
   queries against English slides are not supported.
2. Embeddings are recomputed per request; fine for a class-sized corpus, not for a
   library of thousands of pages.
3. No OCR — scanned PDFs are refused rather than silently empty.
4. Untested against real publisher PDFs; the suite uses hand-built fixtures.
5. Uploads during a live lecture share CPU with ASR; extraction is off the event
   loop but there is no admission control.
6. `pypdf` was installed into the existing venv; a clean-room install of
   `requirements.txt` was not repeated after the pin was added.
7. No browser verification of the new console panel; only `node --check`.
