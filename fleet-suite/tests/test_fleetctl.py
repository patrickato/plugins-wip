"""
test_fleetctl.py - sandbox tests for fleet-suite (B2 controller).

Covers the testable core: config load/save (incl. 0600), URL normalization,
agent selection (the allowlist - only enrolled agents), and the parallel
fan-out with an INJECTED http function (no real network). The safety property
under test: the controller only ever talks to agents that were explicitly
enrolled.

Run:  python3 tests/test_fleetctl.py
"""
import os
import sys
import tempfile
import stat

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import fleetctl as mod  # noqa: E402

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


def test_config_roundtrip():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "sub", "fleet.json")
    check("load missing config -> {}", mod.load_agents(path) == {})
    agents = {"pi4": {"url": "http://pi4:8084", "token": "tok1"}}
    mod.save_agents(path, agents)
    check("save+load roundtrip", mod.load_agents(path) == agents)
    mode = stat.S_IMODE(os.stat(path).st_mode)
    check("config written 0600 (tokens)", mode == 0o600)


def test_normalize_url():
    check("adds http scheme", mod.normalize_url("pi4:8084") == "http://pi4:8084")
    check("keeps https", mod.normalize_url("https://x:8084/") == "https://x:8084")
    check("strips trailing slash", mod.normalize_url("http://x:8084/") == "http://x:8084")


def test_select_agents_allowlist():
    agents = {"a": {}, "b": {}, "c": {}}
    sel, err = mod.select_agents(agents, True, None)
    check("--all selects all enrolled", set(sel) == {"a", "b", "c"} and err is None)
    sel, err = mod.select_agents(agents, False, "a,c")
    check("--agents selects named", set(sel) == {"a", "c"} and err is None)
    sel, err = mod.select_agents(agents, False, "a,zzz")
    check("unenrolled label is REFUSED (allowlist)", sel == {} and "not enrolled" in err)
    sel, err = mod.select_agents(agents, False, None)
    check("no selection -> error", sel == {} and "specify" in err)


def test_fan_out_injected():
    # a fake agent HTTP that records who it was called for and returns a result
    calls = []

    def fake_post(url, token, payload, timeout):
        calls.append((url, token, payload))
        import json
        label = "pi4" if "pi4" in url else "pi5"
        return 200, json.dumps({"ok": True, "exit_code": 0,
                                "stdout": f"ran {payload.get('task')} on {label}"})

    selected = {"pi4": {"url": "http://pi4:8084", "token": "t4"},
                "pi5": {"url": "http://pi5:8084", "token": "t5"}}
    results = mod.fan_out(selected, {"task": "uptime"}, timeout=5, post_fn=fake_post)
    check("fan_out hits every selected agent", set(results) == {"pi4", "pi5"})
    check("fan_out results ok", all(r["ok"] for r in results.values()))
    check("fan_out used the right tokens",
          {c[1] for c in calls} == {"t4", "t5"})
    check("fan_out posted to /run", all(c[0].endswith("/run") for c in calls))


def test_fan_out_handles_agent_error():
    def bad_post(url, token, payload, timeout):
        raise OSError("connection refused")
    selected = {"down": {"url": "http://down:8084", "token": "t"}}
    results = mod.fan_out(selected, {"task": "uptime"}, timeout=2, post_fn=bad_post)
    check("unreachable agent -> ok False, not a crash",
          results["down"]["ok"] is False and "refused" in results["down"]["error"])


def test_run_on_agent_nonjson():
    def html_post(url, token, payload, timeout):
        return 401, "unauthorized"
    r = mod.run_on_agent("x", {"url": "http://x", "token": "t"},
                         {"task": "uptime"}, 5, post_fn=html_post)
    check("non-JSON / 401 handled", r["ok"] is False and r["_status"] == 401)


def test_b3_badhid_helpers():
    # badhid_endpoint only resolves when the agent was enrolled with badhid info
    agent = {"url": "http://pi:8084", "token": "t",
             "badhid": {"url": "http://pi:8083", "token": "bh"}}
    base, tok = mod.badhid_endpoint(agent)
    check("badhid_endpoint resolves", base == "http://pi:8083" and tok == "bh")
    check("no badhid info -> (None,None)",
          mod.badhid_endpoint({"url": "x", "token": "t"}) == (None, None))

    calls = []

    def fake(url, token, payload, timeout):
        import json
        calls.append((url, token, payload))
        return 200, json.dumps({"ok": True, "name": payload.get("name"), "stdout": "ok"})

    r = mod.stage_to_agent("pi", agent, "x.duck", "STRING hi\n", 5, post_fn=fake)
    check("stage hits /stage with badhid token",
          r["ok"] and calls[-1][0].endswith("/stage") and calls[-1][1] == "bh")
    r = mod.fire_on_agent("pi", agent, "x.duck", "lab", 5, post_fn=fake)
    check("fire hits /quickfire", r["ok"] and calls[-1][0].endswith("/quickfire"))
    # an agent with no badhid endpoint refuses stage/fire cleanly
    r = mod.stage_to_agent("x", {"url": "u", "token": "t"}, "a.duck", "x", 5, post_fn=fake)
    check("stage without badhid endpoint refused", r["ok"] is False and "no badhid" in r["error"])


def main():
    test_config_roundtrip()
    test_normalize_url()
    test_select_agents_allowlist()
    test_fan_out_injected()
    test_fan_out_handles_agent_error()
    test_run_on_agent_nonjson()
    test_b3_badhid_helpers()
    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}"); sys.exit(1)
    print("all fleetctl sandbox tests passed")


if __name__ == "__main__":
    main()
