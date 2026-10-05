# netmanager-suite — design & safety notes

Status: **backbone build**, sandbox-verified; needs a real-hardware pass before
graduation. Version `0.1.0`.

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

## Fire = stub, on purpose

The backbone's `/api/fire` dispatches on `kind` and returns a clear "not wired
yet" message per kind. This keeps the UI and the contract complete while the
real actions are built and hardware-tested one at a time:

1. **fleet** — ✅ **wired.** A safe reachability+auth probe: POST a read-only task
   (`uptime`) to the agent's remoteexec `/run` with the stored token, report
   reachable/authed/output. No side effects. Injectable `post_fn` for tests;
   verified end-to-end against a real agent. (Remote BadHID *payload* firing is
   a separate, heavier action, deliberately not this button.)
2. **wifi_join** — connect/switch the pi's WiFi (wpa_cli / nmcli), then confirm.
   Needs care: don't drop the link you're managing it over.
3. **wifi_target** — the offensive one (deauth / handshake capture). **Gated
   behind an explicit authorized-target allowlist (BSSID/SSID), empty by
   default** — the project's non-negotiable rule for anything firing at a
   network. Built last, with its own hardware pass.

## Safety posture

Same as the other servers in this project: disabled by default, no default
token (refuses to start), `bind_scope` defaults away from the open LAN, store is
0600 (fleet tokens live there), every fire logged. The page is dependency-free
so it works on an offline pi and nothing phones home.

## Out of scope (for the backbone)

- The three real fire actions (next).
- Import from existing stores (pull wifi_target rows from the handshake/recon
  data, fleet rows from fleet.json) — a nice "don't hand-type 50" follow-on.
- One-command installer + QR helper (added at graduation, like BadHID).
- Bulk select / fire-across-many, tags, last-seen/geo for wardriven targets.
