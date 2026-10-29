#!/usr/bin/env bash
# Prepare a fresh Ubuntu 24.04 VPS for Jarvis + Hermes. Run ONCE as root:
#   curl -fsSL https://raw.githubusercontent.com/adriankobsin/AK-Jarvis/Main/deploy/vps/bootstrap.sh | bash
# Then:  su - jarvis  →  cd AK-Jarvis  →  ./deploy/setup.sh cloud
#
# What it does: updates the system, sets Bangkok time, adds swap, installs Python, Node 22, Docker
# (sandbox for commands), ffmpeg, Chromium dependencies and Tailscale; creates the `jarvis` user with
# your SSH key; locks the firewall to SSH + Tailscale only; turns on automatic security updates and
# fail2ban; clones AK-Jarvis.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root."; exit 1; }
USER_NAME="${JARVIS_USER:-jarvis}"
REPO_URL="${REPO_URL:-https://github.com/adriankobsin/AK-Jarvis.git}"
BRANCH="${BRANCH:-Main}"
export DEBIAN_FRONTEND=noninteractive
step() { printf '\n\033[1;36m▸ %s\033[0m\n' "$*"; }

step "System update and base packages"
apt-get update -qq && apt-get -y -qq upgrade
apt-get install -y -qq git curl ca-certificates gnupg ufw fail2ban unattended-upgrades python3 python3-venv \
  python3-pip python3-dev build-essential ffmpeg jq sqlite3 portaudio19-dev libsndfile1 cron
timedatectl set-timezone Asia/Bangkok || true
dpkg-reconfigure -f noninteractive unattended-upgrades || true

step "Swap (4 GB) so browser bursts don't crash anything"
if ! swapon --show | grep -q .; then
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

step "Node.js 22 (for MCP connectors) and Docker (command sandbox)"
if ! command -v node >/dev/null || [[ "$(node -v | cut -d. -f1 | tr -d v)" -lt 20 ]]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null && apt-get install -y -qq nodejs
fi
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh >/dev/null

step "Tailscale (private network between the VPS, your sites and your phone)"
command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh >/dev/null

step "Chromium system libraries (for the browser tools)"
python3 -m venv /opt/pw && /opt/pw/bin/pip install -q playwright && /opt/pw/bin/playwright install-deps chromium >/dev/null && rm -rf /opt/pw

step "User '$USER_NAME'"
if ! id "$USER_NAME" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "Jarvis" "$USER_NAME" >/dev/null
  usermod -aG sudo,docker "$USER_NAME"
  echo "$USER_NAME ALL=(ALL) NOPASSWD:/usr/bin/loginctl,/usr/bin/tailscale" > "/etc/sudoers.d/$USER_NAME"
  if [[ -f /root/.ssh/authorized_keys ]]; then
    install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" "/home/$USER_NAME/.ssh"
    install -m 600 -o "$USER_NAME" -g "$USER_NAME" /root/.ssh/authorized_keys "/home/$USER_NAME/.ssh/authorized_keys"
  fi
fi
usermod -aG docker "$USER_NAME"
loginctl enable-linger "$USER_NAME"

step "Firewall: only SSH from the internet; everything else over Tailscale"
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null && ufw default allow outgoing >/dev/null
ufw allow OpenSSH >/dev/null
ufw allow in on tailscale0 >/dev/null
ufw --force enable >/dev/null
systemctl enable --now fail2ban >/dev/null 2>&1 || true
if [[ -s "/home/$USER_NAME/.ssh/authorized_keys" ]]; then   # keys only, once a key is in place
  sed -i -E 's/^#?PasswordAuthentication .*/PasswordAuthentication no/; s/^#?PermitRootLogin .*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
  systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
fi

step "AK-Jarvis code"
sudo -u "$USER_NAME" bash -c "cd ~ && { [[ -d AK-Jarvis ]] && git -C AK-Jarvis pull -q || git clone -q -b '$BRANCH' '$REPO_URL' AK-Jarvis; }"

cat <<DONE

✅ Server ready.
Next:
  1. tailscale up --ssh          (opens a login link; sign in with your Tailscale account)
  2. su - $USER_NAME
  3. cd AK-Jarvis && ./deploy/setup.sh cloud
DONE
