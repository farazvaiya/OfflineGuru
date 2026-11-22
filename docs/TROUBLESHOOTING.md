# Troubleshooting

Start with the health endpoint — it reports model status, RAM, CPU, network and
measured latencies:

```bash
curl -s http://127.0.0.1:8000/api/health | python3 -m json.tool
```

## The server starts but says a model is unavailable

`/api/health` → `models.asr.loaded = false`, or the teacher console shows a red
`✗` pill.

```bash
ls -la models/
du -sh models/*
.venv/bin/python scripts/prepare_models.py --skip-whisper-bench
```

The server **never** downloads a model at runtime by design. If `models/` is
empty, the runtime fails loudly instead of silently using a cloud API.

## `arecord: ... No such file or directory` / no audio captured

```bash
arecord -l                                  # list capture devices
arecord -D default -f S16_LE -r 16000 -c 1 -d 3 /tmp/test.wav && aplay /tmp/test.wav
```

* Several microphones: set `GURU_AUDIO__DEVICE=plughw:1,0` (from `arecord -l`).
* Laptop mute key / hardware mute → check `alsamixer`.
* Bluetooth headsets switch to low-quality HFP mode when recording; prefer the
  built-in mic or a wired mic for the teacher.

## Captions appear but are very late

Check `metrics.e2e_ms` and `resources` in `/api/health`.

| Observation | Likely cause | Action |
|---|---|---|
| `asr_ms` ≈ segment length (RTF ≥ 1) | CPU too slow / thermal throttling | use `whisper-base` or `whisper-tiny`, close other apps, plug in the charger |
| `asr_queue_drops > 0` | sustained overload | smaller model, longer `silence_ms` (fewer, larger segments), disable TTS |
| high `e2e_ms` but low `asr_ms` | TTS/translation sharing CPU | TTS is already decoupled from text; disable TTS to confirm (`GURU_TTS__ENABLED=false`) |
| everything slow after ~20 min | thermal throttling on a thin laptop | `cat /sys/class/thermal/thermal_zone0/temp`, elevate the laptop, cap with `cpu_threads=2` |

Model choice can be changed without editing code:

```bash
GURU_ASR__MODEL_DIR=models/whisper-tiny ./scripts/serve.sh
GURU_ASR__CPU_THREADS=2 ./scripts/serve.sh
```

## Songs/stories: "it translated the wrong word" (e.g. sunlight → sunset)

A 77M-parameter translation model makes lexical mistakes. Mitigations that are
already implemented:

* `numbers_preserved=false` flags segments where numbers disappeared, and the
  student UI shows "সংখ্যা যাচাই করুন".
* The teacher can edit any segment's Bangla (✎ in the teacher console); the
  correction is pushed to students immediately and stored for export.

Larger models (NLLB-600M) improve quality at roughly 3× the CPU cost — see
`docs/MODEL_SELECTION.md` before switching.

## Student page keeps saying "পুনঃসংযোগ…"

* Confirm the student is on the hotspot, not mobile data.
* `curl http://<laptop-ip>:8000/healthz` from the student device.
* Check `ufw status` (port 8000 must be allowed).
* WebSocket upgrades are required — a captive portal or proxy that blocks
  `Upgrade: websocket` will break live captions, but REST (transcript refresh)
  still works.

## Audio button does nothing

Browsers block autoplay until a user gesture. The student must tap **🔈 অডিও
বন্ধ** once to enable audio; after that, captions play automatically. If audio
still fails:

* The segment may have no audio yet (`audio_url = null`) — TTS runs after text.
* Check `/api/health` → `metrics.tts_ms` and the server log for `TTS failed`.
* `GURU_TTS__ENABLED=false` intentionally disables audio; captions remain.

## Bangla text shows as boxes (□□□)

No Bangla font is installed on the student device. The UI deliberately uses only
local fonts (no web fonts, offline). Install a Bengali font on the laptop for the
teacher console (`sudo apt install fonts-noto-bengali`); on student phones Bangla
is normally present. Text is always available as a fallback to audio.

## Port 8000 already in use

```bash
ss -ltnp | grep 8000
PORT=8080 ./scripts/serve.sh
```

## Database is locked / corrupted after a hard power-off

SQLite runs in WAL mode and is resilient, but check:

```bash
sqlite3 data/guru.db "PRAGMA integrity_check;"
```

A truncated WAL is recoverable by starting the server once and letting SQLite
checkpoint. Keep the laptop on mains power during class when possible.

## Memory pressure / OOM killer

```bash
free -h
dmesg | grep -i "killed process"
```

Budget measured on a 4-core / 8 GB laptop: Whisper base ~330 MB RSS, Marian int8
~200 MB, Piper ~120 MB, FastAPI ~120 MB. If free RAM is under ~400 MB the health
endpoint warns. Options: `whisper-tiny` (~180 MB), disable TTS, and generate the
quiz after class rather than during it.

## Everything works, but the summary/quiz is empty

* The transcript needs real content: summaries need ≥ 1 sentence, quizzes need
  definition-like or numeric sentences.
* Check `data/guru.db` → `segments` count, and the teacher console's transcript.
* The quiz is **draft** until the teacher presses "অনুমোদন দিন"; students see
  nothing before approval by design.

## Resetting / deleting data

```bash
# per-session, through the UI: "সব ডেটা মুছুন" (confirm=true)
# or from the API:
curl -X DELETE -H "X-Teacher-Token: $TOKEN" \
  "http://127.0.0.1:8000/api/teacher/session/$SID?confirm=true"

# everything (destructive):
rm -rf data/guru.db data/audio
```

## Logs

```bash
tail -f data/guru.log            # rotating, 2 MB × 3
GURU_LOG_LEVEL=DEBUG ./scripts/serve.sh
```

Logs contain event ids and timings, not student answer text or microphone audio.
