# Phase 1 Final Report — Research and Root-Cause Diagnosis

**Date:** 2026-11-10
**Gate:** `reports/GATE_PHASE1.json`
**Scope:** architecture, baselines, capture, VAD, ASR, translation, TTS, latency, upstream research
**State at close:** 9 findings (3 × P0, 2 × P1, 4 × P2), 8 with root causes, 1 (F-09) with a measured cause and an open optimum.

---

## Executive summary

The classroom symptom in the brief — missing spoken words, incomplete transcript segments, delayed output — is **reproduced and explained**, and it is not the model.

Three independent P0 defects can silence or truncate a lecture:

1. **Capture deadlocks on its own stderr pipe.** A real arecord emits ALSA overrun warnings; nothing reads that pipe; after 64 KiB arecord blocks and PCM stops. Measured: **2.2 s of audio from a 10 s run**, no error recorded, health endpoint still "running" (F-01).
2. **Capture failure is unsupervised.** If arecord exits, the reader thread ends, `last_error` is set, and nothing restarts it or reports it (F-02).
3. **The streaming pipeline deletes live speech at every pause.** The end-of-speech flush resets the rolling window after a ~1.1 s final decode, by which time the teacher has resumed. Measured: **22.2 s of 59.6 s destroyed**, 13 of 14 resets occurring while speech was active (F-03). On the full lecture the shipped default yields **54.1 % WER / 206 of 390 words**, against **5.64 % WER / 391 of 390 words** for the segment path on identical audio.

And one measurement integrity problem that had been published as a model limitation:

4. **The 95.6 % "cross-speaker WER" is a wrong-reference artifact.** The Emma Watson audio was scored against the photosynthesis lecture transcript (`--text` defaults to `lecture.txt` while `--wav` was changed). Reproduced exactly: WER **0.9564**, 353 hypothesis words, matching the stored artifact to three decimals (F-07). The transcript itself is coherent and complete; base and small agree on **97.5 %** of words.

Translation (chrF 0.5923, p50 135 ms) and TTS (RTF 0.12) are healthy and are not implicated.

---

## P0 — must be fixed before anything else

### P0-1 · F-01 · Capture deadlock on an unread stderr pipe
* **Evidence:** `artifacts/capture_stall.json` — 2.20 s delivered / 10.01 s, 7.29 s silent, child alive and blocked, `last_error=null`.
* **Cause:** `stderr=subprocess.PIPE` with no reader anywhere in the capture lifetime.
* **Fix:** drain stderr on a daemon thread into a bounded ring buffer; carry odd-length reads.
* **Test:** stderr flood must not interrupt PCM delivery; odd-byte reads must not kill the reader.

### P0-2 · F-02 · Silent, unrecoverable capture loss
* **Evidence:** same probe, exit scenario — thread dead, `last_error="arecord exited"`, no restart, `/api/health` has no capture field.
* **Cause:** fire-and-forget start with no supervisor.
* **Fix:** `MicCapture.alive/state`, bounded restart with backoff, capture state in `/api/health`, `pipeline.error{stage:"capture"}` event; `start_new_session=True`.
* **Test:** killing arecord mid-capture is detected, reported, and restarted.

### P0-3 · F-03 · End-of-speech flush discards live audio
* **Evidence:** `artifacts/stream_flush.json` (22.2 s / 59.6 s, 13/14 resets during speech), `artifacts/pipeline_baseline_streaming.json` vs `artifacts/pipeline_baseline_segment.json` (206 vs 391 words).
* **Cause:** `_flush_stream_tail()` calls `SpeechWindow.reset()` unconditionally after a decode that outlives the pause.
* **Fix:** never reset at a flush. Trim to the committed frontier (keeping `context_s`), reset the agreement at that same frontier, and leave all audio after it for the next decode.
* **Test:** two utterances with a short pause, fed in real time, must yield zero lost samples and both utterances fully transcribed; invariant `words_out/words_in ≥ 0.98` on the reference lecture.

### P0-4 · F-07 · Published WER derived from a mismatched reference
* **Evidence:** `artifacts/asr_wer_emma_reproduction.json` (0.9564, 353 words, 390-word reference), `artifacts/asr_independent_baseline.json` (0.975 agreement).
* **Cause:** `eval_asr.py` gives `--wav` and `--text` independent defaults.
* **Fix:** make `--text` mandatory; record the pair; refuse to report when the counts differ by >2×; correct the report.
* **Test:** running the eval without `--text` must fail; a mismatched pair must be rejected.

---

## P1 — reliability and correctness

### P1-1 · F-05 · Segmenter discards the sub-frame remainder
* **Evidence:** `artifacts/segment_alignment_baseline.json` — 1000-sample chunks: clock 57.6 s vs 60.0 s (**4.0 % drift**), 3.51 % less audio covered.
* **Fix:** internal pending buffer (mirror `SpeechWindow`).
* **Test:** 1-sample chunks cover the entire input and the clock equals the duration.

### P1-2 · F-04 · Final-decode threshold skips sentence tails
* **Evidence:** `guru/pipeline.py::_worth_final_decode` (0.35 s), corroborated by upstream WhisperLiveKit #411.
* **Fix:** lower to ~0.15 s; the retained window recovers anything shorter once F-03 is fixed.
* **Test:** a short final utterance (< 0.35 s of new audio) is still transcribed.

---

## P2 — quality, observability, efficiency

### P2-1 · F-06 · Overlapping segments after a force-cut
* **Evidence:** `artifacts/segment_alignment_emma_baseline.json` — 11 overlapping pairs, 3.06 s duplicated.
* **Fix:** clamp a new segment's start to the previous `end_s`; drop pre-roll frames older than the cut.
* **Test:** no two consecutive segments overlap on a 200 s clip.

### P2-2 · F-08 · Streaming segments report `asr_ms=0.0` and `confidence=0.0`
* **Evidence:** `baselines/latency_baseline.json` (asr p50 0.0) vs the pipeline's own decoder timing (1104 ms).
* **Fix:** carry the mean decode latency and minimum word probability covering each phrase.
* **Test:** a streaming segment's `asr_ms` is > 0 and `confidence` in (0, 1].

### P2-3 · F-09 · Decode cadence wastes CPU (Phase 3 lever, not a Phase 2 fix)
* **Evidence:** RTF 1.1–3.4 on 0.3–1.0 s windows; 27/125 backlog events.
* **Action deferred to Phase 3** with WER guardrails; changing cadence in Phase 2 would confound the correctness fixes.

---

## Repair order and rationale

1. F-03 first — it is the largest correctness win (47 % of words) and it changes what every later benchmark measures, so the baseline for Phase 3 must be taken after it.
2. F-01/F-02 next — they are the difference between "the system sometimes loses words" and "the system sometimes stops", i.e. classroom-blocking.
3. F-05/F-04 — correctness in the non-default path and tail handling.
4. F-07 immediately, before any accuracy claim is published again.
5. F-06/F-08 — clean-up that makes future measurements trustworthy.
6. F-09 only in Phase 3.

## What Phase 1 deliberately did **not** do

* No application behaviour was changed; only measurement scripts, probes and reports were added (`guru/` untouched at close).
* No model was replaced, downloaded or re-converted.
* No acceptance target was invented before a baseline existed; targets in `config/acceptance_criteria.yaml` are expressed relative to `baselines/`.
* Physical microphone, real phones over a hotspot, and 45-minute thermal behaviour were **not** tested and will not be claimed.

## Gate checklist

| Criterion | Status |
|---|---|
| Architecture inspected and written | PASS — `PHASE1_REPOSITORY_AUDIT.md`, `ARCHITECTURE_MAP.md` |
| Existing tests and benchmarks reproduced | PASS — 239 tests, WER, latency, translation, TTS, 30-client load |
| Observed problems carry evidence | PASS — 9 findings, each with an artifact |
| Root causes identified or marked unresolved | PASS — 8 root-caused, F-09 measured with an open optimum |
| Competing hypotheses tested | PASS — 4 hypotheses rejected with data (incl. the cross-speaker explanation) |
| Prioritised repair plan | this document, P0/P1/P2 |
| QA independent verification | `reports/QA_PHASE1.md` |
| Evaluator approval | `reports/EVALUATOR_PHASE1.md`, `state/signoffs.json` |
