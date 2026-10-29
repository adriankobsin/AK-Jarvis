#!/usr/bin/env bash
# Install Jarvis (+ Hermes) on your own computer: macOS, Linux, or Windows via WSL2 (Ubuntu).
#   curl -fsSL https://raw.githubusercontent.com/adriankobsin/AK-Jarvis/Main/deploy/local/install.sh | bash -s -- all
# Roles: all  = this computer does everything (no VPS)
#        site = this computer is a home/office/yacht Jarvis that the VPS brain controls
set -euo pipefail
ROLE="${1:-all}"
need() { command -v "$1" >/dev/null || { echo "Please install $1 first ($2)"; exit 1; }; }
need git "https://git-scm.com"
need python3 "https://www.python.org/downloads/ (3.11 or newer)"
python3 - <<'PY' || { echo "Python 3.11+ needed"; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)
PY
if ! command -v node >/dev/null; then
  echo "Node.js 22 is needed for the connectors (Outlook, Xero, SEO…)."
  [[ "$(uname -s)" == "Darwin" ]] && echo "  macOS: brew install node@22" || echo "  Linux/WSL: curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt-get install -y nodejs"
  [[ "$ROLE" == "all" ]] && exit 1
fi
command -v docker >/dev/null || echo "Tip: install Docker Desktop so Hermes can sandbox commands (optional)."
DIR="${AKJ_DIR:-$HOME/AK-Jarvis}"
[[ -d "$DIR/.git" ]] && git -C "$DIR" pull -q || git clone -q -b Main https://github.com/adriankobsin/AK-Jarvis.git "$DIR"
cd "$DIR" && exec ./deploy/setup.sh "$ROLE" < /dev/tty
