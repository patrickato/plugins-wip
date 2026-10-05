"""
test_remoteexec_ng.py - sandbox tests for remoteexec-suite (B1).

Focus is the security choke point: resolve_request() must enforce the mode
gates (tasks-only by default; free mode double-gated). Plus run_command,
token validation, and bind-scope resolution.

Run:  python3 tests/test_remoteexec_ng.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))

import remoteexec_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


TASKS = {"uptime": "uptime", "disk": "df -h /"}


def test_tasks_mode_gate():
    # tasks mode: only named tasks; arbitrary command refused
    cmd, label, err = mod.resolve_request("tasks", False, TASKS, {"task": "uptime"})
    check("tasks mode runs a named task", cmd == "uptime" and err is None)
    cmd, label, err = mod.resolve_request("tasks", False, TASKS, {"task": "nope"})
    check("tasks mode rejects unknown task", cmd is None and "unknown task" in err)
    cmd, label, err = mod.resolve_request("tasks", False, TASKS, {"command": "rm -rf /"})
    check("tasks mode REFUSES arbitrary command", cmd is None and "refused" in err)
    cmd, label, err = mod.resolve_request("tasks", False, TASKS, {})
    check("tasks mode needs a task", cmd is None and "provide" in err)
    # even if allow_free_mode is true, tasks mode still refuses arbitrary command
    cmd, label, err = mod.resolve_request("tasks", True, TASKS, {"command": "whoami"})
    check("tasks mode refuses command even if allow_free_mode=true",
          cmd is None and "refused" in err)


def test_free_mode_double_gate():
    # free mode requires BOTH command_mode=free AND allow_free_mode=true
    cmd, label, err = mod.resolve_request("free", False, TASKS, {"command": "whoami"})
    check("free mode without allow_free_mode is REFUSED",
          cmd is None and "allow_free_mode=true" in err)
    cmd, label, err = mod.resolve_request("free", True, TASKS, {"command": "whoami"})
    check("free mode with both gates runs the command", cmd == "whoami" and err is None)
    cmd, label, err = mod.resolve_request("free", True, TASKS, {"command": "  "})
    check("free mode rejects empty command", cmd is None and "non-empty" in err)
    # a named task still works in free mode
    cmd, label, err = mod.resolve_request("free", True, TASKS, {"task": "disk"})
    check("free mode can still run a named task", cmd == "df -h /" and err is None)


def test_run_command():
    r = mod.run_command("echo hello", timeout_seconds=5)
    check("run echo ok", r["ok"] and r["exit_code"] == 0)
    check("run echo stdout", r["stdout"].strip() == "hello")
    r = mod.run_command("exit 3", timeout_seconds=5)
    check("nonzero exit captured", r["ok"] is False and r["exit_code"] == 3)
    r = mod.run_command("sleep 5", timeout_seconds=1)
    check("timeout caught", r["ok"] is False and "timed out" in r["stderr"])
    r = mod.run_command("head -c 100 /dev/zero | tr '\\0' x", timeout_seconds=5, max_output_chars=10)
    check("output truncation flagged", r["truncated"] is True and len(r["stdout"]) <= 10)


def test_token():
    check("good token", mod.validate_token("a-long-enough-token")[0])
    check("blank rejected", not mod.validate_token("")[0])
    check("placeholder rejected", not mod.validate_token("changeme")[0])
    check("short rejected", not mod.validate_token("short")[0])
    check("match", mod.token_matches("abc123xyz789", "abc123xyz789"))
    check("mismatch", not mod.token_matches("abc123xyz789", "nope"))
    check("none-safe", not mod.token_matches(None, None))


def test_bind():
    h, n, ok = mod.resolve_bind_plan("localhost", 8084, lambda: None)
    check("localhost", h == "127.0.0.1" and ok)
    h, n, ok = mod.resolve_bind_plan("tailscale", 8084, lambda: None)
    check("tailscale w/o iface refuses", h is None and not ok)
    h, n, ok = mod.resolve_bind_plan("auto", 8084, lambda: None)
    check("auto -> localhost", h == "127.0.0.1" and ok)
    h, n, ok = mod.resolve_bind_plan("lan", 8084, lambda: None)
    check("lan -> 0.0.0.0", h == "0.0.0.0" and ok)


def test_plugin_execute_gate():
    # the plugin-level _execute honors the gates and logs; mock options
    p = mod.RemoteExecNG()
    p.options = dict(mod.DEFAULTS)
    p.options.update(command_mode="tasks", tasks=TASKS)
    res, status = p._execute({"command": "rm -rf /"}, "test")
    check("_execute refuses arbitrary cmd in tasks mode", res.get("ok") is False and status == 400)
    res, status = p._execute({"task": "uptime"}, "test")
    check("_execute runs a named task", "exit_code" in res)


def main():
    test_tasks_mode_gate()
    test_free_mode_double_gate()
    test_run_command()
    test_token()
    test_bind()
    test_plugin_execute_gate()
    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}"); sys.exit(1)
    print("all remoteexec_ng sandbox tests passed")


if __name__ == "__main__":
    main()
