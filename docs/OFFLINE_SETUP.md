# Offline setup and deployment

Two separate phases. **Preparation** needs Internet. **Classroom operation**
must not.

```
[Internet machine]                      [teacher laptop]                [students]
install deps + download models   ──►    copy app + models/        ──►   browser on
run scripts/prepare_models.py           run offline, no network         hotspot
```

## Phase 0 — prerequisites (Ubuntu)

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ffmpeg \
                    network-manager alsa-utils fonts-noto-bengali
```

* `ffmpeg` — converts demo/lecture audio to 16 kHz mono (prep only).
* `network-manager` — hotspot control (`nmcli`).
* `alsa-utils` — `arecord` for microphone capture (runtime).
* `fonts-noto-bengali` — Bangla rendering on the teacher console.

No GPU drivers are required. There is no Docker dependency.

## Phase 1 — Python environment

```bash
cd offline_guru
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt          # runtime only, no torch
```

Optional, preparation only (needed to convert the translation model once):

```bash
.venv/bin/pip install -r requirements-prep.txt \
  --extra-index-url https://download.pytorch.org/whl/cpu
```

## Phase 2 — fetch and convert models

```bash
.venv/bin/python scripts/prepare_models.py --with-en-demo-voice
```

This downloads and prepares (~1.2 GB total):

| Asset | Size | Purpose |
|---|---|---|
| `models/whisper-tiny/` | 75 MB | benchmark / weak-CPU fallback |
| `models/whisper-base/` | 142 MB | **default ASR** |
| `models/whisper-small/` | 464 MB | optional higher accuracy |
| `models/marian-en-bn-src/` | 611 MB | conversion source (deletable after) |
| `models/marian-en-bn-ct2/` | 83 MB | **runtime translation model** |
| `models/piper/bn_BD-google-medium.onnx` | 77 MB | **Bangla TTS** |
| `models/piper/en_US-lessac-medium.onnx` | 64 MB | demo audio generation only |

After conversion you may delete `models/marian-en-bn-src/` (611 MB) and the
`.venv` `torch` package; neither is used at runtime.

**Important:** the conversion step writes a CTranslate2 `config.json`. Never
overwrite it with the Hugging Face config — the model will fail to load. (This
was a real bug during development; `scripts/prepare_models.py` now avoids it and
`models/marian-en-bn-ct2/guru_meta.json` carries our metadata separately.)

Optional, better Bangla at ~10× the translation cost (CC-BY-NC-4.0, see
`LICENSES.md`):

```bash
.venv/bin/python - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download("JustFrederik/nllb-200-distilled-600M-ct2-int8",
                  local_dir="models/nllb-600m-ct2-int8")
snapshot_download("facebook/nllb-200-distilled-600M", local_dir="models/nllb-tokenizer",
                  allow_patterns=["tokenizer*", "*.spm", "special_tokens_map.json", "config.json"])
PY
GURU_TRANSLATE__BACKEND=nllb ./scripts/serve.sh
```

## Phase 3 — generate the demo lecture (recommended)

```bash
.venv/bin/python scripts/make_demo_lecture.py     # -> data/demo/lecture.wav (160 s)
```

Useful for testing without a teacher, and as a fallback if the microphone fails
during a demo.

## Phase 4 — transfer to the classroom laptop

Copy the whole project directory **including** `.venv/` only if the target has
the same Python version and architecture. Otherwise copy the code + `models/` and
recreate the venv offline with a wheel cache:

```bash
# on the Internet machine (build an offline wheelhouse)
.venv/bin/pip download -r requirements.txt -d wheelhouse

# transfer (USB or rsync)
rsync -av --exclude data/ --exclude .venv \
      offline_guru/ teacher@laptop:/opt/offline_guru/
rsync -av wheelhouse/ teacher@laptop:/opt/offline_guru/wheelhouse/

# on the laptop (no Internet needed)
cd /opt/offline_guru
python3 -m venv .venv
.venv/bin/pip install --no-index --find-links wheelhouse -r requirements.txt
```

## Phase 5 — verify offline

```bash
.venv/bin/python scripts/verify_offline.py
.venv/bin/pytest -q
```

`verify_offline.py` blocks all non-loopback sockets, loads every model, runs a
lecture and generates materials. Expected: `PASS: ... zero external connections.`

Then disarm the network entirely and confirm the classroom still works:

```bash
sudo nmcli networking off        # or unplug Ethernet / disable Wi-Fi client
./scripts/serve.sh
curl -s http://127.0.0.1:8000/api/health | python3 -m json.tool   # models loaded, warnings only
sudo nmcli networking on
```

## Phase 6 — classroom day

```bash
./scripts/hotspot.sh status
sudo ./scripts/hotspot.sh start --ssid OfflineGuru-Class8 --password 'guru-class-2026'
./scripts/serve.sh
```

Open `http://127.0.0.1:8000/teacher`, create a session, note the join URL and QR,
add today's vocabulary under **আজকের মূল শব্দ**, press **🎤 শুরু করুন**.

End of class: **⏹ থামান** → review/approve summary and quiz → **এক্সপোর্ট (JSON)**
→ optionally **সব ডেটা মুছুন**.

## What still needs Internet?

Nothing during class. The only outbound code path is an optional 0.6 s
reachability probe to `1.1.1.1:53` used to label the UI ("Internet available"),
which does not affect functionality and can be disabled:

```bash
GURU_SERVER__ONLINE_PROBE=false ./scripts/serve.sh
```

## Disk / RAM footprint

* Code + models: ~1.4 GB (≈0.85 GB if you delete `whisper-small`, `whisper-tiny`
  and the Marian source checkpoint).
* Runtime RAM: ~800 MB steady state (see `docs/MODEL_SELECTION.md`).
* Per-lesson audio cache: roughly 200 KB per segment WAV; a 45-minute lesson is
  ~50–80 MB. Disable with `GURU_TTS__ENABLED=false` or purge after export.
