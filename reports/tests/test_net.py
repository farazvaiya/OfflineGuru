from __future__ import annotations

from guru.net import Interface, detect_hotspot, join_url, qr_svg


def test_detect_hotspot_from_nmcli_output():
    ifaces = [Interface("wlan0", "10.42.0.1", True), Interface("eth0", "192.168.1.5", False)]
    # nmcli -t -f NAME,TYPE,DEVICE connection show --active
    nmcli = (
        "lo:loopback\n"
        "Wired connection 1:802-3-ethernet:eth0\n"
        "offline-guru-hotspot:802-11-wireless:wlan0\n"
    )
    assert detect_hotspot(ifaces, nmcli) == "wlan0"


def test_detect_hotspot_absent():
    ifaces = [Interface("wlan0", "192.168.1.10", True)]
    nmcli = "Wired connection 1:802-3-ethernet:wlan0\n"
    assert detect_hotspot(ifaces, nmcli) is None  # no hotspot hint words


def test_join_url_falls_back_to_localhost():
    assert join_url(None, 8000) == "http://127.0.0.1:8000/"
    assert join_url("10.42.0.1", 8080) == "http://10.42.0.1:8080/"


def test_qr_svg_is_offline_svg():
    svg = qr_svg("http://10.42.0.1:8000/")
    assert svg.startswith(b"<?xml") or b"<svg" in svg[:200]
    assert b"http" in svg  # the URL is embedded, no external service
