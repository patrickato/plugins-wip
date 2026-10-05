# remoteexec-suite (`remoteexec_ng.py`)

A small **authenticated command API** for a pwnagotchi **you own**. POST it a
task, get back JSON (`stdout`, `stderr`, `exit_code`, `duration`). It's remote
admin of your own device — and the **agent** side of the fleet-coordination
layer (B2) that a controller will drive. This is the **B1** slice of the
remote-exec track.

## What it is (and isn't)

- **Is:** a token-gated HTTP endpoint on a device you control, that runs
  commands *you* authorize and returns their output. Functionally what SSH
  already gives you, as a clean API a script (or a controller) can call.
- **Isn't:** an exploit, a backdoor, or an implant. You install it on your own
  Pi; it doesn't self-propagate, persist beyond a normal plugin, or touch
  anything you don't point it at.

## The safety model (two gates + auth)

- **Default = "tasks" mode.** The API can **only** run the *named* commands you
  put in the `tasks` table. A caller asks for a task by name — it can't inject
  arbitrary shell. An empty table = it runs nothing.
- **"free" mode is double-gated.** Running arbitrary commands needs **both**
  `command_mode = "free"` **and** `allow_free_mode = true`. It can't turn on by
  accident.
- **Token auth** (≥12 chars, no placeholders) on every request.
- **`bind_scope`** never binds the open LAN by default (`auto` → Tailscale or
  localhost). The exact URL is logged.
- **Timeout + output cap** on every command; **every run is logged** at WARNING,
  with an optional JSON `audit_log`.

## Install — the one-command way (recommended)

From inside this folder on the pi (a `plugins-wip` clone):
```bash
sudo ./remoteexec_install.sh
```
It backs up `config.toml` first, copies the plugin, appends the config block
with a **freshly generated token**, `enabled=false`, **tasks mode**, and
validates the result (auto-rolling back if it wouldn't parse). It deliberately
does **not** enable it or open free mode. The installer prints the exact
section-scoped command to turn it on when you're ready, plus:
```bash
./remoteexecctl.sh tasks        # list the allowed named tasks
./remoteexecctl.sh run uptime   # run one, print its JSON
./remoteexecctl.sh status       # is the agent up?
```

## Install — by hand

1. Copy `remoteexec_ng.py` into your custom-plugins dir
   (`/etc/pwnagotchi/custom-plugins/`).
2. Add the `config.toml` block to `/etc/pwnagotchi/config.toml` and set a real
   `auth_token`:
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(24))"
   ```
3. Edit the `[main.plugins.remoteexec_ng.tasks]` table to the commands you want
   this device to expose.
4. Set `enabled = true`, `sudo systemctl restart pwnagotchi`. The log prints the
   bound URL, e.g. `command API up: auto -> tailscale - http://100.x.y.z:8084/`.

## Using it

List the tasks:
```bash
TOKEN=your-token ; BASE=http://127.0.0.1:8084
curl -s -H "Authorization: Bearer $TOKEN" $BASE/tasks
```

Run one:
```bash
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"task":"uptime"}' $BASE/run
# -> {"ok":true,"exit_code":0,"stdout":"...","stderr":"","duration":0.01,"label":"task:uptime"}
```

Free mode (only if you set `command_mode="free"` **and** `allow_free_mode=true`):
```bash
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"command":"ls /etc/pwnagotchi"}' $BASE/run
```

The browser page at `$BASE/?token=...` shows the mode, the task list, the run
count, and a copy-paste example. An on-screen `rexec` counter shows runs on the
TFT.

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/` | — | status page |
| GET | `/tasks` | — | `{mode, tasks:[...]}` |
| POST | `/run` | `{"task":"name"}` or (free) `{"command":"..."}` | result JSON |

All require the token (`Authorization: Bearer`, `X-Auth-Token`, or `?token=`).

## Configuration reference

- `auth_token` — **required**, ≥12 chars.
- `bind_scope` (`auto`/`tailscale`/`localhost`/`lan`), `port` (8084).
- `command_mode` (`tasks`/`free`), `allow_free_mode` (the free-mode second gate).
- `tasks` — the named command table (tasks-mode allowlist).
- `command_timeout_seconds` (30), `max_output_chars` (20000), `audit_log` ("").
- `ui_*` — on-screen run counter.

## Limitations / honesty

- Commands run as the pwnagotchi process user (root on the stock image) — same
  as anything else on the device. Keep it to gear you own and `bind_scope` tight.
- In `tasks` mode the commands are exactly what you configured — no argument
  injection surface. In `free` mode you own the risk (it's arbitrary shell).
- This is the B1 agent. The **B2** controller (fan tasks across a fleet) builds
  on this endpoint.

## Tests

```bash
python3 tests/test_remoteexec_ng.py
```
Covers the mode gates (`resolve_request` — the security choke point), command
execution + timeout + truncation, token validation, and bind-scope resolution.
On-device validation (live server, real tasks) still needs a hardware pass.
