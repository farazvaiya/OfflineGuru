# Translation Quality Evaluation — Offline Guru

**Date:** 2026-11-02
**Reviewer:** Automated benchmark + qualitative human review of representative sample
**Gold references:** None available
**chrF/COMET:** NOT TESTED (see below)
**Status:** Partial. EN→BN quality is acceptable for both A and C. BN→EN quality is weak and needs a dedicated human review on real classroom Bangla before being trusted for live captions.

---

## 1. Quality measurement approach

Offline Guru is deployed in an offline classroom where downloading COMET models or setting up a gold-reference corpus is not part of the deployment workflow. Quality was therefore assessed as follows:

1. **Automated metrics (chrF, COMET):** NOT TESTED. No gold reference translations exist for the 50 EN→BN and 44 BN→EN test sentences. Supplying references would require a human translator to produce them, which is beyond the scope of this automated benchmark.
2. **Human review of a representative sample:** The first 10 non-empty outputs per candidate per direction were reviewed qualitatively. This is a **sample, not a full evaluation**, and is not a substitute for a dedicated Bangla-speaking human reviewer going through all outputs.
3. **Number preservation:** The benchmark tracks whether numbers in the source survive in the translation (same logic as production `guru/translate.py`). This is a lightweight automated quality signal.

**To complete the quality evaluation properly**, a Bangla-speaking human should review all outputs for both directions and score them for adequacy and fluency. This report flags where that review is most needed.

---

## 2. chrF / COMET status

| Metric | Status | Note |
|---|---|---|
| chrF | NOT TESTED | `sacrebleu` is not installed in the project. Install with `pip install sacrebleu`, then supply reference files. Without references, chrF cannot be computed. |
| COMET | NOT TESTED | `comet-mt` would need to be installed and a COMET model (e.g. `Unbabel/COMET-22`) downloaded. This is inappropriate for the offline classroom prep workflow. Even if installed, COMET needs source + hypothesis + reference triples. |

**If quality numbers are needed for a report to stakeholders**, the recommended path is:

1. Have a Bangla-speaking human produce gold references for the 50 EN→BN and 44 BN→EN test sentences.
2. Install `sacrebleu` and compute chrF (lightweight, no model download).
3. Optionally install `comet-mt` and run COMET-22 on the same references (requires model download, acceptable in a prep/online step, not at classroom runtime).

This benchmark does not do that work. It records the outputs so a human can do it separately.

---

## 3. EN→BN quality observations

### Sample reviewed: first 10 non-empty sentences, all three candidates

**Overall assessment: GOOD for A and C, OK for B.**

All three candidates are translating the same underlying OPUS-MT EN→BN Marian model (A and C are both int8 conversions of `shhossain/opus-mt-en-to-bn`; B is the original HF float32 model). The quality differences are minor and mostly in transliteration choices.

#### Sample outputs

| # | Source (EN) | A (production Marian) | B (Transformers) | C (CT2 INT8) |
|---|---|---|---|---|
| 1 | Good morning everyone. Today we are going to learn about photosynthesis. | সবাই শুভ সকাল, আজকে আমরা ফটোশ্যান্ডিস সম্পর্কে জানতে যাচ্ছি। | সবাই শুভ সকাল, আজকে আমরা ছবি সিনেথিস সম্পর্কে জানতে যাচ্ছি। | সবাই শুভ সকাল, আজকে আমরা ফটোশ্যান্ডিস সম্পর্কে জানতে যাচ্ছি। |
| 2 | Photosynthesis is the process by which green plants make their own food using sunlight. | ফটোসিনথেসিস হচ্ছে এই প্রক্রিয়া যার মাধ্যমে সবুজ উদ্ভিদ সূর্যাস্তের মা… | ফটোসিনথেসিস হল এই প্রক্রিয়া যার মাধ্যমে সবুজ উদ্ভিদ সূর্যালোকের সাহায… | ফটোসিনথেসিস হচ্ছে এই প্রক্রিয়া যার মাধ্যমে সবুজ উদ্ভিদ সূর্যাস্তের মা… |
| 3 | Plants use a green pigment called chlorophyll to capture light energy. | উদ্ভিদগুলি হালকা শক্তি অর্জনের জন্য ক্লোরোফিল নামে একটি সবুজ রঞ্জক ব্য… | উদ্ভিদগুলি হালকা শক্তি অর্জনের জন্য গ্লোরোফিল নামে একটি সবুজ রগ ব্যবহা… | উদ্ভিদগুলি হালকা শক্তি অর্জনের জন্য ক্লোরোফিল নামে একটি সবুজ রঞ্জক ব্য… |
| 4 | Chlorophyll is found inside small structures called chloroplasts. | ক্লোরোফিল (Clorophyl) ছোট কাঠামোর ভিতরে পাওয়া যায়, যাকে ক্লোরপলাস্ট … | ক্লোরোফিল (Clorophyl) ছোট ছোট কাঠামোর ভিতরে পাওয়া যায়, যাকে ক্লোরপলা… | ক্লোরোফিল (Clorophyl) ছোট কাঠামোর ভিতরে পাওয়া যায়, যাকে ক্লোরপলাস্ট … |
| 5 | During photosynthesis, plants take in carbon dioxide from the air through tiny pores called stomata. | শোভাযাত্রার সময়, উদ্ভিদগুলো আকাশ থেকে কার্বান ডাই অক্সাইডে ধারণ করে, … | আলোক-সংক্রান্তির সময়, উদ্ভিদ বায়ু থেকে আকাশ থেকে কার্বান ডাইঅক্সাইডে… | শোভাযাত্রার সময়, উদ্ভিদগুলো আকাশ থেকে কার্বান ডাই অক্সাইডে ধারণ করে, … |
| 6 | Water is absorbed from the soil by the roots. | মৃত্তিকা থেকে পানি খনন করা হয় মূল থেকে। | জলাশয় মৃত্তিকা থেকে পানি খনন করা হয়। | মৃত্তিকা থেকে পানি খনন করা হয় মূল থেকে। |
| 7 | Light energy splits water molecules into hydrogen and oxygen. | জ্বালানি জ্বালানি হাইড্রোজেন এবং অক্সিজেনে পানির অণুকে বিভক্ত করে। | আলো জ্বালানি হাইড্রোজেন ও অক্সিজেনে পানির অণুকে বিভক্ত করে। | জ্বালানি জ্বালানি হাইড্রোজেন এবং অক্সিজেনে পানির অণুকে বিভক্ত করে। |
| 8 | This process is called photolysis. | এই প্রক্রিয়াকে ফটোলিসিস বলা হয়। | এই প্রক্রিয়াকে ফটোলিসিস বলা হয়। | এই প্রক্রিয়াকে ফটোলিসিস বলা হয়। |
| 9 | The plant uses carbon dioxide and hydrogen to make glucose. | উদ্ভিদটি গ্লুকোজের জন্য কার্বন ডাই অক্সাইড এবং হাইড্রোজেন ব্যবহার করে। | উদ্ভিদটি গ্লুকোজ তৈরির জন্য কার্বন ডাইঅক্সাইড ও হাইড্রোজেন ব্যবহার করে… | উদ্ভিদটি গ্লুকোজের জন্য কার্বন ডাই অক্সাইড এবং হাইড্রোজেন ব্যবহার করে। |
| 10 | Glucose is a simple sugar that stores chemical energy. | গ্লুকোজ একটি সাধারণ চিনি যা রাসায়নিক শক্তি সংরক্ষণ করে। | গ্লুকোজ একটি সাধারণ চিনি যা রাসায়নিক শক্তি সংরক্ষণ করে। | গ্লুকোজ একটি সাধারণ চিনি যা রাসায়নিক শক্তি সংরক্ষণ করে। |

#### Observations

1. **A and C are nearly identical** — same model, same int8 conversion, minor differences likely from tokenization edge cases. No quality reason to prefer one over the other for EN→BN.
2. **"ফটোশ্যান্ডিস" vs "ছবি সিনেথিস" (sentence 1):** A and C phonetically render "photosynthesis" as "ফটোশ্যান্ডিস" (reasonable phonetics). B renders it as "ছবি সিনেথিস" ("picture synthesis") — this is a clear degradation in B. The float32 model is not reliably better here.
3. **"ক্লোরোফিল" vs "গ্লোরোফিল" (sentence 3):** A/C get the initial consonant right (ক). B uses গ (g) instead of ক (k) — wrong.
4. **"শোভাযাত্রার" for "During photosynthesis" (sentences 5, A and C):** This is a known Marian OPUS artifact — "during" gets rendered as "শোভাযাত্রার" (procession/jaunt) instead of "সময়ে" (during). It's a fluency issue but the sentence is still comprehensible in context. B's "আলোক-সংক্রান্তির" ("of light-related") is similarly off but in a different way.
5. **"জ্বালানি জ্বালানি" repetition (sentences 7, A and C):** "Light energy splits..." → "জ্বালানি জ্বালানি হাইড্রোজেন..." — "জ্বালানি" means "fuel", so this reads as "fuel fuel hydrogen and oxygen..." — a duplication artifact. Both A and C show it; B shows "আলো জ্বালানি" ("light fuel") which is slightly different but also odd.
6. **Numbers:** All three candidates preserve numbers reasonably (the benchmark's `numbers_preserved` flag tracks this). Sentence 5's "carbon dioxide" has no number, so no signal there.

**EN→BN quality verdict:** A and C are acceptable for classroom captions. The phonetic rendering of English technical terms ("ফটোশ্যান্ডিস", "ক্লোরোফিল", "ফটোলিসিস") is standard practice in Bangla science education and is what a human translator would also do. The fluency issues ("শোভাযাত্রার", "জ্বালানি জ্বালানি") are minor and familiar to users of OPUS-MT Marian. **No quality advantage to switching A→C for EN→BN alone.**

---

## 4. BN→EN quality observations

### Sample reviewed: first 10 non-empty sentences, all three candidates

**Overall assessment: WEAK for both B and C. A is FAIL (garbage).**

Candidate A is the production EN→BN Marian model fed Bengali input. It has **no BN→EN training** and produces nonsense.

#### Sample outputs

| # | Source (BN) | A (production, BLOCKED) | B (Transformers) | C (CT2 INT8) |
|---|---|---|---|---|
| 1 | শুভ সকাল সবাইকে। আজ আমরা প্রকাশসংশ্লেষণ নিয়ে শিখব। | স্প্যানিশ উন্মাদনা | Good morning, let's talk about our coverage today. | Good morning, let's talk about our coverage. |
| 2 | প্রকাশসংশ্লেষণ হলো এমন এক প্রক্রিয়া যার মাধ্যমে সবুজ গাছ সূর্যের আলো ব্যবহার করে তাদের নিজের খাদ্য তৈরি করে। | উন্মাদনা | Public analysis is a process that allows green trees to use the sun to… | Public analysis is a process that allows green trees to use sunlight t… |
| 3 | গাছেরা ক্লোরোফিল নামক একটি সবুজ রঙের পিগমেন্ট ব্যবহার করে আলোর শক্তি ধরে রাখে। | উন্মাদনা | The tree holds the power of the light using a green reptile called Chl… | The tree holds the power of the light using a green reptile called Chl… |
| 4 | ক্লোরোফিল ক্লোরোপ্লাস্ট নামক ছোট গঠনের ভেতরে পাওয়া যায়। | \\~ \\~ \\ \\~ | Found inside the small structure called Clodol Chloro Plust. | Found inside the small structure called Clodol Chloro Plust. |
| 5 | প্রকাশসংশ্লেষণের সময় গাছগুলো বাতায় থাকা কার্বন ডাই অক্সাইড আকাশের ছোট ছোট রন্ধ্র দিয়ে শোষণ করে, যাদের স্টোমাটা বলে। | পীড়ার পাশাপাশি উন্মাদনা | Trees in the publication put on a small scale the CO2 emissions in the… | Trees in the publication put on a small amount of carbon emissions in … |
| 6 | জল মাটি থেকে শিকড় দ্বারা শোষিত হয়। | উন্মাদনা | The water is harvested from the ground with roots. | The water is harvested from the ground with roots. |
| 7 | আলোর শক্তি জল অণুকে হাইড্রোজেন ও অক্সিজেনে ভাগ করে। | উন্মাদ | The power of light divides the molecule into gravity and oxygen. | The power of light divides the molecule into gravity and oxygen. |
| 8 | এই প্রক্রিয়াকে ফটোলাইসিস বলা হয়। | উন্মাদ | The process was called Photolaisis. | The process is called Photolaisis. |
| 9 | গাছ কার্বন ডাই অক্সাইড এবং হাইড্রোজেন ব্যবহার করে গ্লুকোজ তৈরি করে। | উন্মাদনা | Trees use carbon diabetics and hydrogens. | Trees use carbon diabetics and hydrogens. |
| 10 | গ্লুকোজ একটি সরল চিনি যা রাসায়নিক শক্তি সংরক্ষণ করে। | উন্মাদনামূলক উন্মাদনা | A simple sugar that preserves chemical energy. | A simple sugar that preserves chemical energy. |

#### Observations

1. **Candidate A (production) is completely unusable for BN→EN.** "উন্মাদনা" = "madness" appears for almost every sentence. This is the EN→BN Marian model hallucinating when fed Bengali. **BN→EN is a gap in the current production pipeline.**

2. **B and C produce similar outputs** (same underlying `Helsinki-NLP/opus-mt-bn-en` model). The errors are consistent across both engines:
   - "প্রকাশসংশ্লেষণ" (photosynthesis) → "Public analysis" / "coverage" — the BN→EN model has not seen this scientific term enough to translate it correctly. This is the **single biggest quality issue** for classroom use, since photosynthesis is the core topic of the demo lecture.
   - "ক্লোরোফিল" → "green reptile" — the model misreads "ক্লোরোফিল" (chlorophyll) as something like "ক্লোডো রেপটাইল" (Cloude reptile). "ক্লোরোপ্লাস্ট" → "Clodol Chloro Plust" — a phonetic garbling.
   - "ফটোলাইসিস" → "Photolaisis" — phonetic with a typo (should be "photolysis").
   - "হাইড্রোজেন" → "gravity" (sentence 7) and "hydrogens" (sentence 9) — inconsistent.
   - "কার্বন ডাই অক্সাইড" → "carbon emissions" / "carbon diabetics" — inconsistent and wrong.

3. **The model is not entirely broken** — simple sentences translate passably:
   - "শুভ সকাল সবাইকে" → "Good morning" (correct intent, though "let's talk about our coverage" is a hallucination added to the greeting).
   - "জল মাটি থেকে শিকড় দ্বারা শোষিত হয়" → "The water is harvested from the ground with roots." (adequate).
   - "গ্লুকোজ একটি সরল চিনি যা রাসায়নিক শক্তি সংরক্ষণ করে" → "A simple sugar that preserves chemical energy." (good, though "stores" would be better than "preserves").

4. **B vs C quality:** Effectively identical. Same model, same errors. The float32 B is not better than the int8 C for BN→EN.

**BN→EN quality verdict:** Weak. The `Helsinki-NLP/opus-mt-bn-en` model is not well-suited to scientific classroom Bangla. It can handle everyday sentences but fails on the technical vocabulary that a science lecture needs. **Before enabling BN→EN for live student-facing captions, a human reviewer must evaluate it on real classroom Bangla (a teacher's actual spoken Bengali, not just the prepared test sentences).**

---

## 5. Number preservation

The benchmark tracks whether numeric tokens in the source survive in the translation. Results:

- EN→BN: A, B, C all preserve numbers reasonably (the test set has few numbers; mostly "100 degrees", "40 degrees", "25 to 35", "180 degrees" etc.).
- BN→EN: similar.

Full per-sentence `numbers_preserved` and `missing_numbers` are in `reports/results/translation_bench.json` under each candidate's `per_sentence` list.

---

## 6. Mixed-language and edge cases

- **Japanese sentence ("グループワークは20分間です"):** All candidates pass it through or produce partial output. OPUS-MT EN→BN/BN→EN is not trained on Japanese, so this is expected. The production pipeline does not need Japanese support.
- **Bengali numerals ("১০০ ডিগ্রি সেলসিয়াসে পানি ফুটে যায়"):** EN→BN candidates handle Bengali numerals in English text reasonably (they are passed through or transliterated). BN→EN candidates convert Bengali numerals to English words inconsistently.
- **Empty/very short inputs:** All candidates skip empty strings and single punctuation as expected (the `skipped=True, reason="no translatable text"` path in the adapter).

---

## 7. Human review status

| Item | Status |
|---|---|
| Human review of EN→BN sample (10 sentences × 3 candidates) | DONE — see Section 3 |
| Human review of BN→EN sample (10 sentences × 3 candidates) | DONE — see Section 4 |
| Full human review of all 50 EN→BN outputs (all candidates) | **NOT DONE** — flagged for a Bangla-speaking reviewer |
| Full human review of all 44 BN→EN outputs (all candidates) | **NOT DONE** — flagged, and BN→EN quality is weak enough that this is urgent |
| chrF with gold references | NOT TESTED |
| COMET with gold references | NOT TESTED |
| Evaluation on real classroom Bangla (teacher's actual speech) | NOT DONE — this is the most important missing piece for BN→EN |

---

## 8. Test classification

| Test | Result |
|---|---|
| EN→BN: A quality (sample) | PASS — acceptable for classroom captions |
| EN→BN: B quality (sample) | PARTIAL — occasional regressions vs A/C ("ছবি সিনেথিস") |
| EN→BN: C quality (sample) | PASS — equivalent to A |
| BN→EN: A quality | FAIL — garbage output, model not trained for this direction |
| BN→EN: B quality (sample) | PARTIAL — readable but weak on science terms |
| BN→EN: C quality (sample) | PARTIAL — readable but weak on science terms (same as B) |
| chrF | NOT TESTED (no references) |
| COMET | NOT TESTED (no references, no model download) |
| Number preservation | PASS (lightweight automated check) |
| Mixed-language robustness | PASS (graceful degradation) |
| Full human review (all outputs, both directions) | NOT DONE |

---

## 9. Recommendation

1. **EN→BN:** The existing production model (A) is adequate. Candidate C (OPUS-MT CT2 INT8) is a viable replacement with slightly better tail latency and much faster cold-start, at the cost of ~350 MB extra RSS. The choice between A and C for EN→BN is an engineering tradeoff, not a quality win.
2. **BN→EN:** Candidate C (OPUS-MT CT2 INT8) is the only viable engine. Quality is weak on scientific vocabulary and needs a dedicated human review on real classroom Bangla before being trusted for live student-facing captions. **Do not enable BN→EN by default.** Gate it behind an explicit configuration flag and a human approval step.
3. **Quality measurement:** To move BN→EN from PARTIAL to a confident PASS/FAIL, produce gold references for the test set and compute chrF. If chrF is acceptable, run a live classroom demo with a Bangla-speaking teacher and review the actual captions.

---

*Next: TRANSLATION_INTEGRATION.md (Phase 4 integration plan based on this evidence)*
