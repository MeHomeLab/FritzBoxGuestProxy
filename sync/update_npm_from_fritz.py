#!/usr/bin/env python3
# =============================================================================
#  Hybrid NPM ↔ Fritz!Box Sync
#  ------------------------------------------------------------
#  Created by: me
#  Repository: https://github.com/MeHomeLab/FritzBoxGuestProxy
#  License: MIT
#
#  Description:
#    Keeps Nginx Proxy Manager proxy targets in sync with local network
#    devices, using ARP (arpwatch) data first and falling back to the
#    Fritz!Box API. Designed to run quietly, with an optional --debug
#    flag for verbose output.
#                                                 _____     ____
#                                                /      \  |  o |
#                                               |        |/ ___\| 
#  Created: 2025-11-01                          |_________/  me
#  Last Updated: 2025-11-01                     |_|_| |_|_|
# =============================================================================


#!/usr/bin/env python3
"""
Hybrid NPM ↔ Fritz!Box sync

- Prefer ARP (arpwatch) data (zero network noise)
- Fallback to Fritz!Box API for unknown MACs (with cooldown)
- Update Nginx Proxy Manager (NPM) proxy targets automatically

Public-repo ready:
- No secrets hardcoded; uses environment variables (.env supported)
- Sensible logging via Python's logging module (INFO by default; DEBUG with --debug)
- Defensive checks for missing configuration
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import requests
from dotenv import load_dotenv
from fritzconnection.lib.fritzhosts import FritzHosts

# ---------- Paths / Files ----------
BASE_DIR = Path(__file__).resolve().parent
REGISTRY_PATH = BASE_DIR / "device_registry.json"
STATE_PATH = BASE_DIR / ".fritz_state.json"

# ---------- Logging ----------
logger = logging.getLogger("npm-fritz-sync")


def setup_logging(debug: bool, log_file: Optional[str]) -> None:
    level = logging.DEBUG if debug else logging.INFO

    fmt = "[%(asctime)s] %(levelname)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    handlers = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(level=level, format=fmt, datefmt=datefmt, handlers=handlers)
    # Keep requests/fritzconnection noise down unless debugging
    logging.getLogger("urllib3").setLevel(logging.WARNING if not debug else logging.DEBUG)
    logging.getLogger("fritzconnection").setLevel(logging.WARNING if not debug else logging.DEBUG)


# ---------- Config ----------
@dataclass(frozen=True)
class Config:
    fritz_ip: str
    fritz_user: str
    fritz_pass: str

    npm_url: str
    npm_user: str
    npm_pass: str

    arp_file: Path
    fritz_cooldown_min: int
    offline_suppress_hours: int


def load_config() -> Config:
    load_dotenv(BASE_DIR / ".env")

    def need(name: str) -> str:
        v = os.getenv(name)
        if not v:
            raise ValueError(f"Missing required environment variable: {name}")
        return v

    npm_url_raw = need("NPM_URL")
    npm_url = npm_url_raw.rstrip("/")

    return Config(
        fritz_ip=need("FRITZ_IP"),
        fritz_user=need("FRITZ_USER"),
        fritz_pass=need("FRITZ_PASS"),
        npm_url=npm_url,
        npm_user=need("NPM_USER"),
        npm_pass=need("NPM_PASS"),
        arp_file=Path(os.getenv("ARP_FILE", "/var/lib/arpwatch/arp.dat")),
        fritz_cooldown_min=int(os.getenv("FRITZ_COOLDOWN_MIN", "60")),
        offline_suppress_hours=int(os.getenv("OFFLINE_SUPPRESS_HOURS", "24")),
    )


# ---------- Helpers ----------
def read_json(path: Path, default):
    if path.exists():
        try:
            return json.load(path.open())
        except Exception as e:
            logger.warning("Failed reading %s: %s", path, e)
    return default


def write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(path)


# ---------- NPM auth / requests ----------
def get_npm_token(cfg: Config) -> str:
    r = requests.post(
        f"{cfg.npm_url}/api/tokens",
        headers={"Content-Type": "application/json; charset=UTF-8"},
        json={"identity": cfg.npm_user, "secret": cfg.npm_pass},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    logger.debug("Obtained NPM token (expires %s)", data.get("expires"))
    return data["token"]


def npm_request(cfg: Config, method: str, endpoint: str, token: str, **kwargs) -> requests.Response:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    return requests.request(method, f"{cfg.npm_url}{endpoint}", headers=headers, timeout=15, **kwargs)


def get_npm_hosts(cfg: Config, token: str) -> Dict[str, dict]:
    r = npm_request(cfg, "GET", "/api/nginx/proxy-hosts", token)
    r.raise_for_status()
    hosts = {str(h["id"]): h for h in r.json()}
    logger.info("Loaded %d NPM proxy hosts", len(hosts))
    logger.debug("NPM hosts: %s", list(hosts.keys()))
    return hosts


def update_npm_target(cfg: Config, npm_id: str, new_ip: str, token: str) -> None:
    r = npm_request(cfg, "GET", f"/api/nginx/proxy-hosts/{npm_id}", token)
    if not r.ok:
        logger.error("Failed to fetch NPM host %s: %s", npm_id, r.status_code)
        return

    data = r.json()
    if data.get("forward_host") == new_ip:
        logger.debug("NPM %s already points to %s; skipped", npm_id, new_ip)
        return

    data["forward_host"] = new_ip
    up = npm_request(cfg, "PUT", f"/api/nginx/proxy-hosts/{npm_id}", token, json=data)
    if up.ok:
        dn = data.get("domain_names") or []
        label = dn[0] if dn else data.get("forward_host", npm_id)
        logger.info("Updated proxy '%s' → %s", label, new_ip)
    else:
        logger.error("Failed updating proxy %s: %s %s", npm_id, up.status_code, up.text[:200])


# ---------- ARP WATCH ----------
def get_arp_table(arp_file: Path) -> Dict[str, str]:
    """Parse arpwatch's arp.dat file -> {MAC: IP}"""
    mac_ip: Dict[str, str] = {}
    if not arp_file.exists():
        logger.warning("ARP file not found: %s", arp_file)
        return mac_ip

    try:
        with arp_file.open() as f:
            for line in f:
                # Example: "192.168.1.23 ether aa:bb:cc:dd:ee:ff ..."
                m = re.search(r"(\d+\.\d+\.\d+\.\d+)\s+ether\s+([0-9a-f:]+)", line, re.I)
                if m:
                    ip, mac = m.groups()
                    mac_ip[mac.upper()] = ip
    except Exception as e:
        logger.error("Failed parsing ARP file %s: %s", arp_file, e)

    logger.info("Loaded %d ARP entries", len(mac_ip))
    logger.debug("ARP entries: %s", mac_ip)
    return mac_ip


# ---------- Fritz fallback (with cooldown) ----------
def _read_state() -> dict:
    return read_json(STATE_PATH, {})


def _write_state(d: dict) -> None:
    write_json(STATE_PATH, d)


def fetch_fritz_hosts_with_cooldown(cfg: Config) -> Optional[Dict[str, dict]]:
    """Fetch Fritz hosts at most once per cfg.fritz_cooldown_min minutes."""
    st = _read_state()
    now = time.time()
    last = st.get("last_fritz_fetch_ts", 0)
    if now - last < cfg.fritz_cooldown_min * 60:
        logger.info("Fritz fetch skipped (cooldown active)")
        return None

    fh = FritzHosts(address=cfg.fritz_ip, user=cfg.fritz_user, password=cfg.fritz_pass)
    hosts = {h["mac"].upper(): h for h in fh.get_hosts_info() if h.get("ip")}
    st["last_fritz_fetch_ts"] = now
    _write_state(st)

    logger.info("Fritz fetch done (%d hosts)", len(hosts))
    logger.debug("Fritz hosts: %s", list(hosts.keys()))
    return hosts


# ---------- Registry helpers ----------
def load_registry() -> Dict[str, dict]:
    reg = read_json(REGISTRY_PATH, {})
    # shape sanity
    if not isinstance(reg, dict):
        logger.warning("Registry corrupted; starting fresh")
        return {}
    return reg


def save_registry(reg: Dict[str, dict]) -> None:
    write_json(REGISTRY_PATH, reg)


def mark_last_seen(reg: Dict[str, dict], mac: str) -> None:
    nid = next((k for k, v in reg.items() if v.get("mac") == mac), None)
    if nid:
        reg[nid]["last_seen"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"


# ---------- MAIN ----------
def sync(cfg: Config) -> None:
    reg = load_registry()
    arp = get_arp_table(cfg.arp_file)
    fritz: Optional[Dict[str, dict]] = None

    token = get_npm_token(cfg)
    npm = get_npm_hosts(cfg, token)

    # Learn phase: map NPM entry -> MAC (by ARP first, Fritz as fallback)
    for nid, entry in npm.items():
        if nid not in reg:
            target_ip = entry.get("forward_host")
            mac_match = next((mac for mac, ip in arp.items() if ip == target_ip), None)

            if not mac_match:
                if fritz is None:
                    fritz = fetch_fritz_hosts_with_cooldown(cfg)
                if fritz:
                    mac_match = next(
                        (mac for mac, h in fritz.items() if h.get("ip") == target_ip), None
                    )

            if mac_match:
                reg[nid] = {
                    "npm_id": nid,
                    "mac": mac_match,
                    "name": (entry.get("domain_names") or [entry.get("forward_host")])[0],
                    "last_seen": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                }
                logger.info("Learned mapping %s → %s", reg[nid]["name"], mac_match)
            else:
                logger.debug("No MAC match for NPM id %s (target %s)", nid, target_ip)

    # Update phase: refresh targets and last_seen
    for nid, info in reg.items():
        mac = info.get("mac")
        if not mac:
            continue

        new_ip = arp.get(mac)

        # suppress Fritz calls for long-offline devices
        last_seen = info.get("last_seen")
        too_old = False
        if last_seen:
            try:
                dt = datetime.fromisoformat(last_seen.replace("Z", ""))
                too_old = (datetime.utcnow() - dt).total_seconds() > cfg.offline_suppress_hours * 3600
            except Exception:
                pass

        if not new_ip and not too_old:
            if fritz is None:
                fritz = fetch_fritz_hosts_with_cooldown(cfg)
            if fritz and mac in fritz:
                new_ip = fritz[mac].get("ip")

        if new_ip:
            update_npm_target(cfg, nid, new_ip, token)
            mark_last_seen(reg, mac)
        else:
            logger.debug("No IP found for %s (%s); skipped", info.get("name", nid), mac)

    save_registry(reg)
    logger.info("Sync run complete")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Hybrid NPM ↔ Fritz!Box sync")
    p.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug logging (default is INFO: only essential output)",
    )
    p.add_argument(
        "--log-file",
        default=os.getenv("LOGFILE", ""),  # optional; not committed
        help="Optional path to write logs (also logs to stderr). Defaults to $LOGFILE if set.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(debug=args.debug, log_file=(args.log_file or None))

    try:
        cfg = load_config()
    except Exception as e:
        # Use print here to ensure visibility even if logging isn't configured
        logger.error("%s", e)
        raise SystemExit(2)

    try:
        sync(cfg)
    except requests.RequestException as e:
        logger.error("Network error: %s", e)
        raise SystemExit(1)
    except Exception as e:
        logger.error("Unhandled error: %s", e)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
