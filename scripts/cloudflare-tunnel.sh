#!/usr/bin/env bash
# Give the phone gateway a permanent HTTPS address (e.g. https://jarvis.example.com) with
# Cloudflare Tunnel — free, no router port-forwarding. Needs your domain's DNS on Cloudflare.
#   ./scripts/cloudflare-tunnel.sh jarvis.example.com
set -euo pipefail
HOST=${1:?usage: $0 jarvis.yourdomain.com}
PORT=${JARVIS_PORT:-8787}
if ! command -v cloudflared >/dev/null; then
  if [[ "$(uname)" == "Darwin" ]]; then brew install cloudflared
  else
    curl -fsSL -o /tmp/cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
    sudo dpkg -i /tmp/cloudflared.deb
  fi
fi
cloudflared tunnel login                         # opens a browser once
cloudflared tunnel create jarvis || true
cloudflared tunnel route dns jarvis "$HOST"
TID=$(cloudflared tunnel list | awk '$2=="jarvis"{print $1}')
mkdir -p ~/.cloudflared
cat > ~/.cloudflared/config.yml <<CFG
tunnel: $TID
credentials-file: $HOME/.cloudflared/$TID.json
ingress:
  - hostname: $HOST
    service: http://localhost:$PORT
  - service: http_status:404
CFG
sudo cloudflared service install || cloudflared service install
echo "✅ https://$HOST → localhost:$PORT. Check: curl https://$HOST/health"
echo "   Use https://$HOST as the Custom LLM URL in Vapi."
