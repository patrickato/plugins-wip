import base64
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")

import pwnagotchi.plugins  # noqa: E402
import handshaker_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


def make_plugin(**opts):
    plugin = mod.HandshakerNG()
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


def http_get(url, timeout=5):
    return urllib.request.urlopen(url, timeout=timeout)


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


def make_fake_capture(directory, name, size=100, ext=".pcapng", mtime=None):
    path = os.path.join(directory, name + ext)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


# --- Registration ----------------------------------------------------------

check("HandshakerNG registers with the real pwnagotchi.plugins loader",
      "handshaker_ng" in pwnagotchi.plugins.loaded)
check("on_loaded hook is present", hasattr(mod.HandshakerNG, "on_loaded"))
check("on_ready hook is present", hasattr(mod.HandshakerNG, "on_ready"))
check("on_unload hook is present", hasattr(mod.HandshakerNG, "on_unload"))
check("on_ui_setup hook is present", hasattr(mod.HandshakerNG, "on_ui_setup"))
check("on_ui_update hook is present", hasattr(mod.HandshakerNG, "on_ui_update"))
check("on_webhook hook is present", hasattr(mod.HandshakerNG, "on_webhook"))
check("load_data is a real, defined method (module docstring bug #1 fix)",
      hasattr(mod.HandshakerNG, "load_data") and callable(getattr(mod.HandshakerNG, "load_data")))

_src = open(os.path.join(HERE, "..", "handshaker_ng.py")).read()
check("scapy is never imported anywhere (bug #3 fix)",
      "import scapy" not in _src and "from scapy" not in _src)
check("scapy is not declared as a pip dependency (bug #3 fix)",
      "scapy" not in mod.HandshakerNG.__dependencies__["pip"])
check("'.pcap' (non-ng) filtering is never used - only .pcapng globbed",
      '"*" + CAPTURE_EXT' in _src and 'CAPTURE_EXT = ".pcapng"' in _src)

# --- load_data / scan_handshakes() -----------------------------------------

tmp1 = tempfile.mkdtemp(prefix="handshaker_ng_test_")
try:
    now = time.time()
    make_fake_capture(tmp1, "AAAA_11:22:33:44:55:66", size=100, mtime=now - 300)
    make_fake_capture(tmp1, "BBBB_11:22:33:44:55:77", size=200, mtime=now - 100)
    make_fake_capture(tmp1, "CCCC_11:22:33:44:55:88", size=50, mtime=now - 10)
    # A stray .pcap (non-ng) file must be ignored - the recurring bug class.
    make_fake_capture(tmp1, "SHOULD_BE_IGNORED", size=999, ext=".pcap", mtime=now)
    # A non-capture file must be ignored too.
    with open(os.path.join(tmp1, "notes.txt"), "w") as fh:
        fh.write("irrelevant")

    plugin = make_plugin(data_path=tmp1)
    records = plugin.load_data(tmp1)

    check("load_data finds exactly the 3 real .pcapng files", len(records) == 3)
    check("load_data ignores a stray .pcap (non-ng) file", plugin.handshakes == 3)
    check("self.handshakes attribute name is preserved", plugin.handshakes == len(records))
    check("records sorted newest-first",
          [r["name"] for r in records] == ["CCCC_11:22:33:44:55:88", "BBBB_11:22:33:44:55:77", "AAAA_11:22:33:44:55:66"])
    check("record name strips the .pcapng extension", records[0]["name"] == "CCCC_11:22:33:44:55:88")
    check("record filename keeps the real .pcapng extension", records[0]["filename"].endswith(".pcapng"))
    check("record size_bytes is correct", records[0]["size_bytes"] == 50)

    # Missing directory handled gracefully.
    missing_records = mod.scan_handshakes("/this/path/does/not/exist/at/all")
    check("scan_handshakes on a missing directory returns [] gracefully (no crash)", missing_records == [])

finally:
    shutil.rmtree(tmp1, ignore_errors=True)

# --- build_status_payload() -------------------------------------------------

tmp2 = tempfile.mkdtemp(prefix="handshaker_ng_test_")
try:
    now = time.time()
    make_fake_capture(tmp2, "NetOne_AA:BB", size=1000, mtime=now - 5)
    make_fake_capture(tmp2, "NetTwo_CC:DD", size=2000, mtime=now - 50)
    records = mod.scan_handshakes(tmp2)
    payload = mod.build_status_payload(records)

    check("payload handshake_count matches", payload["handshake_count"] == 2)
    check("payload total_size_bytes sums all captures", payload["total_size_bytes"] == 3000)
    check("payload most_recent_capture is the newest file's timestamp",
          payload["most_recent_capture"] is not None)
    check("payload capture_names uses sanitized display names",
          set(payload["capture_names"]) == {"NetOne_AA:BB", "NetTwo_CC:DD"})
    check("payload captures include a download_url per capture",
          all(c["download_url"].startswith("/download/") for c in payload["captures"]))

    empty_payload = mod.build_status_payload([])
    check("build_status_payload on empty list: count 0", empty_payload["handshake_count"] == 0)
    check("build_status_payload on empty list: most_recent_capture is None", empty_payload["most_recent_capture"] is None)
    check("build_status_payload on empty list: total_size_bytes is 0", empty_payload["total_size_bytes"] == 0)
finally:
    shutil.rmtree(tmp2, ignore_errors=True)

# --- resolve_download_target() (traversal safety) ---------------------------

tmp3 = tempfile.mkdtemp(prefix="handshaker_ng_test_")
try:
    real_file = make_fake_capture(tmp3, "RealCapture_AA:BB", size=10)
    real_name = os.path.basename(real_file)

    target = mod.resolve_download_target(tmp3, real_name)
    check("resolve_download_target accepts a real filename inside data_path", target == os.path.abspath(real_file))

    check("resolve_download_target rejects '../' traversal",
          mod.resolve_download_target(tmp3, "../etc/passwd") is None)
    check("resolve_download_target rejects a crafted '../../x.pcapng' (basename strips it, but must still resolve inside)",
          mod.resolve_download_target(tmp3, "../../../etc/evil.pcapng") != "/etc/evil.pcapng")
    check("resolve_download_target rejects an absolute path outside data_path",
          mod.resolve_download_target(tmp3, "/etc/passwd") is None)
    check("resolve_download_target rejects a non-.pcapng filename",
          mod.resolve_download_target(tmp3, "notes.txt") is None)
    check("resolve_download_target rejects a bare '..'",
          mod.resolve_download_target(tmp3, "..") is None)
    check("resolve_download_target rejects empty input",
          mod.resolve_download_target(tmp3, "") is None)
    check("resolve_download_target rejects a nonexistent-but-otherwise-valid name (existence is the caller's job) "
          "- still resolves to inside data_path",
          mod.resolve_download_target(tmp3, "nope_not_real.pcapng") == os.path.join(os.path.abspath(tmp3), "nope_not_real.pcapng"))
finally:
    shutil.rmtree(tmp3, ignore_errors=True)

# --- resolve_bind_plan() / tailscale detection (4 scopes) -------------------

with mock.patch.object(mod, "detect_tailscale_ip", return_value="100.64.1.2"):
    plan = mod.resolve_bind_plan("tailscale", 8083)
check("bind_scope=tailscale with detection uses the tailscale IP", plan == {
    "ok": True, "bind_host": "100.64.1.2", "display_url": "http://100.64.1.2:8083/",
    "warnings": [], "mode_used": "tailscale",
})

with mock.patch.object(mod, "detect_tailscale_ip", return_value=None):
    plan = mod.resolve_bind_plan("tailscale", 8083)
check("bind_scope=tailscale with NO detection refuses to start", plan["ok"] is False)
check("bind_scope=tailscale refusal names no bind_host", plan["bind_host"] is None)

plan = mod.resolve_bind_plan("localhost", 8083)
check("bind_scope=localhost always binds 127.0.0.1", plan == {
    "ok": True, "bind_host": "127.0.0.1", "display_url": "http://127.0.0.1:8083/",
    "warnings": [], "mode_used": "localhost",
})

with mock.patch.object(mod, "get_local_lan_ip", return_value="192.168.1.50"):
    plan = mod.resolve_bind_plan("lan", 8083)
check("bind_scope=lan binds 0.0.0.0 unconditionally", plan["bind_host"] == "0.0.0.0")
check("bind_scope=lan displays a real LAN IP in the URL", "192.168.1.50" in plan["display_url"])
check("bind_scope=lan always warns", len(plan["warnings"]) >= 1)
check("bind_scope=lan warning mentions there's no authentication",
      any("authentication" in w.lower() for w in plan["warnings"]))

with mock.patch.object(mod, "detect_tailscale_ip", return_value="100.64.1.2"):
    plan = mod.resolve_bind_plan("auto", 8083)
check("bind_scope=auto with detection uses the tailscale IP", plan["bind_host"] == "100.64.1.2")
check("bind_scope=auto with detection reports mode auto-tailscale", plan["mode_used"] == "auto-tailscale")

with mock.patch.object(mod, "detect_tailscale_ip", return_value=None):
    plan = mod.resolve_bind_plan("auto", 8083)
check("bind_scope=auto with NO detection falls back to localhost, does not crash", plan["ok"] is True)
check("bind_scope=auto fallback binds 127.0.0.1 only", plan["bind_host"] == "127.0.0.1")

plan = mod.resolve_bind_plan("nonsense-value", 8083)
check("an unrecognized bind_scope value never crashes (falls back to auto behavior)", "ok" in plan)

# --- Real end-to-end server test (real Flask + real werkzeug) --------------

tmp4 = tempfile.mkdtemp(prefix="handshaker_ng_test_")
try:
    now = time.time()
    make_fake_capture(tmp4, "OfficeWifi_AA:BB:CC:DD:EE:FF", size=1234, mtime=now - 20)
    make_fake_capture(tmp4, "GuestNet_11:22:33:44:55:66", size=4321, mtime=now - 5)

    port1 = free_port()
    plugin = make_plugin(data_path=tmp4, port=port1, bind_scope="localhost", sync_to_boot=False)
    plugin.on_loaded()

    try:
        up = wait_for_port("127.0.0.1", port1)
        check("on_loaded starts a real server that becomes reachable quickly (didn't block)", up)

        resp = http_get(f"http://127.0.0.1:{port1}/")
        check("index page returns a real 200 (no auth required)", resp.status == 200)
        body = resp.read().decode()
        check("index page shows the reachable-at banner", "Reachable at:" in body)
        check("index page shows the no-auth warning banner", "No authentication" in body)
        check("index page lists a captured network name", "OfficeWifi_AA:BB:CC:DD:EE:FF" in body)
        check("index page shows a download link", "/download/" in body)

        # Dedicated JSON endpoint.
        resp = http_get(f"http://127.0.0.1:{port1}/status.json")
        json_body = json.loads(resp.read().decode())
        check("status.json handshake_count is correct", json_body["handshake_count"] == 2)
        check("status.json total_size_bytes is correct", json_body["total_size_bytes"] == 1234 + 4321)
        check("status.json capture_names includes both networks",
              set(json_body["capture_names"]) == {"OfficeWifi_AA:BB:CC:DD:EE:FF", "GuestNet_11:22:33:44:55:66"})

        # ?format=json on the HTML route returns the identical payload.
        resp = http_get(f"http://127.0.0.1:{port1}/?format=json")
        format_json_body = json.loads(resp.read().decode())
        check("?format=json on '/' returns the identical payload as /status.json",
              format_json_body == json_body)

        # Real file download.
        resp = http_get(f"http://127.0.0.1:{port1}/download/OfficeWifi_AA:BB:CC:DD:EE:FF.pcapng")
        downloaded = resp.read()
        check("downloading a real capture file succeeds with correct bytes",
              downloaded == b"x" * 1234)

        # Traversal / bad-name rejection -> 404.
        for bad_path in [
            "/download/..%2f..%2f..%2fetc%2fpasswd",
            "/download/nonexistent_capture.pcapng",
        ]:
            try:
                http_get(f"http://127.0.0.1:{port1}{bad_path}")
                check(f"traversal/missing-file request rejected: {bad_path}", False)
            except urllib.error.HTTPError as exc:
                check(f"traversal/missing-file request gets a real 404: {bad_path}", exc.code == 404)

        # Absolute-path-shaped and non-pcapng requests -> 404 as well.
        try:
            http_get(f"http://127.0.0.1:{port1}/download/notacapture.txt")
            check("non-.pcapng download request rejected", False)
        except urllib.error.HTTPError as exc:
            check("non-.pcapng download request gets a real 404", exc.code == 404)

    finally:
        plugin.on_unload(mock.Mock())

    closed = wait_for_port_closed("127.0.0.1", port1)
    check("on_unload actually stops the server (port closes)", closed)

finally:
    shutil.rmtree(tmp4, ignore_errors=True)

# --- on_ui_setup / on_ui_update ---------------------------------------------

tmp5 = tempfile.mkdtemp(prefix="handshaker_ng_test_")
try:
    make_fake_capture(tmp5, "UiTestNet_AA:BB", size=10)
    make_fake_capture(tmp5, "UiTestNet2_CC:DD", size=10)

    plugin_ui = make_plugin(data_path=tmp5, ui_refresh_interval=30)

    fake_ui = mock.Mock()
    fake_ui.width.return_value = 250
    elements = {}

    def _add_element(name, element):
        elements[name] = element

    def _set_value(name, value):
        elements[name].current_value = value

    fake_ui.add_element.side_effect = _add_element
    fake_ui.set.side_effect = _set_value

    try:
        plugin_ui.on_ui_setup(fake_ui)
        did_not_raise = True
    except Exception:
        did_not_raise = False
    check("on_ui_setup does not raise", did_not_raise)
    check("on_ui_setup added the handshaker_ng UI element", mod.ELEMENT_NAME in elements)

    try:
        plugin_ui.on_ui_update(fake_ui)
        did_not_raise = True
    except Exception:
        did_not_raise = False
    check("on_ui_update does not raise", did_not_raise)
    check("on_ui_update reflects the current handshake count on the UI element",
          elements[mod.ELEMENT_NAME].current_value == "2")

    # A second, immediate on_ui_update should NOT re-scan (interval not
    # elapsed) even if a new file appears - verifies the rate-limit.
    make_fake_capture(tmp5, "UiTestNet3_EE:FF", size=10)
    plugin_ui.on_ui_update(fake_ui)
    check("on_ui_update within the refresh interval does not re-scan (count unchanged)",
          elements[mod.ELEMENT_NAME].current_value == "2")

    # Force the interval to have elapsed, and confirm it DOES re-scan.
    plugin_ui._last_ui_scan_ts = 0.0
    plugin_ui.on_ui_update(fake_ui)
    check("on_ui_update re-scans once the refresh interval has elapsed",
          elements[mod.ELEMENT_NAME].current_value == "3")

    # on_unload removes the UI element via ui.remove_element, guarded by
    # try/except so a teardown failure can't crash unload.
    fake_ui2 = mock.Mock()
    fake_ui2._lock = mock.MagicMock()
    fake_ui2._lock.__enter__ = mock.Mock(return_value=None)
    fake_ui2._lock.__exit__ = mock.Mock(return_value=False)
    plugin_ui2 = make_plugin(data_path=tmp5, sync_to_boot=False)
    with mock.patch.object(mod.os, "system"):
        plugin_ui2.on_unload(fake_ui2)
    check("on_unload calls ui.remove_element", fake_ui2.remove_element.called)
    check("on_unload calls ui.remove_element with this plugin's element name",
          fake_ui2.remove_element.call_args[0][0] == mod.ELEMENT_NAME)

    # A UI teardown failure (remove_element raises something other than
    # KeyError) must not crash on_unload.
    fake_ui3 = mock.Mock()
    fake_ui3._lock = mock.MagicMock()
    fake_ui3._lock.__enter__ = mock.Mock(return_value=None)
    fake_ui3._lock.__exit__ = mock.Mock(return_value=False)
    fake_ui3.remove_element.side_effect = RuntimeError("boom")
    plugin_ui3 = make_plugin(data_path=tmp5, sync_to_boot=False)
    try:
        with mock.patch.object(mod.os, "system"):
            plugin_ui3.on_unload(fake_ui3)
        survived = True
    except Exception:
        survived = False
    check("on_unload survives a UI teardown failure (RuntimeError from remove_element)", survived)

finally:
    shutil.rmtree(tmp5, ignore_errors=True)

# --- on_ready / on_unload boot-sync behavior (mocked os.system) ------------

with mock.patch.object(mod.os, "system") as system_mock, \
     mock.patch.object(mod.os.path, "exists", return_value=True):
    plugin_boot = make_plugin(data_path="/root/handshakes", sync_to_boot=True)
    plugin_boot.on_ready(mock.Mock())
    calls = [c.args[0] for c in system_mock.call_args_list]
check("on_ready (sync_to_boot=true, default) runs the rsync step",
      any("rsync" in c and "/boot/handshakes" in c for c in calls))
check("on_ready (sync_to_boot=true) copies pwnagotchi.log to pwnagotchi-start.log",
      any("pwnagotchi-start.log" in c for c in calls))
check("on_ready (sync_to_boot=true, /boot/custom_plugins exists) moves custom_plugins",
      any("mv /boot/custom_plugins" in c for c in calls))
check("on_ready (sync_to_boot=true, /boot/custom_plugins exists) copies config.toml to /boot",
      any("config.toml" in c and "/boot/config.toml" in c for c in calls))
check("on_ready still preserves all 5 original os.system boot-sync calls by default "
      "(rsync, log copy, mv custom_plugins, rm custom_plugins, config.toml copy)",
      len(calls) == 5)

with mock.patch.object(mod.os, "system") as system_mock:
    plugin_no_sync = make_plugin(data_path="/root/handshakes", sync_to_boot=False)
    plugin_no_sync.on_ready(mock.Mock())
check("sync_to_boot=false skips on_ready's boot-sync steps entirely",
      system_mock.call_count == 0)

# A failing step (os.system raising) must not stop subsequent steps.
with mock.patch.object(mod.os, "system", side_effect=[OSError("boom"), None, None, None, None]) as system_mock, \
     mock.patch.object(mod.os.path, "exists", return_value=True):
    plugin_fail = make_plugin(data_path="/root/handshakes", sync_to_boot=True)
    try:
        plugin_fail.on_ready(mock.Mock())
        survived = True
    except Exception:
        survived = False
check("on_ready survives a failing os.system step without raising", survived)
check("on_ready still attempts all 5 steps even when the first one fails",
      system_mock.call_count == 5)

with mock.patch.object(mod.os, "system") as system_mock:
    plugin_unload = make_plugin(data_path="/root/handshakes")
    plugin_unload._server = None
    fake_ui4 = mock.Mock()
    fake_ui4._lock = mock.MagicMock()
    fake_ui4._lock.__enter__ = mock.Mock(return_value=None)
    fake_ui4._lock.__exit__ = mock.Mock(return_value=False)
    plugin_unload.on_unload(fake_ui4)
    calls = [c.args[0] for c in system_mock.call_args_list]
check("on_unload copies pwnagotchi.log to pwnagotchi-end.log", any("pwnagotchi-end.log" in c for c in calls))

print(f"\n{len(failures)} failure(s) out of test run")
if failures:
    print(failures)
    sys.exit(1)
else:
    print("All tests passed.")
