# F-04 Decision Report: Final-Decode Threshold

**Date**: 2026-11-14
**Finding**: F-04 (P1) — `_worth_final_decode()` skips final decode when fewer than 0.35 s of audio remain un-decoded.
**Final status**: **READY_FOR_HUMAN_DECISION**

No production code has been modified. F-04 remains confirmed present in the current tree. A fix is proposed, not implemented, not integrated, not approved.

---

## 1. Root Cause Decision

The 0.35 s guard was introduced to avoid paying a full Whisper call for a tiny final tail. The cost rationale is empirically valid on this host: whisper-base int8 with 3 CPU threads costs roughly 1 s flat per decode, independent of tail length (0.35 s: 1002 ms; 0.6 s: 957 ms; 1.5 s: 1020 ms).

But the guard is currently unconditional in the wrong respects:

1. If the agreement buffer is empty, `flush_all()` commits nothing. Skipping the decode discards the tail unconditionally.
2. At pipeline stop, no later speech will trigger recovery; skipping the decode loses the lecture's final sub-threshold audio.
3. The threshold is hardcoded and untested.

The F-03 `rebase()` fix retains the audio in the window, but it does not create a transcription for that audio when no subsequent decode runs.

## 2. Track Comparison

Identical comparison conditions: same streaming defaults, same whisper-base int8 model, same CPU-only target class, same synthetic audio/word-timestamp harness for mechanism checks, same existing regression suite.

| Criterion | Track A: Custom | Track B: Open-source |
|---|---|---|
| Mechanism | Empty-agreement bypass + config knob + unconditional stop decode | Adopt an upstream "never skip short decode" pattern |
| Final-tail word recovery | Guaranteed when agreement empty or at stop; configurable otherwise | Guaranteed, but paid on every end-of-speech |
| Frame loss | None introduced; window semantics unchanged | N/A — adoption would replace the streaming layer |
| Duplicate frames | Unchanged; protected by existing frontier-reset tests | Would need revalidation after re-port |
| Timestamp correctness | Unchanged; same `rebase` frontier | Same family of frontier trimming, but a different implementation |
| Caption latency | No hot-path regression; stop path adds one final decode per session | No threshold delay, but much larger integration risk |
| CPU/RAM | +~1 s only when agreement empty; +~1 s per session at stop; no memory change | +~1 s per end-of-speech on every utterance; heavier runtime footprint |
| Regression risk | Low; ~15-line change, default behavior can remain unchanged if only A-1/A-3 are taken | High; replaces or re-ports proven Offline Guru streaming logic |
| Existing suite | T-A1..T-A6 synthetic plan; T-A4..T-A6 not executed | No drop-in test path |
| License/offline | Own code, fully offline | WhisperLiveKit is Apache-2.0 and offline, but integration cost dominates |

## 3. Recommendation

Recommend **Track A**, specifically:

1. **A-1**: if `self._agreement.committed` is empty and any un-decoded audio remains, run the final decode.
2. **A-3**: at pipeline stop, decode any non-empty remaining window once regardless of threshold.
3. **A-2**: expose `asr.stream_tail_decode_min_s` as a config field defaulting to 0.35 so the operator can tune it without code changes.

Do **not** adopt an external library for this finding. WhisperLiveKit is useful as evidence that never skipping a short decode is safe, but it does not provide a bounded, configurable replacement for Offline Guru's `_worth_final_decode()`.

## 4. Human Decision Required

Choose one:

- **Approve Track A implementation** — would modify `guru/pipeline.py` and possibly `guru/config.py` / `config/default.toml`; must be done as a separate change with tests T-A1..T-A6.
- **Approve Track A-1/A-3 only** — smallest correctness fix with unchanged default threshold.
- **Keep documented trade-off** — leave F-04 open as a known limitation; record rejection reason in `state/findings.json`.
- **Request more measurement** — e.g., end-to-end WER or stop-flush benchmark before approving any code change.

## 5. Not Done

- No production files modified.
- No commits, merges, or integrations.
- No physical-microphone validation.
- No end-to-end WER benchmark after a fix.
- No implementation executed against the real model during Track A simulation.
- F-03 remains `CONFIRMED_FIXED — READY_FOR_HUMAN_CLOSURE`, not marked closed.

## 6. Minimum Acceptance Criteria Before Integration

If Track A is approved:

- Synthetic tests T-A1..T-A6 pass.
- Full suite remains green.
- End-of-lecture WAV replay shows the final tail is decoded once.
- Offline Guru's public behavior otherwise unchanged.
- Human approval is recorded before merge or checkpoint integration.
