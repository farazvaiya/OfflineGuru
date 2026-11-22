# Stability and retention report

**Objective:** remove the recorded limitation that expired sessions were only
cleaned at startup, verify the replacement on a live server, and report what was
and was not observed about long-running behaviour.

## Implemented

| Piece | File | Behaviour |
|---|---|---|
| Background loop | `guru/server/app.py` (`_retention_loop`) | sleeps `storage.retention_interval_s`, then runs the existing `_purge_expired` in a worker thread |
| Lifecycle | `lifespan` | exactly one task created, cancelled in `finally`; no second worker can start because the task is created once per process |
| Scheduling | `config/default.toml`, `guru/config.py` | `retention_interval_s = 3600` (hourly); `0` restores startup-only behaviour |
| Deletion scope | `storage.purge_older_than` | `created_at < cutoff AND status != 'active'` — **active sessions are never deleted** |
| Transaction safety | `storage.purge_session` | each session's rows are deleted under the single storage lock and committed once per table group; audio/cache files removed after the rows |
| Error handling | loop body | any exception is logged and the loop continues; a sweep cannot kill the server |

## Verification

### Unit tests — `tests/test_retention.py` (7 tests, all pass)

| Test | Asserts |
|---|---|
| expired ended session is purged, active kept | both directions of the retention rule |
| recent ended session is kept | cutoff is respected |
| retention disabled keeps everything | `retention_days = 0` disables |
| background loop purges without restart | loop sweeps on a 50 ms interval |
| loop is cancellable before the first sweep | clean shutdown, no stray task |
| purge removes documents, answers, segments | full data removal |
| (plus the storage-level purge assertions in the same file) | |

### Live server check

Server started with `GURU_STORAGE__RETENTION_INTERVAL_S=2`,
`GURU_STORAGE__RETENTION_DAYS=1`, real `uvicorn` on port 8012.

```
LIVE RETENTION: session 4ff1311a11d2 before sweep=200, after 6s=404
server log: retention: removed 1 session(s) older than 1 days
```

The session was created, ended, backdated 5 days, and observed to disappear
without a restart. This is the exact scenario the previous report listed as a gap.

### Soak observed incidentally

One server process stayed up through six consecutive load runs (~10 minutes of
continuous lecture replay, 1→30 clients) with **0 socket errors, 0 pipeline drops,
0 restarts**. RAM available moved from 2067 MB to 1745 MB over the sequence, which
is model + TTS cache residency, not unbounded growth.

## Not verified

1. **No 45-minute or multi-hour soak.** Thermal throttling, SQLite WAL growth and
   TTS cache growth over a full school day were not measured. `/api/health`
   exposes `cpu_percent`, `load1`, `disk_free_gb` and latency percentiles so this
   can be observed on site.
2. **Retention during an active lecture with a huge backlog** was not exercised;
   the loop only deletes non-active old sessions, and each sweep is one thread.
3. **No fault injection** (kill -9, disk full, DB locked). Recovery after those is
   untested.
