# FritzBoxGuestProxy
Proxy setup to allow devices in the main network access to certain devices in the FritzBox Guest network. 

The idea is to have smart home devices which really really need to have internet connection in the Guest wifi, away from the main network, and bridge with a proxy.

# 🧱 Guest Proxy VM

> A self-contained Ubuntu VM that securely bridges a **Fritz!Box Guest (IoT) Wi-Fi network** with your main LAN using **Nginx Proxy Manager**, **Python automation**, and **ARPWatch** — providing full isolation and automatic IP synchronization.

---

## 🚀 Quick Start

```bash
# Ensure wifi is connected to the guest network
git clone https://github.com/<your-username>/guest-proxy-vm.git
cd guest-proxy-vm
sudo ETH_IF=eth0 WIFI_IF=wlan0 ./scripts/install.sh # adjust to your actual interface names
sudo nano /opt/npm-auto-sync/.env   # edit Fritz!Box + NPM credentials
sudo systemctl status npm-auto-sync.timer npm-docker.service
```

> ⚠️ Tested only on **Ubuntu Server 25.x**
> See [`docs/INSTALL.md`](docs/INSTALL.md) for full setup instructions.

---

## 🧩 What This Does

This project sets up a **proxy and automation VM** that:

* Connects to your **Fritz!Box Guest (IoT) Wi-Fi** via USB Wi-Fi dongle.
* Runs **Nginx Proxy Manager** to access IoT devices securely from your main LAN.
* Uses **ARPWatch** and **Fritz!Box TR-064 API** to track changing device IPs.
* Automatically updates NPM proxy targets based on MAC addresses.
* Enforces **strong firewall isolation**: IoT → LAN is blocked, LAN → IoT is proxied.

---

## 📘 Documentation

| File                                                               | Description                                                            |
| ------------------------------------------------------------------ | ---------------------------------------------------------------------- |
| [`docs/INSTALL.md`](docs/INSTALL.md)                               | Full installation guide, Wi-Fi setup, and verification                 |
| [`docs/Readme.md`](docs/README.md) | Detailed technical documentation, system design, and restore checklist |
| [`docs/RTL8811.md`](docs/RTL8811.md)                               | strugle with the RTL8811 chipset  
| [`scripts/install.sh`](scripts/install.sh)                         | Automated installer for a clean Ubuntu host                            |
| [`sync/update_npm_from_fritz.py`](sync/update_npm_from_fritz.py)   | Python script managing dynamic IoT IP mappings                         |

---

## 🧠 Design Rationale

### Why this project exists

I wanted to **isolate my smart-home devices** for security but still access them easily from my main LAN.

### Why Fritz!Box Guest network

I’m stuck with a **Fritz!Box environment** across **three houses** connected via wired backbone.
Building a separate VLAN infrastructure wasn’t feasible, and the Fritz!Box guest Wi-Fi provides natural isolation.

### Why this proxy solution

* **No static IPs** in the Fritz!Box guest network — DHCP leases change often.
* **Hostnames are unreliable** — many IoT devices reuse generic names (e.g., multiple “tapo-plug” devices).
* **MAC addresses** are the only consistent identifier, so the proxy auto-maps them to IPs.

### Why a VM (not LXC or OPNsense)

* Initially planned on my **main OPNsense VM** (central gateway) — but Realtek USB Wi-Fi driver issues under FreeBSD made that impractical.
* **Ubuntu Server VM** offered better driver support for Realtek Wi-Fi dongles. I hoped also easier than debian.
* Chose **VM over LXC**: better isolation, i changed some kernel driver; performance impact is negligible.

### Why not rebuild the whole network

The setup had to **fit into existing infrastructure** — Fritz!Boxes, multiple subnets, and Proxmox nodes —
so this lightweight VM was the cleanest, least disruptive way to add secure IoT isolation.

---

## 💡 Platform Notes

* Runs on **Proxmox VE (PVE)** — use the community installer for quick Ubuntu VM deployment:
  👉 [Ubuntu 25.04 VM Installer](https://community-scripts.github.io/ProxmoxVE/scripts?id=ubuntu2504-vm&category=Operating+Systems)
* should work on any ubuntu/debian, like also on a raspi — it already has both LAN and Wi-Fi, so you can repurpose it as a standalone guest proxy node.

---

## 🛡️ Tech Stack

* **Ubuntu Server 25.x**
* **Docker + Nginx Proxy Manager**
* **Python 3 + fritzconnection + requests**
* **ARPWatch**
* **UFW Firewall**
* **systemd services & timers**
* **logrotate**

---

## 🧾 License

MIT License — feel free to use, modify, and improve.
Contributions and feedback welcome.

---

**Author:** me

**Project:** MeHomeLab – FritzBoxGuestProxy - a Smart-Home Isolation in Fritzbox networks

**Last Updated:** 2025-10-31

You’ll notice that a lot of this has been generated — I basically let it write most of it for me to save some time. It’s mainly my own documentation so I don’t forget what I’ve done, but I’m always happy to get comments and feedback!

There’s a condensed version at `docs/Condensed.md` and a setup prompt at `docs/SetupPrompt.md` that you can drop into your preferred interface to get started quickly.