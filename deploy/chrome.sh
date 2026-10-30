#!/usr/bin/env bash
# Jarvis ↔ your own Google Chrome (run as the jarvis user, from ~/AK-Jarvis).
#
#   ./deploy/chrome.sh on       browser mode "auto": Jarvis uses YOUR Chrome whenever the
#                               "Jarvis Chrome" shortcut on your computer is connected,
#                               otherwise his own browser on the server. Restarts Jarvis.
#   ./deploy/chrome.sh status   is your Chrome connected right now?
#   ./deploy/chrome.sh files    prints the command to copy the Windows launcher to your PC
set -euo pipefail
cd "$(dirname "$0")/.."
J="$HOME/.jarvis"; PY="$PWD/.venv/bin/python"
[[ $EUID -eq 0 ]] && { echo "Run as jarvis (su - jarvis), not root."; exit 1; }
if [[ -z "${XDG_RUNTIME_DIR:-}" && -d "/run/user/$(id -u)" ]]; then export XDG_RUNTIME_DIR="/run/user/$(id -u)"; fi
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn(){ printf '  \033[33m!\033[0m %s\n' "$*"; }

connected() { curl -fs -m 3 http://127.0.0.1:9222/json/version 2>/dev/null; }

case "${1:-}" in
on)
  "$PY" - "$J/config.yaml" <<'PY'
import sys, yaml
path = sys.argv[1]
cfg = yaml.safe_load(open(path)) or {}
b = cfg.setdefault("browser", {})
b["mode"] = "auto"; b["cdp_url"] = "http://127.0.0.1:9222"
yaml.safe_dump(cfg, open(path, "w"), sort_keys=False, allow_unicode=True)
PY
  ok "browser mode: auto (your Chrome when connected, else Jarvis's own)"
  systemctl --user restart jarvis && ok "Jarvis restarted"
  ;;
status)
  if v=$(connected); then
    ok "Your Chrome is connected: $(echo "$v" | "$PY" -c 'import json,sys;print(json.load(sys.stdin).get("Browser",""))')"
  else
    warn "Your Chrome isn't connected. Double-click 'Jarvis Chrome' on your computer."
  fi
  ;;
files)
  cat <<TXT
On your Windows computer, open PowerShell and paste (asks for the ROOT password):

  mkdir \$HOME\JarvisChrome -Force; scp root@$(tailscale ip -4 2>/dev/null | head -1 || echo 2.29.3.208):$PWD/deploy/windows/Jarvis-Chrome.* \$HOME\JarvisChrome\\

then:

  & \$HOME\JarvisChrome\Jarvis-Chrome.cmd -Setup
TXT
  ;;
*) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//' ;;
esac
