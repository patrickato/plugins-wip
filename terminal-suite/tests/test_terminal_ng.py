import sys
import os
import subprocess
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")

import pwnagotchi.plugins  # noqa: E402
import terminal_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


def make_plugin(**opts):
    p = mod.TerminalNG()
    p.options = dict(opts)
    return p


class FakeRequest:
    def __init__(self, remote_addr):
        self.remote_addr = remote_addr


# --- Registration ---------------------------------------------------------

check(
    "TerminalNG registers as a real pwnagotchi.plugins.Plugin subclass",
    issubclass(mod.TerminalNG, pwnagotchi.plugins.Plugin),
)

# --- extract_ip_address (the method that was previously broken by a missing
#     `re` import) actually parses systemctl status output ------------------

sample_status = "Active: active (running)\n   Docs: ...\nlistening on 0.0.0.0:2222\n"
check(
    "extract_ip_address parses a real systemctl status block",
    mod.TerminalNG.extract_ip_address(sample_status) == "0.0.0.0:2222",
)
check(
    "extract_ip_address returns None when there's no 'listening on' line",
    mod.TerminalNG.extract_ip_address("Active: inactive (dead)") is None,
)

# --- on_loaded when the unit file doesn't exist yet: creates it, enables,
#     starts, and polls (using real `time.sleep` via a short poll interval,
#     and real `re` via extract_ip_address - both previously NameErrors) ---

with mock.patch("os.path.exists", return_value=False), \
     mock.patch("builtins.open", mock.mock_open()), \
     mock.patch("os.system") as mock_system, \
     mock.patch.object(
         mod.TerminalNG, "_service_status_output",
         return_value="Active: active (running)\nlistening on 0.0.0.0:2222\n"
     ):
    p = make_plugin(startup_poll_attempts=3, startup_poll_interval=0)
    p.on_loaded()

check("on_loaded enables+starts the service when the unit file is missing", mock_system.call_count >= 2)
check("on_loaded sets ready=True once the service reports it's listening", p.ready is True)

# --- on_loaded's polling loop doesn't crash (re/time were previously missing
#     imports that would NameError here) when the service never comes up ---

with mock.patch("os.path.exists", return_value=False), \
     mock.patch("builtins.open", mock.mock_open()), \
     mock.patch("os.system"), \
     mock.patch.object(mod.TerminalNG, "_service_status_output", return_value="Active: inactive (dead)\n"):
    p2 = make_plugin(startup_poll_attempts=2, startup_poll_interval=0)
    try:
        p2.on_loaded()
        ok = True
    except NameError:
        ok = False
check("on_loaded's polling loop never NameErrors even if the service never comes up", ok)
check("ready stays False if the service never reports listening", p2.ready is False)

# --- on_loaded when the unit file already exists and the service is active -

with mock.patch("os.path.exists", return_value=True), \
     mock.patch.object(mod.TerminalNG, "_service_status_output", return_value="active\n") as mock_status, \
     mock.patch("os.system") as mock_system2:
    p3 = make_plugin()
    p3.on_loaded()

check("existing unit file + already-active service: no re-creation needed", mock_system2.call_count == 0)
check("ready=True when the existing service is already active", p3.ready is True)

# --- on_loaded when the unit file exists but the service isn't active ------

with mock.patch("os.path.exists", return_value=True), \
     mock.patch.object(mod.TerminalNG, "_service_status_output", return_value="inactive\n"), \
     mock.patch("builtins.open", mock.mock_open()), \
     mock.patch("os.system") as mock_system3:
    p4 = make_plugin()
    p4.on_loaded()

check("existing unit file + inactive service: recreates and restarts it", mock_system3.call_count >= 3)

# --- _service_status_output survives a nonzero exit (systemctl status
#     returns exit code 3 for an inactive unit - CalledProcessError) --------

with mock.patch(
    "subprocess.check_output",
    side_effect=subprocess.CalledProcessError(3, "systemctl", output=b"inactive\n"),
):
    p5 = make_plugin()
    output = p5._service_status_output("status")
check("_service_status_output handles a CalledProcessError (nonzero exit) without raising", output == "inactive\n")

# --- Access control: allowed_networks (replaces hardcoded IPs) -------------

p6 = make_plugin(allowed_networks=["10.0.0.0/8", "192.168.0.0/16"])
check("an address inside 10.0.0.0/8 is allowed", p6._is_allowed("10.5.5.5"))
check("an address inside 192.168.0.0/16 is allowed", p6._is_allowed("192.168.44.44"))
check("an address outside both configured ranges is denied", not p6._is_allowed("203.0.113.7"))
check("a garbage remote_addr doesn't crash and is denied", not p6._is_allowed("not-an-ip"))
check("an empty remote_addr is denied, not allowed", not p6._is_allowed(""))
check("None remote_addr is denied, not allowed", not p6._is_allowed(None))

# --- A single bad CIDR entry in config doesn't break the whole check -------

p7 = make_plugin(allowed_networks=["not-a-cidr", "192.168.0.0/16"])
check(
    "one malformed allowed_networks entry doesn't prevent the valid one from matching",
    p7._is_allowed("192.168.1.1"),
)

# --- Default allowed_networks covers real private ranges, not just the
#     original author's two specific subnets --------------------------------

p8 = make_plugin()
check("default allowed_networks allows a 10.x address", p8._is_allowed("10.1.2.3"))
check("default allowed_networks allows a 172.16-31.x address", p8._is_allowed("172.20.1.1"))
check("default allowed_networks allows any 192.168.x address, not just .44.44", p8._is_allowed("192.168.1.50"))
check("default allowed_networks denies a public IP", not p8._is_allowed("8.8.8.8"))

# --- on_webhook renders without crashing, both allowed and denied ----------

with mock.patch("terminal_ng.render_template_string", side_effect=lambda tmpl, **kw: kw) as mock_render:
    p9 = make_plugin(allowed_networks=["192.168.0.0/16"])
    result = p9.on_webhook("/", FakeRequest("192.168.1.1"))
check("on_webhook renders with allowed=True for an in-range address", result["allowed"] is True)

with mock.patch("terminal_ng.render_template_string", side_effect=lambda tmpl, **kw: kw):
    p10 = make_plugin(allowed_networks=["192.168.0.0/16"])
    result2 = p10.on_webhook("/", FakeRequest("8.8.8.8"))
check("on_webhook renders with allowed=False for an out-of-range address", result2["allowed"] is False)

# --- on_unload doesn't crash -----------------------------------------------

with mock.patch("os.system") as mock_system4:
    p11 = make_plugin()
    p11.ready = True
    p11.on_unload(mock.Mock())
check("on_unload stops/disables the service and clears ready", p11.ready is False and mock_system4.call_count == 3)


print(f"\n{len(failures)} failure(s) out of test run")
if failures:
    sys.exit(1)
