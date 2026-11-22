#!/usr/bin/env bash
# Start the Offline Guru classroom server.
#   ./scripts/serve.sh                # 0.0.0.0:8000
#   PORT=8080 ./scripts/serve.sh
#   GURU_ASR__MODEL_DIR=models/whisper-tiny ./scripts/serve.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
  echo "No .venv found. Create it with: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

# Offline by default: never reach for a remote model, even by accident.
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_HUB_DISABLE_TELEMETRY=1

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

exec .venv/bin/python -m uvicorn guru.server.app:app \
  --host "$HOST" --port "$PORT" \
  --ws-ping-interval 25 --ws-ping-timeout 60 \
  --timeout-keep-alive 30 \
  --no-access-log
