"""Local network discovery for the classroom hotspot.

The teacher laptop may be in one of three states:

1. **Normal Wi-Fi client** — connected to the school router (may or may not have
   Internet).  Students reach the app over that LAN.
2. **Wi-Fi hotspot with Internet sharing** — laptop shares its uplink; the
   hotspot interface has its own subnet.
3. **Fully offline local classroom network** — hotspot or direct connection with
   no uplink at all.  This is the deployment target.

This module only *reads* system state.  It never changes NetworkManager or
firewall configuration; those commands live in ``scripts/hotspot.sh`` and are
explicit, reversible and documented.
"""

from __future__ import annotations

import ipaddress
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from typing import Any

HOTSPOT_HINTS = ("hotspot", "ap", "wlan-ap", "shared")


@dataclass
class Interface:
    name: str
    ip: str
    is_wireless: bool

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ip": self.ip, "is_wireless": self.is_wireless}


@dataclass
class NetworkStatus:
    interfaces: list[Interface] = field(default_factory=list)
    primary_ip: str | None = None
    hotspot_active: bool = False
    hotspot_interface: str | None = None
    online: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "interfaces": [i.to_dict() for i in self.interfaces],
            "primary_ip": self.primary_ip,
            "hotspot_active": self.hotspot_active,
            "hotspot_interface": self.hotspot_interface,
            "online": self.online,
            "warnings": self.warnings,
        }


def _run(cmd: list[str], timeout: float = 4.0) -> str:
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return out.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def list_interfaces() -> list[Interface]:
    """Parse ``ip -brief -4 addr`` (loopback excluded)."""
    interfaces: list[Interface] = []
    for line in _run(["ip", "-brief", "-4", "addr"]).splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[0] == "lo":
            continue
        name, _state, addr = parts[0], parts[1], parts[2]
        ip = addr.split("/")[0]
        try:
            if ipaddress.ip_address(ip).is_loopback:
                continue
        except ValueError:
            continue
        interfaces.append(Interface(name=name, ip=ip, is_wireless=name.startswith("wl")))
    return interfaces


def detect_hotspot(interfaces: list[Interface], nmcli_output: str) -> str | None:
    """Return the hotspot interface name, if NetworkManager reports one."""
    for line in nmcli_output.splitlines():
        text = line.lower()
        if not any(h in text for h in HOTSPOT_HINTS):
            continue
        for iface in interfaces:
            if iface.name in line:
                return iface.name
    return None


def network_status(port: int, probe_online: bool = True) -> NetworkStatus:
    status = NetworkStatus()
    status.interfaces = list_interfaces()
    status.warnings = []

    nm = _run(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"])
    if not nm:
        status.warnings.append("NetworkManager not reporting active connections; using raw interface scan.")
    status.hotspot_interface = detect_hotspot(status.interfaces, nm)
    status.hotspot_active = status.hotspot_interface is not None

    # Prefer a hotspot address, then any non-docker/wireless address.
    def rank(iface: Interface) -> tuple[int, int]:
        if status.hotspot_interface and iface.name == status.hotspot_interface:
            return (0, 0)
        if iface.is_wireless:
            return (1, 0)
        return (2, 0 if not iface.name.startswith(("docker", "br-")) else 1)

    candidates = sorted(status.interfaces, key=rank)
    status.primary_ip = candidates[0].ip if candidates else None

    # Optional reachability probe. It does not affect functionality and can be
    # disabled (GURU_SERVER__ONLINE_PROBE=false) for strict offline verification.
    if probe_online:
        try:
            socket.create_connection(("1.1.1.1", 53), timeout=0.6).close()
            status.online = True
        except OSError:
            status.online = False
    else:
        status.online = False

    if not status.interfaces:
        status.warnings.append("No LAN address found. Students cannot connect until Wi-Fi is up.")
    elif not status.hotspot_active:
        status.warnings.append(
            "No hotspot detected — students on the same LAN can still join, but the "
            "offline-classroom hotspot is not active. See docs/NETWORKING.md."
        )
    if status.online:
        status.warnings.append(
            "Internet is reachable. The classroom runtime does not use it; this is informational only."
        )
    if shutil.which("nmcli") is None:
        status.warnings.append("nmcli not installed; hotspot setup must be done manually.")

    return status


def join_url(ip: str | None, port: int) -> str:
    return f"http://{ip or '127.0.0.1'}:{port}/"


def qr_svg(data: str) -> bytes:
    """Generate an offline QR code as SVG bytes (no external service)."""
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    import io

    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue()

