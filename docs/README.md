# 🧱 Guest Proxy VM — Documentation

## 🗺️ Network Summary Diagram

```
Main LAN (192.168.X.0/24)
    │
    │  ┌──────────────┐
    ├─▶│ eth0 (LAN)   │  ┌──────────────────────────────┐
    │  │ Proxy VM     │──│ wlan0 (IoT / Guest Wi-Fi)     │──▶ IoT Devices
    │  │ Ubuntu Server│  └──────────────────────────────┘
    │  │  ├── Nginx Proxy Manager (Docker)
    │  │  ├── Auto Sync Script (Python)
    │  │  ├── ARPWatch (passive)
    │  │  └── UFW Firewall
    │  └──────────────┘
    │
    ▼
Main Network Clients (Access IoT via Proxy)
```

---

## 🧩 Overview

This VM serves as a **secure reverse proxy and automation hub** between the **main home LAN** and the **Fritz!Box Guest (IoT) Wi-Fi network**.

### 🎯 Goals

* Access IoT devices from the main network without exposing the LAN.
* Keep isolation — IoT devices can never reach the main LAN.
* Automatically update proxy targets when IoT devices change IPs.
* Minimize network noise (passive ARP watching, limited Fritz!Box polling).
* Maintain hardened access control — only LAN access to management.

---

## 🌐 Network Layout

| Interface | Role              | Description                                          |
| --------- | ----------------- | ---------------------------------------------------- |
| `eth0`    | Main LAN          | Management + SSH + Nginx Proxy Manager web interface |
| `wlan0`   | IoT / Guest Wi-Fi | Connected to Fritz!Box Guest network (outbound only) |
| `docker0` | Internal          | Bridge used by Nginx Proxy Manager containers        |

Traffic direction:

```
Main LAN → Nginx Proxy Manager (eth0) → IoT Devices (wlan0)
```

IoT → LAN is **blocked**.

---

## 🔒 Security Concept

* **Inbound allowed only on ********`eth0`********:**

  * SSH (22/tcp)
  * HTTP/HTTPS (80, 443)
* `wlan0` is invisible (no ping, no SSH).
* Firewall enforced with **UFW**.

Example rule set:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow in on eth0 to any port 22 proto tcp comment 'SSH LAN only'
sudo ufw allow in on eth0 to any port 80,443 proto tcp comment 'proxy'
sudo ufw allow in on eth0 to any port 81 proto tcp comment 'NPM UI'
sudo ufw deny in on wlan0 comment 'No inbound IoT access'
sudo ufw enable
```

---

## 🧱 Components

### 🐳 Nginx Proxy Manager (Docker)

* Provides HTTPS reverse proxy and web UI.
* Accessible only via main LAN (`eth0`).
* All proxied connections go **outbound** via `wlan0`.

### 🧠 Auto-Sync Script (Python)

* Syncs IoT device IPs to NPM proxy targets automatically.
* Identifies devices by **MAC** (stable) instead of hostname.
* Uses:

  * **ARPWatch** (passive MAC↔IP source)
  * **Fritz!Box TR-064 API** as fallback (once per cooldown)
* Maintains a `device_registry.json` of learned mappings.

### 📡 ARPWatch

* Passive listener on `wlan0` capturing MAC↔IP pairs.
* Stores results in `/var/lib/arpwatch/arp.dat`.

### 🕐 Systemd Automation

* Service executes sync script.
* Timer runs every 5 min (configurable).

### 🧾 Logrotate

* Rotates `/var/log/npm-auto-sync.log` weekly or when >5 MB.
* Keeps 8 compressed backups (~2 months).

---

## 🔁 Data Flow

```
 ┌────────────────────────────────────────────────┐
 │  Fritz!Box Guest Wi-Fi (DHCP) — 192.168.179.0  │
 └──────────────────────────┬─────────────────────┘
                            │ (USB Wi-Fi dongle)
                            ▼
 ┌────────────────────────────────────────────────┐
 │  Proxy VM (Ubuntu Server)                      │
 │  wlan0  – IoT side                             │
 │  eth0   – LAN side                             │
 │                                                │
 │  ┌───────────────┐                             │
 │  │  ARPWatch     │                             │
 │  │  npm-auto-sync│──▶ Updates NPM via API      │
 │  └───────────────┘                             │
 │          │                                     │
 │          ▼                                     │
 │  Nginx Proxy Manager (Docker)                  │
 │  - Reverse Proxy / Web UI                      │
 │  - Proxies traffic to IoT devices              │
 └──────────┬─────────────────────────────────────┘
            ▼
     Main LAN clients
```

---

## ⚙️ Automation Logic

| Step | Action                                                | Data Source                      | Notes                      |
| ---- | ----------------------------------------------------- | -------------------------------- | -------------------------- |
| 1    | Read MAC↔IP from `/var/lib/arpwatch/arp.dat`          | Passive ARP                      | Zero network traffic       |
| 2    | For unknown/offline devices: optional Fritz!Box query | TR-064 API                       | Rate-limited by cooldown   |
| 3    | Compare to existing NPM proxy targets                 | NPM REST API                     | Local only                 |
| 4    | Update changed targets                                | via `/api/nginx/proxy-hosts/:id` | Requires short-lived token |
| 5    | Log results                                           | `/var/log/npm-auto-sync.log`     | Rotated weekly             |

---

## 🛠️ File Inventory

### 🧰 System & Services

| Path                                        | Purpose                    |
| ------------------------------------------- | -------------------------- |
| `/etc/systemd/system/npm-docker.service`    | Autostart NPM Docker stack |
| `/etc/systemd/system/npm-auto-sync.service` | Runs one sync run          |
| `/etc/systemd/system/npm-auto-sync.timer`   | Triggers sync every 5 min  |
| `/etc/logrotate.d/npm-auto-sync`            | Log rotation rules         |
| `/var/log/npm-auto-sync.log`                | Script log output          |

---

### 🐳 Docker / Proxy Manager

| Path                                     | Purpose                 |
| ---------------------------------------- | ----------------------- |
| `/srv/npm/docker-compose.yml`            | Defines NPM containers  |
| `/var/lib/docker`                        | Container data          |
| `/etc/systemd/system/npm-docker.service` | Manages stack lifecycle |

---

### 🧠 Python Auto-Sync Tool

| Path                                          | Purpose                        |
| --------------------------------------------- | ------------------------------ |
| `/opt/npm-auto-sync/update_npm_from_fritz.py` | Main sync script (ARP + Fritz) |
| `/opt/npm-auto-sync/.env`                     | Credentials & config           |
| `/opt/npm-auto-sync/device_registry.json`     | Learned proxy↔MAC mapping      |
| `/opt/npm-auto-sync/.fritz_state.json`        | Fritz!Box cooldown timestamp   |
| `/opt/npm-auto-sync/.venv/`                   | Python virtual environment     |
| `/var/log/npm-auto-sync.log`                  | Runtime logs                   |

---

### 📡 Networking / Monitoring

| Path                        | Purpose                        |
| --------------------------- | ------------------------------ |
| `/etc/default/arpwatch`     | arpwatch config                |
| `/var/lib/arpwatch/arp.dat` | Passive MAC↔IP database        |
| `/etc/netplan/*.yaml`       | Defines LAN + Wi-Fi interfaces |
| `/etc/ufw/user.rules`       | UFW firewall policy            |

---

### ⚙️ Optional Utilities

| Path                                 | Purpose                   |
| ------------------------------------ | ------------------------- |
| `/usr/local/bin/docker-autoclean.sh` | Docker cleanup (optional) |
| `/etc/logrotate.d/docker-autoclean`  | Rotation for cleanup log  |

---

## 🔄 Systemd Controls

| Command                                      | Description          |
| -------------------------------------------- | -------------------- |
| `systemctl start npm-auto-sync.service`      | Run sync manually    |
| `systemctl status npm-auto-sync.service`     | Check last run       |
| `systemctl enable --now npm-auto-sync.timer` | Enable periodic sync |
| `systemctl list-timers npm-auto-sync*`       | See schedule         |
| `journalctl -u npm-auto-sync.service -e`     | View logs            |

---

## 🧾 Logrotate Configuration Example

`/etc/logrotate.d/npm-auto-sync`

```conf
/var/log/npm-auto-sync.log {
    weekly
    rotate 8
    missingok
    notifempty
    compress
    delaycompress
    create 0640 root adm
    dateext
    maxsize 5M
    su root adm
}
```

---

## ✅ Behavior Summary

| Function    | Trigger       | Source         | Output                       |
| ----------- | ------------- | -------------- | ---------------------------- |
| NPM startup | Boot          | docker-compose | Reverse proxy stack          |
| Sync check  | Every 5 min   | ARP + Fritz    | NPM target updates           |
| Fritz fetch | Max once/hour | TR-064         | Offline-device resolution    |
| Logging     | Every run     | Script output  | `/var/log/npm-auto-sync.log` |
| Rotation    | Weekly / 5 MB | logrotate      | Compressed archives          |
| Firewall    | Always        | UFW            | IoT isolation                |

---

## 📘 Maintenance Tips

* Check last sync:
  `sudo tail -n 20 /var/log/npm-auto-sync.log`
* Trigger immediate update:
  `sudo systemctl start npm-auto-sync.service`
* View device registry:
  `cat /opt/npm-auto-sync/device_registry.json`
* Restart Docker proxy stack:
  `sudo systemctl restart npm-docker.service`
* Update Python deps:
  `sudo /opt/npm-auto-sync/.venv/bin/pip install -U fritzconnection requests python-dotenv`

---

## ♻️ Quick Recovery / Restore Checklist

If the proxy VM is lost, migrated, or rebuilt, follow the steps in (docs/INSTALL.md), for more indepth follow (docs/ManualInstall.md)


## 🔧 Proxy Configuration (Nginx Proxy Manager) & Sync Interaction

This section explains how to configure **Nginx Proxy Manager (NPM)** to proxy IoT devices and how the **auto-sync script** keeps targets up-to-date.

### 1) Create a Proxy Host in NPM

1. Open NPM UI: `http://<Proxy-VM-LAN-IP>:81`
2. **Proxy Hosts → Add Proxy Host**
3. **Domain Names:** choose your local DNS name (e.g., `plug1.iot.lan` or `ha.local.lan`)
4. **Scheme:** `http` (or `https` if your device serves TLS)
5. **Forward Hostname / IP:** *temporarily set the current device IP* (e.g., `192.168.179.21`)
6. **Forward Port:** the device’s port (e.g., `80` for HTTP, `8123` for Home Assistant)
7. Enable **Cache Assets** (optional) and **Websockets** (if your device needs it)
8. **Save**

> The initial IP only bootstraps the mapping. After the first sync, the script associates this NPM entry with the device’s **MAC address** and will keep the IP updated automatically.

### 2) How the Sync Script Associates Devices

* On each run, the script pulls all NPM proxy entries and reads their **current forward_host** (IP).
* It then looks up that IP in the passive ARP table (`/var/lib/arpwatch/arp.dat`) to find the **MAC**.
* If ARP doesn’t know it and the device isn’t suppressed by the offline window, it may query Fritz!Box **(cooldown-limited)** to map IP→MAC.
* Once a MAC is found, a persistent mapping is written to:

  * `/opt/npm-auto-sync/device_registry.json` (`npm_id` ↔ `mac` ↔ `name`)
* On subsequent runs, the script:

  * reads MAC→IP from ARP,
  * updates the NPM proxy target **if the IP changed**, and
  * refreshes `last_seen`.

### 3) Notes & Best Practices

* Use **unique domain names** per proxy host (`device1.iot.lan`, `camera.iot.lan`, etc.).
* Don’t rely on device hostnames — many IoT vendors reuse names. The script uses **MAC** as the canonical identifier.
* If a device sleeps for long, the script skips Fritz calls until ARP sees it again (configurable via `OFFLINE_SUPPRESS_HOURS`).
* Keep the **NPM UI reachable only on LAN**; do not expose the Proxy VM directly to the internet.
* Have a look at `docs\RTL8811.md` if you bought a cheap Realtek (TP-link) wifi USB dongle and have driver issues

---

## 🔗 Integrating Guest Proxy VM with Unbound (Local DNS)

If your **main network uses Unbound** (e.g., on OPNsense) for DNS resolution, you can make proxy hostnames easily accessible to LAN clients.

### 🧩 Goal

Resolve proxy hostnames (e.g. `plug1.iot.lan`) to the **Proxy VM’s LAN IP** so traffic flows via NPM → wlan0 → IoT device.

### ⚙️ Unbound Configuration

1. **Open Unbound settings**
   OPNsense: `Services → Unbound DNS → Overrides`
2. **Add a Host Override**

   * **Host:** `plug1`
   * **Domain:** `iot.lan` (or your LAN domain)
   * **Type:** `A`
   * **IP:** `<LAN-IP of Proxy VM>`
   * **Description:** `Proxy to plug1 (IoT via NPM)`
3. **(Optional) Wildcard / Zone Override**

   * Create a zone for `iot.lan` pointing to the Proxy VM, then manage individual hosts in NPM.
4. **Apply & Restart Unbound**

### 🧠 Example Flow

```
Client → DNS query for camera.iot.lan
      ↳ Unbound resolves to Proxy VM (e.g., 192.168.10.20)
      ↳ Proxy VM (NPM) forwards via wlan0 → camera on Guest Wi-Fi
```

### 🖼️ Diagram

```
LAN Client ──DNS──▶ Unbound
   │                  │
   │  camera.iot.lan  │ resolves to Proxy VM LAN IP
   ▼                  ▼
Proxy VM (NPM) ────HTTP(S)──▶ wlan0 (Guest Wi‑Fi) ─▶ IoT Device
```

### ✅ Tips

* Ensure LAN clients use **Unbound** as their primary DNS.
* Match NPM **domain names** with your **DNS overrides** for consistency.
* Consider using a dedicated subdomain like `iot.lan` to keep things tidy.

---

**Author:** me 
**Tested OS:** Ubuntu Server 25.x on Proxmox PVE 9.0.10
**Last Updated:** 2025‑10‑31

---