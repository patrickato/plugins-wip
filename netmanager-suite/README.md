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

> **Fire status.** The **fleet** fire is **wired**: it runs a safe
> reachability+auth probe (a read-only task on the agent's remoteexec API with
> the stored token) and reports back — no side effects. The other two are still
> clear per-kind stubs ("not wired yet") and land next **without changing the
> store or the page** — the `wifi_target` one behind the project's non-negotiable
> **authorized-target allowlist (empty by default)**. (Remote BadHID *payload*
> firing is a separate, heavier action, not this button.)

---

## Safety

- **Ships `enabled = false`.** Nothing serves until you turn it on.
- **No working default token.** Blank/placeholder/<12-char `auth_token` → the
  server refuses to start. Every request is token-authed (Bearer / `X-Auth-Token`
  / `?token=`).
- **`bind_scope`** never binds the open LAN by default (`auto` → Tailscale if
  present, else localhost). The exact bound URL is logged.
- **The store is `0600`** (it may hold fleet tokens), written atomically.
- Firing is deliberately a no-op stub in the backbone; the offensive
  `wifi_target` fire is gated behind an explicit allowlist when it's wired.

---

## Install

```bash
# from a plugins-wip clone, on the pi:
sudo cp netmanager-suite/netmanager_ng.py /etc/pwnagotchi/custom-plugins/
# add the config block (generate a token first):
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
sudo nano /etc/pwnagotchi/config.toml     # paste the block from config.toml, set auth_token, enabled=true
sudo systemctl restart pwnagotchi
```
Then open the page on your phone (same network, or via Tailscale):
`http://<pi>:8085/?token=<your-token>` — add a few networks, search, select,
and try **Fire Test** (it'll tell you that kind isn't wired yet — that's the
backbone talking).

A one-command installer + QR helper (like BadHID's) comes with graduation.

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

Backbone + **fleet Fire: done + sandbox-tested.** Covered: pure store / CRUD /
search / validation; the fleet probe (success, 401 auth-fail, unreachable,
unknown-task); a live HTTP pass (auth, page, add/state/select/fire/delete, 0600
store); and an **end-to-end integration** firing through netmanager at a real
remoteexec agent (`reachable & authed - uptime: …`, and a wrong token →
`auth failed (401)`). Needs a real-hardware pass before it graduates.
Next: the **connectivity** (wifi_join) fire, then the gated **wifi_target**
fire, then the rest. See **NOTES.md**.
