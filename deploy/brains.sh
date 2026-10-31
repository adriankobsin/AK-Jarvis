#!/usr/bin/env bash
# Backup brains for Jarvis (run as the jarvis user, from ~/AK-Jarvis).
# When Claude can't answer (out of credit, rate-limited, down), Hermes falls through:
#   OpenAI (if it has credit) → Gemini → Groq → Cerebras → Mistral → OpenRouter
# Each free brain is used only if you've added its key. Keys stay on the server.
#
#   ./deploy/brains.sh add      paste the keys you have (Enter skips one), applies them, restarts Hermes
#   ./deploy/brains.sh test     asks every brain "reply OK" and shows which ones work right now
#   ./deploy/brains.sh status   the fallback order Hermes is using
set -euo pipefail
cd "$(dirname "$0")/.."
H="$HOME/.hermes"; PY="$PWD/.venv/bin/python"
[[ $EUID -eq 0 ]] && { echo "Run as jarvis (su - jarvis), not root."; exit 1; }
if [[ -z "${XDG_RUNTIME_DIR:-}" && -d "/run/user/$(id -u)" ]]; then export XDG_RUNTIME_DIR="/run/user/$(id -u)"; fi
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad() { printf '  \033[31m✗\033[0m %s\n' "$*"; }
get_env() { grep -E "^$1=" "$H/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true; }
set_env() {
  touch "$H/.env"; chmod 600 "$H/.env"
  if grep -qE "^$1=" "$H/.env"; then
    "$PY" - "$H/.env" "$1" "$2" <<'PY'
import sys; f,k,v=sys.argv[1:4]
lines=open(f).read().splitlines(); open(f,"w").write("\n".join(f"{k}={v}" if l.startswith(k+"=") else l for l in lines)+"\n")
PY
  else echo "$1=$2" >> "$H/.env"; fi
}

case "${1:-}" in
add)
  echo
  echo "Paste each key you have (hidden while you paste). Press Enter to skip one."
  echo "  Gemini     → aistudio.google.com/apikey"
  echo "  Groq       → console.groq.com/keys"
  echo "  Cerebras   → cloud.cerebras.ai  (API Keys)"
  echo "  Mistral    → console.mistral.ai/api-keys  (free 'Experiment' plan)"
  echo "  OpenRouter → openrouter.ai/keys"
  echo
  for pair in GEMINI_API_KEY:Gemini GROQ_API_KEY:Groq CEREBRAS_API_KEY:Cerebras MISTRAL_API_KEY:Mistral OPENROUTER_API_KEY:OpenRouter; do
    k="${pair%%:*}"; label="${pair##*:}"
    have=""; [[ -n "$(get_env "$k")" ]] && have=" (already set — Enter keeps it)"
    read -r -s -p "  $label key$have: " v; echo
    v=$(echo "$v" | tr -d '\r ')
    [[ -n "$v" ]] && { set_env "$k" "$v"; ok "$label saved"; }
  done
  "$PY" deploy/hermes_profile.py --role cloud >/dev/null && ok "Hermes fallback chain updated"
  hermes gateway restart >/dev/null 2>&1 && ok "Hermes restarted" || bad "restart Hermes: hermes gateway restart"
  echo; "$0" test
  ;;
test)
  "$PY" - "$H/.env" <<'PY'
import sys, json, urllib.request, time
sys.path.insert(0, "deploy")
import hermes_profile as hp
env = hp.read_env(__import__("pathlib").Path(sys.argv[1]))
brains = []
if env.get("ANTHROPIC_API_KEY"):
    brains.append(("Claude (main)", "anthropic", hp.MAIN_MODEL, env["ANTHROPIC_API_KEY"]))
if env.get("OPENAI_API_KEY"):
    brains.append(("OpenAI", "https://api.openai.com/v1", hp.GPT_TOOLS_MODEL, env["OPENAI_API_KEY"]))
for b in hp.free_brains(env):
    brains.append((b["label"] + " (free)", b["base_url"], b["model"], env[b["key_env"]]))
print("\nAsking each brain to reply OK:\n")
for label, url, model, key in brains:
    t = time.time()
    try:
        if url == "anthropic":
            req = urllib.request.Request("https://api.anthropic.com/v1/messages",
                data=json.dumps({"model": model, "max_tokens": 10, "messages": [{"role": "user", "content": "Reply with just OK"}]}).encode(),
                headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        else:
            req = urllib.request.Request(url.rstrip("/") + "/chat/completions",
                data=json.dumps({"model": model, "max_tokens": 200, "messages": [{"role": "user", "content": "Reply with just OK"}]}).encode(),
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=40) as r:
            json.load(r)
        print(f"  \033[32m✓\033[0m {label:<18} {model:<28} {time.time()-t:4.1f}s")
    except urllib.error.HTTPError as e:
        msg = e.read().decode(errors="replace")[:160].replace("\n", " ")
        print(f"  \033[31m✗\033[0m {label:<18} {model:<28} HTTP {e.code}: {msg}")
    except Exception as e:
        print(f"  \033[31m✗\033[0m {label:<18} {model:<28} {str(e)[:120]}")
print("\n✗ on a free brain with 'model' in the message: set a current model, e.g.  echo GROQ_MODEL=<name> >> ~/.hermes/.env")
print("  then run  ./deploy/brains.sh add  (Enter through the keys) to apply it.")
PY
  ;;
status)
  "$PY" - "$H/config.yaml" <<'PY'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1])) or {}
print("\n1. " + str((cfg.get("model") or {}).get("default")) + "  (main)")
for i, f in enumerate(cfg.get("fallback_providers") or [], 2):
    print(f"{i}. {f.get('model')}  @ {f.get('base_url')}")
w = (cfg.get("auxiliary") or {}).get("web_extract") or {}
print(f"\nReading web pages with: {w.get('model')} ({w.get('base_url') or w.get('provider')})")
PY
  ;;
*) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//' ;;
esac
