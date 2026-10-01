import os
import sys
import tempfile
import threading
import time
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))  # prctl + tomlkit (native/unavailable here)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")  # real pwnagotchi.plugins framework

import pwnagotchi.plugins  # noqa: E402
import crack_pipeline_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


_TEST_LOG_DIR = tempfile.mkdtemp(prefix="crack_pipeline_ng_test_logs_")


def new_plugin(**opts):
    """A fresh instance with options set, WITHOUT calling on_loaded (no
    background worker thread started) - for tests that only exercise
    classification/matching/path-building logic."""
    opts.setdefault("log_file", os.path.join(_TEST_LOG_DIR, "results.log"))
    p = mod.CrackPipelineNG()
    p.options = dict(opts)
    p._load_targets()
    p.ready = True
    return p


def make_plugin(**opts):
    """A fresh instance that's gone through the real on_loaded() -
    starts a real worker thread. Caller must stop_plugin() it when done."""
    opts.setdefault("log_file", os.path.join(_TEST_LOG_DIR, "results.log"))
    p = mod.CrackPipelineNG()
    p.options = dict(opts)
    p.on_loaded()
    return p


def stop_plugin(p):
    p._stop_event.set()
    if p._worker_thread and p._worker_thread.is_alive():
        p._worker_thread.join(timeout=5.0)


def drain(p, timeout=5.0):
    joined = threading.Event()

    def _join():
        p._queue.join()
        joined.set()

    t = threading.Thread(target=_join, daemon=True)
    t.start()
    joined.wait(timeout=timeout)


# =====================================================================
# Registration
# =====================================================================

check(
    "CrackPipelineNG registers with the real pwnagotchi.plugins loader",
    "crack_pipeline_ng" in pwnagotchi.plugins.loaded,
)

# =====================================================================
# _as_ap_dict - dict and bare-string AP shapes
# =====================================================================

ap_dict = {"mac": "AA:BB:CC:DD:EE:FF", "hostname": "MyNet", "encryption": "WPA2"}
check("_as_ap_dict passes a real dict through unchanged",
      mod._as_ap_dict(ap_dict) is ap_dict)

bare = mod._as_ap_dict("AA:BB:CC:DD:EE:FF")
check("_as_ap_dict normalizes a bare MAC string into a dict",
      bare == {"mac": "AA:BB:CC:DD:EE:FF", "hostname": ""})

check("_as_ap_dict normalizes None into an empty dict",
      mod._as_ap_dict(None) == {})

# =====================================================================
# _classify - WEP / open / unknown / WPA routing
# =====================================================================

route, detail = mod._classify({"encryption": "WEP"})
check("_classify routes WEP correctly", route == "wep")

route, detail = mod._classify({"encryption": ""})
check("_classify routes empty encryption to open", route == "open")

route, detail = mod._classify({"encryption": "OPEN"})
check("_classify routes 'OPEN' string to open", route == "open")

route, detail = mod._classify({})
check("_classify routes a dict with no encryption key to unknown", route == "unknown")

route, detail = mod._classify({"encryption": None})
check("_classify routes an explicit None encryption to unknown", route == "unknown")

route, detail = mod._classify({"encryption": "WPA2"})
check("_classify routes WPA2 to wpa", route == "wpa")

route, detail = mod._classify({"encryption": "PMKID"})
check("_classify routes PMKID-labeled encryption to wpa", route == "wpa")

# =====================================================================
# _safe_name - SSID sanitization
# =====================================================================

check("_safe_name strips path separators",
      "/" not in mod._safe_name("../../etc/passwd"))
check("_safe_name strips dots",
      "." not in mod._safe_name("../../etc/passwd"))
check("_safe_name never returns empty for an all-symbol SSID",
      mod._safe_name("////") != "")
check("_safe_name keeps alnum/underscore untouched",
      mod._safe_name("My_Lab_42") == "My_Lab_42")

# =====================================================================
# on_handshake classification routing + .route sidecar content
# =====================================================================

with tempfile.TemporaryDirectory() as tmp:
    pcap = os.path.join(tmp, "capture.pcapng")
    with open(pcap, "w") as f:
        f.write("fake")

    p = new_plugin(authorized_networks=[])
    wep_ap = {"mac": "AA:AA:AA:AA:AA:AA", "hostname": "WepNet", "encryption": "WEP"}
    p.on_handshake(mock.Mock(), pcap, wep_ap, {})
    with open(pcap + ".route") as f:
        sidecar = f.read()
    check("WEP capture gets a .route sidecar recommending aircrack-ng",
          "route: aircrack-ng" in sidecar)
    check("WEP capture is recorded as skipped-wep in history",
          p.history[-1]["result"] == "skipped-wep")
    check("WEP capture never enqueues a job", p._queue.qsize() == 0)

    pcap2 = os.path.join(tmp, "capture2.pcapng")
    open(pcap2, "w").close()
    open_ap = {"mac": "BB:BB:BB:BB:BB:BB", "hostname": "OpenNet", "encryption": ""}
    p.on_handshake(mock.Mock(), pcap2, open_ap, {})
    with open(pcap2 + ".route") as f:
        sidecar = f.read()
    check("Open-network capture gets a .route sidecar with route: none",
          "route: none" in sidecar)
    check("Open-network capture is recorded as skipped-open", p.history[-1]["result"] == "skipped-open")

    pcap3 = os.path.join(tmp, "capture3.pcapng")
    open(pcap3, "w").close()
    wpa_ap = {"mac": "CC:CC:CC:CC:CC:CC", "hostname": "UnauthNet", "encryption": "WPA2"}
    p.on_handshake(mock.Mock(), pcap3, wpa_ap, {})
    with open(pcap3 + ".route") as f:
        sidecar = f.read()
    check("Unauthorized WPA capture gets a .route sidecar noting it's unauthorized",
          "route: unauthorized" in sidecar)
    check("Unauthorized WPA capture is recorded as skipped-unauthorized",
          p.history[-1]["result"] == "skipped-unauthorized")
    check("Unauthorized WPA capture never enqueues a job", p._queue.qsize() == 0)

    pcap4 = os.path.join(tmp, "capture4.pcapng")
    open(pcap4, "w").close()
    unknown_ap = "DD:DD:DD:DD:DD:DD"  # bare-MAC-string shape, no encryption info at all
    p.on_handshake(mock.Mock(), pcap4, unknown_ap, {})
    with open(pcap4 + ".route") as f:
        sidecar = f.read()
    check("Bare-MAC-string AP (no encryption field) gets a .route sidecar with route: unknown",
          "route: unknown" in sidecar)
    check("Bare-MAC-string AP never crashes on_handshake and never enqueues",
          p._queue.qsize() == 0)

# =====================================================================
# authorized_networks empty => zero jobs EVER enqueued
# =====================================================================

with tempfile.TemporaryDirectory() as tmp:
    p = new_plugin(authorized_networks=[])
    for i in range(5):
        pcap = os.path.join(tmp, f"cap_{i}.pcapng")
        open(pcap, "w").close()
        ap = {"mac": f"11:11:11:11:11:{i:02X}", "hostname": f"Net{i}", "encryption": "WPA2"}
        p.on_handshake(mock.Mock(), pcap, ap, {})
    check("empty authorized_networks never enqueues a single job across 5 WPA captures",
          p._queue.qsize() == 0)

# =====================================================================
# BSSID-form and SSID-form authorized-target matching
# =====================================================================

with tempfile.TemporaryDirectory() as tmp:
    p = new_plugin(authorized_networks=["AA:BB:CC:DD:EE:FF"])
    pcap = os.path.join(tmp, "cap.pcapng")
    open(pcap, "w").close()
    ap = {"mac": "AA:BB:CC:DD:EE:FF", "hostname": "MyLab", "encryption": "WPA2"}
    p.on_handshake(mock.Mock(), pcap, ap, {})
    check("BSSID-form authorized target enqueues a job", p._queue.qsize() == 1)

    p2 = new_plugin(authorized_networks=["MyLabNetwork"])
    pcap2 = os.path.join(tmp, "cap2.pcapng")
    open(pcap2, "w").close()
    ap2 = {"mac": "11:22:33:44:55:66", "hostname": "MyLabNetwork", "encryption": "WPA2"}
    p2.on_handshake(mock.Mock(), pcap2, ap2, {})
    check("SSID-form authorized target enqueues a job", p2._queue.qsize() == 1)

    p3 = new_plugin(authorized_networks=["mylabnetwork"])
    pcap3 = os.path.join(tmp, "cap3.pcapng")
    open(pcap3, "w").close()
    ap3 = {"mac": "11:22:33:44:55:77", "hostname": "MyLabNetwork", "encryption": "WPA2"}
    p3.on_handshake(mock.Mock(), pcap3, ap3, {})
    check("SSID matching is case-insensitive", p3._queue.qsize() == 1)

# =====================================================================
# SSID sanitization survives into actual path-building (never escapes export_dir)
# =====================================================================

with tempfile.TemporaryDirectory() as tmp:
    export_dir = os.path.join(tmp, "exports")
    os.makedirs(export_dir)
    p = new_plugin(authorized_networks=["AA:BB:CC:DD:EE:FF"], export_dir=export_dir,
                   run_local=False)
    pcap = os.path.join(tmp, "evil.pcapng")
    open(pcap, "w").close()

    captured = {}

    def fake_convert(pcap_path, hc_path, timeout):
        captured["hc_path"] = hc_path
        with open(hc_path, "w") as f:
            f.write("fake-hc22000-data")
        return True

    p._convert = fake_convert
    job = {"filename": pcap, "ssid": "../../etc/passwd", "bssid": "AA:BB:CC:DD:EE:FF"}
    p._process_job(job)

    hc_path = captured.get("hc_path", "")
    real_export_dir = os.path.realpath(export_dir)
    real_hc_path = os.path.realpath(hc_path)
    check("a malicious SSID with '../' never escapes export_dir",
          os.path.commonpath([real_export_dir, real_hc_path]) == real_export_dir)
    check("a malicious SSID with '/' never appears unsanitized in the built path",
          ".." not in os.path.basename(hc_path))

# =====================================================================
# empty-dirname os.makedirs guard (bug #3)
# =====================================================================

with tempfile.TemporaryDirectory() as tmp:
    cwd = os.getcwd()
    try:
        os.chdir(tmp)
        p = mod.CrackPipelineNG()
        p.options = {"log_file": "results.log", "export_dir": os.path.join(tmp, "exports")}
        try:
            p.on_loaded()
            ok = True
        except Exception:
            ok = False
        check("on_loaded with a log_file with no directory component doesn't raise", ok)
        check("on_loaded still sets self.ready = True despite the empty dirname", p.ready is True)
        stop_plugin(p)
    finally:
        os.chdir(cwd)

# =====================================================================
# Worker queue processes jobs ONE AT A TIME, not N concurrent threads
# =====================================================================

with tempfile.TemporaryDirectory() as tmp:
    p = new_plugin(authorized_networks=[])

    concurrency = {"current": 0, "max": 0}
    lock = threading.Lock()

    def slow_process_job(job):
        with lock:
            concurrency["current"] += 1
            concurrency["max"] = max(concurrency["max"], concurrency["current"])
        time.sleep(0.05)
        with lock:
            concurrency["current"] -= 1

    p._process_job = slow_process_job

    for i in range(5):
        p._queue.put({"filename": f"/tmp/x{i}.pcapng", "ssid": f"n{i}", "bssid": "AA:BB:CC:DD:EE:FF"})

    p._stop_event.clear()
    p._worker_thread = threading.Thread(target=p._worker_loop, daemon=True)
    p._worker_thread.start()
    drain(p, timeout=5.0)
    stop_plugin(p)

    check("worker thread processes jobs one at a time (max concurrency == 1)",
          concurrency["max"] == 1)

# =====================================================================
# Plain-pass-then-rule-pass sequencing
# =====================================================================

with tempfile.TemporaryDirectory() as tmp:
    export_dir = os.path.join(tmp, "exports")
    os.makedirs(export_dir)
    wordlist_folder = os.path.join(tmp, "wordlists")
    os.makedirs(wordlist_folder)
    wl = os.path.join(wordlist_folder, "list1.txt")
    with open(wl, "w") as f:
        f.write("password123\n")
    rules_file = os.path.join(tmp, "best64.rule")
    with open(rules_file, "w") as f:
        f.write(":\n")

    pcap = os.path.join(tmp, "cap.pcapng")
    open(pcap, "w").close()

    # --- plain pass finds nothing -> rule pass IS attempted ---
    p = new_plugin(authorized_networks=["AA:BB:CC:DD:EE:FF"], export_dir=export_dir,
                   wordlist_folder=wordlist_folder, rules_file=rules_file, run_local=True)
    p._convert = lambda pcap_path, hc_path, timeout: (open(hc_path, "w").write("x") or True)
    p._tool_exists = lambda name: True  # sandbox has neither hcxpcapngtool nor hashcat installed

    calls = []

    def fake_hashcat_none(hc_path, wordlist, timeout, rule_file=None):
        calls.append(rule_file)
        return None

    p._run_hashcat = fake_hashcat_none
    job = {"filename": pcap, "ssid": "MyLab", "bssid": "AA:BB:CC:DD:EE:FF"}
    p._process_job(job)
    check("plain pass finding nothing triggers a rule pass",
          any(rf == rules_file for rf in calls))
    check("plain pass is attempted before the rule pass",
          calls[0] is None)

    # --- plain pass finds something -> rule pass is NEVER attempted ---
    p2 = new_plugin(authorized_networks=["AA:BB:CC:DD:EE:FF"], export_dir=export_dir,
                    wordlist_folder=wordlist_folder, rules_file=rules_file, run_local=True)
    p2._convert = lambda pcap_path, hc_path, timeout: (open(hc_path, "w").write("x") or True)
    p2._tool_exists = lambda name: True

    calls2 = []

    def fake_hashcat_hit(hc_path, wordlist, timeout, rule_file=None):
        calls2.append(rule_file)
        return "foundpassword" if rule_file is None else None

    p2._run_hashcat = fake_hashcat_hit
    pcap2 = os.path.join(tmp, "cap2.pcapng")
    open(pcap2, "w").close()
    job2 = {"filename": pcap2, "ssid": "MyLab", "bssid": "AA:BB:CC:DD:EE:FF"}
    p2._process_job(job2)
    check("plain pass finding a password means the rule pass is never attempted",
          all(rf is None for rf in calls2))
    check("final result recorded as cracked", p2.history[-1]["result"] == "cracked")

# =====================================================================
# run_local=false stops after export, never invokes hashcat
# =====================================================================

with tempfile.TemporaryDirectory() as tmp:
    export_dir = os.path.join(tmp, "exports")
    os.makedirs(export_dir)
    p = new_plugin(authorized_networks=["AA:BB:CC:DD:EE:FF"], export_dir=export_dir,
                   run_local=False)
    p._convert = lambda pcap_path, hc_path, timeout: (open(hc_path, "w").write("x") or True)
    hashcat_mock = mock.Mock()
    p._run_hashcat = hashcat_mock

    pcap = os.path.join(tmp, "cap.pcapng")
    open(pcap, "w").close()
    job = {"filename": pcap, "ssid": "MyLab", "bssid": "AA:BB:CC:DD:EE:FF"}
    p._process_job(job)

    check("run_local=false never calls hashcat", hashcat_mock.call_count == 0)
    check("run_local=false records result as exported", p.history[-1]["result"] == "exported")

# =====================================================================
# max_wordlists_per_run capping
# =====================================================================

with tempfile.TemporaryDirectory() as tmp:
    export_dir = os.path.join(tmp, "exports")
    os.makedirs(export_dir)
    wordlist_folder = os.path.join(tmp, "wordlists")
    os.makedirs(wordlist_folder)
    for i in range(5):
        with open(os.path.join(wordlist_folder, f"list{i}.txt"), "w") as f:
            f.write("x\n")

    p = new_plugin(authorized_networks=["AA:BB:CC:DD:EE:FF"], export_dir=export_dir,
                   wordlist_folder=wordlist_folder, max_wordlists_per_run=2,
                   rules_file="/nonexistent/rules.rule", run_local=True)
    p._convert = lambda pcap_path, hc_path, timeout: (open(hc_path, "w").write("x") or True)
    p._tool_exists = lambda name: True

    attempted = []

    def fake_hashcat(hc_path, wordlist, timeout, rule_file=None):
        attempted.append(wordlist)
        return None

    p._run_hashcat = fake_hashcat
    pcap = os.path.join(tmp, "cap.pcapng")
    open(pcap, "w").close()
    job = {"filename": pcap, "ssid": "MyLab", "bssid": "AA:BB:CC:DD:EE:FF"}
    p._process_job(job)

    check("max_wordlists_per_run=2 caps the plain pass to exactly 2 attempts",
          len(attempted) == 2)

# =====================================================================
# Notification sibling lookup
# =====================================================================

class FakeApprise:
    def __init__(self):
        self.calls = []

    def _queue_notification(self, title, body, agent):
        self.calls.append((title, body, agent))


sibling = FakeApprise()
pwnagotchi.plugins.loaded["apprise_notify_ng"] = sibling
try:
    p = new_plugin(notify_enabled=True, apprise_plugin_name="apprise_notify_ng")
    p._agent = mock.Mock()
    p._notify("Title", "Body")
    check("notify_enabled + sibling found calls _queue_notification",
          len(sibling.calls) == 1 and sibling.calls[0][0] == "Title")

    try:
        p._log_notify_sibling_status()
        ok = True
    except Exception:
        ok = False
    check("_log_notify_sibling_status doesn't crash when the sibling IS found", ok)
finally:
    del pwnagotchi.plugins.loaded["apprise_notify_ng"]

p2 = new_plugin(notify_enabled=True, apprise_plugin_name="nonexistent_plugin_xyz")
p2._agent = mock.Mock()
try:
    p2._notify("Title", "Body")
    ok = True
except Exception:
    ok = False
check("notify_enabled + sibling NOT found doesn't crash", ok)

try:
    p2._log_notify_sibling_status()
    ok = True
except Exception:
    ok = False
check("_log_notify_sibling_status doesn't crash when the sibling is NOT found", ok)

p3 = new_plugin(notify_enabled=False, apprise_plugin_name="apprise_notify_ng")
p3._agent = mock.Mock()
p3._notify("Title", "Body")
check("notify_enabled=False never even looks up the sibling (no crash, no-op)", True)

# =====================================================================
# on_config_changed reloads the allowlist
# =====================================================================

with tempfile.TemporaryDirectory() as tmp:
    p = new_plugin(authorized_networks=[])
    pcap = os.path.join(tmp, "cap.pcapng")
    open(pcap, "w").close()
    ap = {"mac": "AA:BB:CC:DD:EE:FF", "hostname": "MyLab", "encryption": "WPA2"}
    p.on_handshake(mock.Mock(), pcap, ap, {})
    check("before config reload, unlisted target never enqueues", p._queue.qsize() == 0)

    p.options["authorized_networks"] = ["AA:BB:CC:DD:EE:FF"]
    p.on_config_changed({})

    pcap2 = os.path.join(tmp, "cap2.pcapng")
    open(pcap2, "w").close()
    p.on_handshake(mock.Mock(), pcap2, ap, {})
    check("after on_config_changed, the newly-authorized target enqueues a job",
          p._queue.qsize() == 1)

# =====================================================================
# on_unload stops the worker thread cleanly
# =====================================================================

p = make_plugin(authorized_networks=[])
check("worker thread is alive right after on_loaded",
      p._worker_thread is not None and p._worker_thread.is_alive())
p.on_unload(mock.MagicMock())
check("worker thread is stopped after on_unload",
      not p._worker_thread.is_alive())

# =====================================================================
# Webhook status page rendering, including HTML-escaping a malicious SSID
# =====================================================================

p = new_plugin()
p.history.append({
    "time": "2026-01-01T00:00:00",
    "ssid": "<script>alert(1)</script>",
    "bssid": "AA:BB:CC:DD:EE:FF",
    "result": "cracked",
    "detail": "password: hunter2",
})
page = p.on_webhook("", mock.Mock())
check("webhook status page escapes a malicious SSID in history",
      "&lt;script&gt;" in page)
check("webhook status page never contains the raw unescaped <script> tag",
      "<script>alert" not in page)
check("webhook status page shows queue depth and currently-processing section",
      "Queue depth" in page and "Currently processing" in page)

p._current_job = {"ssid": "<img src=x>", "bssid": "AA:BB:CC:DD:EE:FF"}
page2 = p._status_page()
check("webhook status page escapes a malicious SSID in the current job too",
      "&lt;img" in page2 and "<img src=x>" not in page2)


print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("All tests passed.")
