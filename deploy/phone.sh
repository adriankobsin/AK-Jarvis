#!/usr/bin/env bash
# Phone calls for Jarvis through Vapi (run as the jarvis user, from ~/AK-Jarvis).
#
#   ./deploy/phone.sh prepare   1) creates the secret, opens a public HTTPS address for Vapi
#                                  (Tailscale Funnel on port 8443 → Jarvis phone gateway 8787)
#                                  and prints exactly what to paste into Vapi
#   ./deploy/phone.sh finish    2) after you've created the Vapi assistant: saves your Vapi key,
#                                  assistant ID, phone number ID and your mobile, restarts Jarvis
#                                  and tests the line
#   ./deploy/phone.sh status    shows the current phone settings and checks the public address
#   ./deploy/phone.sh off       closes the public address
set -euo pipefail
cd "$(dirname "$0")/.."
J="$HOME/.jarvis"; PY="$PWD/.venv/bin/python"
[[ $EUID -eq 0 ]] && { echo "Run as jarvis (su - jarvis), not root."; exit 1; }
if [[ -z "${XDG_RUNTIME_DIR:-}" && -d "/run/user/$(id -u)" ]]; then export XDG_RUNTIME_DIR="/run/user/$(id -u)"; fi
say() { printf '\n\033[1;36m▸ %s\033[0m\n' "$*"; }
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn(){ printf '  \033[33m!\033[0m %s\n' "$*"; }
get_env() { grep -E "^$1=" "$J/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true; }
set_env() {
  touch "$J/.env"; chmod 600 "$J/.env"
  if grep -qE "^$1=" "$J/.env"; then
    "$PY" - "$J/.env" "$1" "$2" <<'PY'
import sys; f,k,v=sys.argv[1:4]
lines=open(f).read().splitlines(); open(f,"w").write("\n".join(f"{k}={v}" if l.startswith(k+"=") else l for l in lines)+"\n")
PY
  else echo "$1=$2" >> "$J/.env"; fi
}
host() { tailscale status --json 2>/dev/null | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))'; }
URL() { echo "https://$(host):8443"; }
set_phone() {  # set_phone key value  (in ~/.jarvis/config.yaml, under phone:)
  "$PY" - "$J/config.yaml" "$1" "$2" <<'PY'
import sys, yaml
path, key, val = sys.argv[1:4]
cfg = yaml.safe_load(open(path)) or {}
ph = cfg.setdefault("phone", {})
ph[key] = True if val == "true" else False if val == "false" else val
yaml.safe_dump(cfg, open(path, "w"), sort_keys=False, allow_unicode=True)
PY
}
restart() {
  systemctl --user restart jarvis
  for i in $(seq 1 30); do curl -fs -o /dev/null http://127.0.0.1:8787/health 2>/dev/null && return 0; sleep 1; done
  return 1
}

case "${1:-}" in
prepare)
  say "Phone secret (Vapi sends it so only Vapi can talk to the phone line)"
  if [[ -z "$(get_env JARVIS_PHONE_SECRET)" ]]; then
    set_env JARVIS_PHONE_SECRET "$("$PY" -c 'import secrets;print(secrets.token_urlsafe(32))')"; ok "created"
  else ok "already set"; fi
  set_phone enabled true; set_phone port 8787; set_phone host 127.0.0.1

  say "Public HTTPS address for Vapi (Tailscale Funnel → phone gateway only)"
  if ! sudo tailscale funnel --bg --https=8443 http://127.0.0.1:8787; then
    warn "Funnel needs to be allowed once for your Tailscale network: open the link above, click Enable, then run this again."
    exit 1
  fi
  ok "$(URL) → Jarvis phone gateway"

  say "Starting the phone line"
  # setup.sh starts `jarvis server --no-phone` on the cloud until a Vapi key exists; switch the service to the phone-enabled command
  UNIT="$HOME/.config/systemd/user/jarvis.service"
  [[ -f "$UNIT" ]] && sed -i 's#/jarvis server --no-phone#/jarvis server#' "$UNIT" && systemctl --user daemon-reload
  if restart; then ok "Phone gateway running on port 8787"; else warn "Gateway didn't start: journalctl --user -u jarvis -n 40 --no-pager"; exit 1; fi
  if curl -fs "$(URL)/health" >/dev/null 2>&1; then ok "Reachable from the internet: $(URL)/health"; else warn "Not reachable yet from outside (Funnel can take a minute). Check later with: ./deploy/phone.sh status"; fi

  cat <<EOF

────────────────────────────────────────────────────────────────────────────
 Now in the Vapi dashboard (dashboard.vapi.ai):

 1. Assistants → Create Assistant → Blank template. Name: Jarvis
 2. Model tab → Provider: Custom LLM
      Custom LLM URL :  $(URL)
      Model          :  jarvis
      API key        :  $(get_env JARVIS_PHONE_SECRET)
    (If Vapi asks for a credential instead: Organization → Credentials → Custom LLM,
     paste the same key, then pick that credential here.)
 3. First message :  Jarvis here. How can I help?
 4. Voice tab     → Provider ElevenLabs (or Vapi's own) → a British male voice
 5. Transcriber   → Deepgram, nova-3, language English
 6. Advanced      → Max duration 30 min; background denoising on
 7. Publish. Copy the Assistant ID (under the name).
 8. Phone Numbers → your free US number → Inbound settings → Assistant: Jarvis → Save.
    Copy the Phone Number ID.
 9. Organization (top left) → API Keys → copy the PRIVATE key.

 Then run:  ./deploy/phone.sh finish
────────────────────────────────────────────────────────────────────────────
EOF
  ;;
finish)
  say "Vapi details (paste each; the key stays hidden)"
  read -rs -p "  Vapi PRIVATE API key: " K; echo; K=$(echo "$K" | tr -d '\r ')
  [[ -n "$K" ]] && set_env VAPI_API_KEY "$K" && ok "key saved"
  read -r -p "  Assistant ID: " A; A=$(echo "$A" | tr -d '\r ')
  read -r -p "  Phone Number ID: " N; N=$(echo "$N" | tr -d '\r ')
  read -r -p "  YOUR mobile, international format (e.g. +66812345678): " M; M=$(echo "$M" | tr -d '\r ')
  [[ -n "$A" ]] && set_phone assistant_id "$A"
  [[ -n "$N" ]] && set_phone phone_number_id "$N"
  [[ -n "$M" ]] && set_phone owner_number "$M"
  set_phone enabled true
  ok "saved: calls from $M get the full Jarvis; anyone else gets a receptionist who takes a message"
  say "Restarting Jarvis"
  if restart; then ok "Phone line ready"; else warn "Gateway didn't start: journalctl --user -u jarvis -n 40 --no-pager"; exit 1; fi
  if [[ -n "$K" && -n "$A" ]]; then
    code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $K" "https://api.vapi.ai/assistant/$A")
    [[ "$code" == "200" ]] && ok "Vapi key and assistant ID are valid" || warn "Vapi didn't accept the key/assistant ID (HTTP $code): check them"
  fi
  cat <<EOF

 Test: call your Vapi number from your mobile. Jarvis answers and knows it's you.
 (Free Vapi numbers can't call out; for "Jarvis, call me" import a Twilio number later.)
EOF
  ;;
status)
  "$PY" - "$J/config.yaml" <<'PY'
import sys, yaml
ph = (yaml.safe_load(open(sys.argv[1])) or {}).get("phone", {})
for k in ("enabled", "assistant_id", "phone_number_id", "owner_number"):
    print(f"  {k:16} {ph.get(k, '')}")
PY
  echo "  secret set       $([[ -n "$(get_env JARVIS_PHONE_SECRET)" ]] && echo yes || echo no)"
  echo "  Vapi key set     $([[ -n "$(get_env VAPI_API_KEY)" ]] && echo yes || echo no)"
  echo "  local gateway    $(curl -fs http://127.0.0.1:8787/health 2>/dev/null || echo 'not running')"
  echo "  public address   $(URL) → $(curl -fs "$(URL)/health" 2>/dev/null || echo 'not reachable')"
  ;;
off)
  sudo tailscale funnel --https=8443 off && ok "public phone address closed"
  ;;
*) sed -n '2,12p' "$0"; exit 1 ;;
esac
