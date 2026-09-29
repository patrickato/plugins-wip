import sys
import os
import tempfile
import shutil
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))  # prctl + tomlkit (native/unavailable here)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")  # real pwnagotchi.plugins framework

import pwnagotchi.plugins  # noqa: E402
import banthex_ng as mod  # noqa: E402
import requests  # noqa: E402
from flask import Flask  # noqa: E402

app = Flask(__name__)

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


tmpdir = tempfile.mkdtemp()


def make_plugin(**opts):
    p = mod.BanthexNG()
    p.options = {"api_key": "k", "api_url": "https://banthex.de/wpa/", **opts}
    p.report = mock.Mock()
    p.report.data_field_or.return_value = []
    p.ready = True
    return p


def fake_agent(handshake_dir):
    agent = mock.Mock()
    agent.config.return_value = {"bettercap": {"handshakes": handshake_dir}, "main": {"name": "test-pwn"}}
    agent.view.return_value = mock.Mock()
    return agent


# --- Test 1: real framework registration -------------------------------
check(
    "BanthexNG registered itself in the REAL framework's plugins.loaded on import",
    "banthex_ng" in pwnagotchi.plugins.loaded
    and isinstance(pwnagotchi.plugins.loaded["banthex_ng"], mod.BanthexNG),
)

# --- Test 2: .pcapng is uploaded, legacy .pcap is not (fix #1) ----------
open(os.path.join(tmpdir, "real.pcapng"), "wb").close()
open(os.path.join(tmpdir, "legacy.pcap"), "wb").close()

p = make_plugin()
uploaded = []
with mock.patch.object(p, "_upload_to_banthex", side_effect=lambda path: uploaded.append(path)):
    p.on_internet_available(fake_agent(tmpdir))
check(".pcapng file was uploaded (fix #1)", any("real.pcapng" in u for u in uploaded))
check(".pcap (legacy) file was never uploaded (fix #1)", not any("legacy.pcap" in u for u in uploaded))

# --- Test 3: whitelist is applied ----------------------------------------
shutil.rmtree(tmpdir)
os.makedirs(tmpdir)
open(os.path.join(tmpdir, "HomeWifi_AAAAAAAAAAAA.pcapng"), "wb").close()
open(os.path.join(tmpdir, "NeighborWifi_BBBBBBBBBBBB.pcapng"), "wb").close()

p = make_plugin(whitelist=["HomeWifi"])
uploaded = []
with mock.patch.object(p, "_upload_to_banthex", side_effect=lambda path: uploaded.append(path)):
    p.on_internet_available(fake_agent(tmpdir))
check("whitelisted AP was never uploaded", not any("HomeWifi" in u for u in uploaded))
check("non-whitelisted AP was uploaded", any("NeighborWifi" in u for u in uploaded))

# --- Test 4: bounded retry then permanent skip (fix #2) ------------------
shutil.rmtree(tmpdir)
os.makedirs(tmpdir)
open(os.path.join(tmpdir, "Flaky_CCCCCCCCCCCC.pcapng"), "wb").close()

p = make_plugin(max_upload_attempts=2)
agent = fake_agent(tmpdir)
with mock.patch.object(p, "_upload_to_banthex", side_effect=requests.exceptions.ConnectionError("boom")):
    p.on_internet_available(agent)  # attempt 1
    not_yet_skipped = len(p.skip) == 0
    p.on_internet_available(agent)  # attempt 2 -> hits max_upload_attempts
    skipped_now = any("Flaky" in s for s in p.skip)

check("a single failed upload is retried, not immediately permanently skipped (fix #2)", not_yet_skipped)
check("after max_upload_attempts failures, the handshake is finally skipped (fix #2)", skipped_now)

# --- Test 5: a successful upload is never retried/skipped ---------------
shutil.rmtree(tmpdir)
os.makedirs(tmpdir)
open(os.path.join(tmpdir, "Good_DDDDDDDDDDDD.pcapng"), "wb").close()

p = make_plugin()
with mock.patch.object(p, "_upload_to_banthex", return_value=None):
    p.on_internet_available(fake_agent(tmpdir))
check("a successful upload is recorded as reported", p.report.update.called)
check("a successful upload never lands in the skip set", len(p.skip) == 0)

# --- Test 6: download_results respects the configurable interval --------
shutil.rmtree(tmpdir)
os.makedirs(tmpdir)
cracked = os.path.join(tmpdir, "banthex.cracked.potfile")
open(cracked, "wb").close()  # freshly "downloaded" just now

p = make_plugin(download_results=True, download_check_interval_hours=1)
with mock.patch.object(p, "_download_from_banthex") as dl:
    p._maybe_download_cracked(tmpdir)
check("a recently-downloaded potfile is not re-downloaded within the interval", not dl.called)

# --- Test 7: on_webhook sets the auth cookie and redirects (documented, unchanged behavior)
p = make_plugin()
with app.test_request_context("/"):
    resp = p.on_webhook("/", request=None)
    check("on_webhook returns a redirect response with the api_key cookie set", "key" in resp.headers.get("Set-Cookie", ""))

shutil.rmtree(tmpdir, ignore_errors=True)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("ALL TESTS PASSED")
