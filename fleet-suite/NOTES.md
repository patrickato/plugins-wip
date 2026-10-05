# fleet-suite — design & safety notes

Status: **new build**, B2 slice of `BADUSB_AND_REMOTE_EXEC_IDEAS`. Sandbox-green,
and the live B1↔B2 loop was exercised during development (real remoteexec_ng
agent + fleetctl over localhost HTTP). Not yet run across the real fleet, so it
doesn't graduate to `complete-plugins` until an on-fleet pass.

## What it is

B2 is the coordination layer: a controller (`fleetctl`) that drives the B1
agents (`remoteexec_ng`) on devices you own. Enroll agents explicitly, fan a
named task out, collect results. It's a standalone stdlib Python CLI (no deps)
so it runs on any controller box — a Pi, the Pi5, your laptop.

It is deliberately NOT a pwnagotchi plugin: a controller naturally runs from a
workstation or a designated master, not from inside the pwnagotchi engine. The
agents are the plugins; the controller is the conductor.

## The safety argument

The thing that separates "fleet management for your own kit" from "a botnet" is
**how agents join** and **who can command them**. This build draws the line hard:

1. **Explicit enrollment only.** `fleetctl enroll` is the only way an agent
   enters the controller's list. There is no discovery, no broadcast, no
   auto-registration, no "agent calls home and gets added." `select_agents()`
   (pure, tested) refuses any label that isn't already enrolled — so a `run`
   can only ever target agents you added by hand.
2. **The agent is the real authority.** The controller holds each agent's token
   and authenticates to it, but every request is still subject to the agent's
   own gates (B1: tasks-mode by default, token auth, timeout). The controller
   cannot make an agent exceed what the agent allows — proven in the integration
   check: the controller sends an arbitrary command and the agent refuses it
   (400), and a wrong token is 401.
3. **Secrets stay local.** Tokens live in a `0600` JSON config; traffic goes only
   to enrolled agents.

`select_agents()` and `fan_out()` are pure/orchestration with the network call
injected, so the allowlist property and the fan-out are unit-tested without a
network (`test_fleetctl.py`).

## Design points

- stdlib only (`urllib`, `concurrent.futures`) — runs anywhere, nothing to pip.
- parallel fan-out with a per-agent timeout; one agent being down never blocks
  or crashes the others (`test_fan_out_handles_agent_error`).
- non-JSON / error responses from an agent degrade to `ok:false`, never a crash.

## Roadmap (deliberately out of scope for v1)

- **Mutual auth / signed tasks.** Right now the controller authenticates to the
  agent (holds its token); the agent does not cryptographically verify the
  controller beyond that. A later slice: signed task messages + an enrollment
  shared-key handshake so an agent only accepts tasks from a controller it was
  enrolled with. The architecture (explicit enrollment both ways) is ready for
  it.
- Task templates with arguments; structured/full-JSON output; save-to-file.
- **B3:** stage a BadHID payload to an agent, fire on plug-in (where A and B
  meet) — the most powerful combination and the one to treat with the most care.
