#!/usr/bin/env bash
# Start Jarvis automatically at login (macOS launchd).
#   ./scripts/autostart-macos.sh            → voice mode (wake word)
#   ./scripts/autostart-macos.sh server     → mini PC: all profiles + phone line, no mic
#   ./scripts/autostart-macos.sh remove     → uninstall
# Tip: run `jarvis voice` once from Terminal first so macOS grants microphone access.
set -euo pipefail
cd "$(dirname "$0")/.."
LABEL=com.jarvis.assistant
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
MODE=${1:-voice}

if [[ "$MODE" == "remove" ]]; then
  launchctl unload "$PLIST" 2>/dev/null || true; rm -f "$PLIST"; echo "Removed."; exit 0
fi
ARGS="<string>$MODE</string>"
[[ "$MODE" == "server" ]] && ARGS="<string>server</string><string>--speak</string>"

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.jarvis"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PWD/.venv/bin/jarvis</string>$ARGS</array>
  <key>WorkingDirectory</key><string>$HOME/.jarvis</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/.jarvis/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$HOME/.jarvis/launchd.err.log</string>
</dict></plist>
PL
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Jarvis ($MODE) will now start at login. Logs: ~/.jarvis/launchd.*.log"
