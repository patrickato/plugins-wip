# fleet-suite (`fleetctl.py`)

The **B2 fleet controller**: coordinate a set of devices **you own** by driving
their `remoteexec_ng` (B1) agents. Enroll agents explicitly, then fan a named
task out across them and collect the results. Runs anywhere Python 3.7+ does (a
Pi, your laptop) — **no third-party dependencies**.

```
your laptop / a pi                 devices you own
┌─────────────┐   enroll + run    ┌──────────────┐
│  fleetctl   │ ────────────────▶ │ pi4  (B1 agent)│
│ (controller)│ ────────────────▶ │ pi5  (B1 agent)│
└─────────────┘   collect results └──────────────┘
```

## The safety line (a fleet you own, not a botnet)

- **Explicit enrollment only.** You add each agent with its address + token.
  There is **no discovery, no auto-join, no open registration** — the controller
  only ever talks to agents in its own enrolled list (an allowlist by
  construction). The tests prove an unenrolled label is refused.
- **The agent still rules.** Each B1 agent enforces its own gates (tasks-mode by
  default, token auth). The controller holds each agent's token to authenticate;
  it can only ask an agent to do what that agent already allows. It cannot
  bypass an agent's rules — proven in the integration test (the agent refuses an
  arbitrary command the controller sends).
- **Tokens stay local**, in a `0600` config file; nothing is sent anywhere
  except to the agents you enrolled.

## Setup

1. On each device you own, install and enable **remoteexec-suite** (B1) and note
   its URL + `auth_token`.
2. Put `fleetctl.py` anywhere on your controller box (laptop/Pi). Optionally
   `chmod +x` it.
3. Enroll your agents:
   ```bash
   ./fleetctl.py enroll pi4-pwn  http://pwnagotchi.local:8084  <that-pi's-token>
   ./fleetctl.py enroll pi5-field http://100.64.0.12:8084      <that-pi's-token>
   ```
   (Config lands in `~/.config/fleetctl/fleet.json`, `0600`. Override with
   `FLEETCTL_CONFIG=/path`.)

## Use

```bash
./fleetctl.py list --ping                 # show agents + test each one
./fleetctl.py tasks pi4-pwn               # what tasks that agent exposes
./fleetctl.py run uptime --all            # run a named task on every agent
./fleetctl.py run disk --agents pi4-pwn,pi5-field
./fleetctl.py run --command "ls /tmp" --all   # free-mode agents only (double-gated there)
./fleetctl.py remove pi5-field
```

`run` fans out in parallel (per-agent `--timeout`, default 15s) and prints a
one-line result per agent (ok/err, exit code, first line of output):

```
running task 'uptime' on 2 agent(s): pi4-pwn, pi5-field
  [OK ] pi4-pwn          exit=0   14:03:11 up 2 days,  3:22,  load average: ...
  [OK ] pi5-field        exit=0   14:03:11 up 1 day,  live: ...
done: 2/2 ok
```

## Commands

| Command | What it does |
|---|---|
| `enroll <label> <url> <token>` | add an agent you own (explicit) |
| `remove <label>` | drop an agent |
| `list [--ping]` | list enrolled agents (`--ping` tests each) |
| `tasks <label>` | show one agent's named tasks |
| `run <task> [--all\|--agents a,b]` | fan a named task out |
| `run --command "<cmd>" ...` | arbitrary command (only runs on free-mode agents) |

## What this is / isn't

- **Is:** a controller for a fleet you own — "a tiny, honest C2 for your own
  kit." Think Ansible/Salt-lite over the B1 agents.
- **Isn't:** a worm or a tool to compromise machines you don't control. Agents
  are software *you* install on *your* devices and *you* enroll. Nothing here
  discovers, exploits, or self-propagates.

## Roadmap (later hardening)

- Signed task messages + the agent verifying the controller (mutual auth beyond
  "the controller holds the token").
- `run` output modes (full JSON, save-to-file), task templates with arguments.
- **B3:** stage a BadHID payload to an agent and fire on plug-in.

## Tests

```bash
python3 tests/test_fleetctl.py
```
Covers config roundtrip (incl. `0600`), URL normalization, the **allowlist**
(only enrolled agents are targetable), and parallel fan-out with an injected
HTTP function. The live B1↔B2 loop (real agent + real controller over HTTP) is
exercised as an integration check during development; an on-fleet pass across
your real devices is the remaining hardware step.
