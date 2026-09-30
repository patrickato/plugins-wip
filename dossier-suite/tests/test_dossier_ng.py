import sys
import os
import json
import time
import tempfile
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")

import pwnagotchi.plugins  # noqa: E402
import dossier_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


tmpdir = tempfile.mkdtemp()
_counter = [0]


def p(name=None):
    if name is None:
        _counter[0] += 1
        name = f"auto{_counter[0]}"
    return os.path.join(tmpdir, name)


def make_plugin(**opts):
    plugin = mod.DossierNG()
    plugin.options = dict(opts)
    return plugin


def missing_paths(prefix):
    return dict(
        crack_house_potfile_path=p(f"{prefix}_missing.potfile"),
        timer_csv_path=p(f"{prefix}_missing.csv"),
        gps_tagger_dir_path=p(f"{prefix}_missing_dir"),
        bluetooth_device_table_path=p(f"{prefix}_missing_bt.json"),
    )


class FakeUI:
    def __init__(self, height=122):
        self.elements = {}
        self.values = {}
        self._lock = mock.MagicMock()
        self._lock.__enter__ = mock.Mock(return_value=None)
        self._lock.__exit__ = mock.Mock(return_value=False)
        self._height = height

    def add_element(self, name, el):
        self.elements[name] = el

    def remove_element(self, name):
        if name not in self.elements:
            raise KeyError(name)
        del self.elements[name]

    def set(self, name, value):
        self.values[name] = value

    def height(self):
        return self._height


class FakeRequest:
    pass


# --- Registration ------------------------------------------------------

check(
    "DossierNG registers with the real pwnagotchi.plugins loader",
    "dossier_ng" in pwnagotchi.plugins.loaded,
)

for real_hook in ("on_loaded", "on_ready" if hasattr(mod.DossierNG, "on_ready") else "on_loaded",
                  "on_unload", "on_ui_setup", "on_ui_update", "on_webhook"):
    check(f"real hook {real_hook} is present", hasattr(mod.DossierNG, real_hook))

# --- _opt() falls back to DEFAULTS (loader never merges __defaults__) --

plugin = make_plugin()
check(
    "_opt() returns the real default for a key entirely absent from options",
    plugin._opt("refresh_interval_seconds") == mod.DEFAULTS["refresh_interval_seconds"],
)
check(
    "_opt() never raises with completely empty options",
    plugin._opt("crack_house_potfile_path") == mod.DEFAULTS["crack_house_potfile_path"],
)

# =========================================================================
# Fixture builders for the four real source formats
# =========================================================================


def write_potfile(path, entries):
    with open(path, "w") as f:
        for line in entries:
            f.write(line + "\n")


def write_timer_csv(path, rows):
    import csv

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=mod_fieldnames())
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def mod_fieldnames():
    return [
        "timestamp",
        "network",
        "time_to_deauth",
        "time_to_handshake",
        "time_between_deauth_and_handshake",
    ]


def write_gps_tag(gps_dir, hostname, mac, lat, lon, tagged_at=None, update_type="WU"):
    os.makedirs(gps_dir, exist_ok=True)
    fname = f"pn_ap_{hostname}_{mac.replace(':', '')}.json"
    record = {
        "ap": {"hostname": hostname, "mac": mac},
        "gps": {"Latitude": lat, "Longitude": lon} if lat is not None else None,
        "update_type": update_type,
        "tagged_at": tagged_at if tagged_at is not None else time.time(),
    }
    with open(os.path.join(gps_dir, fname), "w") as f:
        json.dump(record, f)
    return os.path.join(gps_dir, fname)


def write_bt_table(path, devices):
    with open(path, "w") as f:
        json.dump(devices, f)


# =========================================================================
# 1. Assembly with all four sources present and matching by hostname
# =========================================================================

potfile1 = p("all_sources.potfile")
write_potfile(potfile1, ["AllSourcesLab:hunter2"])

csv1 = p("all_sources.csv")
write_timer_csv(csv1, [
    {
        "timestamp": "2026-01-01T10:00:00",
        "network": "AllSourcesLab",
        "time_to_deauth": "1.0",
        "time_to_handshake": "2.0",
        "time_between_deauth_and_handshake": "1.0",
    },
])

gps_dir1 = p("all_sources_gps")
write_gps_tag(gps_dir1, "AllSourcesLab", "AA:BB:CC:DD:EE:01", 40.0, -70.0, tagged_at=1700000000)

bt1 = p("all_sources_bt.json")
write_bt_table(bt1, {
    "11:22:33:44:55:66": {
        "mac": "11:22:33:44:55:66",
        "name": "SomePhone",
        "vendor": "Apple, Inc.",
        "is_tracker": False,
        "tracker_type": None,
        "rssi": -50,
        "last_seen": "2026-01-01T10:01:00+00:00",
        "correlated_networks": ["AllSourcesLab"],
        "any_cracked": True,
        "correlated_location": {"lat": 40.0, "lon": -70.0},
    },
})

plugin = make_plugin(
    crack_house_potfile_path=potfile1,
    timer_csv_path=csv1,
    gps_tagger_dir_path=gps_dir1,
    bluetooth_device_table_path=bt1,
)
plugin.on_loaded()
d = plugin._dossiers.get("allsourceslab")
check("assembly finds the hostname (case-normalized key)", d is not None)
check("hostname display preserves original casing", d["hostname"] == "AllSourcesLab")
check("cracked_password populated from potfile", d["cracked_password"] == "hunter2")
check("gps populated from gps-tagger fixture", d["gps"] is not None and d["gps"]["lat"] == 40.0)
check("gps includes a Google Maps URL in the repo's established format",
      d["gps"]["maps_url"] == "https://www.google.com/maps/search/?api=1&query=40.0,-70.0")
check("timing populated from timer CSV fixture", d["timing"] is not None and d["timing"]["time_to_handshake"] == "2.0")
check("nearby_bluetooth_devices populated by inverting correlated_networks",
      len(d["nearby_bluetooth_devices"]) == 1 and d["nearby_bluetooth_devices"][0]["mac"] == "11:22:33:44:55:66")
check("completeness is 4/4 when all sources have data", d["completeness"] == 4)

# =========================================================================
# 2. Each of the four sources individually MISSING
# =========================================================================

# Potfile missing - other three still populate
opts = missing_paths("nopot")
csv2 = p("nopot.csv")
write_timer_csv(csv2, [{
    "timestamp": "2026-01-01T10:00:00", "network": "NoPotLab",
    "time_to_deauth": "1.0", "time_to_handshake": "2.0",
    "time_between_deauth_and_handshake": "1.0",
}])
gps_dir2 = p("nopot_gps")
write_gps_tag(gps_dir2, "NoPotLab", "AA:BB:CC:DD:EE:02", 1.0, 2.0)
opts["timer_csv_path"] = csv2
opts["gps_tagger_dir_path"] = gps_dir2
plugin = make_plugin(**opts)
try:
    plugin.on_loaded()
    ok = True
except Exception:
    ok = False
check("missing crack-house potfile doesn't crash on_loaded", ok)
d = plugin._dossiers.get("nopotlab")
check("timer and gps still populate when potfile is missing", d is not None and d["timing"] is not None and d["gps"] is not None)
check("cracked_password is None when potfile is missing", d["cracked_password"] is None)

# Timer CSV missing - other three still populate
opts = missing_paths("nocsv")
potfile3 = p("nocsv.potfile")
write_potfile(potfile3, ["NoCsvLab:pass123"])
gps_dir3 = p("nocsv_gps")
write_gps_tag(gps_dir3, "NoCsvLab", "AA:BB:CC:DD:EE:03", 3.0, 4.0)
opts["crack_house_potfile_path"] = potfile3
opts["gps_tagger_dir_path"] = gps_dir3
plugin = make_plugin(**opts)
try:
    plugin.on_loaded()
    ok = True
except Exception:
    ok = False
check("missing timer CSV doesn't crash on_loaded", ok)
d = plugin._dossiers.get("nocsvlab")
check("potfile and gps still populate when timer CSV is missing", d is not None and d["cracked_password"] == "pass123" and d["gps"] is not None)
check("timing is None when timer CSV is missing", d["timing"] is None)

# GPS dir missing - other three still populate
opts = missing_paths("nogps")
potfile4 = p("nogps.potfile")
write_potfile(potfile4, ["NoGpsLab:pw"])
csv4 = p("nogps.csv")
write_timer_csv(csv4, [{
    "timestamp": "2026-01-01T10:00:00", "network": "NoGpsLab",
    "time_to_deauth": "1.0", "time_to_handshake": "2.0",
    "time_between_deauth_and_handshake": "1.0",
}])
opts["crack_house_potfile_path"] = potfile4
opts["timer_csv_path"] = csv4
plugin = make_plugin(**opts)
try:
    plugin.on_loaded()
    ok = True
except Exception:
    ok = False
check("missing gps-tagger directory doesn't crash on_loaded", ok)
d = plugin._dossiers.get("nogpslab")
check("potfile and timer still populate when gps directory is missing", d is not None and d["cracked_password"] == "pw" and d["timing"] is not None)
check("gps is None when gps-tagger directory is missing", d["gps"] is None)

# Bluetooth device table missing - other three still populate
opts = missing_paths("nobt")
potfile5 = p("nobt.potfile")
write_potfile(potfile5, ["NoBtLab:pw2"])
opts["crack_house_potfile_path"] = potfile5
plugin = make_plugin(**opts)
try:
    plugin.on_loaded()
    ok = True
except Exception:
    ok = False
check("missing bluetooth device table doesn't crash on_loaded", ok)
d = plugin._dossiers.get("nobtlab")
check("potfile still populates when bluetooth device table is missing", d is not None and d["cracked_password"] == "pw2")
check("nearby_bluetooth_devices is empty when bluetooth device table is missing", d["nearby_bluetooth_devices"] == [])

# =========================================================================
# 3. Malformed/corrupt file for each of the four sources
# =========================================================================

# Malformed potfile line (no colon) alongside a good one
potfile6 = p("malformed.potfile")
write_potfile(potfile6, ["ThisLineHasNoColonAtAll", "GoodLab:goodpass"])
plugin = make_plugin(crack_house_potfile_path=potfile6, **{k: v for k, v in missing_paths("mp6").items() if k != "crack_house_potfile_path"})
try:
    plugin.on_loaded()
    ok = True
except Exception:
    ok = False
check("malformed potfile line doesn't crash on_loaded", ok)
check("a good potfile line still parses despite a malformed sibling line",
      plugin._dossiers.get("goodlab", {}).get("cracked_password") == "goodpass")

# Malformed CSV row (missing network field)
csv7 = p("malformed.csv")
import csv as _csv
with open(csv7, "w", newline="") as f:
    f.write("timestamp,network,time_to_deauth,time_to_handshake,time_between_deauth_and_handshake\n")
    f.write("2026-01-01T10:00:00,,1.0,2.0,1.0\n")  # blank network
    f.write("2026-01-01T10:00:00,GoodTimerLab,1.0,2.0,1.0\n")
plugin = make_plugin(timer_csv_path=csv7, **{k: v for k, v in missing_paths("mp7").items() if k != "timer_csv_path"})
try:
    plugin.on_loaded()
    ok = True
except Exception:
    ok = False
check("malformed (blank-network) CSV row doesn't crash on_loaded", ok)
check("a good CSV row still parses despite a malformed sibling row",
      plugin._dossiers.get("goodtimerlab", {}).get("timing") is not None)

# Malformed GPS tag file (not valid JSON) alongside a good one
gps_dir8 = p("malformed_gps")
os.makedirs(gps_dir8, exist_ok=True)
with open(os.path.join(gps_dir8, "pn_ap_Broken_aabbcc.json"), "w") as f:
    f.write("{not valid json::")
write_gps_tag(gps_dir8, "GoodGpsLab", "AA:BB:CC:DD:EE:08", 9.0, 10.0)
plugin = make_plugin(gps_tagger_dir_path=gps_dir8, **{k: v for k, v in missing_paths("mp8").items() if k != "gps_tagger_dir_path"})
try:
    plugin.on_loaded()
    ok = True
except Exception:
    ok = False
check("a corrupt gps-tagger JSON file doesn't crash on_loaded", ok)
check("a good gps-tagger JSON file still parses despite a corrupt sibling file",
      plugin._dossiers.get("goodgpslab", {}).get("gps") is not None)

# Malformed bluetooth device table (not a JSON object at all)
bt9 = p("malformed_bt.json")
with open(bt9, "w") as f:
    json.dump(["this", "is", "a", "list", "not", "a", "dict"], f)
plugin = make_plugin(bluetooth_device_table_path=bt9, **{k: v for k, v in missing_paths("mp9").items() if k != "bluetooth_device_table_path"})
try:
    plugin.on_loaded()
    ok = True
except Exception:
    ok = False
check("a bluetooth device table that isn't a JSON object doesn't crash on_loaded", ok)
check("dossiers is empty (not crashed) when the bluetooth table is malformed", plugin._dossiers == {})

# =========================================================================
# 4. Case-insensitive hostname matching across all four sources
# =========================================================================

potfile10 = p("case.potfile")
write_potfile(potfile10, ["MyLab:casepass"])
csv10 = p("case.csv")
write_timer_csv(csv10, [{
    "timestamp": "2026-01-01T10:00:00", "network": "mylab",
    "time_to_deauth": "1.0", "time_to_handshake": "2.0",
    "time_between_deauth_and_handshake": "1.0",
}])
gps_dir10 = p("case_gps")
write_gps_tag(gps_dir10, "MYLAB", "AA:BB:CC:DD:EE:10", 5.0, 6.0)
bt10 = p("case_bt.json")
write_bt_table(bt10, {
    "22:22:22:22:22:22": {
        "mac": "22:22:22:22:22:22", "name": "X", "vendor": "V",
        "is_tracker": False, "tracker_type": None, "rssi": -60,
        "last_seen": "now", "correlated_networks": ["MyLab"],
    },
})
plugin = make_plugin(
    crack_house_potfile_path=potfile10,
    timer_csv_path=csv10,
    gps_tagger_dir_path=gps_dir10,
    bluetooth_device_table_path=bt10,
)
plugin.on_loaded()
check("exactly one dossier exists (not four) despite differing casing across sources", len(plugin._dossiers) == 1)
d = plugin._dossiers.get("mylab")
check("case-insensitive match merges all four sources into one dossier", d is not None and d["completeness"] == 4)

# =========================================================================
# 5. "Most recent" selection - GPS (by mtime) and timer CSV (by timestamp)
# =========================================================================

gps_dir11 = p("mtime_gps")
os.makedirs(gps_dir11, exist_ok=True)
old_file = write_gps_tag(gps_dir11, "MtimeLab", "AA:BB:CC:DD:EE:11", 1.0, 1.0, tagged_at=1)
time.sleep(0.05)
new_file = write_gps_tag(gps_dir11, "MtimeLab", "AA:BB:CC:DD:EE:12", 2.0, 2.0, tagged_at=2)
# Force distinguishable mtimes regardless of filesystem timestamp resolution.
os.utime(old_file, (time.time() - 100, time.time() - 100))
os.utime(new_file, (time.time(), time.time()))
plugin = make_plugin(gps_tagger_dir_path=gps_dir11, **{k: v for k, v in missing_paths("mp11").items() if k != "gps_tagger_dir_path"})
plugin.on_loaded()
d = plugin._dossiers.get("mtimelab")
check("GPS 'most recent' selection uses the most-recently-modified file", d is not None and d["gps"]["lat"] == 2.0)

csv12 = p("timerhist.csv")
write_timer_csv(csv12, [
    {"timestamp": "2026-01-01T09:00:00", "network": "TimerHistLab",
     "time_to_deauth": "1.0", "time_to_handshake": "9.0",
     "time_between_deauth_and_handshake": "1.0"},
    {"timestamp": "2026-01-01T10:00:00", "network": "TimerHistLab",
     "time_to_deauth": "2.0", "time_to_handshake": "3.0",
     "time_between_deauth_and_handshake": "1.0"},
])
plugin = make_plugin(timer_csv_path=csv12, **{k: v for k, v in missing_paths("mp12").items() if k != "timer_csv_path"})
plugin.on_loaded()
d = plugin._dossiers.get("timerhistlab")
check("timer 'most recent' selection uses the latest timestamp's row", d["timing"]["time_to_handshake"] == "3.0")
check("historical_row_count reports 2 when more than one row exists for a hostname",
      d["timing"]["historical_row_count"] == 2)

# =========================================================================
# 6. Tracker devices sorted to the front of nearby_bluetooth_devices
# =========================================================================

bt13 = p("tracker_order_bt.json")
write_bt_table(bt13, {
    "AA:AA:AA:AA:AA:AA": {  # non-tracker, recorded first in the dict
        "mac": "AA:AA:AA:AA:AA:AA", "name": "NotATracker", "vendor": "V",
        "is_tracker": False, "tracker_type": None, "rssi": -50,
        "last_seen": "t1", "correlated_networks": ["TrackerOrderLab"],
    },
    "BB:BB:BB:BB:BB:BB": {  # tracker, recorded second in the dict
        "mac": "BB:BB:BB:BB:BB:BB", "name": "SneakyTag", "vendor": "V",
        "is_tracker": True, "tracker_type": "airtag", "rssi": -40,
        "last_seen": "t2", "correlated_networks": ["TrackerOrderLab"],
    },
})
plugin = make_plugin(bluetooth_device_table_path=bt13, **{k: v for k, v in missing_paths("mp13").items() if k != "bluetooth_device_table_path"})
plugin.on_loaded()
d = plugin._dossiers.get("trackerorderlab")
check("tracker device is sorted to the front despite being recorded second in the source table",
      d["nearby_bluetooth_devices"][0]["mac"] == "BB:BB:BB:BB:BB:BB")
check("non-tracker device is second", d["nearby_bluetooth_devices"][1]["mac"] == "AA:AA:AA:AA:AA:AA")

# =========================================================================
# 7. completeness scoring
# =========================================================================

potfile14 = p("completeness0.potfile")  # never written -> missing file
plugin = make_plugin(**missing_paths("comp0"))
plugin.on_loaded()
check("completeness is 0/4 when no sources exist at all", plugin._dossiers == {})

potfile15 = p("completeness1.potfile")
write_potfile(potfile15, ["OnlyPasswordLab:onlypw"])
plugin = make_plugin(crack_house_potfile_path=potfile15, **{k: v for k, v in missing_paths("comp1").items() if k != "crack_house_potfile_path"})
plugin.on_loaded()
d = plugin._dossiers.get("onlypasswordlab")
check("completeness is 1/4 when only one source has data", d["completeness"] == 1)

# (completeness == 4 already checked in test 1 above)

# =========================================================================
# 8. on_webhook: index page, detail page, export, 404s, HTML-escaping
# =========================================================================

potfile16 = p("webhook.potfile")
write_potfile(potfile16, ["WebhookLab:webhookpw"])
bt16 = p("webhook_bt.json")
write_bt_table(bt16, {
    "CC:CC:CC:CC:CC:CC": {
        "mac": "CC:CC:CC:CC:CC:CC",
        "name": "<script>evil_device</script>",
        "vendor": "V", "is_tracker": False, "tracker_type": None,
        "rssi": -50, "last_seen": "t",
        "correlated_networks": ["WebhookLab"],
    },
})
plugin = make_plugin(
    crack_house_potfile_path=potfile16,
    bluetooth_device_table_path=bt16,
    **{k: v for k, v in missing_paths("wh").items() if k not in ("crack_house_potfile_path", "bluetooth_device_table_path")},
)
plugin.on_loaded()

index_html = plugin.on_webhook("", FakeRequest())
check("index page renders and includes the known hostname", "WebhookLab" in index_html)
check("index page includes a link to the target's detail page", "target/WebhookLab" in index_html)

detail_html = plugin.on_webhook("target/WebhookLab", FakeRequest())
check("detail page includes the cracked password", "webhookpw" in detail_html)
check(
    "detail page HTML-escapes a malicious bluetooth device name",
    "<script>evil_device</script>" not in detail_html and "&lt;script&gt;evil_device&lt;/script&gt;" in detail_html,
)

detail_via_query = plugin.on_webhook("?host=WebhookLab", FakeRequest())
check("detail page is also reachable via ?host= query param", "webhookpw" in detail_via_query)

# Malicious hostname itself, HTML-escaped
potfile17 = p("xss.potfile")
write_potfile(potfile17, ["<script>evilhost</script>:xsspw"])
plugin_xss = make_plugin(crack_house_potfile_path=potfile17, **{k: v for k, v in missing_paths("xss").items() if k != "crack_house_potfile_path"})
plugin_xss.on_loaded()
index_xss = plugin_xss.on_webhook("", FakeRequest())
check(
    "index page HTML-escapes a malicious hostname",
    "<script>evilhost</script>" not in index_xss and "&lt;script&gt;evilhost&lt;/script&gt;" in index_xss,
)

export_result = plugin.on_webhook("export", FakeRequest())
check("export route returns a real flask Response", hasattr(export_result, "headers") and hasattr(export_result, "data"))
check(
    "export route sets Content-Disposition: attachment",
    "attachment" in export_result.headers.get("Content-Disposition", ""),
)
exported = json.loads(export_result.data)
check("exported JSON contains the assembled dossier", "webhooklab" in exported)

not_found = plugin.on_webhook("/somewhere-else", FakeRequest())
check("webhook returns a 404-style response for an unrecognized path", not_found[1] == 404)

not_found_host = plugin.on_webhook("target/does-not-exist", FakeRequest())
check("webhook returns a 404-style response for an unknown hostname's detail page", not_found_host[1] == 404)

# =========================================================================
# 9. refresh_interval_seconds caching behavior
# =========================================================================

plugin = make_plugin(refresh_interval_seconds=9999, **missing_paths("cache"))
plugin.on_loaded()  # first build

call_count = [0]
real_assemble = plugin._assemble


def counting_assemble():
    call_count[0] += 1
    return real_assemble()


plugin._assemble = counting_assemble

plugin._maybe_rebuild()
plugin._maybe_rebuild()
plugin._maybe_rebuild()
check("repeated _maybe_rebuild() calls within the interval do not rebuild again", call_count[0] == 0)

plugin._last_build = time.time() - 999999  # force "long elapsed"
plugin._maybe_rebuild()
check("_maybe_rebuild() rebuilds exactly once after the interval has elapsed", call_count[0] == 1)

plugin._maybe_rebuild()
check("a second call right after a fresh rebuild does not rebuild again", call_count[0] == 1)

# --- on_ui_setup/on_ui_update: badge only appears when show_on_screen ------

plugin = make_plugin(show_on_screen=False, **missing_paths("ui_off"))
plugin.on_loaded()
ui = FakeUI()
plugin.on_ui_setup(ui)
check("on_ui_setup adds no element when show_on_screen is false", mod.ELEMENT_NAME not in ui.elements)

potfile18 = p("ui_on.potfile")
write_potfile(potfile18, ["UiOnLab:pw"])
plugin = make_plugin(show_on_screen=True, crack_house_potfile_path=potfile18,
                      **{k: v for k, v in missing_paths("ui_on").items() if k != "crack_house_potfile_path"})
plugin.on_loaded()
ui = FakeUI()
plugin.on_ui_setup(ui)
check("on_ui_setup adds the element when show_on_screen is true", mod.ELEMENT_NAME in ui.elements)
plugin.on_ui_update(ui)
check("on_ui_update sets a badge value mentioning dossier count", "1 dossiers" in ui.values.get(mod.ELEMENT_NAME, ""))
check("on_ui_update badge mentions cracked count", "1 cracked" in ui.values.get(mod.ELEMENT_NAME, ""))

try:
    plugin.on_unload(ui)
    unload_ok = True
except Exception:
    unload_ok = False
check("on_unload doesn't crash and removes the element", unload_ok and mod.ELEMENT_NAME not in ui.elements)

plugin2 = make_plugin(show_on_screen=True, **missing_paths("unload_noel"))
plugin2.on_loaded()
ui2 = FakeUI()  # element never added
try:
    plugin2.on_unload(ui2)
    unload_ok2 = True
except Exception:
    unload_ok2 = False
check("on_unload doesn't crash when the element was never added", unload_ok2)


print(f"\n{len(failures)} failure(s) out of test run")
if failures:
    print(failures)
    sys.exit(1)
else:
    print("All tests passed.")
