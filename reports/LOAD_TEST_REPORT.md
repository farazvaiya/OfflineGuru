# Load test report

**Objective:** measure how the application behaves as simulated clients scale from
1 to 30, against a real server with real models.

**Environment:** `uvicorn` on `127.0.0.1:8010` (throwaway DB), 4-thread CPU, 7 GB
RAM. Clients are Python WebSocket + HTTP simulations on the same machine. Input:
`data/demo/lecture_45s.wav` replayed at real time.

## Results

Command per row: `.venv/bin/python scripts/loadtest.py --base http://127.0.0.1:8010 --clients N --duration 50`

| Clients | Segments published | Received (min/median/max) | Join p50 (ms) | WS connect p50 (ms) | Fan-out spread p95 (ms) | Socket errors | Drops |
|---|---|---|---|---|---|---|---|
| 1 | 10 | 10 / 10 / 10 | 4.4 | 21.1 | 0.0 | 0 | 0 |
| 2 | 10 | 10 / 10 / 10 | 12.0 | 3.3 | 2.4 | 0 | 0 |
| 5 | 10 | 10 / 10 / 10 | 30.0 | 5.8 | 1.1 | 0 | 0 |
| 10 | 10 | 10 / 10 / 10 | 75.8 | 8.9 | 4.0 | 0 | 0 |
| 20 | 10 | 10 / 10 / 10 | 131.2 | 17.2 | 7.0 | 0 | 0 |
| 30 | 10 | 10 / 10 / 10 | 272.1 | 40.5 | **18.2** | **0** | **0** |

End-to-end caption latency stayed flat across client counts (p50 1.39–1.72 s), which
is the expected shape: the CPU work is per-segment, not per-client, and the bus is
fan-out only.

Raw: `data/eval/loadtest_{1,2,5,10,20,30}.json`.

## What this proves and what it does not

**Proves:** the server accepts 30 concurrent authenticated WebSocket clients, one
lecture fans out to all of them with zero drops and zero socket errors, join time
stays under 300 ms for the 30th client, and per-client delivery spread stays under
20 ms at p95.

**Does not prove:** that 30 physical phones can connect through the laptop's Wi-Fi
radio. The radio, DHCP, channel contention, phone power saving and real browser
rendering are all outside this measurement. Loopback load is an upper bound on
application capacity and a lower bound on nothing else.

Also not tested: 30 clients each pulling segment audio simultaneously (the run
used no audio fetches), long sessions beyond 50 s, and mixed teacher/student
traffic.
