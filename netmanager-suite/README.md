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

> **This is the backbone.** The store, CRUD, search, select, the page, and a
> **FIRE endpoint that returns a clear per-kind stub** ("not wired yet") are
> done and tested. The three real fire actions land on top **without changing
> the store or the page** — the `wifi_target` one behind the project's
> non-negotiable **authorized-target allowlist (empty by default)**.

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

Backbone: **done + sandbox-tested** (pure store/CRUD/search + a live HTTP pass:
auth, page, add/state/select/fire-stub/delete, 0600 store). Needs a real-hardware
pass before it graduates. Next: wire the three Fire actions (fleet first — the
chain's already proven), then the rest. See **NOTES.md**.
