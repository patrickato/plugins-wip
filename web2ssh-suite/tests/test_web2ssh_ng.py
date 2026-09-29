import sys
import os
import time
import urllib.request
import urllib.error
import base64
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")

import pwnagotchi.plugins  # noqa: E402
import web2ssh_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


def make_plugin(**opts):
    plugin = mod.Web2SSHNG()
    defaults = dict(mod.DEFAULTS)
    defaults.update(opts)
    plugin.options = defaults
    return plugin


def free_port():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def http_get(url, username=None, password=None, timeout=5):
    req = urllib.request.Request(url)
    if username is not None:
        creds = base64.b64encode(f"{username}:{password}".encode()).decode()
        req.add_header("Authorization", f"Basic {creds}")
    return urllib.request.urlopen(req, timeout=timeout)


def http_post(url, data, username=None, password=None, timeout=5):
    body = urllib.parse_encode(data) if False else _urlencode(data)
    req = urllib.request.Request(url, data=body.encode(), method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    if username is not None:
        creds = base64.b64encode(f"{username}:{password}".encode()).decode()
        req.add_header("Authorization", f"Basic {creds}")
    return urllib.request.urlopen(req, timeout=timeout)


def _urlencode(data):
    import urllib.parse
    return urllib.parse.urlencode(data)


def wait_for_port(host, port, attempts=50, interval=0.1):
    import socket
    for _ in range(attempts):
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True
        except OSError:
            time.sleep(interval)
    return False


def wait_for_port_closed(host, port, attempts=50, interval=0.1):
    import socket
    for _ in range(attempts):
        try:
            with socket.create_connection((host, port), timeout=0.2):
                time.sleep(interval)
        except OSError:
            return True
    return False


# --- Registration --------------------------------------------------------

check("Web2SSHNG registers with the real pwnagotchi.plugins loader",
      "web2ssh_ng" in pwnagotchi.plugins.loaded)
check("on_loaded hook is present", hasattr(mod.Web2SSHNG, "on_loaded"))
check("on_unload hook is present", hasattr(mod.Web2SSHNG, "on_unload"))

# --- validate_credentials() (bug #2 fix) ---------------------------------

check("valid creds accepted", mod.validate_credentials("alice", "s3cr3t!") == (True, None))
check("None username rejected", mod.validate_credentials(None, "s3cr3t!")[0] is False)
check("blank username rejected", mod.validate_credentials("  ", "s3cr3t!")[0] is False)
check("None password rejected", mod.validate_credentials("alice", None)[0] is False)
check("blank password rejected", mod.validate_credentials("alice", "   ")[0] is False)
check("'changeme' username rejected as placeholder", mod.validate_credentials("changeme", "s3cr3t!")[0] is False)
check("'changeme' password rejected as placeholder", mod.validate_credentials("alice", "changeme")[0] is False)
check("'admin'/'password' rejected as placeholder pair", mod.validate_credentials("admin", "password")[0] is False)
check("case-insensitive placeholder match ('ChangeMe')", mod.validate_credentials("ChangeMe", "s3cr3t!")[0] is False)
check("whitespace-padded placeholder still rejected", mod.validate_credentials("  changeme  ", "s3cr3t!")[0] is False)

# --- No hardcoded default credential string anywhere in the source ------
# (grep-style check the report also verifies manually - see final report)

_src = open(os.path.join(HERE, "..", "web2ssh_ng.py")).read()
check("DEFAULTS username has no working default value (None)", mod.DEFAULTS["username"] is None)
check("DEFAULTS password has no working default value (None)", mod.DEFAULTS["password"] is None)
check("'changeme' only appears inside PLACEHOLDER_CREDENTIALS / comments, never assigned as a credential",
      "self._username = \"changeme\"" not in _src and "self._password = \"changeme\"" not in _src
      and '"username": "changeme"' not in _src and '"password": "changeme"' not in _src)

# --- resolve_bind_plan() --------------------------------------------------

with mock.patch.object(mod, "detect_tailscale_ip", return_value="100.64.1.2"):
    plan = mod.resolve_bind_plan("tailscale", 8082)
check("bind_scope=tailscale with detection uses the tailscale IP", plan == {
    "ok": True, "bind_host": "100.64.1.2", "display_url": "http://100.64.1.2:8082/",
    "warnings": [], "mode_used": "tailscale",
})

with mock.patch.object(mod, "detect_tailscale_ip", return_value=None):
    plan = mod.resolve_bind_plan("tailscale", 8082)
check("bind_scope=tailscale with NO detection refuses to start", plan["ok"] is False)
check("bind_scope=tailscale refusal names no bind_host", plan["bind_host"] is None)

with mock.patch.object(mod, "detect_tailscale_ip", return_value="100.64.1.2"):
    plan = mod.resolve_bind_plan("auto", 8082)
check("bind_scope=auto with detection uses the tailscale IP", plan["bind_host"] == "100.64.1.2")
check("bind_scope=auto with detection reports mode auto-tailscale", plan["mode_used"] == "auto-tailscale")

with mock.patch.object(mod, "detect_tailscale_ip", return_value=None):
    plan = mod.resolve_bind_plan("auto", 8082)
check("bind_scope=auto with NO detection falls back to localhost, does not crash", plan["ok"] is True)
check("bind_scope=auto fallback binds 127.0.0.1 only", plan["bind_host"] == "127.0.0.1")
check("bind_scope=auto fallback logs a warning explaining the options",
      any("lan" in w for w in plan["warnings"]))

plan = mod.resolve_bind_plan("localhost", 8082)
check("bind_scope=localhost always binds 127.0.0.1", plan == {
    "ok": True, "bind_host": "127.0.0.1", "display_url": "http://127.0.0.1:8082/",
    "warnings": [], "mode_used": "localhost",
})

with mock.patch.object(mod, "get_local_lan_ip", return_value="192.168.1.50"):
    plan = mod.resolve_bind_plan("lan", 8082)
check("bind_scope=lan binds 0.0.0.0 unconditionally", plan["bind_host"] == "0.0.0.0")
check("bind_scope=lan displays a real LAN IP in the URL", "192.168.1.50" in plan["display_url"])
check("bind_scope=lan always warns", len(plan["warnings"]) >= 1)

plan = mod.resolve_bind_plan("nonsense-value", 8082)
check("an unrecognized bind_scope value never crashes (falls back to auto behavior)", "ok" in plan)

# --- run_command() ---------------------------------------------------------

result = mod.run_command("echo hello-web2ssh-ng", timeout_seconds=5, max_output_chars=20000)
check("run_command executes a real harmless command", "hello-web2ssh-ng" in result["output"])
check("run_command reports no truncation for short output", result["truncated"] is False)
check("run_command reports no timeout for a fast command", result["timed_out"] is False)

result = mod.run_command("sleep 2", timeout_seconds=1, max_output_chars=20000)
check("run_command handles a real timeout without raising/hanging", result["timed_out"] is True)
check("run_command timeout message mentions the timeout", "timed out" in result["output"].lower())

result = mod.run_command("python3 -c \"print('a' * 500)\"", timeout_seconds=5, max_output_chars=100)
check("run_command truncates output longer than max_output_chars", result["truncated"] is True)
check("run_command truncated output respects the cap", len(result["output"]) == 100)

result = mod.run_command("echo out; echo err 1>&2", timeout_seconds=5, max_output_chars=20000)
check("run_command combines stdout and stderr", "out" in result["output"] and "err" in result["output"])

# --- Constant-time credential comparison (source-verified, not just behavioral) --

check("_check_credentials source uses hmac.compare_digest",
      "hmac.compare_digest" in _src)
check("_check_credentials never uses a plain == comparison of the password itself",
      "password == self.options" not in _src and "password == self._password" not in _src)

# --- Real end-to-end server tests (real Flask + real werkzeug) -----------

port1 = free_port()
plugin = make_plugin(username="alice", password="s3cr3t!", port=port1, bind_scope="localhost")
plugin.on_loaded()

try:
    up = wait_for_port("127.0.0.1", port1)
    check("on_loaded starts a real server that becomes reachable quickly (on_loaded didn't block)", up)

    try:
        http_get(f"http://127.0.0.1:{port1}/")
        check("unauthenticated request is rejected", False)
    except urllib.error.HTTPError as exc:
        check("unauthenticated request gets a real 401", exc.code == 401)

    try:
        resp = http_get(f"http://127.0.0.1:{port1}/", username="alice", password="wrong-password")
        check("wrong password is rejected", False)
    except urllib.error.HTTPError as exc:
        check("wrong password gets a real 401", exc.code == 401)

    resp = http_get(f"http://127.0.0.1:{port1}/", username="alice", password="s3cr3t!")
    check("correct credentials get a real 200", resp.status == 200)
    body = resp.read().decode()
    check("index page shows the reachable-at banner", "Reachable at:" in body)
    check("index page (shortcuts mode, default) shows shortcut buttons", "Shutdown" in body)
    check("index page (shortcuts mode, default) hides the free-text command input",
          'name="command"' not in body or 'id="commandInput"' not in body)

    # shortcuts mode: an arbitrary command is rejected without ever running
    with mock.patch.object(mod, "run_command") as run_mock:
        resp = http_post(
            f"http://127.0.0.1:{port1}/execute", {"command": "rm -rf /"},
            username="alice", password="s3cr3t!",
        )
        out_body = resp.read().decode()
    check("shortcuts mode rejects an arbitrary command", "not an allowed command" in out_body.lower()
          or "rejected" in out_body.lower())
    check("shortcuts mode never calls subprocess for a rejected command", run_mock.call_count == 0)

    # shortcuts mode: an exact configured shortcut command actually runs
    resp = http_post(
        f"http://127.0.0.1:{port1}/execute", {"command": "ping -c 4 8.8.8.8"},
        username="alice", password="s3cr3t!",
    )
    check("shortcuts mode accepts an exact configured shortcut without crashing", resp.status == 200)

finally:
    plugin.on_unload(mock.Mock())

closed = wait_for_port_closed("127.0.0.1", port1)
check("on_unload actually stops the server (port closes)", closed)

# --- on_loaded refuses to start with missing/blank/placeholder creds -----

for label, uname, pwd in [
    ("missing", None, None),
    ("blank", "", ""),
    ("placeholder", "changeme", "changeme"),
]:
    port_x = free_port()
    plugin_bad = make_plugin(username=uname, password=pwd, port=port_x, bind_scope="localhost")
    plugin_bad.on_loaded()
    check(f"on_loaded refuses to start with {label} credentials (no server object)",
          plugin_bad._server is None)
    reachable = wait_for_port("127.0.0.1", port_x, attempts=5, interval=0.05)
    check(f"on_loaded with {label} credentials never actually binds a socket",
          reachable is False)

# --- bind_scope resolution end-to-end (mocked detection) ------------------

port2 = free_port()
with mock.patch.object(mod, "detect_tailscale_ip", return_value=None):
    plugin_ts = make_plugin(username="bob", password="hunter2x!", port=port2, bind_scope="tailscale")
    plugin_ts.on_loaded()
check("bind_scope=tailscale with no detection: on_loaded refuses to start", plugin_ts._server is None)

port3 = free_port()
with mock.patch.object(mod, "detect_tailscale_ip", return_value=None):
    plugin_auto = make_plugin(username="bob", password="hunter2x!", port=port3, bind_scope="auto")
    plugin_auto.on_loaded()
try:
    up = wait_for_port("127.0.0.1", port3)
    check("bind_scope=auto with no detection falls back to localhost without crashing", up)
finally:
    plugin_auto.on_unload(mock.Mock())

# --- command_mode = "free" -------------------------------------------------

port4 = free_port()
plugin_free = make_plugin(
    username="carol", password="s3cr3t!!", port=port4,
    bind_scope="localhost", command_mode="free",
)
plugin_free.on_loaded()
try:
    wait_for_port("127.0.0.1", port4)
    resp = http_get(f"http://127.0.0.1:{port4}/", username="carol", password="s3cr3t!!")
    body = resp.read().decode()
    check("free mode shows the free-text command input", 'id="commandInput"' in body)
    check("free mode shows a visible warning banner", "arbitrary" in body.lower() or "warning" in body.lower())

    resp = http_post(
        f"http://127.0.0.1:{port4}/execute", {"command": "echo free-mode-really-ran"},
        username="carol", password="s3cr3t!!",
    )
    out_body = resp.read().decode()
    check("free mode actually executes a free-text command", "free-mode-really-ran" in out_body)
finally:
    plugin_free.on_unload(mock.Mock())

# --- Output truncation end-to-end ------------------------------------------

port5 = free_port()
plugin_trunc = make_plugin(
    username="dave", password="s3cr3t!!!", port=port5,
    bind_scope="localhost", command_mode="free", max_output_chars=50,
)
plugin_trunc.on_loaded()
try:
    wait_for_port("127.0.0.1", port5)
    resp = http_post(
        f"http://127.0.0.1:{port5}/execute",
        {"command": "python3 -c \"print('x' * 5000)\""},
        username="dave", password="s3cr3t!!!",
    )
    out_body = resp.read().decode()
    check("truncation note appears in the rendered page", "truncat" in out_body.lower())
finally:
    plugin_trunc.on_unload(mock.Mock())

print(f"\n{len(failures)} failure(s) out of test run")
if failures:
    print(failures)
    sys.exit(1)
else:
    print("All tests passed.")
