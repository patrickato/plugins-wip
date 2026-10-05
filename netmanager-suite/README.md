# netmanager-suite (`netmanager_ng.py`)

A phone-friendly **Network Manager** page for a pwnagotchi **you own** — the
"command center" for the networks/targets you work with. One page (same pattern
as the BadHID control page: token-gated, `bind_scope`, on-screen status),
backing a **searchable list that scales to 50+** with add / edit / delete, a
**current / selected** marker, and a per-row **Fire Test** button.

A *network* is any target you track, of three **kinds** — the backbone stores
all three the same way; each kind's Fire action wires on top:

| Kind | what it is | "switch to" | "fire a test" |
|---|---|---|---|
| **wifi_join** | a WiFi network the pi can connect to | connect the pi to it | confirm it connects |
| **fleet** | an agent you enrolled (url + token) | make it the active target | BadHID-fire / run a task on it |
| **wifi_target** | a WiFi SSID/BSSID you're *authorized* to test | select it as the aim | a wireless test (deauth/capture) |

> **Fire status — all three wired (software complete).**
> - **fleet** → a safe reachability+auth probe (read-only task on the agent's
>   remoteexec API with the stored token). No side effects.
> - **wifi_join** → a safe read-only check: *are you associated with this SSID
>   right now?* It does **not** switch the radio — the built-in adapter is busy
>   with pwnagotchi, so actually connecting needs a second USB WiFi adapter (a
>   documented hardware step).
> - **wifi_target** → the **GATE**. A wireless test is **REFUSED** unless the
>   target's BSSID/SSID is on your explicit `authorized_targets` allowlist
>   (**empty by default**). netmanager *enforces* authorization; it does **not**
>   send deauth/attack frames itself — the authorized path is an integration
>   point for a backend you run on your own authorized lab hardware.

---

## Safety

- **Ships `enabled = false`.** Nothing serves until you turn it on.
- **No working default token.** Blank/placeholder/<12-char `auth_token` → the
  server refuses to start. Every request is token-authed (Bearer / `X-Auth-Token`
  / `?token=`).
- **`bind_scope`** never binds the open LAN by default (`auto` → Tailscale if
  present, else localhost). The exact bound URL is logged.
- **The store is `0600`** (it may hold fleet tokens), written atomically.
- **`authorized_targets` is the non-negotiable gate** (empty by default): a
  `wifi_target` fire is refused unless the target is explicitly listed. Only add
  networks you own or are authorized to test. netmanager enforces the gate and
  never sends attack frames itself.

---

## Install

From a `plugins-wip` clone on the pi:
```bash
cd ~/plugins-wip/netmanager-suite
sudo ./netmanager_install.sh      # backs up, installs, generates a token, enabled=false, gate empty
```
Then turn it on + make it phone-reachable (the installer prints these), restart,
and get the QR:
```bash
sudo sed -i '/^\[main\.plugins\.netmanager_ng\]/,/^\[/ s/^enabled = false/enabled = true/' /etc/pwnagotchi/config.toml
sudo sed -i '/^\[main\.plugins\.netmanager_ng\]/,/^\[/ s/^bind_scope = "auto"/bind_scope = "lan"/' /etc/pwnagotchi/config.toml
sudo systemctl restart pwnagotchi && sleep 12
sudo /etc/pwnagotchi/netmanager_ng/netmanager_phone.sh    # scannable QR, token baked in
```
Scan it, then hit **Bulk import** to pull your 50+ in one tap. Full undo:
`sudo cp -a <the printed .bak> /etc/pwnagotchi/config.toml && sudo systemctl restart pwnagotchi`.

### Bulk import (never hand-type 50)

Two buttons on the page pull your networks in, **additive and deduped** (safe to
re-run):
- **Import wifi targets from handshakes** — scans your handshakes dir (default
  `/home/pi/handshakes`, configurable) and turns each capture's filename into a
  `wifi_target` row (SSID + BSSID, deduped by BSSID).
- **Import agents from fleet.json** — reads your fleetctl store (default
  `/home/pi/.config/fleetctl/fleet.json`) and adds each agent as a `fleet` row
  (url + token, plus its BadHID endpoint if set), deduped by URL.

Each import reports `+N added, M already there`.

---

## The page

- A sticky **search box** + **kind filter** at the top — built for 50+ entries.
- An **Add** form whose fields adapt to the kind (SSID/BSSID, or agent URL+token,
  etc.).
- Each row: name, kind badge, key fields, a **• here** marker when it matches the
  WiFi you're currently on, a **✓ selected** marker, and **Select / Fire Test /
  Delete** buttons.
- Dependency-free (vanilla JS) so it works on an offline pi; the token comes
  from the page's own `?token=` link.

---

## Status

**Software complete + sandbox-tested.** Backbone (store/CRUD/search/validation),
bulk import (handshakes + fleet.json, deduped), and all three fires (fleet
probe; wifi_join association check; wifi_target allowlist gate) are done, with a
live HTTP pass and an end-to-end integration firing through netmanager at a real
remoteexec agent. Installer + QR helper included.

**Remaining = hardware/lab, not software:** a real on-device pass before it
graduates to `complete-plugins`; the wifi_join *connect* action needs a second
USB WiFi adapter; and the wifi_target *execution* backend is wired on your own
authorized lab hardware (netmanager only enforces the gate). See **NOTES.md**.
