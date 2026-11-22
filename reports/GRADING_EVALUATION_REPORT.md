# Grading evaluation report

**Objective:** measure what the Bangla short-answer grader actually judges, fix
the cases that were purely a matching artefact, and leave the rest honestly
flagged instead of pretending to understand them.

## Change made (`guru/education/grading.py`)

1. **Inflection folding.** Bengali suffixes (`গুলো`, `গুলোর`, `গুলি`, `দের`,
   `য়ের`, `এর`, `ের`, `তে`, `টি`, `টা`, `রা`, `কে`, `য়`) are stripped before
   matching, with a three-character floor so short words are not truncated.
   `ক্লোরোফিলের` now matches the rubric term `ক্লোরোফিল`.
2. **Word-order tolerance.** A rubric term matches when *every* one of its words
   is present in the answer after folding, not only when the exact phrase appears
   contiguously. `light energy` now matches "energy from light".
3. **Zero-width characters** (`U+200B–U+200D`, `U+FEFF`) are removed, because
   copy-paste from a PDF otherwise breaks matching invisibly.
4. **Reported term language.** `matched_terms` now records the term the student
   actually matched (Bangla or English), not always the English label.

Thresholds were **not** loosened: `≥ 0.7` correct, `< 0.35` wrong, middle band →
`needs_review`. The teacher override path is untouched.

## Evaluation dataset — `tests/test_grading_eval.py`

| Case | Expected | Measured |
|---|---|---|
| Exact English answer | correct | correct |
| Correct answer, reversed word order | correct | correct |
| Correct Bangla answer | correct | correct |
| Bangla genitive (`ক্লোরোফিলের`) against base term | matched | matched |
| Empty answer | `needs_review`, never "wrong" | `needs_review` |
| Very short answer (< 3 content words) | `needs_review` | `needs_review` |
| Unrelated answer | wrong | wrong |
| Partially correct answer | `needs_review` | `needs_review` |
| **Pure synonym / paraphrase** | limitation | **wrong (not approved)** |
| MCQ exact match (case-insensitive) | correct / wrong | correct / wrong |

Result: **all 10 cases behave as specified**; the paraphrase case is pinned as a
known limitation rather than dressed up as a pass.

## What is still wrong

1. **Synonyms are not understood.** "the green pigment in leaves traps sunlight"
   shares no token with the rubric and is scored **wrong**, not reviewed. A teacher
   must override it. This is a real false negative and the main remaining quality
   gap. Fixing it needs a synonym lexicon or an embedding model — neither ships
   offline on this hardware.
2. **Overlap can still pass a wrong answer.** "Chlorophyll is not used by plants
   at all" contains the rubric term and scores correct. String matching cannot
   detect negation.
3. **No hand-graded corpus.** The dataset is authored by the engineer, not scored
   by teachers; there is no inter-rater agreement and no Bangla spelling-variant
   set (`ফ`/`ফ়`, `ি`/`ী` confusions) beyond the folding above.
4. **Only one topic.** All cases are photosynthesis; other subject vocabularies
   were not tested.
