# Telemetry Inventory

**Date**: 2026-11-14
**Method**: direct inspection of `guru/` source (not reports).
**Convention**: AVAILABLE = readable today without modifying production code.

## 1. Already available (via `/api/health`, `guru/server/app.py:401-433`)

| Metric | Source | Notes |
|---|---|---|
| uptime_s | app context | process lifetime |
| pipeline health | `ctx.pipeline.health()` | includes PipelineStats: segments_finalized/translated/empty, queue drops (asr/enrichment/tts), stream_decodes/commits/partials/backlog, audio_carried_s, queue_backlog_peak, last_error |
| model status | `ctx.model_status` | loaded/error per model |
| RAM total/available, CPU %, load1, disk free | psutil | process-wide, not per-stage |
| network state | `current_network()` | interface/hotspot info |
| bus subscribers | `ctx.bus.stats()` | per-client delivered/dropped/queued |
| metric summaries | `storage.metric_summary(kind)` | asr_ms / translate_ms / tts_ms / e2e_ms p50/p95/max |
| warnings | `_health_warnings(ctx)` | composite |

## 2. Available in app DB but not exposed (`guru/storage.py`)

| Metric | Source | Access |
|---|---|---|
| per-segment timestamps, text, languages | `segments` table | read-only SQL |
| per-metric raw samples | `metrics` table (kind, value, meta, created_at) | `record_metric` exists; no HTTP read endpoint |
| student answers/grades | `answers` | REST exists |
| sessions (start/stop, wav path, speed) | `sessions` | REST exists |

## 3. NOT available today (missing telemetry)

| Gap | Why it matters | How monitoring gets it (no prod change) |
|---|---|---|
| capture process alive/dead | F-01/F-02 symptom | `/api/health` has no capture state; monitoring must infer from `pipeline.health()` + error events; **limitation: no direct arecord liveness** |
| audio frames received / dropped frames | F-01 | not exposed; proxy: `segments_finalized` delta + `asr_queue_drops` |
| capture restart events | F-02 | not exposed; proxy: `last_error` transitions |
| decode count / empty transcription count | F-04 tail loss | `stream_decodes`, `segments_empty` ARE in PipelineStats — available |
| final-tail decode statistics | F-04 | NOT exposed (threshold skip is silent); **limitation: must be inferred from `stream_decodes` + `audio_carried_s` or accepted as missing** |
| TTS cache hits/misses | cost | not exposed; **limitation** |
| playback started/ended (audible) | rule 3.4 | server cannot know; **must remain NOT_EXECUTED; require student ack or human feedback** |
| WS message delivery latency | network QA | bus has delivered/dropped counts, no per-message latency; **limitation** |
| per-process RAM of the app | budget | psutil from monitor side (read /proc) — available without app change |
| temperature | thermal | `sensors`/sysfs may not exist; collector must treat as unavailable |
| room noise, distances, speaking speed | scenario E | human-recorded only |

## 4. Reusable existing components

| Component | Reuse |
|---|---|
| `guru/server/app.py` `/api/health` | primary telemetry source (poll 1 Hz) |
| `guru/storage.py` `metrics` table + `metric_summary` | raw per-segment latency samples |
| `guru/bus.py` `EventBus.stats()` | per-student delivery/drop evidence |
| `guru/pipeline.py` `PipelineStats` | ASR/TTS/queue counters |
| `engineering_team/harness/db.py` | migration pattern for the new telemetry DB |
| `engineering_team/harness/claude_adapter.py` | base for Phase 5 runtime (currently `pending_external`) |
| `engineering_harness/artifacts/*` | historical baselines for comparison (Phase 7) |

## 5. Explicit non-goals

- No raw audio recording (default off; opt-in only, rule 3.4/§15).
- No transcript content in telemetry payloads (privacy; text stays in app DB).
- No second model load; no inference in collectors.
