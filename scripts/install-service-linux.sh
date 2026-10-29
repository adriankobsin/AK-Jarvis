#!/usr/bin/env bash
# Run Jarvis 24/7 on a Linux mini PC (systemd user service, starts at boot).
#   ./scripts/install-service-linux.sh            → `jarvis server` (all profiles + phone line)
#   ./scripts/install-service-linux.sh voice      → hands-free voice + phone (needs mic/speaker)
#   ./scripts/install-service-linux.sh remove
set -euo pipefail
cd "$(dirname "$0")/.."
MODE=${1:-server}
UNIT="$HOME/.config/systemd/user/jarvis.service"
if [[ "$MODE" == "remove" ]]; then
  systemctl --user disable --now jarvis.service 2>/dev/null || true; rm -f "$UNIT"; echo "Removed."; exit 0
fi
ARGS="server"; [[ "$MODE" == "voice" ]] && ARGS="voice --phone"
mkdir -p "$(dirname "$UNIT")"
cat > "$UNIT" <<UNIT
[Unit]
Description=Jarvis AI assistant
After=network-online.target sound.target

[Service]
WorkingDirectory=$HOME/.jarvis
ExecStart=$PWD/.venv/bin/jarvis $ARGS
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now jarvis.service
sudo loginctl enable-linger "$USER"   # keep running when you're logged out / after reboot
echo "✅ Jarvis ($ARGS) is running. Logs: journalctl --user -u jarvis -f"
