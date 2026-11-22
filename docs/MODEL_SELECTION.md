# Model selection

All numbers below were measured on the actual development laptop, not quoted
from model cards. Hardware: **Intel i5-7300U (2 cores / 4 threads), 7.1 GB RAM,
no GPU, Ubuntu 26.04, Python 3.14**, `faster-whisper 1.2.1`, `ctranslate2 4.8.3`,
int8 quantization. See `docs/EVALUATION.md` for the methodology and raw data.

## Speech recognition

Test set: 37 VAD segments from a 160 s English lecture (390 reference words),
clean synthetic speech from the Piper `en_US-lessac-medium` voice. This is a
**read, noise-free** corpus: real classroom WER will be higher.

| Candidate | WER | Per-segment p50 | RTF | RSS after load | License | Verdict |
|---|---|---|---|---|---|---|
| faster-whisper **tiny** | 5.9% | 0.67 s | 0.19 | 182 MB | MIT | fallback for weak CPUs |
| faster-whisper **base** | **3.8%** | **1.28 s** | **0.36** | **330 MB** | MIT | **chosen default** |
| faster-whisper **small** | 3.1% | 3.92 s | 1.09 | 885 MB | MIT | rejected: RTF ≈ 1 leaves no CPU for translation/TTS, e2e grew to ~19 s in testing |
| Vosk small EN | — | ~0.3 s streaming | — | ~100 MB | Apache-2.0 | rejected: weaker accuracy and punctuation; no Bangla |
| Cloud ASR (Whisper API etc.) | — | network | — | — | — | **excluded by requirement** |

`small` is only 0.7 percentage points better than `base` on this corpus while
costing 3× the latency and 555 MB more RAM. On a 4-core laptop that trade is
clearly wrong for a live class.

**Vocabulary biasing matters more than model size for domain terms.** Without
hotwords the base model transcribed *"Glucose is a simple sugar…"* as
*"Bluecoast is a simple sugar…"*. With
`hotwords = "glucose, chlorophyll, chloroplast, stomata, photosynthesis"` the
same segment is correct. The teacher console exposes this per lesson.

## Machine translation

| Candidate | Size (int8) | p50 / sentence | Quality | License | Verdict |
|---|---|---|---|---|---|
| **Marian** `shhossain/opus-mt-en-to-bn` (CT2 int8) | 83 MB | **0.22 s** | Understandable; occasional lexical errors ("sunlight"→"সূর্যাস্ত") | Apache-2.0 | **chosen default** (meets 1–3 s) |
| **NLLB-200-distilled-600M** (CT2 int8) | 600 MB | 2.15 s | Clearly better and more fluent | **CC-BY-NC-4.0** | optional `backend="nllb"`; non-commercial only |
| Helsinki `opus-mt-en-bn` | — | — | — | — | does not exist upstream (only bn→en); `bnt` is Bantu, not Bangla |
| IndicTrans2 distilled 200M | ~900 MB | > 3 s | Strong | MIT | rejected: needs torch + IndicTransToolkit at runtime, too heavy here |
| General LLM via Ollama (e.g. 1.5B) | 1–2 GB | seconds | Fluent but prone to inventing facts; no dedicated MT guarantee | varies | rejected for translation |
| Cloud translation | — | network | — | — | **excluded by requirement** |

Example comparison from `scripts/compare_translators.py`:

```
EN  Respiration is the process by which living cells release energy from glucose.
Marian  পুনর্বাসন হচ্ছে এমন প্রক্রিয়া যার মাধ্যমে জীবন্ত কোষগুলো গ্লুকোজ থেকে শক্তি লাভ করে।   (wrong: "rehabilitation")
NLLB    শ্বাসকষ্ট হলো জীবন্ত কোষগুলো গ্লুকোজ থেকে শক্তি বের করার প্রক্রিয়া।                    (wrong: "dyspnoea", but closer)
```

Both models fail on this science term; neither is a substitute for teacher
review. Marian is 10× faster, so it is the default. Switch with:

```bash
GURU_TRANSLATE__BACKEND=nllb ./scripts/serve.sh      # needs models/nllb-*
```

Because NLLB is **CC-BY-NC-4.0**, it is not downloaded by default and must not be
used in a commercial deployment without checking the license.

### Number handling

Both backends verify that numbers in the source survive translation (Bengali
digits are normalised before comparison: `১০০` == `100`). Segments that lose a
number are flagged `numbers_preserved=false`, shown to students as
"সংখ্যা যাচাই করুন" and highlighted in the teacher console. This is a check, not
a fix — it exists because NMT models drop numbers often enough to matter in
science lessons.

## Text to speech

| Candidate | Size | p50 / sentence | License | Verdict |
|---|---|---|---|---|
| **Piper** `bn_BD-google-medium` (ONNX) | 77 MB | **0.31 s** (cold, cache empty) | runtime GPL-3.0; voice trained on OpenSLR-37 (CC-BY-SA-4.0) + CMU Indic | **chosen** |
| espeak-ng `bn` | ~10 MB | < 0.1 s | GPL-3.0 | fallback only: robotic, and not installed by default (needs root) |
| MMS-TTS `ben` (VITS) | ~145 MB | 1–3 s | CC-BY-NC-4.0 | rejected: torch runtime, non-commercial |
| Browser `speechSynthesis` | 0 | device | — | rejected as primary: Bengali voice availability offline is not guaranteed |
| Cloud TTS | — | network | — | **excluded by requirement** |

Audio is cached per segment (`data/audio/<session>/<segment>.wav`), so 30
students replaying the same caption cost one synthesis. The Bangla voice is
intelligible but has a noticeable accent; captions are always available as the
authoritative fallback.

## Why no LLM for the education workflow

A local LLM would need ~1–2 GB RAM and most of the CPU on this laptop, competing
directly with live ASR. More importantly, summaries and quizzes must not invent
textbook facts. The implemented pipeline is extractive by construction:

* summary sentences are selected from the stored transcript (TextRank-like
  scoring) and translated — no generated facts;
* quiz items are built from detected definitions and numeric statements, each
  carrying its source segment id, timestamp and quote;
* grading is a deterministic rubric-overlap check with an explicit
  `needs_review` band.

If a faster machine is available, an LLM could refine *English sentence
selection* while keeping translation and evidence links unchanged. That adapter
is not implemented here rather than shipped untested.

## Runtime memory budget (measured)

| Component | RSS |
|---|---|
| Whisper base int8 | ~330 MB |
| Marian int8 + tokenizer | ~200 MB |
| Piper voice | ~120 MB |
| FastAPI/uvicorn + Python | ~150 MB |
| **Total steady state** | **~800 MB** |

Leaves > 2 GB free on an 8 GB laptop for the OS and page cache.
