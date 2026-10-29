#!/usr/bin/env bash
# Jarvis installer for macOS / Linux. Run from the project folder: ./scripts/install.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PYTHON:-python3}
$PY -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ required"'

if [[ "$(uname)" == "Linux" ]] && command -v apt-get >/dev/null; then
  echo "Installing audio packages (sudo)…"
  sudo apt-get install -y libportaudio2 ffmpeg espeak-ng libnotify-bin || true
  NEED_PW_DEPS=1
fi

$PY -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel
pip install -e ".[voice]"
python -m playwright install chromium   # Jarvis's own browser
[[ "${NEED_PW_DEPS:-}" == 1 ]] && sudo .venv/bin/python -m playwright install-deps chromium || true
jarvis init

echo
echo "✅ Installed. Next:"
echo "   1. Put your Anthropic API key in ~/.jarvis/.env"
echo "   2. Edit ~/.jarvis/config.yaml (location, devices, Home Assistant)"
echo "   3. source .venv/bin/activate && jarvis doctor"
echo "   4. jarvis voice      (say 'Hey Jarvis')   or   jarvis chat"
