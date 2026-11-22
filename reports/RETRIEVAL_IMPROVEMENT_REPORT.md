# Retrieval improvement report

**Objective:** fix the measured weak case — Bangla query against English material
(top-1 0.167) — without a network dependency or a new model, and prove the change
with the same benchmark that exposed it.

## The constraint that decided the approach

The mission suggested translating Bangla queries into English with the local
Marian model. **That is not possible with what is installed:** the shipped model
`models/marian-en-bn-ct2` is English→Bangla only. Running a Bangla query through
it would produce Bangla, not English. NLLB-600M can do bn→eng, but it is ~10×
slower per sentence and would add ~500 MB resident on a laptop already using
~800 MB for ASR/MT/TTS — a bad trade for a search box.

The reverse direction is free: translate the **corpus** once, at upload, with the
model that already exists, and score a chunk on the better of its two sides.

## Implementation

| File | Change |
|---|---|
| `guru/education/documents.py` | `search_chunks` embeds `text` and `text_bn` per chunk, scores the max, and still cites the original English `text` |
| `guru/storage.py` | `doc_chunks.text_bn` column + in-place migration for existing databases; `add_document` stores it |
| `guru/server/app.py` | `_bilingual_chunks` translates English chunks at upload in bounded batches (60 s / 120 000 chars), skips Bangla chunks, never fails an upload |
| `guru/config.py`, `config/default.toml` | `education.bilingual_index` (default true) |

Provenance is unchanged: a Bangla query returns `{document, page, quote}` where
the quote is the original slide text. Documents are never rewritten.

## Benchmark (same corpus, same 12 queries, same methodology)

`.venv/bin/python scripts/eval_retrieval.py`

| Query → corpus | Before (top-1 / MRR) | After (top-1 / MRR) |
|---|---|---|
| **bn → en** | **0.167 / 0.250** | **0.667 / 0.806** |
| en → en | 0.833 / 0.917 | 0.833 / 0.917 (unchanged) |
| en → bn | 0.167 / 0.222 | not addressed |
| bn → bn | 1.000 / 1.000 | unchanged (already same-language) |

Raw: `docs/results/retrieval_benchmark.json` (`bilingual` rows).

Cost: index translation 3645.8 ms for 6 pages (~608 ms/page, once per upload),
query 1.0 ms versus 0.5 ms before. No runtime RAM cost — the Bangla text is
stored, not a model.

## Tests added

`tests/test_bilingual_retrieval.py` (7): Bangla query reaches the English slide,
citation stays English, English path unchanged, chunks without a Bangla side
degrade to the old behaviour, empty query/corpus, and a SQLite round-trip.

## Honest limits

1. **bn→en is 0.667, not 1.0.** Two of six queries still miss: the translate
   step paraphrases scientific terms, and lexical matching cannot recover from a
   translation that drifts.
2. **en→bn (0.167) is untouched.** An English query against Bangla slides would
   need English text indexed from the Bangla source, i.e. bn→en translation,
   which the shipped model cannot do. This remains a documented gap.
3. **Sample size is 6 queries per row.** It is a directional result from a small
   synthetic corpus, not a validated retrieval evaluation.
4. **Neural encoder comparison is still BLOCKED** — the multilingual encoder is
   not on disk and the earlier download attempt stalled at 28 MB of ~470 MB.
   Disk (84 GB) and RAM are sufficient; the link is not. No encoder numbers exist
   and none are claimed.
5. Upload cost is real: ~0.6 s per page. A 50-page deck adds ~30 s to an upload
   that is already refused while a lecture is running.
