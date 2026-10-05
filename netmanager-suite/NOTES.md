# netmanager-suite — design & safety notes

Status: **software complete + sandbox-verified**; on-hardware pass in progress
before graduation. Version `0.3.1`.

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

## Wiring a real wireless-test backend (the authorized path)

`fire_wifi_target(entry, allowlist)` is deliberately split: it decides
authorization, and nothing else. The REFUSED branch is the safety gate; the
authorized branch is where a real deauth/capture backend bolts on — **after**
the gate has already said yes. To wire one:

- Keep the gate check exactly as-is. Only the authorized branch changes.
- In that branch you have a vetted `bssid`/`ssid` (already on the allowlist).
  Hand them to *your own* tool on *your own* lab hardware — e.g. shell out to a
  capture/deauth script, or POST to a small local service you run. Make the
  backend path a config option (default empty → stays gate-only), so the plugin
  ships inert and a legit user opts in explicitly.
- Fail safe: if the backend option is unset or the call errors, return the
  current "authorized ✓ (gate only)" result — never fall through to attacking.
- This mirrors the project rule (`CLAUDE.md`): keep the allowlist gate strict;
  make legitimate use low-friction. netmanager owns the *gate*; the *frames* are
  the user's own authorized hardware, on their own lab, against networks they own
  or are allowed to test.

Same shape as wifi_join's "needs a 2nd USB adapter": the remaining step is
hardware/lab, not a software hole.

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
  on-device): the authorize → green-`✓` round trip, fleet fire against a real
  agent, and the search/select/edit/delete feel on the real 50+ list.
- The wifi_target **execution backend** (real deauth/capture) — wired on your own
  authorized lab hardware; see "Wiring a real wireless-test backend" above.
- The wifi_join **connect** action — needs a 2nd USB WiFi adapter.

**Future / nice-to-have:** more import sources (wigle CSV, the data-bus
potfile / bluetooth json) slot in the same way; bulk select / fire-across-many;
tags; last-seen/geo for wardriven targets.
