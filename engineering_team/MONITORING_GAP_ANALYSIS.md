# Monitoring Gap Analysis

**Date**: 2026-11-14
**Companion**: `TELEMETRY_INVENTORY.md` (the AVAILABLE list). This document is
the GAP list, each with disposition.

## 1. Gaps vs the master prompt's Phase 2 collector requirements

| # | Required collector input | Status | Disposition |
|---|---|---|---|
| G-1 | capture process status / alive-dead | MISSING from app | infer from `last_error` transitions + uptime; document limitation; Phase 5 agent can inspect process table on the TEST HOST (monitoring runs on the laptop) |
| G-2 | audio frames received / captured duration | MISSING | proxy via `segments_finalized` + `audio_carried_s`; label as ESTIMATE, never measured |
| G-3 | dropped frames / queue sizes | PARTIAL | queue drops ARE in PipelineStats; frame-level drops are not |
| G-4 | decode count / empty transcriptions | AVAILABLE | `stream_decodes`, `segments_empty` |
| G-5 | final-tail decode statistics | MISSING (skip is silent) | Phase 4 validator flags it as `NOT_EXECUTED` evidence gap; recommend (not implement) a prod counter in the human-review section |
| G-6 | translation count/latency/queue | PARTIAL | `translate_ms` summary + `segments_translated` + `enrichment_queue_drops`; per-call count not exposed |
| G-7 | TTS generation count/latency/cache | PARTIAL | `tts_ms` summary + `tts_queue_drops`; cache hit/miss MISSING |
| G-8 | student connection state / reconnects | PARTIAL | bus subscriber list + delivered/dropped; WS-level reconnect NOT exposed |
| G-9 | playback started/ended (audible) | IMPOSSIBLE server-side | rule 3.4: require student ack or human feedback; mark NOT_EXECUTED |
| G-10 | delivery attempts / acks / message latency | MISSING | bus has counts only; mark as missing, do not fabricate |
| G-11 | CPU/RAM/disk/load | AVAILABLE | `/api/health` resources + monitor-side psutil for per-process RSS |
| G-12 | temperature | MAYBE MISSING | collector treats absence as `unavailable`, never 0 |
| G-13 | network config / internet availability | AVAILABLE | `current_network()`; offline probe is informational only |

## 2. Process/architecture gaps

| # | Gap | Impact | Disposition |
|---|---|---|---|
| G-14 | `ClaudeCodeAdapter` has no runtime handler → every run is `pending_external` | agents never execute | Phase 5 implements subprocess runtime w/ explicit config |
| G-15 | No physical-test session entity anywhere | Phase 1 builds it |
| G-16 | No telemetry DB | Phase 3 builds it (separate from app DB) |
| G-17 | No dashboard | Phase 3 builds local one |
| G-18 | No validation engine (PASS/FAIL vs reference) | Phase 4 builds it |
| G-19 | `engineering_harness/` vs `engineering_team/` duplication risk | confusion | ALL new work in `engineering_team/`; harness left untouched |
| G-20 | No git repo → no branch isolation | Phase 6 uses snapshot+digest+single-writer lock per existing pattern |

## 3. Physical-hardware gaps (cannot be closed in this container)

| # | Gap | Required evidence |
|---|---|---|
| G-21 | real microphone capture | human-run Scenario A on i5-7300U |
| G-22 | audible student playback | student ack / human observation |
| G-23 | RF capacity with 30 devices | physical hotspot test |
| G-24 | 45-min thermals | long session on target laptop |
| G-25 | room noise / distance / accents | Scenario E human records |

## 4. Priority order (drives the roadmap)

1. G-15/G-16/G-18 (session, DB, validator) — everything depends on these.
2. G-1..G-4, G-11 (collectors that work with AVAILABLE telemetry) — honest
   coverage with explicit ESTIMATE/UNAVAILABLE labels.
3. G-14 (agent runtime) — needed before any AI diagnosis claim.
4. G-5/G-7/G-8/G-10 — documented as missing; recommend prod counters to the
   human (no prod change without approval).
5. G-21..G-25 — physical; stay NOT_EXECUTED until a human runs them.

## 5. What monitoring must NOT do

- Modify `guru/` in any way (rule: no production modification).
- Poll faster than 1 Hz (avoid load on the lecture).
- Block on collector failure (bounded queues + independent failure).
- Treat missing as zero (rule 3.2).
- Claim playback from delivery events (rule 3.4).
