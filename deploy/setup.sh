#!/usr/bin/env bash
# Jarvis + Hermes setup. Run as your normal user (not root) from the AK-Jarvis folder.
#
#   ./deploy/setup.sh cloud   VPS: Hermes (brain, Telegram/WhatsApp, memory, schedules, connectors) + cloud Jarvis
#   ./deploy/setup.sh site    Home/office/yacht machine: Jarvis only (core screen, voice, building systems)
#   ./deploy/setup.sh all     One machine does everything (no VPS)
#
# Options:  --site NAME=URL   (cloud/all) connect a site Jarvis, e.g. --site home=http://home-pc:8788/mcp
#           --refresh         re-apply config after editing keys (no questions)
# Safe to run again: it keeps existing keys, memories and settings.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
ROLE="${1:-}"; shift || true
SITES=(); REFRESH=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --site) SITES+=("--site" "$2"); shift 2 ;;
    --refresh) REFRESH=1; shift ;;
    *) echo "Unknown option $1"; exit 1 ;;
  esac
done
[[ "$ROLE" =~ ^(cloud|site|all)$ ]] || { sed -n '2,12p' "$0"; exit 1; }
[[ $EUID -eq 0 ]] && { echo "Run this as your normal user, not root."; exit 1; }

J="$HOME/.jarvis"; H="${HERMES_HOME:-$HOME/.hermes}"
# `su - user` leaves no user session bus; point systemctl --user at the lingering one
if [[ "$(uname -s)" == "Linux" && -z "${XDG_RUNTIME_DIR:-}" && -d "/run/user/$(id -u)" ]]; then
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
  export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
fi
OS="$(uname -s)"
say()  { printf '\n\033[1;36m▸ %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }

# -- .env helpers ------------------------------------------------------------------------------
get_env() { [[ -f "$1" ]] && grep -E "^$2=" "$1" | tail -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' || true; }
set_env() {  # set_env FILE KEY VALUE  (replace or append; file kept private)
  local f="$1" k="$2" v="$3"; touch "$f"; chmod 600 "$f"
  if grep -qE "^$k=" "$f"; then
    python3 - "$f" "$k" "$v" <<'PY'
import sys; f,k,v=sys.argv[1:4]
lines=open(f).read().splitlines(); out=[(f"{k}={v}" if l.startswith(k+"=") else l) for l in lines]
open(f,"w").write("\n".join(out)+"\n")
PY
  else echo "$k=$v" >> "$f"; fi
}
is_placeholder() { [[ -z "$1" || "$1" == *"..."* ]]; }
ask_key() {  # ask_key KEY "question" [required]
  local k="$1" q="$2" req="${3:-}" cur; cur="$(get_env "$J/.env" "$k")"
  if ! is_placeholder "$cur"; then ok "$k already set"; return; fi
  [[ $REFRESH -eq 1 ]] && return
  local v=""
  while :; do
    read -r -s -p "  $q${req:+ (required)}: " v; echo
    [[ -n "$v" || -z "$req" ]] && break
  done
  [[ -n "$v" ]] && set_env "$J/.env" "$k" "$v" && ok "$k saved"
}
rand() { python3 -c 'import secrets;print(secrets.token_urlsafe(32))'; }

# -- 1. Jarvis ---------------------------------------------------------------------------------
say "Installing Jarvis"
if [[ ! -d .venv ]]; then python3 -m venv .venv; fi
EXTRAS=""; [[ "$ROLE" != "cloud" ]] && EXTRAS="[voice]"
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e ".$EXTRAS" || .venv/bin/pip install -q -e .
.venv/bin/pip install -q edge-tts >/dev/null 2>&1 || true
.venv/bin/python -m playwright install chromium >/dev/null 2>&1 || warn "Browser download failed (run: .venv/bin/python -m playwright install --with-deps chromium)"
.venv/bin/jarvis init >/dev/null
ok "Jarvis $(.venv/bin/python -c 'import jarvis;print(jarvis.__version__)') installed"

say "Keys (paste each one; input is hidden; press Enter to skip optional ones)"
ask_key ANTHROPIC_API_KEY "Anthropic API key (console.anthropic.com)" required
ask_key OPENAI_API_KEY "OpenAI API key (platform.openai.com), for GPT second opinions"
ask_key ELEVENLABS_API_KEY "ElevenLabs API key, for the premium British voice"
for k in JARVIS_WEB_PASSWORD JARVIS_MCP_TOKEN; do
  is_placeholder "$(get_env "$J/.env" "$k")" && set_env "$J/.env" "$k" "$(rand)" && ok "$k generated"
done
if [[ "$ROLE" != "site" ]]; then
  ask_key JARVIS_APPROVALS_BOT_TOKEN "Telegram APPROVALS bot token (a 2nd bot from @BotFather)"
  ask_key JARVIS_APPROVALS_CHAT_ID "Your Telegram user id (message @userinfobot to get it)"
fi

# Jarvis settings: who it serves, voice, approvals
.venv/bin/python - "$J/config.yaml" "$ROLE" "$(get_env "$J/.env" ELEVENLABS_API_KEY)" <<'PY'
import sys, yaml
path, role, eleven = sys.argv[1:4]
cfg = yaml.safe_load(open(path)) or {}
a = cfg.setdefault("assistant", {})
a.setdefault("user_name", "Adrian"); a.setdefault("company", "Wave AVI")
if not a.get("home_location"): a["home_location"] = "Bangkok, Thailand"
v = cfg.setdefault("voice", {})
if eleven and not str(eleven).endswith("..."):
    v["tts"] = "elevenlabs"; v.setdefault("elevenlabs_voice_id", "onwK4e9ZLuTAKqWW03F9")
w = cfg.setdefault("web", {}); w.setdefault("port", 8788)
cfg.setdefault("mcp", {})["enabled"] = True
yaml.safe_dump(cfg, open(path, "w"), sort_keys=False, allow_unicode=True)
PY
ok "Jarvis configured for Adrian / Wave AVI"

say "Teaching Jarvis the basics about you and Wave"
.venv/bin/jarvis learn knowledge/about-us --topic about-us >/dev/null 2>&1 && ok "Starter knowledge added (more via the teach-me skill)" \
  || warn "Couldn't import knowledge now; run later: .venv/bin/jarvis learn knowledge/about-us --topic about-us"

# -- 2. Run Jarvis as a service ------------------------------------------------------------------
say "Starting Jarvis as a background service"
ARGS="server"; [[ "$ROLE" == "cloud" ]] && ARGS="server --no-phone"
if [[ -n "$(get_env "$J/.env" VAPI_API_KEY)" ]]; then ARGS="server"; fi
if [[ "$OS" == "Darwin" ]]; then
  PL="$HOME/Library/LaunchAgents/com.akjarvis.jarvis.plist"
  cat > "$PL" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.akjarvis.jarvis</string>
  <key>ProgramArguments</key><array><string>$REPO/.venv/bin/jarvis</string>$(for a in $ARGS; do printf '<string>%s</string>' "$a"; done)</array>
  <key>WorkingDirectory</key><string>$J</string>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$J/jarvis.log</string><key>StandardErrorPath</key><string>$J/jarvis.log</string>
</dict></plist>
PLIST
  launchctl unload "$PL" 2>/dev/null || true; launchctl load "$PL"
else
  UNIT="$HOME/.config/systemd/user/jarvis.service"; mkdir -p "$(dirname "$UNIT")"
  cat > "$UNIT" <<UNIT
[Unit]
Description=Jarvis (core, MCP for Hermes, building systems)
After=network-online.target

[Service]
WorkingDirectory=$J
ExecStart=$REPO/.venv/bin/jarvis $ARGS
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable --now jarvis.service
  loginctl enable-linger "$USER" 2>/dev/null || sudo loginctl enable-linger "$USER" || true
  systemctl --user restart jarvis.service
fi
for i in $(seq 1 30); do curl -fs -o /dev/null "http://127.0.0.1:8788/manifest.webmanifest" 2>/dev/null && break; sleep 1; done
curl -fsS -o /dev/null "http://127.0.0.1:8788/manifest.webmanifest" && ok "Jarvis is running on port 8788" || warn "Jarvis didn't answer yet: check the logs (journalctl --user -u jarvis -n 50)"

if [[ "$ROLE" == "site" ]]; then
  say "Done: this site is ready"
  echo "  Core screen:  http://$(hostname):8788   (password: JARVIS_WEB_PASSWORD in $J/.env)"
  echo "  Give the cloud this endpoint + token so Hermes can use this site:"
  echo "     URL    http://$(hostname):8788/mcp      (over Tailscale)"
  echo "     TOKEN  $(get_env "$J/.env" JARVIS_MCP_TOKEN)"
  echo "  On the VPS: add JARVIS_SITE_<NAME>_TOKEN=<token> to ~/.hermes/.env and run"
  echo "     ./deploy/setup.sh cloud --site <name>=http://$(hostname):8788/mcp --refresh"
  echo "  Building systems: add them to $J/config.yaml (see README → Building systems)."
  exit 0
fi

# -- 3. Hermes ---------------------------------------------------------------------------------
say "Installing Hermes Agent"
export PATH="$HOME/.local/bin:$PATH"
if ! command -v hermes >/dev/null; then
  curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-setup --non-interactive
  export PATH="$HOME/.local/bin:$PATH"
fi
command -v hermes >/dev/null && ok "Hermes $(hermes --version 2>/dev/null | head -1)" || { warn "Hermes install failed"; exit 1; }
mkdir -p "$H"; touch "$H/.env"; chmod 600 "$H/.env"

say "Chat channels"
if is_placeholder "$(get_env "$H/.env" TELEGRAM_BOT_TOKEN)" && [[ $REFRESH -eq 0 ]]; then
  read -r -s -p "  Telegram bot token for chatting with Jarvis (from @BotFather): " v; echo
  [[ -n "$v" ]] && set_env "$H/.env" TELEGRAM_BOT_TOKEN "$v"
fi
CHAT_ID="$(get_env "$J/.env" JARVIS_APPROVALS_CHAT_ID)"
if [[ -n "$CHAT_ID" ]] && is_placeholder "$(get_env "$H/.env" TELEGRAM_ALLOWED_USERS)"; then
  set_env "$H/.env" TELEGRAM_ALLOWED_USERS "$CHAT_ID"; ok "Only your Telegram account can talk to Jarvis"
fi

# shared keys Hermes needs (copied from Jarvis's .env)
for k in ANTHROPIC_API_KEY OPENAI_API_KEY ELEVENLABS_API_KEY JARVIS_MCP_TOKEN HA_URL HA_TOKEN; do
  v="$(get_env "$J/.env" "$k")"; is_placeholder "$v" || set_env "$H/.env" "$k" "$v"
done
EV="$(get_env "$J/.env" ELEVENLABS_API_KEY)"; is_placeholder "$EV" || set_env "$H/.env" ELEVENLABS_VOICE_ID onwK4e9ZLuTAKqWW03F9

say "Installing the Jarvis profile into Hermes (persona, memory, skills, connectors)"
.venv/bin/python deploy/hermes_profile.py --role "$ROLE" "${SITES[@]}"

say "Starting the Hermes gateway (Telegram/WhatsApp) as a service"
hermes gateway install --start-now --start-on-login >/dev/null 2>&1 || hermes gateway install || true
hermes gateway restart >/dev/null 2>&1 || true
hermes gateway status 2>/dev/null | head -3 || true

# daily backup of Hermes + Jarvis at 03:15
{ { crontab -l 2>/dev/null || true; } | { grep -v akjarvis-backup || true; }; echo "15 3 * * * $REPO/deploy/backup.sh >/dev/null 2>&1 # akjarvis-backup"; } | crontab -
ok "Nightly backup scheduled (keeps 14 days in ~/backups)"

say "Done. Next steps"
cat <<NEXT
  1. Telegram: open your bot and say "hello". If it asks for pairing, approve with: hermes pairing list / approve
  2. WhatsApp: run  hermes gateway setup  → WhatsApp → scan the QR code with WhatsApp (Linked devices)
  3. Outlook/Calendar: run  npx -y @softeria/ms-365-mcp-server --org-mode --login  (sign in with your
     Wave Microsoft 365 account), then: echo MS365_ENABLED=1 >> $H/.env && ./deploy/setup.sh $ROLE --refresh
  4. More connectors (GitHub, Xero, SEO, social, n8n, Home Assistant/AirTouch): add their keys to
     $H/.env (see docs/HERMES.md), then ./deploy/setup.sh $ROLE --refresh
  5. Teach him: in Telegram say "Let's do a teach-me session".
  Web console: http://<this machine>:8788   password: $(get_env "$J/.env" JARVIS_WEB_PASSWORD)
NEXT
