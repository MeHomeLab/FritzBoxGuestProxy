## ♻️ Manual Recovery / Re-Install Guide (no install.sh)

Use this guide to manually rebuild the proxy VM if it’s lost, migrated, or rebuilt.
(Automatic setup with install.sh is documented elsewhere.)

### 1️⃣ Base System Setup

Install essential base packages:

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg lsb-release \
  python3-venv arpwatch ufw logrotate
```

**Install Docker (official):**

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo tee /etc/apt/keyrings/docker.asc >/dev/null
sudo chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
```

### 🧩 Optional Step: Copy Files from Repo

If you already have your repo available locally, copy all required files in one go:

```bash
# Path to your repo
REPO_ROOT=~/repo/npm-proxy

# Create directories
sudo mkdir -p /srv/npm /opt/npm-auto-sync /etc/systemd/system /usr/local/bin /etc/logrotate.d

# Copy NPM stack
sudo cp -f "$REPO_ROOT/npm/docker-compose.yml" /srv/npm/docker-compose.yml
sudo cp -f "$REPO_ROOT/npm/npm-docker.service" /etc/systemd/system/npm-docker.service

# Copy docker autoclean helper + services
sudo cp -f "$REPO_ROOT/npm/docker-autoclean.sh" /usr/local/bin/docker-autoclean.sh
sudo chmod +x /usr/local/bin/docker-autoclean.sh
sudo cp -f "$REPO_ROOT/npm/docker-autoclean" /etc/logrotate.d/docker-autoclean
sudo cp -f "$REPO_ROOT/npm/docker-autoclean.service" /etc/systemd/system/docker-autoclean.service
sudo cp -f "$REPO_ROOT/npm/docker-autoclean.timer" /etc/systemd/system/docker-autoclean.timer

# Copy sync tool + units
sudo cp -f "$REPO_ROOT/sync/update_npm_from_fritz.py" /opt/npm-auto-sync/update_npm_from_fritz.py
sudo cp -f "$REPO_ROOT/sync/.env.example" /opt/npm-auto-sync/.env
sudo cp -f "$REPO_ROOT/sync/requirements.txt" /opt/npm-auto-sync/requirements.txt
sudo cp -f "$REPO_ROOT/sync/systemd/npm-auto-sync.service" /etc/systemd/system/npm-auto-sync.service
sudo cp -f "$REPO_ROOT/sync/systemd/npm-auto-sync.timer" /etc/systemd/system/npm-auto-sync.timer
sudo cp -f "$REPO_ROOT/sync/logrotate/npm-auto-sync" /etc/logrotate.d/npm-auto-sync
```

💡 Skip this step if you’re recreating files manually — contents are listed below.

### 2️⃣ UFW Configuration

```bash
ETH_IF=eth0
WIFI_IF=wlan0

sudo ufw --force disable || true
sudo ufw --force reset || true
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow in on "$ETH_IF" to any port 22,81,80,443 proto tcp comment 'NPM + SSH LAN only'
sudo ufw deny  in on "$WIFI_IF" comment 'Block inbound from IoT'
sudo ufw --force enable
```

### 3️⃣ Nginx Proxy Manager (Docker + systemd)

Create `/srv/npm/docker-compose.yml`:

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

Create `/etc/systemd/system/npm-docker.service`:

```ini
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

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now npm-docker.service
```

### 4️⃣ Docker Auto-Clean Helper

Automatically prunes Docker when disk usage > 80%.

Create `/usr/local/bin/docker-autoclean.sh`:

```bash
#!/bin/bash
# docker-autoclean.sh — conditional Docker cleanup when disk usage > 80%

LOGFILE="/var/log/docker-autoclean.log"
THRESHOLD=80
TARGET="/var/lib/docker"
DATE="$(date '+%Y-%m-%d %H:%M:%S')"

USAGE=$(df -P "$TARGET" | awk 'NR==2 {print $5}' | tr -d '%')
echo "[$DATE] Checking disk usage for $TARGET: ${USAGE}% full (threshold ${THRESHOLD}%)" >> "$LOGFILE"

if [ "$USAGE" -ge "$THRESHOLD" ]; then
  echo "[$DATE] Usage above threshold — starting cleanup..." >> "$LOGFILE"
  /usr/bin/docker system prune -af --volumes >> "$LOGFILE" 2>&1
  /usr/bin/docker builder prune -af >> "$LOGFILE" 2>&1
  echo "[$DATE] Cleanup complete." >> "$LOGFILE"
else
  echo "[$DATE] Usage below threshold — skipping cleanup." >> "$LOGFILE"
fi

echo "----------------------------------------" >> "$LOGFILE"
```

```bash
sudo chmod +x /usr/local/bin/docker-autoclean.sh
```

Create `/etc/systemd/system/docker-autoclean.service`:

```ini
[Unit]
Description=Docker Auto-Clean (manual trigger)

[Service]
Type=oneshot
ExecStart=/usr/local/bin/docker-autoclean.sh
```

Create `/etc/systemd/system/docker-autoclean.timer`:

```ini
[Unit]
Description=Run Docker Auto-Clean daily

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now docker-autoclean.timer
```

### 5️⃣ Sync Tool (npm-auto-sync)

Keeps NPM targets synced with Fritz!Box and local ARP data.

**Files:**

```
/opt/npm-auto-sync/update_npm_from_fritz.py
/opt/npm-auto-sync/requirements.txt
/opt/npm-auto-sync/.env
```

**Systemd Units:**

```
/etc/systemd/system/npm-auto-sync.service
/etc/systemd/system/npm-auto-sync.timer
```

Example `.env`:

```ini
# Fritz!Box connection
FRITZ_IP=fritz.box
FRITZ_USER=myuser
FRITZ_PASS=secret

# NPM credentials
NPM_URL=http://localhost:81
NPM_USER=admin
NPM_PASS=changeme

# Optional tuning
ARP_FILE=/var/lib/arpwatch/arp.dat
FRITZ_COOLDOWN_MIN=60
OFFLINE_SUPPRESS_HOURS=24
```

### 6️⃣ Python Environment Setup

```bash
sudo python3 -m venv /opt/npm-auto-sync/.venv
sudo /opt/npm-auto-sync/.venv/bin/pip install --upgrade pip
sudo /opt/npm-auto-sync/.venv/bin/pip install -r /opt/npm-auto-sync/requirements.txt
sudo touch /var/log/npm-auto-sync.log && sudo chmod 0640 /var/log/npm-auto-sync.log
sudo systemctl daemon-reload
sudo systemctl enable --now npm-auto-sync.timer
```

### 7️⃣ Enable arpwatch

```bash
WIFI_IF=wlan0
sudo systemctl enable --now "arpwatch@${WIFI_IF}.service" || true
# Optional update log location in .env file
sudo sed -i "s|^ARP_FILE=.*|ARP_FILE=/var/lib/arpwatch/${WIFI_IF}.dat|" /opt/npm-auto-sync/.env
```

### 8️⃣ Verify Everything

```bash
sudo systemctl status npm-docker.service
sudo systemctl status npm-auto-sync.timer
sudo systemctl list-timers | grep npm
sudo tail -n 50 /var/log/npm-auto-sync.log
```

Access the NPM web UI:
👉 `http://<LAN-IP>:81`
