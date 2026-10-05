# remoteexec-suite — design & safety notes

Status: **new build**, B1 slice of `BADUSB_AND_REMOTE_EXEC_IDEAS`
(in `patrickato/test-plugins`). Sandbox-green; not hardware-tested yet, so it
does not graduate to `complete-plugins` until an on-device pass.

## What this is, and the line it holds

B1 is "remote admin of your own pwns": a hardened, authenticated command API.
It is deliberately **infrastructure for devices you own**, not an implant:

- You install it yourself, on your own Pi. It does not exploit anything,
  self-propagate, or persist beyond a normal plugin.
- It does nothing you can't already do over SSH — it just exposes it as a clean,
  token-gated API that a script or the B2 controller can call.

The repo already has `web2ssh_ng` (an interactive web shell) and `terminal` as
"seeds"; this is the **programmatic** sibling, designed as the **agent** the
fleet controller (B2) will drive. It's intentionally *more* restrictive than a
shell by default (named tasks only).

## The gates (why this stays safe)

1. **tasks mode by default.** `resolve_request()` (the single security choke
   point, unit-tested directly) only returns a command for a *named task* from
   the configured `tasks` table. An arbitrary `command` is refused in tasks
   mode — even if `allow_free_mode` is true. Empty table = nothing runs.
2. **free mode is double-gated.** Arbitrary commands require BOTH
   `command_mode="free"` AND `allow_free_mode=true`. One switch alone does
   nothing. This makes "oops, left it wide open" structurally hard.
3. **token auth** (≥12 chars, no placeholders), same gate as badhid/web2ssh.
4. **bind_scope** never `lan` by default; `auto` → tailscale/localhost, URL
   logged.
5. **timeout + output cap** per command; **loud WARNING log** on every
   run/refusal; optional JSON `audit_log`.

`resolve_request` is pure and isolated precisely so the gate logic can be
proven in tests without spawning anything — see `test_remoteexec_ng.py`
(`test_tasks_mode_gate`, `test_free_mode_double_gate`).

## Fork conventions followed

- Section `[main.plugins.remoteexec_ng]` = file basename; `DEFAULTS` + `_opt*`
  readers (no `__defaults__` merge); real hooks only (`on_loaded`, `on_unload`,
  `on_webhook`, `on_ui_setup/update`); server via `make_server` in a daemon
  thread so `on_loaded` never blocks; `bind_scope` with the URL logged — same as
  web2ssh_ng/handshaker/badhid_ng.
- `>>> USER INPUT REQUIRED <<<` on `auth_token`, which refuses placeholders.
- TOML gotcha documented: all scalar keys before the `[.tasks]` sub-table (can't
  re-open the parent table after a sub-table).

## Real-hardware pass checklist (before graduating)

1. Server binds at the expected `bind_scope` URL; token auth rejects a
   wrong/absent token.
2. `/tasks` lists the configured tasks; `/run` with `{"task":"uptime"}` returns
   real output + exit 0.
3. tasks mode refuses an arbitrary `{"command": ...}` on the real endpoint.
4. free mode runs an arbitrary command ONLY when both switches are set.
5. a command that exceeds `command_timeout_seconds` is killed and reported.

## Out of scope here (later slices)

- **B2** — the controller: enrollment (explicit shared key, no open
  registration), mutual auth, signed task messages, an allowlist of agents it
  will talk to, and task fan-out. Builds on this agent endpoint.
- **B3** — staging a BadHID payload to an agent to fire on plug-in.
