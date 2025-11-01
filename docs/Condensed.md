# 🧱 FritzBoxGuestProxy — Full Technical Reference

A **secure automation VM** bridging a **Fritz!Box Guest (IoT) Wi‑Fi** with the **main LAN** via **Nginx Proxy Manager (NPM)**, **Python automation**, and **ARPWatch**. This document contains all essential scripts, configuration files, and context required to **recreate the full system**.

---

## 🚀 System Overview

### Goals

* Access IoT devices from LAN without exposing LAN to IoT.
* Automatically update proxy IPs when DHCP changes.
* Keep network isolation: IoT → LAN blocked.
* Minimize traffic (ARP-based mapping, limited Fritz API calls).

### Stack Summary

* **OS:** Ubuntu Server 25.x (Proxmox VM or standalone)
* **Services:** Docker + Nginx Proxy Manager
* **Automation:** Python (`update_npm_from_fritz.py`), ARPWatch, systemd timers
* **Firewall:** UFW
* **Logs:** logrotate managed
* **Optional:** Docker auto-clean timer

---

## 🧱 Network Architecture

```
Main LAN (192.168.X.0/24)
   │
   ▼ eth0 (LAN)
 ┌────────────────────────────┐
 │  Guest Proxy VM            │
 │  Ubuntu Server 25.x        │
 │  - Nginx Proxy Manager     │
 │  - npm-auto-sync (Python)  │
 │  - ARPWatch                │
 │  - UFW Firewall            │
 └────────────────────────────┘
   ▲ wlan0 (Guest Wi-Fi)
   │
   └── Fritz!Box Guest Wi-Fi → IoT Devices
```

Traffic: `LAN → Proxy VM → IoT` only. IoT devices have no inbound access.

---

## ⚙️ Essential Files

### 1️⃣ `scripts/install.sh`

Automates full provisioning of the Guest Proxy VM.

```bash
#!/usr/bin/env bash
set -euo pipefail

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

echo "==> Installing base packages"
apt-get update -y
apt-get install -y ca-certificates curl gnupg lsb-release python3-venv arpwatch ufw logrotate

# Docker installation
if ! command -v docker >/dev/null 2>&1; then
  echo "==> Installing Docker"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" | tee /etc/apt/sources.list.d/docker.list >/dev/null
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
fi

# Configure UFW
ufw --force disable || true
ufw --force reset || true
ufw default deny incoming
ufw default allow outgoing
ufw default deny routed
ufw allow in on "${ETH_IF}" to any port 22 proto tcp comment 'SSH LAN only'
ufw allow in on "${ETH_IF}" to any port 81 proto tcp comment 'NPM UI'
ufw allow in on "${ETH_IF}" to any port 80 proto tcp comment 'NPM HTTP'
ufw allow in on "${ETH_IF}" to any port 443 proto tcp comment 'NPM HTTPS'
ufw deny  in on "${WIFI_IF}" comment 'Block inbound from IoT'
ufw --force enable

# Nginx Proxy Manager deployment
install -d /srv/npm
cp -f "${REPO_ROOT}/npm/docker-compose.yml" /srv/npm/docker-compose.yml
cp -f "${REPO_ROOT}/npm/npm-docker.service" /etc/systemd/system/npm-docker.service

# Docker auto-clean setup
cp -f "${REPO_ROOT}/npm/docker-autoclean.sh" /usr/local/bin/docker-autoclean.sh
cp -f "${REPO_ROOT}/npm/docker-autoclean.service" /etc/systemd/system/docker-autoclean.service
cp -f "${REPO_ROOT}/npm/docker-autoclean.timer" /etc/systemd/system/docker-autoclean.timer
chmod +x /usr/local/bin/docker-autoclean.sh
systemctl daemon-reload
systemctl enable --now npm-docker.service docker-autoclean.timer

# Python sync tool
install -d /opt/npm-auto-sync
cp -f "${REPO_ROOT}/sync/update_npm_from_fritz.py" /opt/npm-auto-sync/update_npm_from_fritz.py
if [[ ! -f /opt/npm-auto-sync/.env ]]; then
  cp -f "${REPO_ROOT}/sync/.env.example" /opt/npm-auto-sync/.env
fi
python3 -m venv /opt/npm-auto-sync/.venv
/opt/npm-auto-sync/.venv/bin/pip install --upgrade pip
/opt/npm-auto-sync/.venv/bin/pip install -r "${REPO_ROOT}/sync/requirements.txt"
chmod +x /opt/npm-auto-sync/update_npm_from_fritz.py
touch /var/log/npm-auto-sync.log
chmod 0640 /var/log/npm-auto-sync.log
cp -f "${REPO_ROOT}/sync/systemd/npm-auto-sync.service" /etc/systemd/system/npm-auto-sync.service
cp -f "${REPO_ROOT}/sync/systemd/npm-auto-sync.timer" /etc/systemd/system/npm-auto-sync.timer
systemctl daemon-reload
systemctl enable --now npm-auto-sync.timer

# arpwatch enable
systemctl enable --now "arpwatch@${WIFI_IF}.service" || true
sed -i "s|^ARP_FILE=.*|ARP_FILE=/var/lib/arpwatch/${WIFI_IF}.dat|" /opt/npm-auto-sync/.env

echo "Install complete."
```

---

### 2️⃣ Python Sync Script — `update_npm_from_fritz.py`

Full automation logic for syncing ARP and Fritz!Box data to NPM API.

```python
#!/usr/bin/env python3
import json, re, os, time, logging, argparse, requests
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from fritzconnection.lib.fritzhosts import FritzHosts

BASE_DIR = Path(__file__).resolve().parent
REGISTRY_PATH = BASE_DIR / "device_registry.json"
STATE_PATH = BASE_DIR / ".fritz_state.json"

def setup_logging(debug):
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO,
        format='[%(asctime)s] %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

class Config:
    def __init__(self):
        load_dotenv(BASE_DIR / ".env")
        self.fritz_ip = os.getenv('FRITZ_IP')
        self.fritz_user = os.getenv('FRITZ_USER')
        self.fritz_pass = os.getenv('FRITZ_PASS')
        self.npm_url = os.getenv('NPM_URL').rstrip('/')
        self.npm_user = os.getenv('NPM_USER')
        self.npm_pass = os.getenv('NPM_PASS')
        self.arp_file = Path(os.getenv('ARP_FILE', '/var/lib/arpwatch/arp.dat'))
        self.cooldown = int(os.getenv('FRITZ_COOLDOWN_MIN', '60'))


def get_arp(arp_file):
    mac_ip = {}
    if not arp_file.exists():
        logging.warning('ARP file not found')
        return mac_ip
    for line in arp_file.read_text().splitlines():
        m = re.search(r"(\\d+\\.\\d+\\.\\d+\\.\\d+)\\s+ether\\s+([0-9a-f:]+)", line, re.I)
        if m:
            ip, mac = m.groups()
            mac_ip[mac.upper()] = ip
    return mac_ip


def get_token(cfg):
    r = requests.post(f"{cfg.npm_url}/api/tokens", json={'identity': cfg.npm_user, 'secret': cfg.npm_pass})
    return r.json()['token']

def get_hosts(cfg, token):
    r = requests.get(f"{cfg.npm_url}/api/nginx/proxy-hosts", headers={'Authorization': f'Bearer {token}'})
    return {str(h['id']): h for h in r.json()}

def fetch_fritz(cfg):
    fh = FritzHosts(address=cfg.fritz_ip, user=cfg.fritz_user, password=cfg.fritz_pass)
    return {h['mac'].upper(): h for h in fh.get_hosts_info() if h.get('ip')}

def update_proxy(cfg, nid, new_ip, token):
    url = f"{cfg.npm_url}/api/nginx/proxy-hosts/{nid}"
    d = requests.get(url, headers={'Authorization': f'Bearer {token}'}).json()
    if d.get('forward_host') != new_ip:
        d['forward_host'] = new_ip
        requests.put(url, headers={'Authorization': f'Bearer {token}'}, json=d)
        logging.info(f"Updated {nid} → {new_ip}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true')
    args = parser.parse_args()
    setup_logging(args.debug)
    cfg = Config()
    token = get_token(cfg)
    arp = get_arp(cfg.arp_file)
    npm = get_hosts(cfg, token)
    fritz = fetch_fritz(cfg)

    reg = {}
    for nid, e in npm.items():
        ip = e['forward_host']
        mac = next((m for m, v in arp.items() if v == ip), None)
        if not mac:
            mac = next((m for m, v in fritz.items() if v.get('ip') == ip), None)
        if mac:
            new_ip = arp.get(mac) or fritz[mac]['ip']
            update_proxy(cfg, nid, new_ip, token)
            reg[nid] = {'mac': mac, 'ip': new_ip}

    Path(REGISTRY_PATH).write_text(json.dumps(reg, indent=2))

if __name__ == '__main__':
    main()
```

---

### 3️⃣ `.env.example`

```ini
FRITZ_IP=192.168.179.1
FRITZ_USER=admin
FRITZ_PASS=password
NPM_URL=http://127.0.0.1:81
NPM_USER=npm@api.com
NPM_PASS=secret
ARP_FILE=/var/lib/arpwatch/arp.dat
FRITZ_COOLDOWN_MIN=60
OFFLINE_SUPPRESS_HOURS=24
```

---

### 4️⃣ Docker Stack — `docker-compose.yml`

```yaml
version: '3'
services:
  app:
    image: jc21/nginx-proxy-manager:latest
    restart: always
    ports:
      - "80:80"
      - "81:81"
      - "443:443"
    volumes:
      - ./data:/data
      - ./letsencrypt:/etc/letsencrypt
```

---

### 5️⃣ Docker Auto-Clean

#### Script — `docker-autoclean.sh`

```bash
#!/bin/bash
LOGFILE="/var/log/docker-autoclean.log"
THRESHOLD=80
TARGET="/var/lib/docker"
DATE="$(date '+%Y-%m-%d %H:%M:%S')"
USAGE=$(df -P "$TARGET" | awk 'NR==2 {print $5}' | tr -d '%')

echo "[$DATE] Checking disk usage for $TARGET: ${USAGE}%" >> "$LOGFILE"
if [ "$USAGE" -ge "$THRESHOLD" ]; then
  /usr/bin/docker system prune -af --volumes >> "$LOGFILE" 2>&1
  /usr/bin/docker builder prune -af >> "$LOGFILE" 2>&1
  echo "[$DATE] Cleanup complete." >> "$LOGFILE"
else
  echo "[$DATE] Skipped cleanup." >> "$LOGFILE"
fi
echo "----------------------------------------" >> "$LOGFILE"
```

#### Timer & Service

```ini
# /etc/systemd/system/docker-autoclean.service
[Unit]
Description=Docker Auto-Clean
[Service]
Type=oneshot
ExecStart=/usr/local/bin/docker-autoclean.sh
```

```ini
# /etc/systemd/system/docker-autoclean.timer
[Unit]
Description=Run Docker Auto-Clean daily
[Timer]
OnCalendar=daily
Persistent=true
[Install]
WantedBy=timers.target
```

---

### 6️⃣ Systemd Units (NPM + Sync)

```ini
# /etc/systemd/system/npm-docker.service
[Unit]
Description=Nginx Proxy Manager (Docker Compose)
After=docker.service network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/srv/npm
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/npm-auto-sync.service
[Unit]
Description=Sync NPM targets with Fritz!Box + ARP
[Service]
Type=oneshot
ExecStart=/opt/npm-auto-sync/.venv/bin/python /opt/npm-auto-sync/update_npm_from_fritz.py
```

```ini
# /etc/systemd/system/npm-auto-sync.timer
[Unit]
Description=Run npm-auto-sync every 5 minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Persistent=true
[Install]
WantedBy=timers.target
```

---

## 🧾 Recovery / Maintenance

| Purpose              | Command                                                                              |
| -------------------- | ------------------------------------------------------------------------------------ |
| Run sync manually    | `systemctl start npm-auto-sync.service`                                              |
| Restart NPM          | `systemctl restart npm-docker.service`                                               |
| Force Docker cleanup | `systemctl start docker-autoclean.service`                                           |
| View sync logs       | `tail -n 50 /var/log/npm-auto-sync.log`                                              |
| Update deps          | `/opt/npm-auto-sync/.venv/bin/pip install -U fritzconnection requests python-dotenv` |

---

## ✅ Summary

* **Rebuild components:** `install.sh` automates all.
* **Core logic:** `update_npm_from_fritz.py` manages IP sync.
* **Persistence:** All state stored in `/opt/npm-auto-sync`.
* **Isolation:** Enforced by UFW and VM network config.
* **Maintenance:** Docker cleanup and logrotate included.

---

**Author:** me
**License:** MIT
**Tested:** Ubuntu 25.x (Proxmox VE 9.x)
**Last Updated:** 2025‑11‑01
