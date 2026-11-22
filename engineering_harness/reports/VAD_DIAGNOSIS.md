# VAD / Segmentation Diagnosis

**Findings:** F-05 (P1, dropped remainder + clock drift), F-06 (P2, overlapping segments after force-cut)
**Evidence:** `artifacts/segment_alignment_baseline.json`, `artifacts/segment_alignment_emma_baseline.json`
**Reproduce:** `.venv/bin/python engineering_harness/diag_segments.py [--wav …]`

---

## 1. Two VAD implementations

| | `audio.Segmenter` | `realtime.SpeechWindow` |
|---|---|---|
| Used by | segment mode, `eval_asr.py`, `bench_asr_upload.py` | streaming mode (default), `probes/stream_flush.py` |
| Input | arbitrary-length float32 chunks | arbitrary-length float32 chunks |
| Frame handling | `n = len(samples)//320`; **remainder dropped** | `_pending` carry buffer |
| Output | finished `Segment` objects | rolling buffer + `started/ended` flags |
| Timestamp source | frame counter × 20 ms | sample clock, absolute |

The energy VAD itself is the same in both: adaptive noise floor capped at `vad_threshold`, trigger `max(0.006, 2.5×floor)`, 600 ms silence to end, 300 ms pre-roll. On the demo lecture it detects 144.4 s of speech in a 160 s file with zero false rejections — the threshold is **not** the problem.

## 2. F-05 — the remainder is discarded

```python
def feed(self, samples):
    n = len(samples) // FRAME_SAMPLES          # FRAME_SAMPLES = 320
    for i in range(n):
        frame = samples[i*320:(i+1)*320]
        ...
    return out                                  # samples[n*320:] is lost
```

`feed()`'s docstring says "keep caller responsible for frame alignment". Callers are not aligned:

* `MicCapture` calls `read(3200)` on a pipe — short reads are legal and produce any byte count.
* Any integration that pushes 1024-sample audio blocks loses 64 samples (6.25 %) per call.

### Measurement (60 s of `lecture.wav`)

| Chunk size | Segments | Audio covered | Internal clock | Drift |
|---|---|---|---|---|
| 1600 samples (5 frames) | 14 | 54.66 s | 60.00 s | 0.0 % |
| 1000 samples (3 frames + 40) | 14 | 52.74 s | **57.60 s** | **4.0 %** |

Two distinct harms:

1. **Audio loss** — 3.51 % less audio reaches the segmenter for a 40-sample remainder, and the loss grows as chunk size shrinks (1-sample chunks would drop 99.7 %).
2. **Clock drift** — every timestamp is computed from `_total_frames`, so the transcript's `start_s`/`end_s` fall behind the real recording by the same 4 %. Captions then appear to "lag" and segment boundaries no longer line up with the audio a student could replay.

Because streaming mode uses `SpeechWindow` (which has the carry buffer), this defect is invisible in the default path — and fully active in the mode that produced the 5.6 % WER headline.

## 3. F-06 — overlapping segments at force-cuts

`Segmenter._finish()` clears `_frames` and `_in_speech` but **not `_preroll`**. On the next speech frame:

```python
self._preroll.append(frame)                # deque(maxlen=15) still holds pre-cut frames
self._frames = list(self._preroll)         # 14 old + current
self._speech_start_frame = frame_index - len(self._frames) + 1
```

so the new segment starts up to 280 ms *before* the previous one ended.

### Measurement (200 s cross-speaker clip, long unbroken speech)

| Property | Value |
|---|---|
| Consecutive pairs with negative gap | **12** |
| Overlap per pair | 0.14 – 0.28 s |
| Total duplicated audio | **3.06 s** |
| All overlaps immediately after a 12.00 s force-cut | yes |
| Overlaps with no force-cut present | 0 |

Effect: duplicated words at boundaries and timestamp ranges that cannot both be true. Not catastrophic, but it corrupts any downstream alignment (quiz evidence, clip extraction, latency measurement) that trusts `start_s`/`end_s`.

## 4. What is *not* wrong with the VAD

* Threshold calibration on in-domain audio: 144.4 s of 160 s detected, 0 dropped segments, 0 empty decodes.
* Pre-roll duration: 300 ms is enough to keep the first phoneme.
* Trailing-silence trim (keeps ~150 ms): Whisper output on short utterances is clean.
* Max-segment force-cut: necessary; without it a monologue blocks captions for its whole duration.

## 5. Hypothesis table

| Hypothesis | Test | Verdict |
|---|---|---|
| VAD false-rejects quiet speech | compare segment coverage to known speech duration | rejected — 144.4 s detected, none dropped |
| Remainder loss is theoretical (callers aligned) | 1000-sample feed vs 1600-sample feed | **accepted** — 4.0 % drift measured |
| Overlap comes from VAD re-triggering | correlate overlaps with 12 s cuts | **accepted** — 12/12 follow a force-cut |
| `max_segment_s=10` in config vs 12 in code | config says 10.0, `Segmenter` default is 12.0; `_new_segmenter` passes the config value | **config value wins** in production; the 12 s figures come from eval scripts constructing `Segmenter()` directly — a benchmark/production divergence worth noting |

## 6. Fix direction (Phase 2)

1. Give `Segmenter` a `_pending` carry buffer, mirroring `SpeechWindow`, and consume every sample exactly once.
2. Track `_last_end_frame` at `_finish`; on a new speech start, discard pre-roll frames at or before it (or clamp `start_s`).
3. Add tests: 1-sample chunks cover the whole input; no two segments overlap; clock equals audio duration within one frame.

## 7. Confidence

High. Both effects are arithmetic consequences of the code, reproduced by a deterministic script on real audio, with no model or timing dependence.
