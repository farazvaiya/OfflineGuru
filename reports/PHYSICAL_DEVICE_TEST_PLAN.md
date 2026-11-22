# Physical device test plan

**Status: BLOCKED on this host.** No phones, no AP-capable Wi-Fi control and no
students are available here. What follows is the complete procedure so a human
tester can execute it and record real evidence. Nothing in this file is a result.

## What must be proven

| Stage | Devices | Question it answers |
|---|---|---|
| A | 2 physical phones | does the hotspot + join + captions + audio work at all on real hardware |
| B | 10 phones | does it hold with a normal class |
| C | 30 phones | does the stated target actually work, and what degrades first |

## Prerequisites

- Laptop with an AP-capable Wi-Fi adapter (`iw list | grep -A8 "Supported interface modes"` must show `AP`).
- A second network path for the laptop only if you also want to keep Internet; the classroom path must not need it.
- Phones charged ≥50 %, screen-lock disabled for the session, at least one iPhone and one low-end Android in the mix.
- `models/` populated (`scripts/prepare_models.py`), `.venv` installed.

## Procedure

```bash
# 1. Start the hotspot (needs root; reversible with the same script)
sudo ./scripts/hotspot.sh start --ssid OfflineGuru --password '<8+ chars>'
./scripts/hotspot.sh verify            # sockets + health

# 2. Start the classroom server
./scripts/serve.sh                     # prints the join URL and code

# 3. Connect devices in stages: 2 → 10 → 30
#    On each phone: join the Wi-Fi, open the printed URL, enter the class code
#    and a name. Confirm Bangla captions appear and audio plays in headphones.

# 4. Run one real lesson segment (≥ 10 minutes) with a human speaker
#    (the WAV replay is not a radio or microphone test).

# 5. Record measurements while the lesson runs (every 60 s)
curl -s "http://127.0.0.1:8000/api/health?token=$TOKEN" | python3 -m json.tool
curl -s -H "X-Teacher-Token: $TOKEN" http://127.0.0.1:8000/api/teacher/session/$SID | python3 -m json.tool

# 6. Stop and tear down
sudo ./scripts/hotspot.sh stop
```

## Data collection template (copy per stage)

```
Stage:                A / B / C
Date, place, duration:
Laptop model, CPU, RAM, OS:
Wi-Fi adapter + driver, channel, band (2.4/5 GHz):
Phone models, Android/iOS versions (one row per device):
Distance from laptop (m), obstacles, other APs visible:
Join success:         __ / __ devices, first-attempt failures and reason
Time to first caption (per device, s):
Caption delivery gaps > 3 s (count, timestamps):
Audio playback success: __ / __ devices
Reconnect after screen-lock / walk-out-and-back: PASS / FAIL (notes)
Health at 60 s intervals: cpu_percent, load1, ram_available_mb, e2e_ms p50/p95
Server-side drops:    asr_queue_drops, subscriber drops
Battery drop per phone over the session (%):
Laptop temperature (sensors) start / end:
Failures observed (exact symptom + device + time):
```

## Acceptance thresholds to record against

| Metric | Target |
|---|---|
| Join success, first attempt | ≥ 95 % of devices |
| Time to first caption | ≤ 5 s after the teacher speaks |
| Caption delivery gap | no gap > 5 s without a server-side drop being counted |
| Audio playback | ≥ 90 % of devices after the one-tap gesture |
| Reconnect | automatic within 10 s after screen-lock |
| Server-side drops at 30 devices | 0 in `asr_queue_drops`, 0 subscriber drops |
| Laptop temperature | stays below the throttling point reported by `sensors` |

## Reporting rules

- Record the **actual** device count and models. "30 students" with 22 phones is
  reported as 22.
- A failure is a result, not something to smooth over. If captions stall at 18
  devices, that is the finding.
- Do not reuse loopback load-test numbers as radio results.
- Append the completed template to this file or to `reports/results/` and update
  `FINAL_VERIFICATION_REPORT.md` rows 15–17 from BLOCKED to PASS/FAIL.
