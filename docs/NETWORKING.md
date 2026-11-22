# Local networking

## The three situations people call "offline"

| Mode | What it means | Internet? | Students reach the app via |
|---|---|---|---|
| **A. School Wi-Fi (client)** | Laptop joins an existing router/AP | usually yes | the router's LAN, e.g. `192.168.0.131:8000` |
| **B. Hotspot with sharing** | Laptop creates an AP **and** shares its uplink | yes (shared) | hotspot subnet, e.g. `10.42.0.1:8000` |
| **C. Hotspot, no uplink** ← target | Laptop creates an AP; no Internet at all | no | hotspot subnet, e.g. `10.42.0.1:8000` |

Offline Guru behaves identically in all three. It never needs the Internet and
never falls back to a cloud API. Mode C is the deployment target.

**Do not hardcode the IP.** The server prints the actual address from
`ip -brief -4 addr` at session creation, and `/api/health` reports it live. A
typical NetworkManager shared hotspot uses `10.42.0.1/24`, but this must be
verified on the machine in front of you.

## Create the hotspot (reversible)

Prerequisites:

* Ubuntu with NetworkManager (`nmcli` present).
* A Wi-Fi adapter that supports **AP mode** (`iw list | grep -A8 "Supported interface modes"`).
* One adapter cannot be both a hotspot and a client of another Wi-Fi network at
  the same time. If the laptop has a single Wi-Fi card, the hotspot replaces its
  normal Wi-Fi connection (Ethernet can still provide an uplink if present).

```bash
cd offline_guru
./scripts/hotspot.sh status                     # read-only inspection
sudo ./scripts/hotspot.sh start \
  --ssid "OfflineGuru-Class8" \
  --password "guru-class-2026"
```

The script runs these explicit, documented commands (nothing hidden):

```bash
nmcli connection add type wifi ifname wlp1s0 con-name offline-guru-hotspot \
      autoconnect no ssid "OfflineGuru-Class8"
nmcli connection modify offline-guru-hotspot \
      802-11-wireless.mode ap \
      802-11-wireless.band bg \
      802-11-wireless.channel 6 \
      ipv4.method shared \
      ipv6.method ignore \
      wifi-sec.key-mgmt wpa-psk \
      wifi-sec.psk "guru-class-2026"
nmcli connection up offline-guru-hotspot
```

Verify the address:

```bash
ip -brief -4 addr show wlp1s0      # expect something like 10.42.0.1/24
```

Undo everything:

```bash
sudo ./scripts/hotspot.sh stop
# fully remove the saved profile:
sudo nmcli connection delete offline-guru-hotspot
```

`ipv4.method shared` makes NetworkManager run DHCP + NAT for clients. If you want
a **pure local network with no Internet sharing**, use `ipv4.method manual` with
`ipv4.addresses 10.42.0.1/24` and run your own DHCP server (`dnsmasq`) — this is
more work and not required for the classroom, because the app needs no uplink.
Leaving `shared` on is harmless when there is no uplink: clients simply have no
Internet, which is exactly the offline classroom.

## Firewall

The project does **not** change firewall rules automatically. If `ufw` is active,
allow the app port explicitly:

```bash
sudo ufw status
sudo ufw allow 8000/tcp        # only if ufw is enabled
```

## Start the classroom server

```bash
./scripts/serve.sh             # binds 0.0.0.0:8000
```

Then open `http://127.0.0.1:8000/teacher` on the laptop. Creating a session shows:

* the 6-character class code,
* the join URL for the current interface,
* a QR code generated locally (no QR web service).

Students connect to the hotspot SSID, open the join URL, enter the code and their
name. No app install, no Internet, no account.

## Student devices: the "no Internet" warning

Android and iOS detect that a Wi-Fi network has no Internet and may:

* show "This network has no Internet access — stay connected?" → choose **stay connected**;
* open a captive-portal browser page that will fail to load → close it;
* prefer mobile data over Wi-Fi → turn mobile data off during class, or use
  "Keep Wi-Fi on when mobile data is unavailable".

These are OS behaviours, not application bugs. Document them on the board for
students on day one.

## Capacity expectations (measure, don't assume)

The app is designed for 30 students, and `scripts/loadtest.py` measures the
*application* side (WebSocket fan-out, REST, DB). The Wi-Fi radio is a separate
limit:

* 2.4 GHz, 20 MHz channel, one laptop AP: typically fine for 20–40 mostly-idle
  clients; heavy simultaneous audio downloads are the real risk.
* Keep audio opt-in (students who need it), and keep students near the laptop.
* Use channel 1/6/11 and avoid congested channels in the school.
* Captions are tiny JSON messages; audio is fetched on demand and cached once per
  segment, so 30 students replaying the same caption cost one synthesis.

Run the load test from a second device over the hotspot to include the radio:

```bash
.venv/bin/python scripts/loadtest.py --base http://10.42.0.1:8000 --clients 30
```

## Reconnection behaviour

* WebSocket keepalive every 20 s (`ping`/`pong` at the application layer, plus
  uvicorn's protocol ping).
* On close, the browser retries with exponential backoff (0.8 s → 15 s).
* After reconnect the student client re-fetches `/api/student/state` and merges
  segments by id, so captions missed during the outage are recovered from the
  server rather than lost.

## Diagnostics

```bash
ip -brief -4 addr                     # addresses
nmcli device status                   # adapter states
nmcli -t -f NAME,DEVICE connection show --active
sudo journalctl -u NetworkManager -n 50
curl -s http://127.0.0.1:8000/api/health | python3 -m json.tool
```
