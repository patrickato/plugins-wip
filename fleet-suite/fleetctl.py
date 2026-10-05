#!/usr/bin/env python3
"""
fleetctl.py - the B2 fleet controller (runs anywhere: a Pi, your laptop).

Coordinates a fleet of devices YOU OWN by driving their remoteexec_ng (B1)
agents: enroll agents explicitly, then fan a named task out across them and
collect the results. This is the B2 slice of the remote-exec track.

THE SAFETY LINE (infrastructure for a fleet you own, not a botnet):

  * Agents are enrolled EXPLICITLY, by you, with their address + token. There is
    NO discovery, NO auto-join, NO open registration. The controller only ever
    talks to agents in its own enrolled list (an allowlist by construction).
  * Each agent independently enforces its own gates (tasks-mode by default,
    token auth) - the controller can only ask an agent to do what that agent
    already allows. The controller holds each agent's token to authenticate to
    it; it cannot bypass an agent's rules.
  * Tokens live in a local config file written 0600. Nothing is sent anywhere
    except to the agents you enrolled.

No third-party dependencies (stdlib urllib) so it runs on any Python 3.7+ box.

Usage:
  fleetctl.py enroll <label> <url> <token>   # add an agent you own (explicit)
  fleetctl.py remove <label>                 # drop an agent
  fleetctl.py list [--ping]                  # show enrolled agents (--ping tests them)
  fleetctl.py tasks <label>                  # list one agent's named tasks
  fleetctl.py run <task> [--all | --agents a,b,c] [--timeout N]
  fleetctl.py run --command "<cmd>" [--all|--agents ...]   # free-mode agents only

Config: ~/.config/fleetctl/fleet.json  (override with FLEETCTL_CONFIG=/path)
"""

import argparse
import concurrent.futures
import json
import os
import sys
import time
import urllib.request
import urllib.error


DEFAULT_CONFIG = os.environ.get(
    "FLEETCTL_CONFIG",
    os.path.join(os.path.expanduser("~"), ".config", "fleetctl", "fleet.json"))


# ===========================================================================
# Config (pure-ish: file I/O isolated, logic testable)
# ===========================================================================

def load_agents(path):
    """Return {label: {url, token}} from the config file, or {} if none."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        agents = data.get("agents", {})
        return agents if isinstance(agents, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        raise SystemExit(f"error: could not read config {path}: {exc}")


def save_agents(path, agents):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"agents": agents}, fh, indent=2)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)  # tokens live here
    except Exception:
        pass


def normalize_url(url):
    """Ensure a scheme and no trailing slash. Returns the base URL."""
    u = url.strip()
    if not (u.startswith("http://") or u.startswith("https://")):
        u = "http://" + u
    return u.rstrip("/")


def select_agents(agents, all_flag, agents_csv):
    """Resolve which enrolled agents a command targets. Pure + testable.
    Returns (selected_dict, error_or_None)."""
    if all_flag:
        return dict(agents), None
    if not agents_csv:
        return {}, "specify --all or --agents <comma,separated,labels>"
    wanted = [a.strip() for a in agents_csv.split(",") if a.strip()]
    missing = [a for a in wanted if a not in agents]
    if missing:
        return {}, f"not enrolled: {', '.join(missing)} (see `fleetctl list`)"
    return {a: agents[a] for a in wanted}, None


# ===========================================================================
# HTTP to an agent (injectable for tests)
# ===========================================================================

def _http_post(url, token, payload, timeout):  # pragma: no cover - real network
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode()


def _http_get(url, token, timeout):  # pragma: no cover - real network
    req = urllib.request.Request(
        url, method="GET", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode()


def run_on_agent(label, agent, payload, timeout, post_fn=None):
    """Call one agent's /run. Returns a result dict (never raises)."""
    post_fn = post_fn or _http_post
    url = normalize_url(agent["url"]) + "/run"
    start = time.time()
    try:
        status, text = post_fn(url, agent.get("token", ""), payload, timeout)
        try:
            data = json.loads(text)
        except Exception:
            data = {"ok": False, "error": f"non-JSON response: {text[:200]}"}
        data.setdefault("ok", status == 200)
        data["_status"] = status
        data["_agent"] = label
        return data
    except urllib.error.HTTPError as exc:
        return {"ok": False, "_agent": label, "_status": exc.code,
                "error": f"HTTP {exc.code}", "duration": round(time.time()-start, 3)}
    except Exception as exc:
        return {"ok": False, "_agent": label, "_status": None,
                "error": str(exc), "duration": round(time.time()-start, 3)}


def fan_out(selected, payload, timeout, post_fn=None, max_workers=8):
    """Run `payload` on each selected agent in parallel. Returns {label: result}.
    Pure orchestration - the network call is injected, so this is testable."""
    results = {}
    if not selected:
        return results
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(run_on_agent, label, agent, payload, timeout, post_fn): label
                for label, agent in selected.items()}
        for fut in concurrent.futures.as_completed(futs):
            label = futs[fut]
            try:
                results[label] = fut.result()
            except Exception as exc:
                results[label] = {"ok": False, "_agent": label, "error": str(exc)}
    return results


# ===========================================================================
# CLI
# ===========================================================================

def cmd_enroll(args):
    agents = load_agents(args.config)
    url = normalize_url(args.url)
    agents[args.label] = {"url": url, "token": args.token}
    save_agents(args.config, agents)
    print(f"enrolled '{args.label}' -> {url}  (token stored in {args.config}, 0600)")
    print("tip: `fleetctl list --ping` to confirm it answers.")


def cmd_remove(args):
    agents = load_agents(args.config)
    if args.label not in agents:
        raise SystemExit(f"not enrolled: {args.label}")
    del agents[args.label]
    save_agents(args.config, agents)
    print(f"removed '{args.label}'")


def cmd_list(args):
    agents = load_agents(args.config)
    if not agents:
        print("no agents enrolled. add one: fleetctl enroll <label> <url> <token>")
        return
    for label in sorted(agents):
        a = agents[label]
        line = f"  {label:16s} {normalize_url(a['url'])}"
        if args.ping:
            try:
                status, text = _http_get(normalize_url(a["url"]) + "/tasks",
                                         a.get("token", ""), args.timeout)
                data = json.loads(text)
                line += f"   [online, {len(data.get('tasks', []))} tasks, mode={data.get('mode')}]"
            except Exception as exc:
                line += f"   [unreachable: {exc}]"
        print(line)


def cmd_tasks(args):
    agents = load_agents(args.config)
    if args.label not in agents:
        raise SystemExit(f"not enrolled: {args.label}")
    a = agents[args.label]
    try:
        status, text = _http_get(normalize_url(a["url"]) + "/tasks",
                                 a.get("token", ""), args.timeout)
        print(text)
    except Exception as exc:
        raise SystemExit(f"error reaching {args.label}: {exc}")


def cmd_run(args):
    agents = load_agents(args.config)
    selected, err = select_agents(agents, args.all, args.agents)
    if err:
        raise SystemExit("error: " + err)
    if args.command:
        payload = {"command": args.command}
        what = f"command {args.command!r}"
    elif args.task:
        payload = {"task": args.task}
        what = f"task {args.task!r}"
    else:
        raise SystemExit("error: give a <task> or --command <cmd>")
    print(f"running {what} on {len(selected)} agent(s): {', '.join(sorted(selected))}")
    results = fan_out(selected, payload, args.timeout)
    ok = 0
    for label in sorted(results):
        r = results[label]
        tag = "OK " if r.get("ok") else "ERR"
        if r.get("ok"):
            ok += 1
        exit_code = r.get("exit_code", r.get("_status"))
        out = (r.get("stdout") or r.get("error") or "").strip()
        first = out.splitlines()[0] if out else ""
        print(f"  [{tag}] {label:16s} exit={exit_code}  {first[:100]}")
    print(f"done: {ok}/{len(results)} ok")


def build_parser():
    p = argparse.ArgumentParser(prog="fleetctl", description=__doc__.strip().splitlines()[0])
    p.add_argument("--config", default=DEFAULT_CONFIG, help="config file path")
    p.add_argument("--timeout", type=float, default=15, help="per-agent timeout (s)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("enroll", help="add an agent you own")
    pe.add_argument("label"); pe.add_argument("url"); pe.add_argument("token")
    pe.set_defaults(func=cmd_enroll)

    pr = sub.add_parser("remove", help="drop an agent")
    pr.add_argument("label"); pr.set_defaults(func=cmd_remove)

    pl = sub.add_parser("list", help="list enrolled agents")
    pl.add_argument("--ping", action="store_true", help="test each agent")
    pl.set_defaults(func=cmd_list)

    pt = sub.add_parser("tasks", help="list one agent's tasks")
    pt.add_argument("label"); pt.set_defaults(func=cmd_tasks)

    prun = sub.add_parser("run", help="run a task/command across agents")
    prun.add_argument("task", nargs="?", help="named task to run")
    prun.add_argument("--command", help="arbitrary command (free-mode agents only)")
    g = prun.add_mutually_exclusive_group()
    g.add_argument("--all", action="store_true", help="all enrolled agents")
    g.add_argument("--agents", help="comma-separated labels")
    prun.set_defaults(func=cmd_run)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
