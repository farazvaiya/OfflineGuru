# QA Review — Phase 1 (independent verification)

**Reviewer:** qa-agent
**Date:** 2026-11-10
**Method:** re-derived every headline number from the stored artifacts (not from the written reports), re-ran the two decisive probes, checked that every cited evidence path exists, and attempted to falsify each P0 finding.

---

## 1. Evidence integrity

| Check | Result |
|---|---|
| Findings recorded | 9 |
| Evidence paths cited | all exist (0 missing) |
| Every finding carries observation/evidence/hypothesis/experiment/result/conclusion/confidence/recommendation | yes |
| Artifacts carry a source digest | yes (`_evidence.source_digest`) |
| Source modified during Phase 1 | **no** — `find guru tests config -newermt "2026-11-10 03:50"` returns nothing, i.e. no application code changed while diagnosing |
| Baseline files write-protected from later runs | by convention: `run_bench.py` only writes `baselines/` when `--label baseline` |

## 2. Numbers re-derived from artifacts

| Claim in report | Recomputed | Match |
|---|---|---|
| Streaming: 206 words / 390, coverage 0.528, WER 0.541 | 206/390 = 0.528; WER 0.5410 | ✅ |
| Segment: 391 words / 390, WER 0.056 | 391/390 = 1.003; WER 0.0564 | ✅ |
| Flush probe: 22.2 s of 59.6 s discarded (37 %), 13/14 resets during speech | 22.2/59.6 = 37.2 %; 13/14 | ✅ |
| Capture: 2.2 s of 10 s, ratio 0.22, no error | exact | ✅ |
| Emma: WER 0.9564, 390-word reference, 353 hypothesis words | exact | ✅ |
| Independent sample: base 353 words, small 355, agreement 0.975 | exact | ✅ |
| Segmenter drift 4.0 %, coverage −3.51 % | exact | ✅ |
| Overlap: 12 pairs, 3.06 s duplicated | exact | ✅ (report initially said 11; **corrected on review**) |

One discrepancy was found and fixed during this review: the overlap pair count was written as 11 in `state/findings.json` and `VAD_DIAGNOSIS.md`; the artifact says **12**. Both were corrected. This is recorded rather than silently edited because it is exactly the kind of small transcription error that erodes trust in a report.

## 3. Falsification attempts

### F-01 (capture deadlock)
* *Could the stall be the shim's own pacing rather than the pipe?* The shim paces stdout with `sleep 0.1` per chunk; delivery stops at 22 chunks while the process is still alive, and the 22 × ~2.9 KB ≈ 64 KiB coincidence matches the pipe capacity. Re-ran: reproducible (2.2 s / 10 s both times).
* *Could the reader thread be dead?* No: `reader_thread_alive=true`, blocked in `read(2)`. The distinction matters — a dead thread would set `last_error`; this one cannot.
* **Verdict: confirmed.** Caveat honestly stated: no real ALSA device was used.

### F-02 (unsupervised capture)
* Verified by inspection that `app.py` starts capture once and `/api/health` contains no capture field (`grep -n capture guru/server/app.py` shows only start/stop, no health read).
* Probe exit scenario: thread dead, `last_error="arecord exited"`, no restart.
* **Verdict: confirmed.**

### F-03 (streaming audio loss)
* *Could the WER difference be caused by the eval harness rather than the pipeline?* The same harness (`run_pipeline_eval.py`) produces 5.64 % in segment mode and 54.1 % in streaming mode on the same file, same model, same VAD thresholds. The instrument is therefore not biased against streaming.
* *Could LocalAgreement be withholding words?* `stream_commits` increases monotonically; the pending buffer never exceeds 6 words; the missing words never appear in any hypothesis.
* *Could `trim_to` be over-cutting?* Trim events are logged with before/after bounds; no trim removed audio beyond the committed frontier minus context.
* *Is 22.2 s lost in 60 s consistent with 184 missing words in 160 s?* 37 % of audio lost ≈ 47 % of words lost — the same order, with words lost slightly more because truncation also destroys phrase context.
* **Verdict: confirmed, high confidence.**

### F-07 (wrong reference)
* *Could the 0.956 agreement be coincidence?* The hypothesis word count (353) also matches the stored JSON exactly, and the reference word count (390) equals `lecture.txt` exactly. Two independent values matching is not coincidence.
* *Could the Emma transcript actually be bad?* Read end to end; it is coherent, complete, and base/small agree on 97.5 % of words.
* **Verdict: confirmed. The published explanation in `EMMA_WATSON_ASR_EVALUATION.md` must be corrected.**

## 4. What QA refuses to accept as proven

| Claim | Why not |
|---|---|
| "The absolute cross-speaker WER is ~5 %" | No human reference exists for the clip. Only agreement (0.975) and coherence are supportable. |
| "The microphone path loses words in the field" | The mechanism is reproduced with a shim; no physical device was available. Stated as a mechanism, not a field measurement. |
| "p95 latency is 15.3 s in a real classroom" | That is a 45 s loopback run with n=15 phrases and one outlier; p95 ≈ max at that sample size. Reported with n and caveat. |
| "F-09 fix will reduce latency by X" | No experiment yet. Deferred to Phase 3. |

## 5. Preservation checks

* No `guru/` change during Phase 1.
* No model replaced, moved or re-converted.
* No user data touched: `data/guru.db` unmodified (worker probes/pipeline evals use `/tmp/og/` or purge their own session).
* Benchmarks wrote only to `engineering_harness/artifacts`, `baselines`, `/tmp`, and the project's existing `data/eval/loadtest_30.json` (its documented output).

## 6. Conclusion

**PASS.** The nine findings are supported by reproducible artifacts, the three P0s are independently re-verified, one transcription error was caught and corrected, and the limits of what was tested (no microphone, no phones, no thermal run, no human reference for the cross-speaker clip) are stated rather than glossed over.

Phase 1 may proceed to evaluator approval.
