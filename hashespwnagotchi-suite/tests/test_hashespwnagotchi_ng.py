import sys
import os
import json
import tempfile
import shutil
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))  # only for `prctl` (native, not installable here)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")  # real pwnagotchi.plugins framework

import pwnagotchi.plugins  # noqa: E402
import hashespwnagotchi_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


tmpdir = tempfile.mkdtemp()


def make_plugin(**opts):
    p = mod.HashesPwnagotchiNG()
    p.options = {"api_key": "k", "api_url": "https://hashes.pw/api/v1", **opts}
    p.report = mock.Mock()
    p.report.data_field_or.return_value = []
    p.ready = True
    return p


# --- Test 1: real framework registration -------------------------------
check(
    "HashesPwnagotchiNG registered itself in the REAL framework's plugins.loaded on import",
    "hashespwnagotchi_ng" in pwnagotchi.plugins.loaded
    and isinstance(pwnagotchi.plugins.loaded["hashespwnagotchi_ng"], mod.HashesPwnagotchiNG),
)

# --- Test 2: NO shell=True anywhere in the module's actual CODE (fix #1)
# (checked against the source with its module docstring stripped, since
# the docstring itself quotes the original's buggy calls when explaining
# what was fixed - checking the raw source would flag its own comments)
import inspect
import ast

tree = ast.parse(inspect.getsource(mod))
tree.body = [n for n in tree.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
code_only = ast.unparse(tree)
check("no subprocess.getoutput() calls remain in real code (fix #1)", "getoutput" not in code_only)
check("no shell=True anywhere in real code (fix #1)", "shell=True" not in code_only)
check("no bare string-concatenated shell commands remain in real code (fix #1)", "2>/dev/null" not in code_only and "| sed" not in code_only)

# --- Test 3: malicious "ESSID" filename never reaches a shell (fix #1) --
# (no literal "/" in the crafted name - that would just be a nested
# directory as far as the filesystem is concerned, not a shell escape;
# backticks/$()/semicolons are the actual injection vector being tested)
malicious_name = "$(id)_touch_pwned;`whoami`_AA-BB-CC-DD-EE-FF.pcapng"
malicious_path = os.path.join(tmpdir, malicious_name)
open(malicious_path, "wb").close()

with mock.patch("hashespwnagotchi_ng.subprocess.run") as run_mock:
    run_mock.return_value = mock.Mock(returncode=1, stdout=b"")
    mod._write_eapol_args_seen = None
    ok, out = mod._run(["hcxpcapngtool", "-o", "/tmp/x.22000", malicious_path])
    called_args, called_kwargs = run_mock.call_args
    cmd_list = called_args[0]
    check("malicious filename passed as a single argv element, not shell text", cmd_list[-1] == malicious_path)
    check("subprocess.run called with a list, not a shell string", isinstance(cmd_list, list))
    check("subprocess.run never passed shell=True", called_kwargs.get("shell", False) is False)

# --- Test 4: on_config_changed does not crash when `interval` is set (fix #2)
p = make_plugin(interval=24)
p.report = mock.Mock()
p.report.newer_then_hours.return_value = True  # pretend we just ran recently
config = {"bettercap": {"handshakes": tmpdir}, "main": {"name": "test-pwn"}}
try:
    with mock.patch.object(mod.HashesPwnagotchiNG, "_open_report", return_value=p.report):
        p.on_config_changed(config)
    check("on_config_changed with interval set does not raise (fix #2)", True)
except AttributeError as e:
    check(f"on_config_changed with interval set does not raise (fix #2) (got {e})", False)
check("batch scan skipped because report says it ran recently (fix #2 logic)", p.report.newer_then_hours.called)

# --- Test 5: batch scan finds .pcapng, not .pcap (fix #3) ---------------
shutil.rmtree(tmpdir)
os.makedirs(tmpdir)
open(os.path.join(tmpdir, "real.pcapng"), "wb").close()
open(os.path.join(tmpdir, "legacy.pcap"), "wb").close()

p = make_plugin()
with mock.patch.object(p, "_write_eapol", return_value=False), mock.patch.object(p, "_write_pmkid", return_value=False):
    p._process_stale_captures(tmpdir)
    seen = [c.args[0] for c in p._write_eapol.call_args_list]
check(".pcapng file was processed by the batch scan (fix #3)", any("real.pcapng" in s for s in seen))
check(".pcap (legacy) file was never processed by the batch scan (fix #3)", not any("legacy.pcap" in s for s in seen))

# --- Test 6: os.path.splitext handles a dot inside the ESSID (fix #4) ---
tricky = os.path.join(tmpdir, "Motorola.5G_AA:BB:CC:DD:EE:FF.pcapng")
open(tricky, "wb").close()
open(tricky[: -len(".pcapng")] + ".22000", "wb").close()
p = make_plugin()
with mock.patch.object(p, "_write_eapol") as we, mock.patch.object(p, "_write_pmkid", return_value=False):
    p._process_stale_captures(tmpdir)
    # since the .22000 file already exists at the CORRECT (dot-aware) path, _write_eapol should never be called for it
    called_paths = [c.args[0] for c in we.call_args_list]
check(
    "a dot inside the ESSID does not break extension handling (fix #4)",
    tricky not in called_paths,
)

# --- Test 7: whitelist is actually applied (fix #5) ---------------------
shutil.rmtree(tmpdir)
os.makedirs(tmpdir)
open(os.path.join(tmpdir, "HomeWifi_AA:AA:AA:AA:AA:AA.22000"), "wb").write(b"hash1\n")
open(os.path.join(tmpdir, "NeighborWifi_BB:BB:BB:BB:BB:BB.22000"), "wb").write(b"hash2\n")

p = make_plugin(whitelist=["HomeWifi"])
fake_agent = mock.Mock()
fake_agent.config.return_value = {"bettercap": {"handshakes": tmpdir}, "main": {"name": "test-pwn"}}
fake_view = mock.Mock()
fake_agent.view.return_value = fake_view
uploaded = []
with mock.patch.object(p, "_upload_eapol", side_effect=lambda path, name=None: uploaded.append(path)), \
     mock.patch.object(p, "_connected_to_internet", return_value=True):
    p._report_handshakes(fake_agent)
check("whitelisted AP's hash file was never uploaded (fix #5)", not any("HomeWifi" in u for u in uploaded))
check("non-whitelisted AP's hash file was uploaded (fix #5)", any("NeighborWifi" in u for u in uploaded))

# --- Test 8: Python 2 .encode("hex") bug is fixed (fix #6) --------------
shutil.rmtree(tmpdir)
os.makedirs(tmpdir)
cap = os.path.join(tmpdir, "SomeAP_CCCCCCCCCCCC.pcapng")
open(cap, "wb").close()
hash16800 = cap[: -len(".pcapng")] + ".16800"
with open(hash16800, "w") as fp:
    fp.write("WPA*01*hash*CCCCCCCCCCCC*\n")
p = make_plugin()
ap_json = {"mac": "CC:CC:CC:CC:CC:CC", "hostname": "SomeAP"}
try:
    p._repair_pmkid(cap, ap_json)
    check("PMKID repair with a real AP dict does not raise (fix #6, was .encode('hex'))", True)
except LookupError as e:
    check(f"PMKID repair with a real AP dict does not raise (fix #6) (got {e})", False)

# --- Test 9: bounded retry then permanent skip (fix #7) -----------------
shutil.rmtree(tmpdir)
os.makedirs(tmpdir)
open(os.path.join(tmpdir, "Flaky_DDDDDDDDDDDD.22000"), "wb").write(b"hash\n")

p = make_plugin(max_upload_attempts=2, upload_retry_delay=0)
fake_agent = mock.Mock()
fake_agent.config.return_value = {"bettercap": {"handshakes": tmpdir}, "main": {"name": "test-pwn"}}
fake_agent.view.return_value = mock.Mock()

import requests as _requests

with mock.patch.object(p, "_upload_eapol", side_effect=_requests.exceptions.ConnectionError("boom")), \
     mock.patch.object(p, "_connected_to_internet", return_value=True):
    p._report_handshakes(fake_agent)  # attempt 1: fails, not yet skipped
    still_pending_after_1 = len(p.skip) == 0
    p._report_handshakes(fake_agent)  # attempt 2: fails, now hits max_upload_attempts -> skip
    skipped_after_2 = any("Flaky" in s for s in p.skip)

check("a single failed upload is retried, not immediately permanently skipped (fix #7)", still_pending_after_1)
check("after max_upload_attempts failures, the handshake is finally skipped (fix #7)", skipped_after_2)

# --- Test 10: _validate_or_fetch_token raises ValueError, not KeyError (fix #8)
p = make_plugin()
fake_response = mock.Mock()
fake_response.content = json.dumps({"not_token": "oops"}).encode()
with mock.patch("hashespwnagotchi_ng.requests.post", return_value=fake_response):
    try:
        p._validate_or_fetch_token()
        check("missing 'token' key raises ValueError, not KeyError (fix #8)", False)
    except ValueError:
        check("missing 'token' key raises ValueError, not KeyError (fix #8)", True)
    except KeyError:
        check("missing 'token' key raises ValueError, not KeyError (fix #8) (got KeyError)", False)

shutil.rmtree(tmpdir, ignore_errors=True)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("ALL TESTS PASSED")
