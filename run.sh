#!/usr/bin/env bash
# Sets up (if needed) and starts cutwave on http://localhost:3000
set -e
cd "$(dirname "$0")"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is required but not found on PATH. Install it (e.g. 'brew install ffmpeg') and re-run." >&2
  exit 1
fi

if [ ! -d venv ]; then
  echo "Creating virtualenv..."
  python3 -m venv venv
  ./venv/bin/pip install --quiet --upgrade pip
  ./venv/bin/pip install --quiet -r requirements.txt
fi

exec ./venv/bin/python3 server/app.py
