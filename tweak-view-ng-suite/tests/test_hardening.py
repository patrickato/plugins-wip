"""Regression tests for the suite-harvest hardening items (2-8).

Runs against the offline stub framework (see conftest.py), same as test_core.py.
Covers:

* item 2 - earlier layout preload in on_loaded()
* item 3 - per-entry corruption recovery + import report
* item 4 - explicit startup/readiness state
* item 5 - transactional edit/save rollback
* item 6 - HTML/DOM injection resistance (data stays data)
* item 7 - legacy + NG fixtures (loaded from tests/fixtures/)
* item 8 - minimal server-rendered recovery editor
"""

import json
import os

import pytest

from conftest import FakeView
import tweak_view_ng as tv
from pwnagotchi.ui.components import Text, Line
import pwnagotchi.ui.fonts as fonts

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def registry():
    return {"Small": fonts.Small, "BoldSmall": fonts.BoldSmall, "Bold": fonts.Bold, "Huge": fonts.Huge}


def fixture(name):
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as fh:
        return json.load(fh)


class Req:
    """JSON request stub (api/* routes)."""
    def __init__(self, method="POST", data=None):
        self.method = method
        self._data = data

    def get_json(self, silent=True):
        return self._data


class FormReq:
    """Form-encoded request stub (recovery editor)."""
    def __init__(self, method="POST", form=None):
        self.method = method
        self.form = dict(form or {})

    def get_json(self, silent=True):
        return None


def make_ready_plugin(tmp_path, monkeypatch, layout=None, width=480, height=320, extra_elements=None):
    """Construct a plugin taken all the way to PHASE_READY against a FakeView."""
    store = tv.LayoutStore(str(tmp_path / "ng.json"))
    if layout is not None:
        store.save(layout)
    p = tv.TweakViewNG()
    p.options = {"filename": str(tmp_path / "ng.json"), "legacy_filename": str(tmp_path / "none.json")}
    monkeypatch.setattr(p, "_build_fonts", lambda: setattr(p, "_fonts", registry()))
    p.on_loaded()
    v = FakeView(width, height)
    v.add_element("face", Text("x", (2, 3), font=fonts.Small))
    v.add_element("status", Text("hi", (10, 20), font=fonts.Bold, wrap=True, max_length=20))
    for name, widget in (extra_elements or {}).items():
        v.add_element(name, widget)
    p.on_ui_setup(v)
    return p, v


# --------------------------------------------------------------------------- #
# item 2 - earlier layout preload
# --------------------------------------------------------------------------- #

def test_layout_is_preloaded_in_on_loaded(tmp_path, monkeypatch):
    store = tv.LayoutStore(str(tmp_path / "ng.json"))
    d = store.empty(); d["profiles"]["default"]["edits"] = {"face": {"xy": [50, 60]}}; store.save(d)
    p = tv.TweakViewNG()
    p.options = {"filename": str(tmp_path / "ng.json"), "legacy_filename": str(tmp_path / "none.json")}
    monkeypatch.setattr(p, "_build_fonts", lambda: setattr(p, "_fonts", registry()))
    p.on_loaded()
    # Layout parsed + validated before any UI object exists.
    assert p._layout is not None
    assert p._phase == tv.TweakViewNG.PHASE_WAITING_UI
    assert p._layout["profiles"]["default"]["edits"]["face"]["xy"] == [50, 60]
    assert p._load_report is not None


def test_on_loaded_never_raises_on_unreadable_layout(tmp_path, monkeypatch):
    bad = tmp_path / "ng.json"; bad.write_text("{ this is not json")
    p = tv.TweakViewNG()
    p.options = {"filename": str(bad), "legacy_filename": str(tmp_path / "none.json")}
    monkeypatch.setattr(p, "_build_fonts", lambda: setattr(p, "_fonts", registry()))
    p.on_loaded()  # must not raise
    assert p._layout is not None  # fell back to empty
    assert p._last_error is not None
    assert p._phase == tv.TweakViewNG.PHASE_WAITING_UI


# --------------------------------------------------------------------------- #
# item 3 - per-entry corruption recovery + report
# --------------------------------------------------------------------------- #

def test_sanitize_keeps_valid_drops_corrupt():
    data = fixture("ng_schema_corrupt_entries.json")
    clean, report = tv.sanitize_layout(data, tv.LayoutStore("/x").empty())
    assert set(clean["profiles"].keys()) == {"default", "healthy"}
    # broken_profile (not an object) dropped
    assert "broken_profile" not in clean["profiles"]
    # face keeps valid props, drops bogus
    assert set(clean["profiles"]["default"]["edits"]["face"].keys()) == {"xy", "font"}
    # status (not an object) and "" element dropped
    assert "status" not in clean["profiles"]["default"]["edits"]
    assert "" not in clean["profiles"]["default"]["edits"]
    # only the valid shape survives
    assert set(clean["profiles"]["default"]["shapes"].keys()) == {"good_rect"}
    # missing active profile falls back
    assert clean["active_profile"] == "default"
    assert report["skipped"], "expected recorded skip reasons"
    assert all("reason" in s for s in report["skipped"])


def test_corrupt_ng_file_loads_via_load_report(tmp_path):
    path = tmp_path / "ng.json"
    path.write_text(json.dumps(fixture("ng_schema_corrupt_entries.json")))
    store = tv.LayoutStore(str(path))
    layout, report = store.load_report()
    assert report["ok"] is True
    assert set(layout["profiles"].keys()) == {"default", "healthy"}
    assert report["skipped"]


def test_healthy_ng_file_round_trips(tmp_path):
    path = tmp_path / "ng.json"
    path.write_text(json.dumps(fixture("ng_schema_current.json")))
    store = tv.LayoutStore(str(path))
    layout, report = store.load_report()
    assert report["ok"] is True
    assert set(layout["profiles"].keys()) == {"default", "night"}
    assert layout["profiles"]["default"]["edits"]["face"]["xy"] == [10, 40]
    assert not report["skipped"]


def test_load_report_missing_file_is_clean(tmp_path):
    store = tv.LayoutStore(str(tmp_path / "absent.json"))
    layout, report = store.load_report()
    assert report["ok"] is True and report["present"] is False
    assert layout["profiles"]["default"] == {"edits": {}, "shapes": {}}


def test_malformed_legacy_import_recovers(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps(fixture("legacy_malformed.json")))
    p = tv.TweakViewNG()
    p.options = {"filename": str(tmp_path / "ng.json"), "legacy_filename": str(legacy), "auto_import_legacy": True}
    monkeypatch.setattr(p, "_build_fonts", lambda: setattr(p, "_fonts", registry()))
    p.on_loaded()
    prof = p._layout["profiles"]["default"]
    # The single valid edit + valid custom shape survived.
    assert prof["edits"].get("face") == {"font": "Huge"}
    assert prof["edits"].get("name") == {"xy": "10,10"}
    assert "good" in prof["shapes"]
    report = p._load_report["legacy_import"]
    assert report["imported"] and report["skipped"]


def test_import_report_distinguishes_scopes():
    _profile, report = tv.import_legacy_report(fixture("legacy_malformed.json"))
    scopes = {s.get("scope") for s in report["skipped"]}
    # at least a bad legacy key and a bad shape were recorded
    assert "legacy_key" in scopes or "shape" in scopes


# --------------------------------------------------------------------------- #
# item 4 - readiness state
# --------------------------------------------------------------------------- #

def test_phase_progression(tmp_path, monkeypatch):
    store = tv.LayoutStore(str(tmp_path / "ng.json")); store.save(store.empty())
    p = tv.TweakViewNG()
    p.options = {"filename": str(tmp_path / "ng.json"), "legacy_filename": str(tmp_path / "none.json")}
    monkeypatch.setattr(p, "_build_fonts", lambda: setattr(p, "_fonts", registry()))
    assert p._phase == tv.TweakViewNG.PHASE_INIT
    p.on_loaded()
    assert p._phase == tv.TweakViewNG.PHASE_WAITING_UI
    assert p._is_ready() is False
    p.on_ui_setup(FakeView())
    assert p._phase == tv.TweakViewNG.PHASE_READY
    assert p._is_ready() is True


def test_api_ready_works_before_ui_setup(tmp_path, monkeypatch):
    store = tv.LayoutStore(str(tmp_path / "ng.json")); store.save(store.empty())
    p = tv.TweakViewNG()
    p.options = {"filename": str(tmp_path / "ng.json"), "legacy_filename": str(tmp_path / "none.json")}
    monkeypatch.setattr(p, "_build_fonts", lambda: setattr(p, "_fonts", registry()))
    p.on_loaded()
    payload = p._route_api("api/ready", Req("GET"))
    assert payload["phase"] == tv.TweakViewNG.PHASE_WAITING_UI
    assert payload["ready"] is False
    assert payload["layout_loaded"] is True
    assert "load_report" in payload


def test_mutating_api_returns_503_when_not_ready(tmp_path, monkeypatch):
    store = tv.LayoutStore(str(tmp_path / "ng.json")); store.save(store.empty())
    p = tv.TweakViewNG()
    p.options = {"filename": str(tmp_path / "ng.json"), "legacy_filename": str(tmp_path / "none.json")}
    monkeypatch.setattr(p, "_build_fonts", lambda: setattr(p, "_fonts", registry()))
    p.on_loaded()
    result = p._route_api("api/update", Req("POST", {"element": "face", "properties": {"xy": [1, 2]}}))
    assert isinstance(result, tuple) and result[1] == 503
    assert result[0]["ok"] is False
    assert result[0]["ready"] is False


def test_state_payload_includes_phase(tmp_path, monkeypatch):
    p, v = make_ready_plugin(tmp_path, monkeypatch)
    payload = p._state_payload()
    assert payload["phase"] == tv.TweakViewNG.PHASE_READY
    assert payload["ready"] is True


# --------------------------------------------------------------------------- #
# item 5 - transactional edit/save rollback
# --------------------------------------------------------------------------- #

def test_failed_save_rolls_back_runtime_and_layout(tmp_path, monkeypatch):
    p, v = make_ready_plugin(tmp_path, monkeypatch)
    before_xy = tuple(v._state._state["face"].xy)
    before_layout = p._snapshot_config()
    # Make persistence fail.
    def boom(_data):
        raise OSError("disk full")
    monkeypatch.setattr(p._store, "save", boom)
    result = p._route_api("api/update", Req("POST", {"element": "face", "properties": {"xy": [200, 150]}}))
    assert isinstance(result, tuple) and result[1] == 500
    assert result[0]["rolled_back"] is True
    # Runtime widget restored to its pre-edit value...
    assert tuple(v._state._state["face"].xy) == before_xy
    # ...and in-memory layout matches the pre-edit snapshot (no phantom edit).
    assert p._layout == before_layout
    assert "face" not in p._profile().get("edits", {})


def test_failed_shape_add_rolls_back(tmp_path, monkeypatch):
    p, v = make_ready_plugin(tmp_path, monkeypatch)
    monkeypatch.setattr(p._store, "save", lambda _d: (_ for _ in ()).throw(OSError("nope")))
    result = p._route_api("api/add_shape", Req("POST", {"name": "boxit", "type": "rect", "properties": {"xy": [1, 1, 9, 9]}}))
    assert isinstance(result, tuple) and result[1] == 500
    # the shape added to runtime by the failed edit was removed on rollback
    assert "boxit" not in v._state._state
    assert "boxit" not in p._profile().get("shapes", {})


def test_successful_edit_persists_after_rollback_capability_added(tmp_path, monkeypatch):
    # sanity: normal path still works with the transaction wrapper in place
    p, v = make_ready_plugin(tmp_path, monkeypatch)
    r = p._route_api("api/update", Req("POST", {"element": "face", "properties": {"xy": [12, 34]}}))
    assert r["ok"] is True
    assert tuple(v._state._state["face"].xy) == (12, 34)
    saved = json.load(open(tmp_path / "ng.json"))
    assert saved["profiles"]["default"]["edits"]["face"]["xy"] == [12, 34]


def test_intended_4xx_not_treated_as_rollback(tmp_path, monkeypatch):
    p, v = make_ready_plugin(tmp_path, monkeypatch)
    # unknown element -> 404, must be a clean intended result, not a 500 rollback
    result = p._route_api("api/update", Req("POST", {"element": "ghost", "properties": {"xy": [1, 2]}}))
    assert isinstance(result, tuple) and result[1] == 404
    assert "rolled_back" not in result[0]


# --------------------------------------------------------------------------- #
# item 6 - HTML/DOM injection resistance
# --------------------------------------------------------------------------- #

HOSTILE_NAMES = [
    "<script>alert(1)</script>",
    "x\"><img src=x onerror=alert(1)>",
    "name&amp;<b>bold</b>",
    "'; DROP TABLE--",
]


def test_snapshot_keeps_hostile_names_as_data(tmp_path, monkeypatch):
    extra = {HOSTILE_NAMES[0]: Text("v", (1, 1), font=fonts.Small)}
    p, v = make_ready_plugin(tmp_path, monkeypatch, extra_elements=extra)
    snap = p._adapter.snapshot()
    # The hostile name is present verbatim as a JSON key - it is data, never
    # interpreted. The API returns JSON, so there is no HTML context here.
    assert HOSTILE_NAMES[0] in snap["elements"]


def test_recovery_page_escapes_hostile_values(tmp_path, monkeypatch):
    # Uses REAL jinja2 so autoescaping is exercised (the stub render passes text
    # through unchanged, so we render with jinja2 directly here).
    jinja2 = pytest.importorskip("jinja2")
    hostile = "<script>alert('xss')</script>"
    extra = {"evilname<script>": Text(hostile, (1, 1), font=fonts.Small)}
    p, v = make_ready_plugin(tmp_path, monkeypatch, extra_elements=extra)
    rows = p._recovery_rows()
    html = jinja2.Environment(autoescape=True).from_string(
        tv.RECOVERY_UI
    ).render(version="t", rows=rows, width=480, height=320,
             active_profile="default", message=None, ok=True, last_error=None,
             csrf_token=lambda: "tok")
    # Raw hostile markup must not appear; it must be entity-escaped.
    assert "<script>alert('xss')</script>" not in html
    assert "evilname<script>" not in html
    assert "&lt;script&gt;" in html


def test_web_ui_still_has_no_external_dependencies():
    lower = tv.WEB_UI.lower()
    assert "http://" not in lower and "https://" not in lower
    rlower = tv.RECOVERY_UI.lower()
    assert "http://" not in rlower and "https://" not in rlower


# --------------------------------------------------------------------------- #
# item 7 - legacy fixtures convert correctly
# --------------------------------------------------------------------------- #

def test_legacy_tweak_view_fixture_converts():
    profile, report = tv.import_legacy_report(fixture("legacy_tweak_view.json"))
    assert profile["edits"]["face"]["font"] == "Huge"
    assert profile["edits"]["status"]["max_length"] == 30
    assert profile["edits"]["status"]["wrap"] is True
    assert report["imported"]


def test_legacy_tweak_view_2_custom_shapes_map():
    profile, report = tv.import_legacy_report(fixture("legacy_tweak_view_2.json"))
    assert profile["shapes"]["divider"]["type"] == "line"
    assert profile["shapes"]["badge"]["type"] == "rect"
    assert profile["shapes"]["dot"]["type"] == "ellipse"


def test_mixed_legacy_fixture_keeps_valid():
    profile, report = tv.import_legacy_report(fixture("legacy_mixed_valid_invalid.json"))
    assert "face" in profile["edits"] and "name" in profile["edits"]
    assert "unknown_prop" not in profile["edits"].get("status", {})
    assert "frame" in profile["shapes"]
    assert report["skipped"]


# --------------------------------------------------------------------------- #
# item 8 - recovery editor
# --------------------------------------------------------------------------- #

def test_recovery_page_renders_when_ready(tmp_path, monkeypatch):
    p, v = make_ready_plugin(tmp_path, monkeypatch)
    # stub render_template_string returns the template text; good enough to
    # confirm the ready path chose the editor template, not the not-ready one.
    html = p._recovery_html()
    assert "RECOVERY" in html


def test_recovery_not_ready_page_before_setup(tmp_path, monkeypatch):
    store = tv.LayoutStore(str(tmp_path / "ng.json")); store.save(store.empty())
    p = tv.TweakViewNG()
    p.options = {"filename": str(tmp_path / "ng.json"), "legacy_filename": str(tmp_path / "none.json")}
    monkeypatch.setattr(p, "_build_fonts", lambda: setattr(p, "_fonts", registry()))
    p.on_loaded()
    html = p._recovery_html()
    assert "Not ready" in html


def test_recovery_update_applies_and_persists(tmp_path, monkeypatch):
    p, v = make_ready_plugin(tmp_path, monkeypatch)
    p._route_api("api/recovery/update", FormReq("POST", {"element": "face", "property": "xy", "value": "33,44"}))
    assert tuple(v._state._state["face"].xy) == (33, 44)
    saved = json.load(open(tmp_path / "ng.json"))
    assert saved["profiles"]["default"]["edits"]["face"]["xy"] == "33,44"


def test_recovery_update_rejects_unknown_element(tmp_path, monkeypatch):
    p, v = make_ready_plugin(tmp_path, monkeypatch)
    result = p._route_api("api/recovery/update", FormReq("POST", {"element": "ghost", "property": "xy", "value": "1,2"}))
    assert isinstance(result, tuple) and result[1] == 404


def test_recovery_update_rejects_unsafe_property(tmp_path, monkeypatch):
    p, v = make_ready_plugin(tmp_path, monkeypatch)
    result = p._route_api("api/recovery/update", FormReq("POST", {"element": "face", "property": "value", "value": "evil"}))
    assert isinstance(result, tuple) and result[1] == 400
    assert v._state._state["face"].value == "x"  # unchanged


# --------------------------------------------------------------------------- #
# CSRF / non-JSON response handling in the editor JS (hardware-found, 2.9.5.9)
# --------------------------------------------------------------------------- #

def test_editor_js_guards_against_non_json_responses():
    """The api() helper must check content-type BEFORE calling r.json(), so a
    stale-CSRF 400 (which flask-wtf returns as an HTML page) yields a clear
    'session expired' message instead of the cryptic 'Unexpected token <'
    JSON parse error. Regression guard for the on-hardware finding."""
    js = tv.WEB_UI
    # content-type is inspected
    assert "content-type" in js
    # the raw json() call is no longer unconditional (guarded by a ct check)
    assert "ct.includes('application/json')" in js
    # a human-readable CSRF/session message exists
    assert "Session expired" in js
    # csrf detection on 400
    assert "csrf" in js.lower()


def test_recovery_and_editor_still_have_no_external_deps():
    for blob in (tv.WEB_UI, tv.RECOVERY_UI):
        low = blob.lower()
        assert "http://" not in low and "https://" not in low


# --------------------------------------------------------------------------- #
# Auto-align endpoint (editor upgrade, alpha4)
# --------------------------------------------------------------------------- #

def _align_setup(tmp_path, monkeypatch):
    """A view with line1/line2 dividers and several strip elements at varied
    positions, taken to READY."""
    store = tv.LayoutStore(str(tmp_path / "ng.json")); store.save(store.empty())
    p = tv.TweakViewNG()
    p.options = {"filename": str(tmp_path / "ng.json"), "legacy_filename": str(tmp_path / "none.json")}
    monkeypatch.setattr(p, "_build_fonts", lambda: setattr(p, "_fonts", registry()))
    p.on_loaded()
    from pwnagotchi.ui.components import Line
    v = FakeView(480, 320)
    # dividers
    v.add_element("line1", Line((0, 20, 480, 20)))
    v.add_element("line2", Line((0, 300, 480, 300)))
    # top strip (y < 20): varied y and x
    v.add_element("channel", Text("ch", (5, 2), font=fonts.Small))
    v.add_element("aps", Text("aps", (120, 6), font=fonts.Small))
    v.add_element("uptime", Text("up", (400, 10), font=fonts.Small))
    # bottom strip (y > 300)
    v.add_element("shakes", Text("pwnd", (5, 305), font=fonts.Small))
    v.add_element("mode", Text("AUTO", (420, 310), font=fonts.Small))
    p.on_ui_setup(v)
    return p, v


def test_align_element_left_right_center(tmp_path, monkeypatch):
    p, v = _align_setup(tmp_path, monkeypatch)
    # aps is a Text (2-point); left -> x=0
    r = p._route_api("api/align", Req("POST", {"element": "aps", "edge": "left"}))
    assert r["ok"] and r["edge"] == "left"
    assert v._state._state["aps"].xy[0] == 0
    # right -> x = width - element_width (480 - 18 default = 462)
    p._route_api("api/align", Req("POST", {"element": "aps", "edge": "right"}))
    assert v._state._state["aps"].xy[0] == 480 - 18
    # hcenter -> (480-18)//2
    p._route_api("api/align", Req("POST", {"element": "aps", "edge": "hcenter"}))
    assert v._state._state["aps"].xy[0] == (480 - 18) // 2


def test_align_element_top_bottom_within_region(tmp_path, monkeypatch):
    # aps sits at y=6, above line1 (y=20): its region is [0,20).
    p, v = _align_setup(tmp_path, monkeypatch)
    p._route_api("api/align", Req("POST", {"element": "aps", "edge": "top"}))
    assert v._state._state["aps"].xy[1] == 0          # top of its band
    p._route_api("api/align", Req("POST", {"element": "aps", "edge": "bottom"}))
    # bottom of the [0,20) band, minus the element height (default 12) -> 8
    assert v._state._state["aps"].xy[1] == 20 - 12


def test_align_element_persists_and_undoes(tmp_path, monkeypatch):
    p, v = _align_setup(tmp_path, monkeypatch)
    before = tuple(v._state._state["aps"].xy)
    p._route_api("api/align", Req("POST", {"element": "aps", "edge": "left"}))
    saved = json.load(open(tmp_path / "ng.json"))
    assert "aps" in saved["profiles"]["default"]["edits"]
    p._route_api("api/undo", Req("POST", {}))
    assert tuple(v._state._state["aps"].xy) == before


def test_align_element_rejects_bad_input(tmp_path, monkeypatch):
    p, v = _align_setup(tmp_path, monkeypatch)
    r = p._route_api("api/align", Req("POST", {"element": "aps", "edge": "sideways"}))
    assert isinstance(r, tuple) and r[1] == 400
    r = p._route_api("api/align", Req("POST", {"element": "ghost", "edge": "left"}))
    assert isinstance(r, tuple) and r[1] == 404
    r = p._route_api("api/align", Req("POST", {"edge": "left"}))
    assert isinstance(r, tuple) and r[1] == 400


def test_align_single_element_works(tmp_path, monkeypatch):
    # unlike the old strip design, aligning ONE element needs no second element
    store = tv.LayoutStore(str(tmp_path / "ng.json")); store.save(store.empty())
    p = tv.TweakViewNG()
    p.options = {"filename": str(tmp_path / "ng.json"), "legacy_filename": str(tmp_path / "none.json")}
    monkeypatch.setattr(p, "_build_fonts", lambda: setattr(p, "_fonts", registry()))
    p.on_loaded()
    from pwnagotchi.ui.components import Line
    v = FakeView(480, 320)
    v.add_element("line1", Line((0, 14, 480, 14)))
    v.add_element("line2", Line((0, 300, 480, 300)))
    v.add_element("aps", Text("aps", (120, 6), font=fonts.Small))  # lone element
    p.on_ui_setup(v)
    r = p._route_api("api/align", Req("POST", {"element": "aps", "edge": "left"}))
    assert r["ok"]  # no "need 2 elements" error any more
    assert v._state._state["aps"].xy[0] == 0


def test_editor_js_has_new_features():
    js = tv.WEB_UI
    # real-time drag
    assert "liveBox" in js
    # arrow-key nudge
    assert "ArrowLeft" in js and "shiftKey" in js
    # border warning + clear-on-release
    assert "crosses" in js and "classList.remove('warn')" in js
    # per-element align buttons (not the old strip ones)
    assert "alignEl" in js and "alignStrip" not in js
    # snap + zones toggles
    assert "toggleSnap" in js and "toggleZones" in js
    # safe-zone + labels + no-rebuild-on-drag guard
    assert "zone" in js and "blabel" in js and "drag.cur" in js


def test_startdrag_does_not_rebuild_overlay():
    """Regression: alpha4 startDrag called drawBoxes() which detached the
    dragged element and killed dragging. startDrag must NOT call drawBoxes;
    boxes carry user-select:none so the browser doesn't text-select instead."""
    js = tv.WEB_UI
    import re
    sd = re.search(r'function startDrag\(.*?\n\}', js, re.S).group(0)
    assert "drawBoxes()" not in sd, "startDrag must not rebuild the overlay"
    assert "preventDefault" in sd
    assert "setPointerCapture" in sd
    assert "user-select:none" in js
    assert "pointer-events:none" in js  # the name label must not steal the pointer
