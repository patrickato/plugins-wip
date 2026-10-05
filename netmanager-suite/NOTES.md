# netmanager-suite — design & safety notes

Status: **software complete + sandbox-verified**; on-hardware pass in progress
before graduation. Version `0.4.0`.

Since the backbone build this suite gained: all three fires wired, bulk import
(handshakes + fleet.json, fleetctl `{"agents":…}` unwrapped), a floating toast +
first-run onboarding, per-row **Edit**, and `netmanagerctl.sh` (one-command
allowlist management). The notes below reflect the current state.

## Why one page, three kinds

The user has 50+ "networks" and wants one place to see what they're on, switch,
add/delete, and fire a test. "Network" turned out to mean three things at once
(wifi they join, fleet agents, wifi attack targets), so the design is **one
store + one page** with a `kind` discriminator rather than three plugins. The
four verbs (see / switch / add-delete / fire) are identical across kinds; only
the per-kind *fire* and *switch* actions differ, and those bolt on without
touching the backbone.

## Layering (what's testable without hardware)

- **Pure layer** (module-level, no flask/pwnagotchi): `validate_token`,
  `resolve_bind_plan`, `validate_entry`, `empty_store`/`normalize_store`, the
  CRUD ops (`add/update/delete/select_network`), `search_networks`, `fire_stub`.
  All unit-tested.
- **Plugin layer**: `_opt*` readers, the file-backed store (atomic write, 0600,
  lock), token auth, the Flask server, the endpoints, the on-screen count, and
  the best-effort `current_ssid()` (read-only; `iwgetid`). Exercised by a live
  HTTP test when flask is importable.

Store shape: `{"version":1, "selected":<id|null>, "networks":{<id>:{name,kind,
notes,fields{...},added_at}}}`. `normalize_store` coerces anything on load so a
hand-edited or partial file never crashes the server (bad entries dropped, a
dangling `selected` cleared).

## What each Fire does today (all three wired; none transmit attack frames)

`/api/fire` dispatches on `kind`. Every kind is a **safe check** — the point of
this plugin is to enforce authorization and report state, not to attack:

1. **fleet** — ✅ a reachability+auth probe: POST a read-only task (`uptime`) to
   the agent's remoteexec `/run` with the stored token, report
   reachable/authed/output. No side effects. Injectable `post_fn` for tests;
   verified end-to-end against a real agent. (Remote BadHID *payload* firing is a
   separate, heavier action, deliberately not this button.)
2. **wifi_join** — ✅ a read-only association check: "is the pi on this SSID right
   now?" (`iwgetid`). It does **not** switch the radio — the built-in adapter is
   busy with pwnagotchi, so actually connecting needs a second USB WiFi adapter
   (a documented hardware step). Switching would also risk dropping the link
   you're managing it over.
3. **wifi_target** — ✅ **the gate, and only the gate.** A wireless test is
   REFUSED unless the target's BSSID/SSID is on the explicit `authorized_targets`
   allowlist (**empty by default** — the project's non-negotiable rule). When
   authorized, `fire_wifi_target` returns `ok/authorized: true` **without sending
   any frames** — that success means "the gate passed," and is the integration
   point for a backend you run on your own authorized lab hardware (below).

## The capture backend (built; authorized-path only)

`fire_wifi_target(entry, allowlist)` stays a pure gate: it decides authorization
and nothing else. `fire_dispatch` runs the capture backend **only after** the
gate says yes, and only when it's enabled + configured. The backend is split for
testability the same way `fire_fleet` is:

- **`plan_capture(entry, cfg)`** — pure. Builds the exact commands or refuses,
  and a refusal (`plan is None`) sends nothing. Enforced, unit-tested properties:
  - two independent switches on top of the allowlist: `capture_backend_enabled`
    **and** a set `capture_iface`;
  - `capture_iface` must not be a `builtin_ifaces` entry (the pwnagotchi radio) —
    capture runs on a **second** adapter, never the engine's;
  - a **BSSID is required**, and `airodump-ng` is **locked to that one BSSID**
    (`--bssid`) — no broad sweep;
  - deauth is **off unless `deauth_count > 0`**, is **targeted at that BSSID**
    (`aireplay-ng --deauth N -a <bssid>`, never a broadcast `ff:…`), and is
    **hard-clamped to `MAX_DEAUTH` (64)** — a handshake nudge, not a jam.
- **`run_capture_backend(entry, cfg, execute=…)`** — `execute(plan)` is the real
  hardware step (injectable; tests pass a fake). It fails safe: backend off or a
  plan refusal returns the gate-only "authorized ✓" result; an executor
  exception is contained, never crashing the fire handler.
- **`_execute_capture(plan)`** — the real tier-3 step (`# pragma: no cover`):
  airodump locked to the BSSID for `capture_seconds`, optional bounded deauth,
  then a best-effort handshake check (`hcxpcapngtool`/`aircrack-ng`), landing the
  `.pcapng` in the handshakes dir so the rest of the bus (import, crack-house)
  picks it up.

This mirrors the project rule (`CLAUDE.md`): keep the allowlist gate strict; make
legitimate use low-friction. netmanager owns the *gate* and the *orchestration*;
the frames go out via standard tools on the user's own authorized hardware,
against networks they own or are allowed to test.

**Needs a real-hardware pass** (tier 3): the pure plan/gating/parse layer is
sandbox-verified, but `_execute_capture` needs a 2nd monitor-mode USB adapter +
aircrack-ng on the Pi — `netmanager_wifi_probe.sh` finds the adapter. Same
category as wifi_join's "needs a 2nd USB adapter": a hardware step, not a hole.

## Safety posture

Same as the other servers in this project: disabled by default, no default
token (refuses to start), `bind_scope` defaults away from the open LAN, store is
0600 (fleet tokens live there), every fire logged. The page is dependency-free
so it works on an offline pi and nothing phones home.

## Done vs. remaining

**Done (software, sandbox-verified):**
- All three fires (fleet probe, wifi_join association check, wifi_target gate).
- Import from existing stores: `/api/import` pulls `wifi_target` rows from the
  handshakes dir (filename → SSID/BSSID) and `fleet` rows from fleetctl's
  `fleet.json` (unwraps `{"agents":…}`), additive + deduped. Pure parsers
  unit-tested; endpoint has a live pass.
- Phone UX: floating toast, first-run welcome/empty-state, plain-language help,
  per-row **Edit** (edit-in-place via `/api/update`), and a refused-target toast
  that hands you the exact authorize command.
- `netmanagerctl.sh` — one-command `authorize`/`deauthorize`/`list` for the
  allowlist (section-scoped, deduped, backed-up + tomllib-validated with
  rollback, restarts pwnagotchi). Installer deploys it + the QR helper.

**Remaining (hardware/lab, not software):**
- The on-hardware pass that gates graduation (install per README, verify
  on-device): the authorize → green-`✓` round trip (✅ done), fleet fire against a
  real agent, and the search/select/edit/delete feel on the real 50+ list.
- The capture backend's **tier-3 pass**: `_execute_capture` on a real 2nd
  monitor-mode adapter + aircrack-ng (passive capture first, then a small
  `deauth_count`). Gating/commands are sandbox-verified; the radio step isn't.
- The wifi_join **connect** action — needs a 2nd USB WiFi adapter.

**Future / nice-to-have:** more import sources (wigle CSV, the data-bus
potfile / bluetooth json) slot in the same way; bulk select / fire-across-many;
tags; last-seen/geo for wardriven targets.
