import sys
import os
import json
import tempfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")

import pwnagotchi.plugins  # noqa: E402
import fix_region_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


tmpdir = tempfile.mkdtemp()


def p(name):
    return os.path.join(tmpdir, name)


def make_plugin(**opts):
    plugin = mod.FixRegionNG()
    defaults = dict(mod.DEFAULTS)
    defaults.update(opts)
    plugin.options = defaults
    return plugin


def default_paths(tag):
    return {
        "sh_path": p(f"{tag}_network-fix.sh"),
        "service_path": p(f"{tag}_network-fix.service"),
        "state_path": p(f"{tag}_state.json"),
    }


# --- Registration ------------------------------------------------------------

check(
    "FixRegionNG registers with the real pwnagotchi.plugins loader",
    "fix_region_ng" in pwnagotchi.plugins.loaded,
)

check("on_unload hook is present", hasattr(mod.FixRegionNG, "on_unload"))
check("on_loaded hook is present", hasattr(mod.FixRegionNG, "on_loaded"))
check("on_webhook hook is present", hasattr(mod.FixRegionNG, "on_webhook"))

# --- Region format validation (bug fix #1) -----------------------------------

check("valid 2-letter uppercase code accepted", mod._normalize_region("US") == "US")
check("valid 2-letter lowercase code is normalized to uppercase", mod._normalize_region("nl") == "NL")
check("mixed-case code is normalized", mod._normalize_region("Gb") == "GB")
check("empty string is rejected", mod._normalize_region("") is None)
check("one-letter code is rejected", mod._normalize_region("U") is None)
check("three-letter code is rejected", mod._normalize_region("USA") is None)
check("a digit-containing value is rejected", mod._normalize_region("U1") is None)
check("None is rejected", mod._normalize_region(None) is None)
check("a non-string (int) is rejected", mod._normalize_region(42) is None)
check("whitespace-padded valid code is accepted", mod._normalize_region("  nl  ") == "NL")

# --- _opt() falls back to real defaults instead of KeyError ------------------

plugin = mod.FixRegionNG()
plugin.options = {}
check("_opt() returns the real default for a key entirely absent from options",
      plugin._opt("region") == mod.DEFAULTS["region"])
check("_opt() never raises for a completely empty options dict",
      plugin._opt("service_name") == mod.DEFAULTS["service_name"])

# --- iw reg get parsing -------------------------------------------------------

check(
    "_parse_reg_get extracts the country code from real iw output",
    mod._parse_reg_get("global\ncountry US: DFS-FCC\n") == "US",
)
check("_parse_reg_get returns None for output with no country line", mod._parse_reg_get("global\n") is None)
check("_parse_reg_get returns None for empty/None output", mod._parse_reg_get(None) is None)

# --- GPS bounding box lookup --------------------------------------------------

check("a point inside the NL bounding box resolves to NL", mod._guess_country_from_latlon(52.1, 5.1) == "NL")
check("a point inside the US bounding box resolves to US", mod._guess_country_from_latlon(40.0, -100.0) == "US")
check("a point in the middle of the ocean matches no bounding box", mod._guess_country_from_latlon(0.0, -140.0) is None)
check("a non-numeric coordinate returns None instead of raising", mod._guess_country_from_latlon("nope", None) is None)


# --- Helpers for mocking subprocess.run and systemd/iw calls -----------------

class FakeCompleted:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def run_side_effect(script_fs, service_fs, iw_reg_get_output="global\ncountry US: DFS-FCC\n",
                     enabled_services=()):
    """Builds a subprocess.run side_effect that never touches the real
    system: 'iw reg get' returns iw_reg_get_output, 'systemctl
    is-enabled <name>' reports enabled only for names in
    enabled_services, and everything else (iw reg set, systemctl
    enable/start/stop/disable) just succeeds."""

    def _run(cmd, *args, **kwargs):
        assert not kwargs.get("shell", False), f"subprocess.run called with shell=True: {cmd}"
        assert isinstance(cmd, list), f"subprocess.run called with a non-list command: {cmd!r}"
        if cmd[:2] == ["iw", "reg"] and cmd[2] == "get":
            return FakeCompleted(stdout=iw_reg_get_output)
        if cmd[:2] == ["systemctl", "is-enabled"]:
            name = cmd[2]
            return FakeCompleted(stdout=("enabled\n" if name in enabled_services else "disabled\n"))
        return FakeCompleted()

    return _run


# --- No shell command is ever built via string interpolation (bug fix #2) ---

plugin = make_plugin(region="NL", **default_paths("shellcheck"))
with mock.patch("subprocess.run") as run_mock, mock.patch("_thread.start_new_thread"):
    run_mock.side_effect = run_side_effect({}, {})
    plugin.on_loaded()
    for call in run_mock.call_args_list:
        cmd = call.args[0] if call.args else call.kwargs.get("args")
        check(f"subprocess.run call uses a list, not a string: {cmd!r}", isinstance(cmd, list))
        check(f"subprocess.run call never sets shell=True: {cmd!r}", not call.kwargs.get("shell", False))
    check("iw reg set was called with the configured region as a list arg",
          ["iw", "reg", "set", "NL"] in [c.args[0] for c in run_mock.call_args_list])
    check("systemctl enable was called with a list", ["systemctl", "enable", "network-fix"] in
          [c.args[0] for c in run_mock.call_args_list])

# --- on_loaded applies a region, writes the script/service, and restarts once

paths = default_paths("apply1")
plugin = make_plugin(region="GB", **paths)
with mock.patch("subprocess.run") as run_mock, mock.patch("_thread.start_new_thread") as thread_mock:
    run_mock.side_effect = run_side_effect({}, {})
    plugin.on_loaded()

check("shell script was written", os.path.exists(paths["sh_path"]))
check("systemd service file was written", os.path.exists(paths["service_path"]))
with open(paths["sh_path"]) as f:
    sh_content = f.read()
check("shell script contains the configured region", "iw reg set GB" in sh_content)
check("shell script does not contain a shebang-breaking injection", sh_content.count("\n") < 5)
check("state file records the applied region", json.load(open(paths["state_path"]))["applied_region"] == "GB")
check("plugin restarted exactly once after first apply", thread_mock.call_count == 1)

# --- bug fix #3: reloading with the SAME region does nothing again ----------

with mock.patch("subprocess.run") as run_mock, mock.patch("_thread.start_new_thread") as thread_mock:
    run_mock.side_effect = run_side_effect({}, {})
    plugin2 = make_plugin(region="GB", **paths)  # same paths -> same already-applied state
    plugin2.on_loaded()

iw_calls = [c.args[0] for c in run_mock.call_args_list if c.args and c.args[0][:2] == ["iw", "reg"] and c.args[0][2] == "set"]
check("no 'iw reg set' call when the region hasn't changed", iw_calls == [])
check("no restart triggered when the region hasn't changed", thread_mock.call_count == 0)

# --- bug fix #3: reloading with a DIFFERENT region re-applies ---------------

with mock.patch("subprocess.run") as run_mock, mock.patch("_thread.start_new_thread") as thread_mock:
    run_mock.side_effect = run_side_effect({}, {})
    plugin3 = make_plugin(region="DE", **paths)
    plugin3.on_loaded()

iw_calls = [c.args[0] for c in run_mock.call_args_list if c.args and c.args[0][:2] == ["iw", "reg"] and c.args[0][2] == "set"]
check("'iw reg set' called with the NEW region after a real config change", ["iw", "reg", "set", "DE"] in iw_calls)
check("restart triggered exactly once after a real config change", thread_mock.call_count == 1)
check("state file now records the new region", json.load(open(paths["state_path"]))["applied_region"] == "DE")

# --- Invalid region: refused, nothing applied, nothing crashes --------------

paths_bad = default_paths("badregion")
plugin_bad = make_plugin(region="not-a-region", **paths_bad)
with mock.patch("subprocess.run") as run_mock, mock.patch("_thread.start_new_thread") as thread_mock:
    run_mock.side_effect = run_side_effect({}, {})
    plugin_bad.on_loaded()

check("invalid region: no shell script written", not os.path.exists(paths_bad["sh_path"]))
check("invalid region: no service file written", not os.path.exists(paths_bad["service_path"]))
check("invalid region: no restart triggered", thread_mock.call_count == 0)
iw_set_calls = [c.args[0] for c in run_mock.call_args_list if c.args and c.args[0][:3] == ["iw", "reg", "set"]]
check("invalid region: 'iw reg set' never called", iw_set_calls == [])

# --- on_unload: existence-guarded cleanup (bug fix #4) -----------------------

paths_unload = default_paths("unload1")
plugin_u = make_plugin(region="US", **paths_unload)
with mock.patch("subprocess.run") as run_mock, mock.patch("_thread.start_new_thread"):
    run_mock.side_effect = run_side_effect({}, {})
    plugin_u.on_loaded()  # creates the files, applies the region

check("files exist before unload", os.path.exists(paths_unload["sh_path"]) and os.path.exists(paths_unload["service_path"]))

with mock.patch("subprocess.run") as run_mock, mock.patch("_thread.start_new_thread"):
    run_mock.side_effect = run_side_effect({}, {}, enabled_services=("network-fix",))
    plugin_u.on_unload(mock.Mock())
    stop_calls = [c.args[0] for c in run_mock.call_args_list if c.args and c.args[0][:2] == ["systemctl", "stop"]]
    disable_calls = [c.args[0] for c in run_mock.call_args_list if c.args and c.args[0][:2] == ["systemctl", "disable"]]
    check("on_unload stops the service when it's actually enabled", stop_calls == [["systemctl", "stop", "network-fix"]])
    check("on_unload disables the service when it's actually enabled", disable_calls == [["systemctl", "disable", "network-fix"]])

check("shell script removed on unload", not os.path.exists(paths_unload["sh_path"]))
check("service file removed on unload", not os.path.exists(paths_unload["service_path"]))

# unload again: nothing exists any more, must not raise and must not call
# systemctl stop/disable (never-enabled / never-installed guard)
with mock.patch("subprocess.run") as run_mock, mock.patch("_thread.start_new_thread"):
    run_mock.side_effect = run_side_effect({}, {}, enabled_services=())
    try:
        plugin_u.on_unload(mock.Mock())
        no_crash = True
    except Exception:
        no_crash = False
    check("on_unload never crashes when nothing was ever created", no_crash)
    stop_calls = [c.args[0] for c in run_mock.call_args_list if c.args and c.args[0][:2] == ["systemctl", "stop"]]
    check("on_unload does not call systemctl stop for a never-installed service", stop_calls == [])

# a fresh plugin that never ran on_loaded at all must also unload cleanly
fresh_paths = default_paths("neverloaded")
plugin_fresh = make_plugin(region="US", **fresh_paths)
with mock.patch("subprocess.run") as run_mock, mock.patch("_thread.start_new_thread"):
    run_mock.side_effect = run_side_effect({}, {}, enabled_services=())
    try:
        plugin_fresh.on_unload(mock.Mock())
        no_crash = True
    except Exception:
        no_crash = False
    check("on_unload never crashes for a plugin that never applied anything", no_crash)

# --- Webhook status page: renders + HTML-escapes ------------------------------

paths_wh = default_paths("webhook1")
plugin_wh = make_plugin(region="<script>evil</script>", **paths_wh)
with mock.patch("subprocess.run") as run_mock, mock.patch("_thread.start_new_thread"):
    run_mock.side_effect = run_side_effect({}, {})
    page = plugin_wh.on_webhook("", mock.Mock())

check("webhook status page renders without crashing", isinstance(page, str) and "<html" in page)
check("webhook status page HTML-escapes a malicious configured region",
      "<script>evil</script>" not in page and "&lt;script&gt;" in page)
check("webhook status page labels the configured region", "Configured region" in page)
check("webhook status page flags the invalid region as such", "NO" in page)

paths_wh2 = default_paths("webhook2")
plugin_wh2 = make_plugin(region="NL", **paths_wh2)
with mock.patch("subprocess.run") as run_mock, mock.patch("_thread.start_new_thread"):
    run_mock.side_effect = run_side_effect({}, {})
    plugin_wh2.on_loaded()
    page2 = plugin_wh2.on_webhook("status", mock.Mock())

check("webhook status page shows the last-applied region after a real apply", "NL" in page2)
check("webhook status page shows the live iw reg get domain", "US" in page2)  # from run_side_effect's default stdout

with mock.patch("subprocess.run") as run_mock, mock.patch("_thread.start_new_thread"):
    run_mock.side_effect = run_side_effect({}, {})
    not_found = plugin_wh2.on_webhook("/somewhere-else", mock.Mock())
check("webhook returns a 404-style response for an unrecognized path", not_found[1] == 404)

# --- GPS region suggestion: fires correctly with a stub GPS plugin present --


class StubGPSPlugin:
    def __init__(self, lat, lon, hot=True):
        self.pn_gps_coords = {"Latitude": lat, "Longitude": lon} if lat is not None else None
        self.gps_hot = hot


# In-bounding-box coordinates, different from the configured region -> suggestion fires
pwnagotchi.plugins.loaded["gps_tagger_ng"] = StubGPSPlugin(52.1, 5.1)  # NL
paths_gps1 = default_paths("gps1")
plugin_gps1 = make_plugin(region="US", gps_region_suggestion=True, **paths_gps1)
with mock.patch("subprocess.run") as run_mock, mock.patch("_thread.start_new_thread"):
    run_mock.side_effect = run_side_effect({}, {})
    plugin_gps1.on_loaded()
check("GPS suggestion fires with a stub GPS plugin present and in-bbox coords",
      plugin_gps1._last_gps_suggestion == "NL")

# Suggestion matches configured region -> no crash, suggestion still recorded
pwnagotchi.plugins.loaded["gps_tagger_ng"] = StubGPSPlugin(40.0, -100.0)  # US
paths_gps2 = default_paths("gps2")
plugin_gps2 = make_plugin(region="US", gps_region_suggestion=True, **paths_gps2)
with mock.patch("subprocess.run") as run_mock, mock.patch("_thread.start_new_thread"):
    run_mock.side_effect = run_side_effect({}, {})
    plugin_gps2.on_loaded()
check("GPS suggestion equal to configured region doesn't crash anything",
      plugin_gps2._last_gps_suggestion == "US")

del pwnagotchi.plugins.loaded["gps_tagger_ng"]

# No GPS plugin loaded at all -> silently does nothing
paths_gps3 = default_paths("gps3")
plugin_gps3 = make_plugin(region="US", gps_region_suggestion=True, **paths_gps3)
with mock.patch("subprocess.run") as run_mock, mock.patch("_thread.start_new_thread"):
    run_mock.side_effect = run_side_effect({}, {})
    try:
        plugin_gps3.on_loaded()
        no_crash = True
    except Exception:
        no_crash = False
check("GPS suggestion feature doesn't crash when no GPS plugin is loaded", no_crash)
check("GPS suggestion stays None when no GPS plugin is loaded", plugin_gps3._last_gps_suggestion is None)

# GPS plugin loaded but with no coordinates -> silently does nothing
pwnagotchi.plugins.loaded["gps_tagger_ng"] = StubGPSPlugin(None, None)
paths_gps4 = default_paths("gps4")
plugin_gps4 = make_plugin(region="US", gps_region_suggestion=True, **paths_gps4)
with mock.patch("subprocess.run") as run_mock, mock.patch("_thread.start_new_thread"):
    run_mock.side_effect = run_side_effect({}, {})
    try:
        plugin_gps4.on_loaded()
        no_crash = True
    except Exception:
        no_crash = False
check("GPS suggestion doesn't crash when the GPS plugin has no coordinates", no_crash)
check("GPS suggestion stays None when coordinates are unavailable", plugin_gps4._last_gps_suggestion is None)
del pwnagotchi.plugins.loaded["gps_tagger_ng"]

# GPS plugin loaded with coordinates outside every bounding box -> no suggestion
pwnagotchi.plugins.loaded["gps_tagger_ng"] = StubGPSPlugin(0.0, -140.0)  # open ocean
paths_gps5 = default_paths("gps5")
plugin_gps5 = make_plugin(region="US", gps_region_suggestion=True, **paths_gps5)
with mock.patch("subprocess.run") as run_mock, mock.patch("_thread.start_new_thread"):
    run_mock.side_effect = run_side_effect({}, {})
    plugin_gps5.on_loaded()
check("GPS suggestion stays None for coordinates outside every known bounding box",
      plugin_gps5._last_gps_suggestion is None)
del pwnagotchi.plugins.loaded["gps_tagger_ng"]

# gps_region_suggestion left off (default False): feature never runs at all
paths_gps6 = default_paths("gps6")
pwnagotchi.plugins.loaded["gps_tagger_ng"] = StubGPSPlugin(52.1, 5.1)
plugin_gps6 = make_plugin(region="US", **paths_gps6)  # gps_region_suggestion defaults to False
with mock.patch("subprocess.run") as run_mock, mock.patch("_thread.start_new_thread"):
    run_mock.side_effect = run_side_effect({}, {})
    plugin_gps6.on_loaded()
check("GPS suggestion feature is off by default", plugin_gps6._last_gps_suggestion is None)
del pwnagotchi.plugins.loaded["gps_tagger_ng"]

# A lookup that raises must degrade silently, never block plugin load
class ExplodingGPSPlugin:
    @property
    def pn_gps_coords(self):
        raise RuntimeError("simulated failure reading GPS coordinates")


pwnagotchi.plugins.loaded["gps_tagger_ng"] = ExplodingGPSPlugin()
paths_gps7 = default_paths("gps7")
plugin_gps7 = make_plugin(region="US", gps_region_suggestion=True, **paths_gps7)
with mock.patch("subprocess.run") as run_mock, mock.patch("_thread.start_new_thread"):
    run_mock.side_effect = run_side_effect({}, {})
    try:
        plugin_gps7.on_loaded()
        no_crash = True
    except Exception:
        no_crash = False
check("a raising GPS lookup degrades silently and never blocks on_loaded", no_crash)
check("region is still applied even when the GPS lookup raises",
      os.path.exists(paths_gps7["sh_path"]))
del pwnagotchi.plugins.loaded["gps_tagger_ng"]


print(f"\n{len(failures)} failure(s) out of test run")
if failures:
    print(failures)
    sys.exit(1)
else:
    print("All tests passed.")
