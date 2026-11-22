# F-04 Root Cause Analysis: Streaming Final-Decode Threshold

**Finding**: F-04 (P1) — last 0–0.35 s of every utterance can be skipped:
`_worth_final_decode()` refuses a final decode when fewer than 0.35 s remains
un-decoded; the flush then commits only the previous hypothesis.

**Date**: 2026-11-14
**Status**: CONFIRMED PRESENT (threshold still hardcoded at pipeline.py:409)

---

## 1. Verified Facts

### 1.1 The code (`guru/pipeline.py:404-412`)

```python
def _worth_final_decode(self, window: SpeechWindow) -> bool:
    if window.duration_s <= 0:
        return False
    un_decoded = window.end_time - self._agreement.last_committed_time
    if un_decoded >= 0.35:
        return True
    return False
```

- `0.35` is a **hardcoded literal** (not a config knob; `config/default.toml`
  exposes `stream_step_s`, `stream_min_audio_s`, `stream_max_window_s`,
  `stream_context_s`, `stream_beam_size` — no threshold field).
- No test in `tests/` references `_worth_final_decode` or the 0.35 value
  (grep-verified).
- Call sites: `pipeline.py:374` (mid-stream end-of-speech skip) and
  `pipeline.py:395` (final flush at pipeline stop).

### 1.2 Why the threshold exists (verified from code comment)

`pipeline.py:375-377`:

> "The tail is only a few hundred milliseconds of audio. Whisper pads to a
> 30 s window, so decoding it costs a full call (~1.3 s on this CPU) for
> nothing: commit what we already have."

**Measured (this investigation, real whisper-base int8, 3 CPU threads,
this host)**: a 0.35 s tail decode costs ~1.0 s wall; 0.6 s ~0.96 s; 1.5 s
~1.02 s. Per-call overhead is flat — the comment's rationale is empirically
correct: the marginal cost of decoding a 0.35 s tail equals the cost of a
1.5 s decode (~1 s on this CPU).

### 1.3 Exactly how the tail remains undecoded

Sequence (verified by simulation, synthetic audio):

1. Speech ends; VAD sets `_ended`; ASR loop hits `ended and not
   _worth_final_decode(window)` → skip decode, run `_flush_stream_tail()`.
2. `flush_all()` commits the **agreement buffer's previous hypothesis** —
   words the model already produced in earlier decodes.
3. Audio in the window between `last_committed_time` and `end_time` is
   **retained** by `rebase(frontier)` (F-03 fix), so the *audio* is not
   discarded.
4. **But no hypothesis exists for it** — `flush_all` can only commit words
   `insert()` has seen. If no further speech arrives (end of sentence, end of
   lecture, or a long pause), the retained tail is **never decoded** and its
   words never reach captions.

### 1.4 Who is affected

- **Short utterances** (< 0.35 s un-decoded at end): tail words lost
  whenever they fall below the threshold — e.g. "food." at the end of "The
  plant uses sunlight to make food" if "food" lands in the last 0.35 s.
- **Pauses mid-lecture**: the tail is retained but undecoded until the next
  sentence's first decode — recovered only if the teacher continues; the
  recovery decode includes it because `rebase` kept the audio.
- **End-of-lecture speech**: the final-flush call site (line 395) uses the
  same threshold, so the lecture's last sub-0.35 s tail is lost even though
  no further speech will ever trigger a decode.

### 1.5 Magnitude

Historical finding (state/findings.json F-04): ~37 utterances/lecture ×
worst case 0.35 s ≈ 13 s speech/lecture worst case; typical loss smaller.
The F-03 fix bounds it (audio retained, not destroyed), but the *caption*
loss persists whenever no subsequent decode occurs.

## 2. Hypotheses (unverified unless stated)

- H-1: Lowering the threshold to ~0.15 s recovers most tails at the cost of
  one extra ~1 s decode per utterance (the comment's own numbers imply the
  trade is CPU time vs tail words). *Not measured end-to-end.*
- H-2: A "decode once more if any un-decoded audio exists" final flush at
  pipeline stop (line 395) would close the end-of-lecture loss for ~1 extra
  decode per session. *Not measured.*
- H-3: The threshold is unnecessary when the agreement buffer is empty
  (first utterance) — `flush_all` on an empty buffer commits nothing, so
  skipping guarantees loss. *Verified by code reading: `flush_all` with
  empty `buffer`+`new` returns [].*

## 3. Root Cause

`_worth_final_decode` conflates two costs: the Whisper per-call overhead
(~1 s, flat) and the information value of the tail. It optimizes CPU at the
price of guaranteed tail-word loss, with no config knob and no test.

## 4. Trade-offs of Changing the Threshold Alone

| Aspect | Lower to 0.15 s | Remove (decode always) | Keep 0.35 s |
|---|---|---|---|
| Tail words recovered | most (≥0.15 s) | all | none below 0.35 s |
| Extra decodes/utterance | ~1 (flat ~1 s CPU) | ~1 + every mid-stream end | 0 |
| CPU on i5-7300U | +~1 s/utterance | +~1 s/utterance | — |
| Caption latency | no change (flush path) | no change | — |
| Memory | unchanged (window capped 15 s) | unchanged | — |
| Accuracy | slight gain (tails decoded) | full gain | current |

Changing the threshold alone solves the *end-of-utterance* loss only if the
tail is ≥ the new threshold; a tail of 0.1 s with a 0.15 s threshold is still
lost. A threshold change is therefore a partial fix; the complete fix needs
"decode any non-empty un-decoded tail at end-of-speech" (Track A proposal).

## 5. Conclusion

F-04 is **CONFIRMED PRESENT** in the current tree. The mechanism is verified
by code inspection and a synthetic simulation; the cost rationale is verified
by a real whisper-base measurement (~1 s flat per call). The threshold is
still necessary as a CPU guard but is currently unconditional — it should be
configurable and bypassed when the agreement buffer is empty or at pipeline
stop.
