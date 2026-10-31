#!/usr/bin/env bash
# Xero for Jarvis — every Wave company with one sign-in (run as the jarvis user, from ~/AK-Jarvis).
#
#   ./deploy/xero.sh setup     paste the Client ID and Client Secret of your Xero app (hidden), restarts Jarvis
#   ./deploy/xero.sh connect   sign in to Xero once and tick all Wave companies
#   ./deploy/xero.sh status    which companies Jarvis can see
#
# Create the app first: developer.xero.com → My Apps → New app → "Web app",
#   Company/app URL: https://www.waveavi.com   Redirect URI: http://localhost:8765/callback
set -euo pipefail
cd "$(dirname "$0")/.."
J="$HOME/.jarvis"; PY="$PWD/.venv/bin/python"
[[ $EUID -eq 0 ]] && { echo "Run as jarvis (su - jarvis), not root."; exit 1; }
if [[ -z "${XDG_RUNTIME_DIR:-}" && -d "/run/user/$(id -u)" ]]; then export XDG_RUNTIME_DIR="/run/user/$(id -u)"; fi
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
ok() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
set_env() {
  touch "$J/.env"; chmod 600 "$J/.env"
  "$PY" - "$J/.env" "$1" "$2" <<'PY'
import sys; f,k,v=sys.argv[1:4]
lines=[l for l in open(f).read().splitlines() if not l.startswith(k+"=")]+[f"{k}={v}"]
open(f,"w").write("\n".join(lines)+"\n")
PY
}
case "${1:-}" in
setup)
  read -r -s -p "  Xero Client ID: " A; echo
  read -r -s -p "  Xero Client Secret: " B; echo
  A=$(echo "$A" | tr -d '\r '); B=$(echo "$B" | tr -d '\r ')
  [[ -n "$A" && -n "$B" ]] || { echo "Both are needed."; exit 1; }
  set_env XERO_CLIENT_ID "$A"; set_env XERO_CLIENT_SECRET "$B"; ok "saved"
  echo; "$0" connect
  ;;
connect)
  "$PY" -m jarvis xero connect
  systemctl --user restart jarvis && ok "Jarvis restarted — Xero tools are live (Telegram: mcp_jarvis_xero_*)"
  ;;
status) "$PY" -m jarvis xero status ;;
*) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//' ;;
esac
