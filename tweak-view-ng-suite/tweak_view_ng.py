"""Tweak View NG - safe, resolution-independent Pwnagotchi UI layout editor.

Primary target: Jayofelony Pwnagotchi v2.9.5.8 (64-bit).
The only intentional private-API access is isolated in JayUIAdapter.
"""

import copy
import json
import logging
import os
import tempfile
import time
from contextlib import contextmanager
from textwrap import TextWrapper

import pwnagotchi
import pwnagotchi.plugins as plugins
import pwnagotchi.ui.fonts as fonts
from pwnagotchi.ui.components import Widget, Line, Rect, FilledRect, Text, LabeledValue
from PIL import ImageFont
from flask import abort, jsonify, render_template_string


LOG = logging.getLogger(__name__)
SCHEMA_VERSION = 1
TARGET = "jayofelony-pwnagotchi-2.9.5.8-64bit"
SAFE_PROPS = {
    "xy", "font", "text_font", "label_font", "alt_font",
    "label", "label_spacing", "max_length", "width", "wrap",
    "color", "bgcolor", "fill",
}
FONT_PROPS = {"font", "text_font", "label_font", "alt_font"}
INT_PROPS = {"label_spacing", "max_length", "width", "color", "bgcolor", "fill"}
BOOL_PROPS = {"wrap"}


class Ellipse(Widget):
    def __init__(self, xy, color=0, width=1, fill=None):
        super().__init__(xy, color)
        self.width = width
        self.fill = fill

    def draw(self, canvas, drawer):
        drawer.ellipse(self.xy, outline=self.color, fill=self.fill, width=self.width)


class FilledEllipse(Widget):
    def __init__(self, xy, color=0):
        super().__init__(xy, color)

    def draw(self, canvas, drawer):
        drawer.ellipse(self.xy, fill=self.color)


def _safe_copy(value):
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, tuple):
        return tuple(value)
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict):
        return dict(value)
    return value


def _parse_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class JayUIAdapter:
    """All Jayofelony private UI access lives here.

    Lock order mirrors View.update(): View._lock -> State._lock.
    Public View methods are used for add/remove where practical.
    """

    def __init__(self, ui, font_registry, logger=None):
        self.ui = ui
        self.fonts = font_registry
        self.log = logger or LOG

    @contextmanager
    def locked(self):
        view_lock = getattr(self.ui, "_lock", None)
        state = getattr(self.ui, "_state", None)
        state_lock = getattr(state, "_lock", None)
        if view_lock:
            view_lock.acquire()
        try:
            if state_lock:
                state_lock.acquire()
            try:
                yield
            finally:
                if state_lock:
                    state_lock.release()
        finally:
            if view_lock:
                view_lock.release()

    def dimensions(self):
        return int(self.ui.width()), int(self.ui.height())

    def _raw_state_unlocked(self):
        state = getattr(self.ui, "_state", None)
        raw = getattr(state, "_state", None)
        if not isinstance(raw, dict):
            raise RuntimeError("Unsupported Pwnagotchi UI state implementation")
        return raw

    def element_names(self):
        with self.locked():
            return list(self._raw_state_unlocked().keys())

    def has(self, name):
        with self.locked():
            return name in self._raw_state_unlocked()

    def get_widget(self, name):
        with self.locked():
            return self._raw_state_unlocked().get(name)

    def _font_name(self, font_obj):
        for name, obj in self.fonts.items():
            if font_obj is obj or font_obj == obj:
                return name
        size = getattr(font_obj, "size", None)
        path = getattr(font_obj, "path", None)
        if size is not None:
            return "custom:%s:%s" % (os.path.basename(str(path or "font")), size)
        return "Unknown"

    def _serialize_prop(self, key, value):
        if key in FONT_PROPS:
            return self._font_name(value)
        if key == "xy":
            try:
                return list(value)
            except Exception:
                return value
        if isinstance(value, (str, int, float, bool, type(None))):
            return value
        if isinstance(value, (list, tuple)):
            out = []
            for item in value:
                if isinstance(item, (str, int, float, bool, type(None))):
                    out.append(item)
                else:
                    return None
            return out
        return None

    def snapshot(self):
        width, height = self.dimensions()
        out = {"screen": {"width": width, "height": height}, "elements": {}}
        with self.locked():
            raw = self._raw_state_unlocked()
            for name, widget in list(raw.items()):
                props = {}
                editable = []
                for key in SAFE_PROPS:
                    if not hasattr(widget, key):
                        continue
                    try:
                        value = self._serialize_prop(key, getattr(widget, key))
                    except Exception:
                        continue
                    if value is not None:
                        props[key] = value
                        editable.append(key)
                out["elements"][name] = {"type": type(widget).__name__, "properties": props, "editable": sorted(editable)}
        return out

    def capture_original(self, originals, element, prop):
        with self.locked():
            widget = self._raw_state_unlocked().get(element)
            if widget is None or not hasattr(widget, prop):
                return False
            originals.setdefault(element, {})
            if prop not in originals[element]:
                originals[element][prop] = _safe_copy(getattr(widget, prop))
            return True

    def _resolve_xy(self, value, widget):
        if isinstance(value, str):
            parts = [int(float(x.strip())) for x in value.split(",") if x.strip()]
        elif isinstance(value, (list, tuple)):
            parts = [int(float(x)) for x in value]
        else:
            raise ValueError("xy must be a list/tuple or comma-separated string")
        current = getattr(widget, "xy", ())
        required = 4 if isinstance(widget, (Line, Rect, FilledRect, Ellipse, FilledEllipse)) or len(current) == 4 else 2
        if len(parts) < required:
            raise ValueError("xy requires %d coordinates for %s" % (required, type(widget).__name__))
        parts = parts[:required]
        width, height = self.dimensions()
        for i in range(required):
            bound = width if i % 2 == 0 else height
            if parts[i] < 0:
                parts[i] = bound + parts[i]
            parts[i] = max(0, min(parts[i], max(0, bound - 1)))
        return tuple(parts) if isinstance(current, tuple) else list(parts)

    def normalize_property(self, widget, prop, value):
        if prop not in SAFE_PROPS:
            raise ValueError("Property %s is not editable" % prop)
        if not hasattr(widget, prop):
            raise ValueError("%s has no property %s" % (type(widget).__name__, prop))
        if prop == "xy":
            return self._resolve_xy(value, widget)
        if prop in FONT_PROPS:
            if value not in self.fonts:
                raise ValueError("Unknown font: %s" % value)
            return self.fonts[value]
        if prop in INT_PROPS:
            return int(value)
        if prop in BOOL_PROPS:
            return _parse_bool(value)
        if prop == "label":
            return None if value is None else str(value)
        return value

    def apply_properties(self, element, properties, originals=None):
        if not isinstance(properties, dict):
            raise ValueError("properties must be an object")
        changed = []
        with self.locked():
            raw = self._raw_state_unlocked()
            widget = raw.get(element)
            if widget is None:
                raise KeyError("UI element not found: %s" % element)
            for prop, value in properties.items():
                if prop not in SAFE_PROPS or not hasattr(widget, prop):
                    continue
                if originals is not None:
                    originals.setdefault(element, {})
                    if prop not in originals[element]:
                        originals[element][prop] = _safe_copy(getattr(widget, prop))
                normalized = self.normalize_property(widget, prop, value)
                setattr(widget, prop, normalized)
                if prop == "max_length" and hasattr(widget, "wrap"):
                    widget.wrapper = TextWrapper(width=int(normalized), replace_whitespace=False) if widget.wrap else None
                changed.append(prop)
            state = getattr(self.ui, "_state", None)
            changes = getattr(state, "_changes", None)
            if isinstance(changes, dict) and changed:
                changes[element] = True
        return changed

    def restore_properties(self, element, properties):
        if not properties:
            return
        with self.locked():
            raw = self._raw_state_unlocked()
            widget = raw.get(element)
            if widget is None:
                return
            for prop, value in properties.items():
                if hasattr(widget, prop):
                    setattr(widget, prop, _safe_copy(value))
                    if prop == "max_length" and hasattr(widget, "wrap"):
                        widget.wrapper = TextWrapper(width=int(value), replace_whitespace=False) if widget.wrap else None
            changes = getattr(getattr(self.ui, "_state", None), "_changes", None)
            if isinstance(changes, dict):
                changes[element] = True

    def add_shape(self, name, spec):
        shape_type = spec.get("type")
        props = dict(spec.get("properties") or {})
        color = int(props.get("color", 0xFF))
        xy = props.get("xy", [0, 0, 10, 10])
        width = int(props.get("width", 1))
        fill = props.get("fill", None)
        if fill not in (None, "", "null", "None"):
            fill = int(fill)
        else:
            fill = None
        dummy = Line([0, 0, 1, 1])
        dummy.xy = [0, 0, 1, 1]
        parsed_xy = self._resolve_xy(xy, dummy)
        if shape_type == "line":
            widget = Line(parsed_xy, color=color, width=width)
        elif shape_type == "rect":
            widget = Rect(parsed_xy, color=color)
        elif shape_type == "filled_rect":
            widget = FilledRect(parsed_xy, color=color)
        elif shape_type == "ellipse":
            widget = Ellipse(parsed_xy, color=color, width=width, fill=fill)
        elif shape_type == "filled_ellipse":
            widget = FilledEllipse(parsed_xy, color=color)
        else:
            raise ValueError("Unknown shape type: %s" % shape_type)
        with self.locked():
            self.ui.add_element(name, widget)
        return widget

    def remove(self, name):
        with self.locked():
            raw = self._raw_state_unlocked()
            if name in raw:
                self.ui.remove_element(name)
                return True
        return False

    def redraw(self):
        try:
            self.ui.update(force=True)
        except TypeError:
            self.ui.update()


class LayoutStore:
    def __init__(self, filename, backup=True):
        self.filename = filename
        self.backup = backup

    def empty(self):
        return {"schema": SCHEMA_VERSION, "target": TARGET, "active_profile": "default", "profiles": {"default": {"edits": {}, "shapes": {}}}, "meta": {}}

    def load(self):
        if not os.path.isfile(self.filename):
            return self.empty()
        with open(self.filename, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("layout file must contain a JSON object")
        if data.get("schema") != SCHEMA_VERSION:
            raise ValueError("unsupported layout schema: %r" % data.get("schema"))
        data.setdefault("profiles", {})
        data.setdefault("active_profile", "default")
        data["profiles"].setdefault("default", {"edits": {}, "shapes": {}})
        return data

    def save(self, data):
        parent = os.path.dirname(self.filename) or "."
        os.makedirs(parent, exist_ok=True)
        if self.backup and os.path.isfile(self.filename):
            try:
                with open(self.filename, "rb") as src, open(self.filename + ".bak", "wb") as dst:
                    dst.write(src.read())
            except Exception:
                LOG.warning("Tweak View NG: could not create backup", exc_info=True)
        fd, tmp = tempfile.mkstemp(prefix=".tweak_view_ng.", suffix=".json", dir=parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.filename)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


def import_legacy(data):
    if not isinstance(data, dict):
        raise ValueError("legacy config must be a JSON object")
    profile = {"edits": {}, "shapes": {}}
    custom = data.get("__custom_shapes__", {})
    if isinstance(custom, dict):
        for name, old in custom.items():
            old_type = str(old.get("type", "CustomLine"))
            type_map = {"CustomLine": "line", "CustomRect": "rect", "CustomEllipse": "ellipse"}
            profile["shapes"][name] = {"type": type_map.get(old_type, "line"), "properties": dict(old.get("props") or {})}
    for key, value in data.items():
        if not (isinstance(key, str) and key.startswith("VSS.")):
            continue
        parts = key.split(".", 2)
        if len(parts) != 3:
            continue
        _, element, prop = parts
        if prop not in SAFE_PROPS:
            continue
        profile["edits"].setdefault(element, {})[prop] = value
    return profile


class TweakViewNG(plugins.Plugin):
    __author__ = "OpenAI + Pwnagotchi community lineage (NurseJackass/Sniffleupagus/BraedenP232)"
    __version__ = "0.1.0-alpha1"
    __license__ = "GPL3"
    __description__ = "Safe, resolution-independent Pwnagotchi UI layout editor for Jayofelony 2.9.5.8."

    DEFAULTS = {"filename": "/etc/pwnagotchi/tweak_view_ng.json", "legacy_filename": "/etc/pwnagotchi/tweak_view.json", "auto_import_legacy": True, "backup": True, "history_limit": 50, "strict_version": False}

    def __init__(self):
        self.options = {}
        self._ui = None
        self._agent = None
        self._adapter = None
        self._store = None
        self._layout = None
        self._originals = {}
        self._history = []
        self._redo = []
        self._fonts = {}
        self._pending_missing = set()
        self._last_error = None

    def _merge_options(self):
        merged = dict(self.DEFAULTS)
        merged.update(self.options or {})
        self.options = merged

    def _build_fonts(self):
        names = ("Small", "BoldSmall", "Medium", "Bold", "BoldBig", "Huge")
        self._fonts = {name: getattr(fonts, name) for name in names if hasattr(fonts, name)}
        for size in (6, 7, 8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 30, 35, 42, 48, 54, 60, 72, 90, 120):
            for prefix, family in (("Deja", "DejaVuSansMono.ttf"), ("DejaB", "DejaVuSansMono-Bold.ttf")):
                try:
                    self._fonts["%s %s" % (prefix, size)] = ImageFont.truetype(family, size)
                except Exception:
                    pass

    def _profile(self):
        name = self._layout.get("active_profile", "default")
        profiles = self._layout.setdefault("profiles", {})
        return profiles.setdefault(name, {"edits": {}, "shapes": {}})

    def _snapshot_config(self):
        return copy.deepcopy(self._layout)

    def _push_history(self):
        self._history.append(self._snapshot_config())
        limit = max(1, int(self.options.get("history_limit", 50)))
        if len(self._history) > limit:
            del self._history[:-limit]
        self._redo = []

    def _restore_runtime_to_originals(self):
        if not self._adapter:
            return
        for element, props in self._originals.items():
            self._adapter.restore_properties(element, props)
        for name in list(self._profile().get("shapes", {}).keys()):
            self._adapter.remove(name)

    def _apply_profile(self, redraw=True):
        if not self._adapter:
            return {"applied": 0, "missing": []}
        profile = self._profile()
        applied = 0
        missing = []
        for element, props in profile.get("edits", {}).items():
            if not self._adapter.has(element):
                missing.append(element)
                continue
            try:
                applied += len(self._adapter.apply_properties(element, props, self._originals))
            except Exception as exc:
                LOG.warning("Tweak View NG: failed applying %s: %s", element, exc)
        for name, spec in profile.get("shapes", {}).items():
            try:
                self._adapter.add_shape(name, spec)
                applied += 1
            except Exception as exc:
                LOG.warning("Tweak View NG: failed shape %s: %s", name, exc)
        self._pending_missing = set(missing)
        if redraw:
            self._adapter.redraw()
        return {"applied": applied, "missing": missing}

    def _save(self):
        self._layout.setdefault("meta", {})["last_saved"] = int(time.time())
        self._layout["meta"]["pwnagotchi_version"] = getattr(pwnagotchi, "__version__", "unknown")
        self._store.save(self._layout)

    def _maybe_import_legacy(self):
        if os.path.isfile(self.options["filename"]):
            return False
        legacy = self.options.get("legacy_filename")
        if not self.options.get("auto_import_legacy", True) or not legacy or not os.path.isfile(legacy):
            return False
        try:
            with open(legacy, "r", encoding="utf-8") as handle:
                old = json.load(handle)
            converted = import_legacy(old)
            self._layout = self._store.empty()
            self._layout["profiles"]["default"] = converted
            self._layout["meta"]["imported_legacy"] = legacy
            self._layout["meta"]["imported_at"] = int(time.time())
            self._save()
            LOG.info("Tweak View NG: imported legacy layout from %s", legacy)
            return True
        except Exception as exc:
            LOG.warning("Tweak View NG: legacy import failed: %s", exc)
            self._last_error = "Legacy import failed: %s" % exc
            return False

    def on_loaded(self):
        self._merge_options()
        ver = getattr(pwnagotchi, "__version__", "unknown")
        if self.options.get("strict_version") and ver != "2.9.5.8":
            raise RuntimeError("Tweak View NG alpha targets Pwnagotchi 2.9.5.8; found %s" % ver)
        LOG.info("Tweak View NG %s loaded (Pwnagotchi %s)", self.__version__, ver)

    def on_ready(self, agent):
        self._agent = agent

    def on_ui_setup(self, ui):
        self._ui = ui
        self._build_fonts()
        self._adapter = JayUIAdapter(ui, self._fonts, LOG)
        self._store = LayoutStore(self.options["filename"], bool(self.options.get("backup", True)))
        try:
            self._layout = self._store.load()
        except Exception as exc:
            self._last_error = "Layout load failed: %s" % exc
            LOG.error("Tweak View NG: %s", self._last_error)
            self._layout = self._store.empty()
        self._maybe_import_legacy()
        result = self._apply_profile(redraw=False)
        LOG.info("Tweak View NG ready: %dx%d, %d properties/shapes applied, %d pending", ui.width(), ui.height(), result["applied"], len(result["missing"]))

    def on_ui_update(self, ui):
        if not self._pending_missing or not self._adapter:
            return
        for element in list(self._pending_missing):
            if not self._adapter.has(element):
                continue
            props = self._profile().get("edits", {}).get(element, {})
            try:
                self._adapter.apply_properties(element, props, self._originals)
                self._pending_missing.discard(element)
            except Exception as exc:
                LOG.debug("Tweak View NG pending apply %s failed: %s", element, exc)

    def on_unload(self, ui):
        try:
            for name in list(self._profile().get("shapes", {}).keys()):
                self._adapter.remove(name)
            for element, props in self._originals.items():
                self._adapter.restore_properties(element, props)
            self._adapter.redraw()
        except Exception:
            LOG.exception("Tweak View NG: unload restore failed")

    def _state_payload(self):
        snap = self._adapter.snapshot()
        profile = self._profile()
        snap.update({"plugin_version": self.__version__, "target": TARGET, "pwnagotchi_version": getattr(pwnagotchi, "__version__", "unknown"), "active_profile": self._layout.get("active_profile", "default"), "profiles": sorted(self._layout.get("profiles", {}).keys()), "configured": profile, "history": {"undo": len(self._history), "redo": len(self._redo)}, "pending_missing": sorted(self._pending_missing), "last_error": self._last_error, "fonts": sorted(self._fonts.keys())})
        return snap

    def _json_body(self, request):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        return data

    def _route_api(self, path, request):
        if path == "api/state" and request.method == "GET":
            return jsonify(self._state_payload())
        if path == "api/health" and request.method == "GET":
            width, height = self._adapter.dimensions()
            return jsonify({"ok": True, "plugin": self.__version__, "target": TARGET, "pwnagotchi": getattr(pwnagotchi, "__version__", "unknown"), "screen": [width, height], "pending": sorted(self._pending_missing)})
        if path == "api/update" and request.method == "POST":
            data = self._json_body(request)
            element = str(data.get("element", "")).strip()
            props = data.get("properties", {})
            if not element:
                return jsonify({"ok": False, "error": "element required"}), 400
            if not self._adapter.has(element):
                return jsonify({"ok": False, "error": "unknown element"}), 404
            widget = self._adapter.get_widget(element)
            validated = {}
            for prop, value in props.items():
                if prop in SAFE_PROPS and hasattr(widget, prop):
                    self._adapter.normalize_property(widget, prop, value)
                    validated[prop] = value
            self._push_history()
            profile = self._profile()
            if element in profile.setdefault("shapes", {}):
                profile["shapes"][element].setdefault("properties", {}).update(validated)
                self._adapter.add_shape(element, profile["shapes"][element])
                changed = sorted(validated.keys())
            else:
                profile.setdefault("edits", {}).setdefault(element, {}).update(validated)
                changed = self._adapter.apply_properties(element, validated, self._originals)
            self._save()
            self._adapter.redraw()
            return jsonify({"ok": True, "changed": changed})
        if path == "api/revert" and request.method == "POST":
            data = self._json_body(request)
            element = str(data.get("element", "")).strip()
            edits = self._profile().setdefault("edits", {})
            if element not in edits:
                return jsonify({"ok": True, "changed": []})
            self._push_history()
            touched = list(edits[element].keys())
            originals = self._originals.get(element, {})
            self._adapter.restore_properties(element, {k: originals[k] for k in touched if k in originals})
            edits.pop(element, None)
            self._save()
            self._adapter.redraw()
            return jsonify({"ok": True, "changed": touched})
        if path == "api/add_shape" and request.method == "POST":
            data = self._json_body(request)
            name = str(data.get("name", "")).strip()
            if not name or name.startswith("__"):
                return jsonify({"ok": False, "error": "valid name required"}), 400
            if self._adapter.has(name) and name not in self._profile().get("shapes", {}):
                return jsonify({"ok": False, "error": "name collides with existing UI element"}), 409
            spec = {"type": str(data.get("type", "line")), "properties": dict(data.get("properties") or {})}
            self._push_history()
            self._adapter.add_shape(name, spec)
            self._profile().setdefault("shapes", {})[name] = spec
            self._save()
            self._adapter.redraw()
            return jsonify({"ok": True})
        if path == "api/delete_shape" and request.method == "POST":
            data = self._json_body(request)
            name = str(data.get("name", "")).strip()
            shapes = self._profile().setdefault("shapes", {})
            if name not in shapes:
                return jsonify({"ok": False, "error": "custom shape not found"}), 404
            self._push_history()
            self._adapter.remove(name)
            shapes.pop(name, None)
            self._save()
            self._adapter.redraw()
            return jsonify({"ok": True})
        if path == "api/reset" and request.method == "POST":
            self._push_history()
            profile = self._profile()
            for name in list(profile.get("shapes", {}).keys()):
                self._adapter.remove(name)
            for element, props in self._originals.items():
                self._adapter.restore_properties(element, props)
            profile["edits"] = {}
            profile["shapes"] = {}
            self._pending_missing.clear()
            self._save()
            self._adapter.redraw()
            return jsonify({"ok": True})
        if path == "api/undo" and request.method == "POST":
            if not self._history:
                return jsonify({"ok": False, "error": "nothing to undo"}), 409
            current = self._snapshot_config()
            previous = self._history.pop()
            self._redo.append(current)
            self._restore_runtime_to_originals()
            self._layout = previous
            result = self._apply_profile(redraw=True)
            self._save()
            return jsonify({"ok": True, "result": result})
        if path == "api/redo" and request.method == "POST":
            if not self._redo:
                return jsonify({"ok": False, "error": "nothing to redo"}), 409
            current = self._snapshot_config()
            nxt = self._redo.pop()
            self._history.append(current)
            self._restore_runtime_to_originals()
            self._layout = nxt
            result = self._apply_profile(redraw=True)
            self._save()
            return jsonify({"ok": True, "result": result})
        if path == "api/export" and request.method == "GET":
            return jsonify(self._layout)
        if path == "api/import" and request.method == "POST":
            data = self._json_body(request)
            if "schema" in data and data.get("schema") != SCHEMA_VERSION:
                return jsonify({"ok": False, "error": "unsupported layout schema"}), 400
            self._push_history()
            self._restore_runtime_to_originals()
            if data.get("schema") == SCHEMA_VERSION and "profiles" in data:
                self._layout = copy.deepcopy(data)
            else:
                converted = import_legacy(copy.deepcopy(data))
                self._layout = self._store.empty()
                self._layout["profiles"]["default"] = converted
                self._layout["meta"]["manual_legacy_import"] = int(time.time())
            result = self._apply_profile(redraw=True)
            self._save()
            return jsonify({"ok": True, "result": result})
        if path == "api/profile" and request.method == "POST":
            data = self._json_body(request)
            name = str(data.get("name", "")).strip()
            if not name or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in name):
                return jsonify({"ok": False, "error": "invalid profile name"}), 400
            self._push_history()
            self._restore_runtime_to_originals()
            self._layout.setdefault("profiles", {}).setdefault(name, {"edits": {}, "shapes": {}})
            self._layout["active_profile"] = name
            result = self._apply_profile(redraw=True)
            self._save()
            return jsonify({"ok": True, "result": result})
        abort(404)

    def on_webhook(self, path, request):
        try:
            path = (path or "").lstrip("/")
            if path.startswith("api/"):
                return self._route_api(path, request)
            if request.method == "GET" and path in ("", "/"):
                return render_template_string(WEB_UI, version=self.__version__)
            abort(404)
        except Exception as exc:
            self._last_error = str(exc)
            LOG.exception("Tweak View NG webhook failure")
            return jsonify({"ok": False, "error": str(exc)}), 500


WEB_UI = r"""
<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no"><meta name="csrf_token" content="{{ csrf_token() }}"><title>Tweak View NG</title>
<style>:root{--bg:#0b0e10;--panel:#14191d;--line:#263039;--text:#d7e0e5;--dim:#83919a;--a:#5bd1ff;--ok:#79e28b;--bad:#ff6b78}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px system-ui,sans-serif;height:100vh;overflow:hidden}header{height:50px;display:flex;align-items:center;gap:10px;padding:0 12px;background:var(--panel);border-bottom:1px solid var(--line)}header b{color:var(--a);letter-spacing:.08em}.grow{flex:1}.muted{color:var(--dim);font-size:12px}button,select,input{background:#0d1114;color:var(--text);border:1px solid #34414b;border-radius:5px;padding:7px}button{cursor:pointer}button:hover{border-color:var(--a)}main{display:grid;grid-template-columns:230px 1fr 300px;height:calc(100vh - 50px)}aside,.props{background:var(--panel);overflow:auto;padding:10px}.left{border-right:1px solid var(--line)}.props{border-left:1px solid var(--line)}#elements{list-style:none;padding:0;margin:8px 0}.el{padding:7px;border:1px solid transparent;border-radius:4px;cursor:pointer}.el:hover,.el.sel{border-color:var(--a);background:#101a20}.type{display:block;color:var(--dim);font-size:11px}.stage{overflow:auto;display:flex;align-items:center;justify-content:center;padding:18px}.frame{position:relative;border:1px solid #4b5b66;background:#fff;box-shadow:0 10px 35px #0008}.frame img{display:block;image-rendering:pixelated;max-width:none}.overlay{position:absolute;inset:0;pointer-events:auto}.box{position:absolute;border:1px dashed #00a7ff;background:#00a7ff1a;min-width:5px;min-height:5px;cursor:move}.box.sel{border:2px solid #00a7ff;background:#00a7ff22}.row{display:grid;grid-template-columns:100px 1fr;gap:8px;align-items:center;margin:7px 0}.row label{color:var(--dim);font-size:12px}.actions{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}.danger{border-color:#6d3037}.ok{color:var(--ok)}.err{color:var(--bad)}@media(max-width:850px){body{overflow:auto;height:auto}header{position:sticky;top:0;z-index:5}main{display:flex;flex-direction:column;height:auto}.left,.props{border:0;border-bottom:1px solid var(--line);max-height:38vh}.stage{min-height:45vh;justify-content:flex-start}.props{max-height:none}#elements{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px}.el{overflow:hidden;text-overflow:ellipsis}}</style></head>
<body><header><b>TWEAK VIEW NG</b><span class="muted">{{ version }}</span><span class="grow"></span><span id="screen" class="muted"></span><button onclick="undo()">Undo</button><button onclick="redo()">Redo</button></header><main><aside class="left"><input id="search" placeholder="filter elements" style="width:100%" oninput="renderList()"><div class="actions"><button onclick="addShape('line')">+ Line</button><button onclick="addShape('rect')">+ Rect</button><button onclick="addShape('ellipse')">+ Ellipse</button></div><ul id="elements"></ul></aside><section class="stage"><div id="frame" class="frame"><img id="preview" src="/ui"><div id="overlay" class="overlay"></div></div></section><section class="props"><div id="status" class="muted">loading…</div><h3 id="title">Select an element</h3><div id="editor"></div><div class="actions"><button onclick="apply()">Apply</button><button onclick="revertEl()">Revert element</button><button class="danger" onclick="resetAll()">Reset profile</button></div><hr style="border:0;border-top:1px solid var(--line)"><div class="row"><label>Profile</label><div><select id="profile"></select> <button onclick="newProfile()">New</button></div></div><div class="actions"><button onclick="exportCfg()">Export</button><button onclick="document.getElementById('importFile').click()">Import</button><input id="importFile" type="file" accept="application/json" hidden onchange="importCfg(this)"></div></section></main>
<script>const CSRF=document.querySelector('meta[name=csrf_token]').content;let S=null,selected=null,drag=null;const api=async(path,method='GET',body=null)=>{let o={method,headers:{'X-CSRFToken':CSRF}};if(body!==null){o.headers['Content-Type']='application/json';o.body=JSON.stringify(body)}let r=await fetch('/plugins/tweak_view_ng/'+path,o);let j=await r.json();if(!r.ok)throw Error(j.error||r.statusText);return j};function msg(t,bad=false){let e=document.getElementById('status');e.textContent=t;e.className=bad?'err':'muted'}async function refresh(){try{S=await api('api/state');document.getElementById('screen').textContent=`${S.screen.width}×${S.screen.height} • Pwn ${S.pwnagotchi_version}`;renderList();renderEditor();renderProfiles();scale();msg(`undo ${S.history.undo} • redo ${S.history.redo}${S.pending_missing.length?' • pending '+S.pending_missing.join(', '):''}`)}catch(e){msg(e.message,true)}}function renderList(){if(!S)return;let q=document.getElementById('search').value.toLowerCase(),ul=document.getElementById('elements');ul.innerHTML='';Object.entries(S.elements).filter(([n])=>n.toLowerCase().includes(q)).forEach(([n,e])=>{let li=document.createElement('li');li.className='el'+(n===selected?' sel':'');li.innerHTML=`<b>${esc(n)}</b><span class=type>${esc(e.type)}</span>`;li.onclick=()=>{selected=n;renderList();renderEditor();drawBoxes()};ul.appendChild(li)})}function esc(s){return String(s).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}function renderEditor(){let ed=document.getElementById('editor'),t=document.getElementById('title');ed.innerHTML='';if(!S||!selected||!S.elements[selected]){t.textContent='Select an element';return}let e=S.elements[selected];t.textContent=selected+' · '+e.type;e.editable.forEach(k=>{let v=e.properties[k],row=document.createElement('div');row.className='row';let label=document.createElement('label');label.textContent=k;let input;if(['font','text_font','label_font','alt_font'].includes(k)){input=document.createElement('select');S.fonts.forEach(f=>{let o=document.createElement('option');o.value=f;o.textContent=f;if(f===v)o.selected=true;input.appendChild(o)})}else if(k==='wrap'){input=document.createElement('input');input.type='checkbox';input.checked=!!v}else{input=document.createElement('input');input.value=Array.isArray(v)?v.join(','):v??'';if(k==='xy')input.dataset.xy='1'}input.id='p_'+k;row.append(label,input);ed.appendChild(row)})}function readProps(){let e=S.elements[selected],p={};e.editable.forEach(k=>{let i=document.getElementById('p_'+k);if(!i)return;p[k]=k==='wrap'?i.checked:i.value});return p}async function apply(){if(!selected)return;try{await api('api/update','POST',{element:selected,properties:readProps()});await refresh();reloadPreview();msg('applied ✓')}catch(e){msg(e.message,true)}}async function revertEl(){if(!selected)return;try{await api('api/revert','POST',{element:selected});await refresh();reloadPreview();msg('reverted ✓')}catch(e){msg(e.message,true)}}async function resetAll(){if(!confirm('Reset the active profile?'))return;try{await api('api/reset','POST',{});await refresh();reloadPreview()}catch(e){msg(e.message,true)}}async function undo(){try{await api('api/undo','POST',{});await refresh();reloadPreview()}catch(e){msg(e.message,true)}}async function redo(){try{await api('api/redo','POST',{});await refresh();reloadPreview()}catch(e){msg(e.message,true)}}function addShape(type){let name=prompt('Shape name');if(!name)return;api('api/add_shape','POST',{name,type,properties:{xy:[5,5,40,25],color:255,width:1}}).then(()=>{selected=name;refresh();reloadPreview()}).catch(e=>msg(e.message,true))}function renderProfiles(){let s=document.getElementById('profile');s.innerHTML='';S.profiles.forEach(n=>{let o=document.createElement('option');o.value=n;o.textContent=n;o.selected=n===S.active_profile;s.appendChild(o)});s.onchange=()=>api('api/profile','POST',{name:s.value}).then(()=>{selected=null;refresh();reloadPreview()}).catch(e=>msg(e.message,true))}function newProfile(){let n=prompt('New profile name');if(!n)return;api('api/profile','POST',{name:n}).then(()=>{selected=null;refresh();reloadPreview()}).catch(e=>msg(e.message,true))}async function exportCfg(){let d=await api('api/export');let a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(d,null,2)],{type:'application/json'}));a.download='tweak_view_ng.json';a.click();URL.revokeObjectURL(a.href)}function importCfg(inp){let f=inp.files[0];if(!f)return;let r=new FileReader();r.onload=()=>{try{let d=JSON.parse(r.result);api('api/import','POST',d).then(()=>{selected=null;refresh();reloadPreview()}).catch(e=>msg(e.message,true))}catch(e){msg('invalid JSON',true)}};r.readAsText(f)}function reloadPreview(){let im=document.getElementById('preview');im.src='/ui?t='+Date.now()}function scale(){if(!S)return;let im=document.getElementById('preview'),frame=document.getElementById('frame'),maxW=Math.max(250,document.querySelector('.stage').clientWidth-40),maxH=Math.max(150,document.querySelector('.stage').clientHeight-40),sc=Math.min(maxW/S.screen.width,maxH/S.screen.height,4);if(window.innerWidth<850)sc=Math.min((window.innerWidth-38)/S.screen.width,3);sc=Math.max(.5,sc);frame.style.width=(S.screen.width*sc)+'px';frame.style.height=(S.screen.height*sc)+'px';im.style.width='100%';im.style.height='100%';drawBoxes()}function drawBoxes(){let ov=document.getElementById('overlay');ov.innerHTML='';if(!S)return;Object.entries(S.elements).forEach(([n,e])=>{let xy=e.properties.xy;if(!xy)return;if(!Array.isArray(xy))xy=String(xy).split(',').map(Number);let x=xy[0]||0,y=xy[1]||0,w=xy.length>=4?Math.max(4,(xy[2]-x)):18,h=xy.length>=4?Math.max(4,(xy[3]-y)):12;let b=document.createElement('div');b.className='box'+(n===selected?' sel':'');b.style.left=(x/S.screen.width*100)+'%';b.style.top=(y/S.screen.height*100)+'%';b.style.width=(w/S.screen.width*100)+'%';b.style.height=(h/S.screen.height*100)+'%';b.title=n;b.onpointerdown=ev=>startDrag(ev,n,xy);b.onclick=()=>{selected=n;renderList();renderEditor();drawBoxes()};ov.appendChild(b)})}function startDrag(ev,n,xy){selected=n;renderList();renderEditor();let frame=document.getElementById('frame').getBoundingClientRect();drag={id:ev.pointerId,n,xy:[...xy],sx:ev.clientX,sy:ev.clientY,fw:frame.width,fh:frame.height};ev.target.setPointerCapture(ev.pointerId);ev.target.onpointermove=moveDrag;ev.target.onpointerup=endDrag}function moveDrag(ev){if(!drag)return;let dx=Math.round((ev.clientX-drag.sx)*S.screen.width/drag.fw),dy=Math.round((ev.clientY-drag.sy)*S.screen.height/drag.fh),a=[...drag.xy];a[0]+=dx;a[1]+=dy;if(a.length>=4){a[2]+=dx;a[3]+=dy}let i=document.getElementById('p_xy');if(i)i.value=a.join(',')}function endDrag(ev){if(!drag)return;drag=null;apply()}window.addEventListener('resize',scale);document.getElementById('preview').onload=()=>{scale();drawBoxes()};refresh();setInterval(()=>reloadPreview(),7000);</script></body></html>
"""
