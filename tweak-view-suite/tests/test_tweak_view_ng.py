import sys
import os
import json
import html
import tempfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))  # prctl + tomlkit (native/unavailable here)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")  # real pwnagotchi.plugins framework

import pwnagotchi.plugins  # noqa: E402
import tweak_view_ng as mod  # noqa: E402
from flask import Flask  # noqa: E402

app = Flask(__name__)
# The real pwnagotchi web server registers flask_wtf's CSRFProtect, which
# is what makes {{ csrf_token() }} resolve in a real render_template_string
# call (see pwnagotchi/ui/web/server.py). This bare test Flask app doesn't
# have that extension, so register the same Jinja global by hand.
app.jinja_env.globals["csrf_token"] = lambda: "test-csrf-token"

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


tmpdir = tempfile.mkdtemp()


def conf_path(name="tweaks.json"):
    return os.path.join(tmpdir, name)


def make_plugin(filename=None):
    p = mod.TweakViewNG()
    p.options = {"filename": filename or conf_path()}
    return p


class FakeElement:
    def __init__(self, xy=(0, 0), label="L", font=None, text_font=None,
                 label_font=None, label_spacing=5, max_length=0, value=""):
        self.xy = xy
        self.label = label
        self.font = font
        self.text_font = text_font
        self.label_font = label_font
        self.label_spacing = label_spacing
        self.max_length = max_length
        self.value = value


class FakeState:
    def __init__(self, **elements):
        self._state = elements


class FakeView:
    def __init__(self, **elements):
        self._state = FakeState(**elements)


# --- Test 1: real plugin registration ---
check("TweakViewNG registers with the real pwnagotchi.plugins loader",
      "tweak_view_ng" in pwnagotchi.plugins.loaded)

# --- Test 2: parse_tweak_key ---
check("parse_tweak_key accepts a well-formed key", mod.parse_tweak_key("VSS.face.xy") == ("face", "xy"))
check("parse_tweak_key rejects a key with too few parts", mod.parse_tweak_key("VSS.face") is None)
check("parse_tweak_key rejects a key not starting with VSS", mod.parse_tweak_key("XXX.face.xy") is None)

# --- Test 3: on_loaded with no saved file yet -> empty, no crash ---
p = make_plugin(filename=conf_path("does_not_exist.json"))
p.on_loaded()
check("on_loaded with a missing file starts with no tweaks", p._tweaks == {})

# --- Test 4: on_loaded skips malformed entries with a warning, keeps valid ones ---
path = conf_path("mixed.json")
with open(path, "w") as f:
    json.dump({"VSS.face.xy": "10,20", "not-a-real-key": "x", "VSS.onlytwo": "y"}, f)
p = make_plugin(filename=path)
p.on_loaded()
check("on_loaded keeps the well-formed entry", p._tweaks.get("VSS.face.xy") == "10,20")
check("on_loaded drops the malformed entries", "not-a-real-key" not in p._tweaks and "VSS.onlytwo" not in p._tweaks)
check("on_loaded drops exactly the 2 malformed entries, keeps exactly 1", len(p._tweaks) == 1)

# --- Test 5: on_loaded with a non-dict JSON top level -> empty, no crash ---
path = conf_path("not_a_dict.json")
with open(path, "w") as f:
    json.dump([1, 2, 3], f)
p = make_plugin(filename=path)
p.on_loaded()
check("on_loaded ignores a non-dict JSON file", p._tweaks == {})

# --- Test 6: THE CORE FIX - tweaks loaded in on_loaded apply on the very first on_ui_setup ---
# (the original loaded tweaks in on_ready, which fires AFTER on_ui_setup - so the first
# frame never got the saved tweaks applied)
path = conf_path("apply.json")
with open(path, "w") as f:
    json.dump({"VSS.face.xy": "5,6"}, f)
p = make_plugin(filename=path)
p.on_loaded()  # simulates the real load-order: on_loaded fires before on_ui_setup
face = FakeElement(xy=[0, 0])
view = FakeView(face=face)
p.on_ui_setup(view)
check("a tweak loaded in on_loaded is applied on the very first on_ui_setup call", face.xy == [5, 6])

# --- Test 7: update_elements applies xy/font/label and skips unknown element/attr cleanly ---
p = make_plugin()
face = FakeElement(xy=[1, 1], label="old")
view = FakeView(face=face)
p._tweaks = {
    "VSS.face.label": "NEW",
    "VSS.face.font": "Bold",
    "VSS.ghost.xy": "9,9",  # element doesn't exist - must not crash
    "VSS.face.nonexistent_attr": "z",  # attr not on the element - must not crash
}
p.update_elements(view)
check("update_elements applies a label tweak", face.label == "NEW")
check("update_elements applies a font tweak via the font-name map", face.font == mod.fonts.Bold)
check("update_elements silently skips a tweak for a nonexistent element", True)  # no exception raised above

# --- Test 8: dump_item actually pretty-prints a JSON-looking string value (FIX #1) ---
p = make_plugin()
out = p.dump_item("somefield", '{"a": 1, "b": 2}')
check("dump_item does not crash on a JSON-looking string", isinstance(out, str))
check(
    "dump_item's output contains the pretty-printed JSON for a parseable value",
    "JSON:" in out and html.escape('"a": 1') in out,
)

# --- Test 9: dump_item handles a non-JSON string, ints, bools, lists without crashing ---
out = p.dump_item("x", "hello")
check("dump_item handles a plain string", "hello" in out)
out = p.dump_item("n", 42)
check("dump_item handles an int", "42" in out)
out = p.dump_item("flag", True)
check("dump_item handles a bool", "True" in out)
out = p.dump_item("lst", [1, 2])
check("dump_item handles a list without crashing", isinstance(out, str))

# --- Test 10: update_from_request applies a valid int change and saves (FIX #2 scenario: save succeeds) ---
p = make_plugin(filename=conf_path("save_ok.json"))
p.on_loaded()  # sets self._conf_file from options - update_from_request saves to it
face = FakeElement(label_spacing=5)
agent = mock.Mock()
agent.view.return_value = FakeView(face=face)
p._agent = agent
with app.test_request_context("/update", method="POST", data={"VSS.face.label_spacing": "9"}):
    from flask import request
    out = p.update_from_request(request)
check("update_from_request records a changed int tweak", p._tweaks.get("VSS.face.label_spacing") == 9)
check("update_from_request reports the change", "face.label_spacing" in out)
check("update_from_request actually saved to disk", os.path.exists(conf_path("save_ok.json")))

# --- Test 11: update_from_request handles a save failure cleanly (FIX #2: no NameError) ---
p = make_plugin(filename="/definitely/not/a/writable/path.json")
p.on_loaded()
face = FakeElement(label_spacing=5)
agent = mock.Mock()
agent.view.return_value = FakeView(face=face)
p._agent = agent
with app.test_request_context("/update", method="POST", data={"VSS.face.label_spacing": "9"}):
    from flask import request
    out = p.update_from_request(request)  # must not raise NameError like the original did
check("a failed save is reported cleanly instead of crashing with NameError", "Unable to save settings" in out)

# --- Test 12: update_from_request skips a malformed field name without crashing ---
p = make_plugin()
agent = mock.Mock()
agent.view.return_value = FakeView(face=FakeElement())
p._agent = agent
with app.test_request_context("/update", method="POST", data={"VSS.onlytwo": "9"}):
    from flask import request
    out = p.update_from_request(request)
check("update_from_request reports a malformed field instead of crashing", "malformed" in out)

# --- Test 13: webhook GET / renders without an agent yet ---
p = make_plugin()
with app.test_request_context("/"):
    from flask import request
    resp = p.on_webhook("", request)
check("webhook GET / renders even before the agent is ready", "Not ready yet" in resp)

# --- Test 14: webhook GET / renders the view elements once the agent is ready ---
p = make_plugin()
agent = mock.Mock()
agent.view.return_value = FakeView(face=FakeElement())
p._agent = agent
with app.test_request_context("/"):
    from flask import request
    resp = p.on_webhook("", request)
check("webhook GET / shows the available view elements once ready", "Available View Elements" in resp)

# --- Test 15: webhook POST delete_mods reverts and removes a tracked tweak ---
p = make_plugin(filename=conf_path("delete.json"))
face = FakeElement(xy=[1, 1])
agent = mock.Mock()
agent.view.return_value = FakeView(face=face)
p._agent = agent
p._tweaks = {"VSS.face.xy": "9,9"}
p._untweak = {"VSS.face.xy": [1, 1]}
with app.test_request_context("/delete_mods", method="POST", data={"delete_me": "VSS.face.xy"}):
    from flask import request
    resp = p.on_webhook("delete_mods", request)
check("deleting a mod removes it from tracked tweaks", "VSS.face.xy" not in p._tweaks)
check("deleting a mod reverts the element's value", face.xy == [1, 1])

# --- Test 16: on_unload reverts tracked tweaks against the real state dict ---
p = make_plugin()
face = FakeElement(label="changed")
view = FakeView(face=face)
p._untweak = {"VSS.face.label": "original"}
p.on_unload(view)
check("on_unload reverts a tracked tweak", face.label == "original")


print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("All tests passed.")
