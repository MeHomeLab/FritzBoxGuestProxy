# ⚙️ INSTALL.md — Guest Proxy VM Setup Guide

## 🧱 Overview

This document describes how to set up the **Guest Proxy VM** on a fresh Ubuntu Server installation. It installs and configures all components required for isolating IoT devices in the Fritz!Box Guest Wi‑Fi network while allowing secure access from the main LAN.

> ⚠️ **Note:** Tested only on **Ubuntu Server 25.x** (64‑bit). Other versions may require adjustments.

---

## 📋 Requirements

### Hardware
- Ubuntu Server 25.x (64‑bit)
- Ethernet connection to your **main LAN**
- USB Wi‑Fi dongle (connected to Fritz!Box Guest Wi‑Fi)

### Software Packages
The install script will automatically install:
- Docker + Docker Compose plugin
- Python 3 (with `venv`)
- ARPWatch
- UFW Firewall
- logrotate

### Information Needed
Before running the installer, identify:
- `ETH_IF` → name of your LAN interface (e.g., `eth0`, `ens18`)
- `WIFI_IF` → name of your Wi‑Fi interface (e.g., `wlan0`)

---

## 🔎 Identify Network Interfaces

Run the following to list interfaces:

```bash
ip link show
```

You’ll see output such as:
```
2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc mq state UP mode DEFAULT group default qlen 1000
3: wlan0: <BROADCAST,MULTICAST> mtu 1500 qdisc noop state DOWN mode DEFAULT group default qlen 1000
```

In this example:
- `eth0` → wired LAN
- `wlan0` → Wi‑Fi dongle (used for Guest network)

---

## 📶 Set Up Wi‑Fi (Guest Network)

Connect the Wi‑Fi dongle to the **Fritz!Box Guest Wi‑Fi**:

```bash
sudo apt install -y network-manager
sudo nmcli dev wifi list               # List available networks
sudo nmcli dev wifi connect "FRITZ!Box Gastzugang" --ask
```

Verify connectivity:
```bash
ping -c 3 8.8.8.8
```

If successful, note the assigned IP address:
```bash
ip a show wlan0
```

---

## ⚙️ Installation

Clone your repo and run the installer:

```bash
git clone https://github.com/MeHomeLab/FritzBoxGuestProxy.git
cd guest-proxy-vm
sudo ETH_IF=eth0 WIFI_IF=wlan0 bash ./scripts/install.sh
```

### What the Script Does
1. Installs required packages and Docker.
2. Configures UFW firewall for isolation.
3. Deploys **Nginx Proxy Manager** via Docker.
4. Sets up **npm-auto-sync** service and timer.
5. Enables **ARPWatch** on the Wi‑Fi interface.
6. Configures logrotate for the sync logs.

---

## 🧠 Post‑Install Steps

### 1️⃣ Configure Environment
Edit `/opt/npm-auto-sync/.env` with your settings:
```bash
sudo nano /opt/npm-auto-sync/.env
```
Example variables:
```
FRITZ_IP=192.168.179.1
FRITZ_USER=myuser
FRITZ_PASS=mypass
NPM_URL=http://127.0.0.1:81
NPM_USER=npm@api.com
NPM_PASS=yourpassword
FRITZ_COOLDOWN_MIN=60
OFFLINE_SUPPRESS_HOURS=24
```

Instead of putting NPM_USER and NPM_PASS in the .env you could create an api key via curl and modify the script to directly use an API key, but since the key only has a lifetime of 24h, it was consider good enough to put the credentials of an npm_api user in the file.

### 2️⃣ Verify Components
```bash
sudo systemctl status npm-docker.service
sudo systemctl status npm-auto-sync.timer
sudo tail -n 30 /var/log/npm-auto-sync.log
```

### 3️⃣ Access the NPM Web UI
Visit:  
👉 `http://<LAN-IP>:81`

Use the default NPM credentials and change them immediately.
username: admin@example.com. password: changeme

You can then create a npm_api user.

---

## 🔍 Troubleshooting

| Issue | Possible Fix |
|--------|---------------|
| Wi‑Fi not detected | Check `lsusb`, install latest kernel or drivers (RTL8811AU / 8812AU, see seperate document) |
| No internet from VM | Ensure correct routing and that `wlan0` is connected |
| NPM not reachable | Check Docker status: `sudo docker ps` |
| Sync not updating | Check logs: `sudo tail -n 50 /var/log/npm-auto-sync.log` |
| Manual sync run | `sudo /opt/npm-auto-sync/venv/bin/python update_npm_from_fritz.py --debug` |

---

## 🧾 Maintenance Commands

| Purpose | Command |
|----------|----------|
| Run sync manually | `sudo systemctl start npm-auto-sync.service` |
| Restart NPM | `sudo systemctl restart npm-docker.service` |
| Update Python deps | `sudo /opt/npm-auto-sync/.venv/bin/pip install -U fritzconnection requests python-dotenv` |
| Rotate logs manually | `sudo logrotate -f /etc/logrotate.d/npm-auto-sync` |

---

## ♻️ Removal (Optional)

```bash
sudo systemctl disable --now npm-auto-sync.timer npm-docker.service
sudo ufw disable
sudo apt remove -y docker.io arpwatch logrotate network-manager
sudo rm -rf /opt/npm-auto-sync /srv/npm /etc/systemd/system/npm-* /etc/logrotate.d/npm-auto-sync
```

---

**Author:** me 
**Tested OS:** Ubuntu Server 25.x on Proxmox PVE 9.0.10
**Last Updated:** 2025‑10‑31

---