"""Integration tests: Tweak View NG against the REAL Jayofelony framework.

These exercise NG's adapter, lifecycle and loader-compatibility against genuine
``pwnagotchi.ui.View`` / ``State`` / widgets and the real plugin loader, rather
than the hand-written stubs used by the offline unit suite. They are skipped
cleanly when a real checkout is not available (see conftest for how it is
located). Hardware-only behavior (framebuffer refresh, touch, display drivers)
is explicitly out of scope and marked as such in PROJECT_STATUS.
"""

import threading

import pytest


# --------------------------------------------------------------------------- #
# Framework reality checks: confirm NG's private-API assumptions hold on 2.9.5.8
# --------------------------------------------------------------------------- #

def test_real_view_exposes_expected_private_surface(real_view_factory):
    view = real_view_factory(480, 320)
    # The exact attributes JayUIAdapter reaches for.
    assert hasattr(view, "_lock")
    assert hasattr(view, "_state")
    state = view._state
    assert hasattr(state, "_state") and isinstance(state._state, dict)
    assert hasattr(state, "_lock")
    assert hasattr(state, "_changes") and isinstance(state._changes, dict)
    assert callable(view.width) and callable(view.height)
    assert callable(view.add_element) and callable(view.remove_element)


def test_real_view_update_accepts_force_kwarg(real_view_factory):
    view = real_view_factory()
    # NG calls ui.update(force=True); verify the real signature accepts it.
    import inspect
    sig = inspect.signature(view.update)
    assert "force" in sig.parameters


def test_real_components_match_ng_imports(real_pwnagotchi):
    # NG imports these names from the real module; ensure they exist.
    from pwnagotchi.ui.components import (  # noqa: F401
        Widget, Line, Rect, FilledRect, Text, LabeledValue,
    )


# --------------------------------------------------------------------------- #
# Adapter against the real View
# --------------------------------------------------------------------------- #

def _registry(fonts):
    names = ("Small", "BoldSmall", "Medium", "Bold", "BoldBig", "Huge")
    return {n: getattr(fonts, n) for n in names if getattr(fonts, n, None) is not None}


def test_snapshot_reads_real_default_widgets(ng_module, real_view_factory, real_fonts):
    view = real_view_factory(480, 320)
    adapter = ng_module.JayUIAdapter(view, _registry(real_fonts))
    snap = adapter.snapshot()
    assert snap["screen"] == {"width": 480, "height": 320}
    # Jayofelony's stock layout always includes these.
    for name in ("face", "status", "channel", "aps", "uptime", "line1", "line2"):
        assert name in snap["elements"], name
    # face is a Text widget and must expose an editable xy.
    assert "xy" in snap["elements"]["face"]["editable"]


def test_apply_property_mutates_real_widget_and_captures_original(ng_module, real_view_factory, real_fonts):
    view = real_view_factory(480, 320)
    adapter = ng_module.JayUIAdapter(view, _registry(real_fonts))
    originals = {}
    before = tuple(view._state._state["face"].xy)
    changed = adapter.apply_properties("face", {"xy": "123,45"}, originals)
    assert changed == ["xy"]
    assert tuple(view._state._state["face"].xy) == (123, 45)
    assert tuple(originals["face"]["xy"]) == before
    # the real change bus was notified
    assert view._state._changes.get("face") is True


def test_font_application_uses_real_font_objects(ng_module, real_view_factory, real_fonts):
    view = real_view_factory()
    adapter = ng_module.JayUIAdapter(view, _registry(real_fonts))
    adapter.apply_properties("face", {"font": "Bold"}, {})
    assert view._state._state["face"].font is real_fonts.Bold
    with pytest.raises(ValueError):
        adapter.apply_properties("face", {"font": "NoSuchFont"}, {})


def test_add_and_remove_real_shape_widgets(ng_module, real_view_factory, real_fonts):
    view = real_view_factory(320, 240)
    adapter = ng_module.JayUIAdapter(view, _registry(real_fonts))
    from pwnagotchi.ui.components import Line, Rect, FilledRect
    adapter.add_shape("ng_line", {"type": "line", "properties": {"xy": [0, 0, 50, 50], "color": 255}})
    adapter.add_shape("ng_rect", {"type": "rect", "properties": {"xy": [1, 1, 40, 30], "color": 255}})
    adapter.add_shape("ng_frect", {"type": "filled_rect", "properties": {"xy": [2, 2, 20, 20], "color": 0}})
    assert isinstance(view._state._state["ng_line"], Line)
    assert isinstance(view._state._state["ng_rect"], Rect)
    assert isinstance(view._state._state["ng_frect"], FilledRect)
    assert adapter.remove("ng_line") is True
    assert "ng_line" not in view._state._state
    assert adapter.remove("ng_line") is False


def test_custom_ellipse_draws_on_real_canvas(ng_module, real_view_factory, real_fonts):
    """NG's Ellipse/FilledEllipse subclass the real Widget; verify draw() works
    against a real PIL drawer on a real-sized canvas."""
    from PIL import Image, ImageDraw
    view = real_view_factory(480, 320)
    adapter = ng_module.JayUIAdapter(view, _registry(real_fonts))
    adapter.add_shape("ng_ell", {"type": "ellipse", "properties": {"xy": [10, 10, 60, 40], "color": 255, "width": 2}})
    adapter.add_shape("ng_fell", {"type": "filled_ellipse", "properties": {"xy": [10, 10, 60, 40], "color": 255}})
    img = Image.new("1", (view.width(), view.height()))
    drawer = ImageDraw.Draw(img)
    # Must not raise against a genuine Pillow drawer.
    view._state._state["ng_ell"].draw(img, drawer)
    view._state._state["ng_fell"].draw(img, drawer)


def test_resolution_independence_against_real_view(ng_module, real_view_factory, real_fonts):
    for w, h in [(250, 122), (296, 128), (480, 320), (320, 480), (800, 480)]:
        view = real_view_factory(w, h)
        adapter = ng_module.JayUIAdapter(view, _registry(real_fonts))
        assert adapter.dimensions() == (w, h)
        adapter.apply_properties("face", {"xy": [w + 500, h + 500]}, {})
        x, y = view._state._state["face"].xy
        assert 0 <= x < w and 0 <= y < h


def test_lock_order_matches_real_view_update(ng_module, real_view_factory, real_fonts):
    """NG's adapter.locked() acquires View._lock then State._lock, the same
    order real View.update() uses. Run concurrent snapshot/edit against the real
    View and assert no deadlock or exception."""
    view = real_view_factory()
    adapter = ng_module.JayUIAdapter(view, _registry(real_fonts))
    errors = []

    def reader():
        try:
            for _ in range(200):
                adapter.snapshot()
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    def writer():
        try:
            for i in range(200):
                adapter.apply_properties("face", {"xy": [i % 400, i % 300]}, {})
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=reader), threading.Thread(target=writer)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "possible deadlock against real View lock"
    assert not errors


# --------------------------------------------------------------------------- #
# Real plugin-loader compatibility
# --------------------------------------------------------------------------- #

def test_real_loader_static_metadata(real_pwnagotchi):
    """Some Jayofelony builds read plugin metadata statically via
    ``get_plugin_metadata()`` without importing. That helper was added *after*
    the 2.9.5.8 tag, so skip when the pinned target lacks it; when present,
    verify NG's ``__version__``/``__author__``/``__description__`` parse."""
    import pwnagotchi.plugins as plugins
    if not hasattr(plugins, "get_plugin_metadata"):
        pytest.skip("get_plugin_metadata not present in this Jayofelony build (post-2.9.5.8)")
    from conftest import SUITE_ROOT  # type: ignore
    meta = plugins.get_plugin_metadata(str(SUITE_ROOT / "tweak_view_ng.py"))
    assert meta["__version__"]
    assert meta["__author__"]
    assert meta["__description__"]


def test_real_loader_imports_ng_module(real_pwnagotchi, tmp_path):
    """Exercise the genuine importlib-based load_from_file() path on NG."""
    import pwnagotchi.plugins as plugins
    from conftest import SUITE_ROOT  # type: ignore
    name, instance = plugins.load_from_file(str(SUITE_ROOT / "tweak_view_ng.py"))
    assert name == "tweak_view_ng"
    assert hasattr(instance, "TweakViewNG")
    # The class really subclasses the real plugins.Plugin base.
    assert issubclass(instance.TweakViewNG, plugins.Plugin)


def test_ng_plugin_instance_is_real_plugin_subclass(ng_module, real_pwnagotchi):
    import pwnagotchi.plugins as plugins
    assert issubclass(ng_module.TweakViewNG, plugins.Plugin)


# --------------------------------------------------------------------------- #
# Full lifecycle against the real View
# --------------------------------------------------------------------------- #

def test_full_lifecycle_on_real_view(ng_module, real_view_factory, real_fonts, tmp_path, monkeypatch):
    view = real_view_factory(480, 320)
    plugin = ng_module.TweakViewNG()
    plugin.options = {
        "filename": str(tmp_path / "ng.json"),
        "legacy_filename": str(tmp_path / "none.json"),
    }
    plugin.on_loaded()
    # Use the real font registry rather than rebuilding (truetype lookups differ
    # by platform); point NG's font map at the real fonts.
    monkeypatch.setattr(plugin, "_build_fonts", lambda: setattr(plugin, "_fonts", _registry(real_fonts)))
    plugin.on_ui_setup(view)
    # An edit through the public API path mutates the real widget + persists.

    class Req:
        def __init__(self, method="POST", data=None):
            self.method = method
            self._data = data

        def get_json(self, silent=True):
            return self._data

    # jsonify() needs a real Flask app context here (the genuine flask, not a
    # stub), which is itself part of verifying NG works on the real stack.
    import flask
    app = flask.Flask(__name__)
    with app.app_context():
        res = plugin._route_api("api/update", Req(data={"element": "face", "properties": {"xy": [77, 88]}}))
        res_json = res.get_json() if hasattr(res, "get_json") else res
        assert res_json["ok"]
        assert tuple(view._state._state["face"].xy) == (77, 88)
        # Undo restores the real widget.
        plugin._route_api("api/undo", Req(data={}))
    # Unload cleans up and restores.
    plugin.on_unload(view)


# --------------------------------------------------------------------------- #
# Real Flask/Jinja webhook behavior (harvest items 4, 6, 8 end-to-end)
# --------------------------------------------------------------------------- #

def _ready_plugin_real(ng_module, real_view_factory, real_fonts, tmp_path, monkeypatch, extra=None):
    view = real_view_factory(480, 320)
    for name, widget in (extra or {}).items():
        view.add_element(name, widget)
    p = ng_module.TweakViewNG()
    p.options = {"filename": str(tmp_path / "ng.json"), "legacy_filename": str(tmp_path / "none.json")}
    monkeypatch.setattr(p, "_build_fonts", lambda: setattr(p, "_fonts", _registry(real_fonts)))
    p.on_loaded()
    p.on_ui_setup(view)
    return p, view


def test_recovery_page_renders_through_real_jinja(ng_module, real_view_factory, real_fonts, tmp_path, monkeypatch):
    """The real Flask render_template_string autoescapes; hostile widget names
    and label values must come back entity-escaped, proving item 6 holds on the
    genuine stack."""
    import flask
    from pwnagotchi.ui.components import LabeledValue
    hostile_label = "<script>alert('x')</script>"
    # LabeledValue exposes an editable 'label' prop, so the hostile value is
    # actually rendered into the recovery page and must be escaped. The element
    # name is hostile too.
    extra = {"evil<b>name</b>": LabeledValue(label=hostile_label, value="v", position=(1, 1),
                                             label_font=real_fonts.Bold, text_font=real_fonts.Small)}
    p, view = _ready_plugin_real(ng_module, real_view_factory, real_fonts, tmp_path, monkeypatch, extra)
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_request_context("/"):
        # csrf_token() is provided by flask_wtf in production; stub it for render.
        flask.current_app.jinja_env.globals.setdefault("csrf_token", lambda: "tok")
        html = p._recovery_html()
    # Neither the hostile name nor the hostile label value appears as raw markup.
    assert "<script>alert('x')</script>" not in html
    assert "evil<b>name</b>" not in html
    assert "&lt;script&gt;" in html  # escaped hostile label value is present


def test_webhook_not_ready_returns_503_through_real_stack(ng_module, real_pwnagotchi, tmp_path, monkeypatch):
    import flask
    p = ng_module.TweakViewNG()
    p.options = {"filename": str(tmp_path / "ng.json"), "legacy_filename": str(tmp_path / "none.json")}
    monkeypatch.setattr(p, "_build_fonts", lambda: setattr(p, "_fonts", {}))
    p.on_loaded()  # not ui-set-up yet
    app = flask.Flask(__name__)
    with app.test_request_context("/plugins/tweak_view_ng/api/update", method="POST", json={"element": "face", "properties": {}}):
        result = p.on_webhook("api/update", flask.request)
    # Flask-style (body, status) tuple with 503
    assert isinstance(result, tuple) and result[1] == 503


def test_webhook_api_ready_before_setup_real(ng_module, real_pwnagotchi, tmp_path, monkeypatch):
    import flask
    p = ng_module.TweakViewNG()
    p.options = {"filename": str(tmp_path / "ng.json"), "legacy_filename": str(tmp_path / "none.json")}
    monkeypatch.setattr(p, "_build_fonts", lambda: setattr(p, "_fonts", {}))
    p.on_loaded()
    app = flask.Flask(__name__)
    with app.test_request_context("/plugins/tweak_view_ng/api/ready", method="GET"):
        resp = p.on_webhook("api/ready", flask.request)
    # jsonify returns a Response; pull JSON back out
    data = resp.get_json() if hasattr(resp, "get_json") else resp
    assert data["ready"] is False
    assert data["phase"] == ng_module.TweakViewNG.PHASE_WAITING_UI
