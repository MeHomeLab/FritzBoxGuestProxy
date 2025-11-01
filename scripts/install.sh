#!/usr/bin/env bash
set -euo pipefail

# -------- Config from env --------
ETH_IF="${ETH_IF:-}"
WIFI_IF="${WIFI_IF:-}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root: sudo ETH_IF=eth0 WIFI_IF=wlan0 $0"
  exit 1
fi

if [[ -z "${ETH_IF}" || -z "${WIFI_IF}" ]]; then
  echo "Missing interface names. Example:"
  echo "  sudo ETH_IF=eth0 WIFI_IF=wlan0 $0"
  exit 1
fi

echo "==> Using ETH_IF=${ETH_IF}, WIFI_IF=${WIFI_IF}"
echo "==> Repo root: ${REPO_ROOT}"

# -------- Packages --------
echo "==> Installing base packages"
apt-get update -y
apt-get install -y ca-certificates curl gnupg lsb-release \
  python3-venv arpwatch ufw logrotate

# Docker (official) ------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  echo "==> Installing Docker (engine + compose plugin)"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo \
"deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
${VERSION_CODENAME} stable" | tee /etc/apt/sources.list.d/docker.list >/dev/null
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
fi

# -------- UFW rules --------
echo "==> Configuring UFW"
ufw --force disable || true
ufw --force reset || true
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw default deny routed
ufw allow in on "${ETH_IF}" to any port 22 proto tcp comment 'SSH LAN only'
ufw allow in on "${ETH_IF}" to any port 81 proto tcp comment 'NPM UI'
ufw allow in on "${ETH_IF}" to any port 80 proto tcp comment 'NPM HTTP'
ufw allow in on "${ETH_IF}" to any port 443 proto tcp comment 'NPM HTTPS'
ufw deny  in on "${WIFI_IF}" comment 'Block inbound from IoT/Guest'
ufw --force enable

# -------- NPM (docker compose) --------
echo "==> Deploying Nginx Proxy Manager service"
install -d /srv/npm
cp -f "${REPO_ROOT}/npm/docker-compose.yml" /srv/npm/docker-compose.yml
install -d /etc/systemd/system
cp -f "${REPO_ROOT}/npm/npm-docker.service" /etc/systemd/system/npm-docker.service
# docker cleanup + logrotate
cp -f "${REPO_ROOT}/npm/docker-autoclean.sh" /usr/local/bin/docker-autoclean.sh
cp -f "${REPO_ROOT}/npm/docker-autoclean" /etc/logrotate.d/docker-autoclean
cp -f "${REPO_ROOT}/npm/docker-autoclean.service" /etc/systemd/system/docker-autoclean.service
cp -f "${REPO_ROOT}/npm/docker-autoclean.timer" /etc/systemd/system/docker-autoclean.timer
chmod +x /usr/local/bin/docker-autoclean.sh
systemctl daemon-reload
systemctl enable --now npm-docker.service
systemctl enable --now docker-autoclean.timer

# -------- Sync tool (venv + service + timer) --------
echo "==> Installing npm-auto-sync"
install -d /opt/npm-auto-sync
# Copy Python app
cp -f "${REPO_ROOT}/sync/update_npm_from_fritz.py" /opt/npm-auto-sync/update_npm_from_fritz.py
# Only copy .env if not present, so secrets persist
if [[ ! -f /opt/npm-auto-sync/.env ]]; then
  cp -f "${REPO_ROOT}/sync/.env.example" /opt/npm-auto-sync/.env
  echo "!! Edit /opt/npm-auto-sync/.env with Fritz and NPM credentials"
fi
# venv
python3 -m venv /opt/npm-auto-sync/.venv
/opt/npm-auto-sync/.venv/bin/pip install --upgrade pip
if [[ -f "${REPO_ROOT}/sync/requirements.txt" ]]; then
  /opt/npm-auto-sync/.venv/bin/pip install -r "${REPO_ROOT}/sync/requirements.txt"
else
  /opt/npm-auto-sync/.venv/bin/pip install fritzconnection requests python-dotenv
fi
chmod +x /opt/npm-auto-sync/update_npm_from_fritz.py
touch /var/log/npm-auto-sync.log
chmod 0640 /var/log/npm-auto-sync.log || true

# systemd units for sync
cp -f "${REPO_ROOT}/sync/systemd/npm-auto-sync.service" /etc/systemd/system/npm-auto-sync.service
cp -f "${REPO_ROOT}/sync/systemd/npm-auto-sync.timer"   /etc/systemd/system/npm-auto-sync.timer
# ensure ExecStart path is venv python
sed -i 's|ExecStart=.*|ExecStart=/opt/npm-auto-sync/.venv/bin/python /opt/npm-auto-sync/update_npm_from_fritz.py|' \
  /etc/systemd/system/npm-auto-sync.service

systemctl daemon-reload
systemctl enable --now npm-auto-sync.timer

# logrotate
install -d /etc/logrotate.d
cp -f "${REPO_ROOT}/sync/logrotate/npm-auto-sync" /etc/logrotate.d/npm-auto-sync

# -------- arpwatch --------
echo "==> Enabling arpwatch on ${WIFI_IF}"
systemctl enable --now "arpwatch@${WIFI_IF}.service" || true
# put arpwatch log into .env for reference
sudo sed -i "s|^ARP_FILE=.*|ARP_FILE=/var/lib/arpwatch/${WIFI_IF}.dat|" /opt/npm-auto-sync/.env

# -------- Done --------
echo "==> Install complete."
echo "Next steps:"
echo "  1) Edit /opt/npm-auto-sync/.env (Fritz creds, NPM URL, cooldowns, ARP file if needed)"
echo "  2) Verify NPM is up:    systemctl status npm-docker.service"
echo "  3) Kick a sync run:     systemctl start npm-auto-sync.service && tail -n 50 /var/log/npm-auto-sync.log"
echo "  4) NPM UI (LAN only):   http://<${ETH_IF}-IP>:81"
echo "  5) Monitor sync timer:  systemctl list-timers npm-auto-sync.timer"