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


class _HttpResult(Exception):
    """Carries an intended HTTP response out of a mutation handler.

    Used so a deliberate 4xx (bad input, unknown element, name collision) is
    returned verbatim rather than being treated as a failed edit and rolled
    back by _mutating_route(). It is raised *before* any state mutation.
    """

    def __init__(self, value):
        super().__init__("http-result")
        self.value = value


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

    def load_report(self):
        """Load the NG layout with per-entry corruption recovery.

        Harvest item 3: a malformed profile/element/property must not invalidate
        an otherwise healthy layout. Returns ``(layout, report)`` where report is
        ``{"ok": bool, "recovered": [...], "skipped": [{reason, ...}], ...}``.

        Fatal problems that make *nothing* recoverable (file unreadable, not an
        object, incompatible schema) still raise - the caller falls back to an
        empty layout and records the fatal reason.
        """
        if not os.path.isfile(self.filename):
            return self.empty(), {"ok": True, "source": self.filename, "present": False,
                                  "recovered": [], "skipped": []}
        with open(self.filename, "r", encoding="utf-8") as handle:
            data = json.load(handle)  # JSON syntax error here is fatal (caller recovers)
        if not isinstance(data, dict):
            raise ValueError("layout file must contain a JSON object")
        if data.get("schema") != SCHEMA_VERSION:
            raise ValueError("unsupported layout schema: %r" % data.get("schema"))
        clean, report = sanitize_layout(data, self.empty())
        report["ok"] = True
        report["source"] = self.filename
        report["present"] = True
        return clean, report

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


VALID_SHAPE_TYPES = {"line", "rect", "filled_rect", "ellipse", "filled_ellipse"}


def sanitize_layout(data, fallback):
    """Return ``(clean_layout, report)`` keeping every valid profile/entry.

    Harvest item 3. A malformed profile, element edit-map, or shape is dropped
    with an explicit reason rather than discarding the whole file. ``fallback``
    supplies default top-level keys (schema/target/active_profile/meta).
    """
    report = {"recovered": [], "skipped": []}
    clean = {
        "schema": fallback["schema"],
        "target": data.get("target", fallback["target"]),
        "active_profile": data.get("active_profile", "default"),
        "profiles": {},
        "meta": data.get("meta", {}) if isinstance(data.get("meta"), dict) else {},
    }
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        report["skipped"].append({"scope": "profiles", "reason": "profiles is not an object"})
        profiles = {}
    for pname, pbody in profiles.items():
        if not isinstance(pname, str) or not pname:
            report["skipped"].append({"scope": "profile", "profile": repr(pname), "reason": "invalid profile name"})
            continue
        if not isinstance(pbody, dict):
            report["skipped"].append({"scope": "profile", "profile": pname, "reason": "profile body is not an object"})
            continue
        clean_profile = {"edits": {}, "shapes": {}}
        # --- edits ---
        edits = pbody.get("edits", {})
        if not isinstance(edits, dict):
            report["skipped"].append({"scope": "edits", "profile": pname, "reason": "edits is not an object"})
            edits = {}
        for element, props in edits.items():
            if not isinstance(element, str) or not element:
                report["skipped"].append({"scope": "element", "profile": pname, "element": repr(element), "reason": "invalid element name"})
                continue
            if not isinstance(props, dict):
                report["skipped"].append({"scope": "element", "profile": pname, "element": element, "reason": "property map is not an object"})
                continue
            kept = {}
            for prop, value in props.items():
                if prop not in SAFE_PROPS:
                    report["skipped"].append({"scope": "property", "profile": pname, "element": element, "property": prop, "reason": "property not in safe allow-list"})
                    continue
                kept[prop] = value
            if kept:
                clean_profile["edits"][element] = kept
                report["recovered"].append({"scope": "element", "profile": pname, "element": element, "properties": sorted(kept.keys())})
        # --- shapes ---
        shapes = pbody.get("shapes", {})
        if not isinstance(shapes, dict):
            report["skipped"].append({"scope": "shapes", "profile": pname, "reason": "shapes is not an object"})
            shapes = {}
        for sname, spec in shapes.items():
            if not isinstance(sname, str) or not sname or sname.startswith("__"):
                report["skipped"].append({"scope": "shape", "profile": pname, "shape": repr(sname), "reason": "invalid shape name"})
                continue
            if not isinstance(spec, dict):
                report["skipped"].append({"scope": "shape", "profile": pname, "shape": sname, "reason": "shape spec is not an object"})
                continue
            stype = spec.get("type")
            if stype not in VALID_SHAPE_TYPES:
                report["skipped"].append({"scope": "shape", "profile": pname, "shape": sname, "reason": "unknown shape type: %r" % (stype,)})
                continue
            sprops = spec.get("properties", {})
            if not isinstance(sprops, dict):
                report["skipped"].append({"scope": "shape", "profile": pname, "shape": sname, "reason": "shape properties is not an object"})
                sprops = {}
            clean_profile["shapes"][sname] = {"type": stype, "properties": dict(sprops)}
            report["recovered"].append({"scope": "shape", "profile": pname, "shape": sname, "type": stype})
        clean["profiles"][pname] = clean_profile
    if "default" not in clean["profiles"]:
        clean["profiles"]["default"] = {"edits": {}, "shapes": {}}
    if clean["active_profile"] not in clean["profiles"]:
        report["skipped"].append({"scope": "active_profile", "reason": "active profile %r missing; falling back to default" % (clean["active_profile"],)})
        clean["active_profile"] = "default"
    return clean, report


def import_legacy_report(data):
    """Convert a legacy Tweak View config to an NG profile, per-entry tolerant.

    Harvest item 3. Returns ``(profile, report)``. Malformed custom shapes or
    ``VSS.*`` entries are skipped with reasons; valid ones are preserved.
    """
    report = {"imported": [], "skipped": []}
    if not isinstance(data, dict):
        # Non-dict legacy content yields an empty profile rather than throwing,
        # so one bad file never blocks startup.
        report["skipped"].append({"scope": "root", "reason": "legacy config is not a JSON object"})
        return {"edits": {}, "shapes": {}}, report
    profile = {"edits": {}, "shapes": {}}
    type_map = {"CustomLine": "line", "CustomRect": "rect", "CustomEllipse": "ellipse"}
    custom = data.get("__custom_shapes__", {})
    if custom and not isinstance(custom, dict):
        report["skipped"].append({"scope": "__custom_shapes__", "reason": "not an object"})
    elif isinstance(custom, dict):
        for name, old in custom.items():
            if not isinstance(name, str) or not name:
                report["skipped"].append({"scope": "shape", "shape": repr(name), "reason": "invalid shape name"})
                continue
            if not isinstance(old, dict):
                report["skipped"].append({"scope": "shape", "shape": name, "reason": "shape entry is not an object"})
                continue
            old_type = str(old.get("type", "CustomLine"))
            mapped = type_map.get(old_type, "line")
            props = old.get("props")
            if props is not None and not isinstance(props, dict):
                report["skipped"].append({"scope": "shape", "shape": name, "reason": "props is not an object"})
                props = {}
            profile["shapes"][name] = {"type": mapped, "properties": dict(props or {})}
            report["imported"].append({"scope": "shape", "shape": name, "type": mapped})
    for key, value in data.items():
        if not isinstance(key, str) or not key.startswith("VSS."):
            continue
        parts = key.split(".", 2)
        if len(parts) != 3:
            report["skipped"].append({"scope": "legacy_key", "key": key, "reason": "not in VSS.<element>.<prop> form"})
            continue
        _, element, prop = parts
        if not element:
            report["skipped"].append({"scope": "legacy_key", "key": key, "reason": "empty element name"})
            continue
        if prop not in SAFE_PROPS:
            report["skipped"].append({"scope": "legacy_key", "key": key, "element": element, "property": prop, "reason": "property not in safe allow-list"})
            continue
        profile["edits"].setdefault(element, {})[prop] = value
        report["imported"].append({"scope": "edit", "element": element, "property": prop})
    return profile, report


def import_legacy(data):
    """Back-compatible wrapper returning just the converted profile.

    Retained for callers/tests that only need the profile. Note: the legacy
    behavior raised on a non-dict input; the tolerant path now returns an empty
    profile instead, so this wrapper preserves the raise for that one case to
    keep the original contract.
    """
    if not isinstance(data, dict):
        raise ValueError("legacy config must be a JSON object")
    profile, _report = import_legacy_report(data)
    return profile


class TweakViewNG(plugins.Plugin):
    __author__ = "patrickato"
    __version__ = "0.2.0-beta1"
    __license__ = "GPL3"
    __description__ = "Tweak View NG - an independent, hardened, resolution-independent Pwnagotchi UI layout editor for Jayofelony 2.9.5.8/2.9.5.9. A separate plugin, not the original Tweak View / Tweak View 2 (concept lineage credited in the README)."

    DEFAULTS = {"filename": "/etc/pwnagotchi/tweak_view_ng.json", "legacy_filename": "/etc/pwnagotchi/tweak_view.json", "auto_import_legacy": True, "backup": True, "history_limit": 50, "strict_version": False}

    # Explicit lifecycle phases (harvest item 4). Ordered; higher = further along.
    PHASE_INIT = "init"                  # constructed, options not yet merged
    PHASE_LOADED = "loaded"              # on_loaded done: options merged, layout preloaded/validated from disk
    PHASE_WAITING_UI = "waiting_ui"      # layout ready, waiting for on_ui_setup to supply the UI adapter
    PHASE_READY = "ready"                # adapter built, profile applied; editor fully operational
    _PHASE_ORDER = (PHASE_INIT, PHASE_LOADED, PHASE_WAITING_UI, PHASE_READY)

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
        self._phase = self.PHASE_INIT
        self._load_report = None  # structured import/load report (harvest item 3)

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
        """Import a legacy Tweak View layout when no NG layout exists yet.

        Per-entry tolerant (harvest item 3): a malformed entry is skipped and
        reported rather than aborting the whole import. Returns an import report
        dict, or None when no import was attempted.
        """
        if os.path.isfile(self.options["filename"]):
            return None
        legacy = self.options.get("legacy_filename")
        if not self.options.get("auto_import_legacy", True) or not legacy or not os.path.isfile(legacy):
            return None
        try:
            with open(legacy, "r", encoding="utf-8") as handle:
                old = json.load(handle)
        except Exception as exc:
            LOG.warning("Tweak View NG: legacy file unreadable: %s", exc)
            self._last_error = "Legacy import failed: %s" % exc
            return {"source": legacy, "ok": False, "fatal": str(exc),
                    "imported": [], "skipped": []}
        converted, report = import_legacy_report(old)
        report["source"] = legacy
        self._layout = self._store.empty()
        self._layout["profiles"]["default"] = converted
        self._layout["meta"]["imported_legacy"] = legacy
        self._layout["meta"]["imported_at"] = int(time.time())
        self._layout["meta"]["import_report"] = report
        try:
            self._save()
        except Exception as exc:
            LOG.warning("Tweak View NG: could not persist imported legacy layout: %s", exc)
            self._last_error = "Imported legacy layout but save failed: %s" % exc
        if report["skipped"]:
            LOG.info("Tweak View NG: imported legacy layout from %s (%d entries, %d skipped)",
                     legacy, len(report["imported"]), len(report["skipped"]))
        else:
            LOG.info("Tweak View NG: imported legacy layout from %s (%d entries)",
                     legacy, len(report["imported"]))
        return report

    def _preload_layout(self):
        """Load + validate the layout from disk as early as possible.

        Harvest item 2: this runs in on_loaded(), before any UI object exists,
        so layout data is parsed and validated before the first UI application
        pass. No UI-dependent work happens here.
        """
        self._store = LayoutStore(self.options["filename"], bool(self.options.get("backup", True)))
        try:
            self._layout, load_report = self._store.load_report()
        except Exception as exc:
            # Only truly unrecoverable errors reach here (e.g. unreadable file).
            self._last_error = "Layout load failed: %s" % exc
            LOG.error("Tweak View NG: %s", self._last_error)
            self._layout = self._store.empty()
            load_report = {"ok": False, "fatal": str(exc), "recovered": [], "skipped": []}
        import_report = self._maybe_import_legacy()
        self._load_report = {"layout": load_report, "legacy_import": import_report}
        self._phase = self.PHASE_WAITING_UI

    def on_loaded(self):
        self._merge_options()
        ver = getattr(pwnagotchi, "__version__", "unknown")
        if self.options.get("strict_version") and ver != "2.9.5.8":
            raise RuntimeError("Tweak View NG alpha targets Pwnagotchi 2.9.5.8; found %s" % ver)
        self._phase = self.PHASE_LOADED
        # Preload layout from disk now, before any UI object is available.
        try:
            self._preload_layout()
        except Exception as exc:
            # Preload must never prevent the plugin from loading; fall back to
            # an empty layout and surface the problem via the readiness payload.
            self._last_error = "Layout preload failed: %s" % exc
            LOG.exception("Tweak View NG: layout preload failed")
            if self._store is None:
                self._store = LayoutStore(self.options["filename"], bool(self.options.get("backup", True)))
            self._layout = self._store.empty()
            self._phase = self.PHASE_WAITING_UI
        LOG.info("Tweak View NG %s loaded (Pwnagotchi %s); layout preloaded, awaiting UI", self.__version__, ver)

    def on_ready(self, agent):
        self._agent = agent

    def on_ui_setup(self, ui):
        self._ui = ui
        self._build_fonts()
        self._adapter = JayUIAdapter(ui, self._fonts, LOG)
        # Layout was preloaded in on_loaded(); only reload if that never ran
        # (e.g. a host that calls on_ui_setup without on_loaded, or a test).
        if self._store is None:
            self._store = LayoutStore(self.options["filename"], bool(self.options.get("backup", True)))
        if self._layout is None:
            try:
                self._layout, load_report = self._store.load_report()
            except Exception as exc:
                self._last_error = "Layout load failed: %s" % exc
                LOG.error("Tweak View NG: %s", self._last_error)
                self._layout = self._store.empty()
                load_report = {"ok": False, "fatal": str(exc), "recovered": [], "skipped": []}
            import_report = self._maybe_import_legacy()
            self._load_report = {"layout": load_report, "legacy_import": import_report}
        result = self._apply_profile(redraw=False)
        self._phase = self.PHASE_READY
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

    def _is_ready(self):
        return self._phase == self.PHASE_READY and self._adapter is not None

    def _readiness_payload(self):
        """Small, always-safe status payload (harvest item 4).

        Readable at any phase, even before on_ui_setup() - it never touches the
        UI adapter unless one exists. Lets the browser show 'waiting for UI'
        instead of getting an opaque 500.
        """
        phase = self._phase
        messages = {
            self.PHASE_INIT: "plugin constructed",
            self.PHASE_LOADED: "plugin loaded",
            self.PHASE_WAITING_UI: "layout loaded; waiting for UI adapter",
            self.PHASE_READY: "ready",
        }
        payload = {
            "phase": phase,
            "ready": self._is_ready(),
            "message": messages.get(phase, phase),
            "plugin_version": self.__version__,
            "target": TARGET,
            "pwnagotchi_version": getattr(pwnagotchi, "__version__", "unknown"),
            "layout_loaded": self._layout is not None,
            "active_profile": (self._layout or {}).get("active_profile", "default"),
            "last_error": self._last_error,
        }
        if self._load_report is not None:
            payload["load_report"] = self._load_report
        if self._adapter is not None:
            try:
                width, height = self._adapter.dimensions()
                payload["screen"] = [width, height]
            except Exception:
                pass
        return payload

    def _state_payload(self):
        snap = self._adapter.snapshot()
        profile = self._profile()
        snap.update({"plugin_version": self.__version__, "target": TARGET, "pwnagotchi_version": getattr(pwnagotchi, "__version__", "unknown"), "phase": self._phase, "ready": self._is_ready(), "active_profile": self._layout.get("active_profile", "default"), "profiles": sorted(self._layout.get("profiles", {}).keys()), "configured": profile, "history": {"undo": len(self._history), "redo": len(self._redo)}, "pending_missing": sorted(self._pending_missing), "last_error": self._last_error, "load_report": self._load_report, "fonts": sorted(self._fonts.keys())})
        return snap

    def _json_body(self, request):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        return data

    def _route_api(self, path, request):
        # Readiness endpoint - safe at any phase, never needs the UI adapter.
        if path == "api/ready" and request.method == "GET":
            return jsonify(self._readiness_payload())
        # Everything below needs the UI adapter. If we're not ready yet, return
        # a structured 503 instead of letting an AttributeError become a 500.
        if not self._is_ready():
            payload = self._readiness_payload()
            payload["ok"] = False
            payload["error"] = "not ready: " + payload["message"]
            return jsonify(payload), 503
        if path == "api/state" and request.method == "GET":
            return jsonify(self._state_payload())
        if path == "api/health" and request.method == "GET":
            width, height = self._adapter.dimensions()
            return jsonify({"ok": True, "plugin": self.__version__, "target": TARGET, "pwnagotchi": getattr(pwnagotchi, "__version__", "unknown"), "phase": self._phase, "ready": True, "screen": [width, height], "pending": sorted(self._pending_missing)})
        if path == "api/export" and request.method == "GET":
            return jsonify(self._layout)
        # All remaining routes are state-mutating POSTs. Run them inside a
        # transaction (harvest item 5): if the handler or its atomic save fails,
        # roll runtime AND in-memory layout back to the pre-edit snapshot so the
        # two never diverge. The handlers still call self._save() as their last
        # step; a failure there is caught here and rolled back.
        if request.method == "POST":
            return self._mutating_route(path, request)
        abort(404)

    def _mutating_route(self, path, request):
        prior_layout = copy.deepcopy(self._layout)
        names_before = set(self._adapter.element_names()) if self._adapter else set()
        history_before = len(self._history)
        try:
            return self._dispatch_mutation(path, request)
        except _HttpResult as hr:
            # A clean, intended HTTP response (e.g. 400/404/409) - not a failure.
            return hr.value
        except Exception as exc:
            LOG.warning("Tweak View NG: mutation %s rolled back: %s", path, exc)
            self._last_error = "Edit rolled back: %s" % exc
            # Roll back in-memory layout.
            self._layout = prior_layout
            # Drop any history entry the handler pushed before it failed.
            if len(self._history) > history_before:
                del self._history[history_before:]
            # Roll back runtime: remove elements the failed handler added, then
            # restore touched originals and re-apply the restored profile.
            if self._adapter is not None:
                try:
                    for name in set(self._adapter.element_names()) - names_before:
                        self._adapter.remove(name)
                except Exception:
                    LOG.debug("Tweak View NG: rollback add-cleanup failed", exc_info=True)
                self._restore_runtime_to_originals()
                try:
                    self._apply_profile(redraw=True)
                except Exception:
                    LOG.exception("Tweak View NG: rollback re-apply failed")
            return jsonify({"ok": False, "error": "edit rolled back: %s" % exc, "rolled_back": True}), 500

    def _dispatch_mutation(self, path, request):
        if path == "api/update" and request.method == "POST":
            data = self._json_body(request)
            element = str(data.get("element", "")).strip()
            props = data.get("properties", {})
            if not element:
                raise _HttpResult((jsonify({"ok": False, "error": "element required"}), 400))
            if not self._adapter.has(element):
                raise _HttpResult((jsonify({"ok": False, "error": "unknown element"}), 404))
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
                raise _HttpResult((jsonify({"ok": False, "error": "valid name required"}), 400))
            if self._adapter.has(name) and name not in self._profile().get("shapes", {}):
                raise _HttpResult((jsonify({"ok": False, "error": "name collides with existing UI element"}), 409))
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
                raise _HttpResult((jsonify({"ok": False, "error": "custom shape not found"}), 404))
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
                raise _HttpResult((jsonify({"ok": False, "error": "nothing to undo"}), 409))
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
                raise _HttpResult((jsonify({"ok": False, "error": "nothing to redo"}), 409))
            current = self._snapshot_config()
            nxt = self._redo.pop()
            self._history.append(current)
            self._restore_runtime_to_originals()
            self._layout = nxt
            result = self._apply_profile(redraw=True)
            self._save()
            return jsonify({"ok": True, "result": result})
        if path == "api/import" and request.method == "POST":
            data = self._json_body(request)
            if "schema" in data and data.get("schema") != SCHEMA_VERSION:
                raise _HttpResult((jsonify({"ok": False, "error": "unsupported layout schema"}), 400))
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
            op = str(data.get("op", "switch")).strip()  # switch (default) | rename | delete
            name = str(data.get("name", "")).strip()

            def _valid(nm):
                return nm and not any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in nm)

            profiles = self._layout.setdefault("profiles", {})
            if op == "delete":
                if name == "default":
                    raise _HttpResult((jsonify({"ok": False, "error": "cannot delete the default profile"}), 400))
                if name not in profiles:
                    raise _HttpResult((jsonify({"ok": False, "error": "no such profile"}), 404))
                self._push_history()
                self._restore_runtime_to_originals()
                profiles.pop(name, None)
                if self._layout.get("active_profile") == name:
                    self._layout["active_profile"] = "default"
                result = self._apply_profile(redraw=True)
                self._save()
                return jsonify({"ok": True, "result": result, "active": self._layout["active_profile"]})
            if op == "rename":
                new = str(data.get("new", "")).strip()
                if not _valid(new):
                    raise _HttpResult((jsonify({"ok": False, "error": "invalid new profile name"}), 400))
                if name == "default":
                    raise _HttpResult((jsonify({"ok": False, "error": "cannot rename the default profile"}), 400))
                if name not in profiles:
                    raise _HttpResult((jsonify({"ok": False, "error": "no such profile"}), 404))
                if new in profiles:
                    raise _HttpResult((jsonify({"ok": False, "error": "a profile named %s already exists" % new}), 409))
                self._push_history()
                profiles[new] = profiles.pop(name)
                if self._layout.get("active_profile") == name:
                    self._layout["active_profile"] = new
                self._save()
                return jsonify({"ok": True, "active": self._layout["active_profile"]})
            # default: switch to (creating if needed)
            if not _valid(name):
                raise _HttpResult((jsonify({"ok": False, "error": "invalid profile name"}), 400))
            self._push_history()
            self._restore_runtime_to_originals()
            profiles.setdefault(name, {"edits": {}, "shapes": {}})
            self._layout["active_profile"] = name
            result = self._apply_profile(redraw=True)
            self._save()
            return jsonify({"ok": True, "result": result})
        if path == "api/align" and request.method == "POST":
            return self._align_element(request)
        if path == "api/match" and request.method == "POST":
            return self._match_coordinate(request)
        if path == "api/stack" and request.method == "POST":
            return self._stack_elements(request)
        if path == "api/recovery/update" and request.method == "POST":
            return self._recovery_update(request)
        # Unknown mutating route - a clean 404, not a rolled-back edit.
        raise _HttpResult((jsonify({"ok": False, "error": "not found"}), 404))

    # ------------------------------------------------------------------ #
    # Auto-align the top / bottom status strips (editor convenience)
    # ------------------------------------------------------------------ #
    def _xy_of(self, props):
        """Return (x, y, extra) from a serialized xy, or None. extra is the
        remaining coords for 4-point widgets (so width is preserved on move)."""
        xy = props.get("xy")
        if xy is None:
            return None
        if isinstance(xy, str):
            parts = [int(float(p)) for p in xy.split(",") if p.strip() != ""]
        elif isinstance(xy, (list, tuple)):
            try:
                parts = [int(float(p)) for p in xy]
            except Exception:
                return None
        else:
            return None
        if len(parts) < 2:
            return None
        return parts[0], parts[1], parts[2:]

    def _region_bounds(self, snap, y):
        """Return (y_lo, y_hi) of the horizontal band a given y sits in, bounded
        by the divider lines (line1/line2) and the screen. Lets "top of this
        element's area" mean the top of whichever band it lives in, so the
        direction buttons are never surprising regardless of how thin a strip
        is."""
        h = snap["screen"]["height"]
        els = snap["elements"]

        def _ly(name):
            e = els.get(name)
            got = self._xy_of(e.get("properties", {})) if e else None
            return got[1] if got else None

        l1 = _ly("line1")
        l2 = _ly("line2")
        edges = [0, h - 1]
        if l1 is not None:
            edges.append(l1)
        if l2 is not None:
            edges.append(l2)
        edges = sorted(set(edges))
        # lo = largest edge at or below y; hi = smallest edge strictly above y.
        lo, hi = 0, h - 1
        for e in edges:
            if e <= y and e > lo:
                lo = e
            if e > y and e < hi:
                hi = e
        # keep a sane minimum band so a sliver strip still has room
        if hi - lo < 4:
            hi = min(h - 1, lo + 4)
        return lo, hi

    def _align_element(self, request):
        """Snap ONE selected element to an edge (or center) of the region it
        lives in. Intuitive per-element alignment:

        Body: {"element": "<name>", "edge": top|bottom|left|right|hcenter|vcenter}.

        "top"/"bottom" snap within the element's horizontal band (bounded by the
        divider lines / screen); "left"/"right"/"hcenter" work across the screen
        width; "vcenter" centers within the band. One undoable transaction.
        """
        data = self._json_body(request)
        element = str(data.get("element", "")).strip()
        edge = str(data.get("edge", "")).strip()
        valid = {"top", "bottom", "left", "right", "hcenter", "vcenter"}
        if not element or edge not in valid:
            raise _HttpResult((jsonify({"ok": False, "error": "element required; edge must be one of %s" % sorted(valid)}), 400))
        if not self._adapter.has(element):
            raise _HttpResult((jsonify({"ok": False, "error": "unknown element"}), 404))
        snap = self._adapter.snapshot()
        props = snap["elements"].get(element, {}).get("properties", {})
        got = self._xy_of(props)
        if got is None:
            raise _HttpResult((jsonify({"ok": False, "error": "element has no position to align"}), 409))
        x, y, extra = got
        w = snap["screen"]["width"]
        h = snap["screen"]["height"]
        # element footprint (width/height) from a 4-point widget, else a small default
        ew = (extra[0] - x) if len(extra) >= 2 else 18
        eh = (extra[1] - y) if len(extra) >= 2 else 12
        ew = max(1, ew); eh = max(1, eh)

        nx, ny = x, y
        if edge in ("top", "bottom", "vcenter"):
            lo, hi = self._region_bounds(snap, y)
            if edge == "top":
                ny = lo
            elif edge == "bottom":
                ny = max(lo, hi - eh)
            else:  # vcenter
                ny = max(lo, lo + ((hi - lo) - eh) // 2)
        else:
            if edge == "left":
                nx = 0
            elif edge == "right":
                nx = max(0, w - ew)
            else:  # hcenter
                nx = max(0, (w - ew) // 2)

        if len(extra) >= 2:
            new_xy = [nx, ny, nx + ew, ny + eh]
        else:
            new_xy = [nx, ny]

        self._push_history()
        profile = self._profile()
        profile.setdefault("edits", {}).setdefault(element, {})["xy"] = new_xy
        self._adapter.apply_properties(element, {"xy": new_xy}, self._originals)
        self._save()
        self._adapter.redraw()
        return jsonify({"ok": True, "element": element, "edge": edge, "xy": new_xy})

    def _apply_xy(self, element, new_xy):
        """Set one element's xy in the active profile + runtime (caller handles
        the transaction/history/save)."""
        self._profile().setdefault("edits", {}).setdefault(element, {})["xy"] = new_xy
        self._adapter.apply_properties(element, {"xy": new_xy}, self._originals)

    def _match_coordinate(self, request):
        """Match the selected element's x or y to another element's coordinate.

        Body: {"element": "<name>", "target": "<other>", "axis": "x"|"y"}.
        """
        data = self._json_body(request)
        element = str(data.get("element", "")).strip()
        target = str(data.get("target", "")).strip()
        axis = str(data.get("axis", "")).strip()
        if not element or not target or axis not in ("x", "y"):
            raise _HttpResult((jsonify({"ok": False, "error": "element, target and axis (x|y) required"}), 400))
        if element == target:
            raise _HttpResult((jsonify({"ok": False, "error": "element and target must differ"}), 400))
        if not self._adapter.has(element):
            raise _HttpResult((jsonify({"ok": False, "error": "unknown element"}), 404))
        if not self._adapter.has(target):
            raise _HttpResult((jsonify({"ok": False, "error": "unknown target"}), 404))
        snap = self._adapter.snapshot()
        g_el = self._xy_of(snap["elements"].get(element, {}).get("properties", {}))
        g_tg = self._xy_of(snap["elements"].get(target, {}).get("properties", {}))
        if g_el is None or g_tg is None:
            raise _HttpResult((jsonify({"ok": False, "error": "both elements need a position"}), 409))
        x, y, extra = g_el
        tx, ty, _te = g_tg
        if axis == "x":
            nx, ny = tx, y
        else:
            nx, ny = x, ty
        new_xy = [nx, ny] + ([nx + (extra[0] - x), ny + (extra[1] - y)] if len(extra) >= 2 else [])
        self._push_history()
        self._apply_xy(element, new_xy)
        self._save()
        self._adapter.redraw()
        return jsonify({"ok": True, "element": element, "target": target, "axis": axis, "xy": new_xy})

    def _stack_elements(self, request):
        """Stack a set of elements into a clean column (the vertical-space
        feature). All elements share a common x (the first one's, or a given x)
        and are spaced top-to-bottom, anchored at the current top-most element.

        Body: {"elements": ["a","b","c"], "x": <optional int>, "gap": <optional int>}.
        With an explicit gap, elements are placed that many px apart from the
        top-most. With no gap, a sensible default pitch is derived from the
        tallest element so rows form an even, non-overlapping column that
        visibly tidies up even when the elements were already roughly spaced
        (the alpha8 "stack does nothing" bug: distributing within the existing
        min..max span barely moved elements that were already spread out).
        """
        data = self._json_body(request)
        names = data.get("elements")
        if not isinstance(names, list) or len([n for n in names if isinstance(n, str)]) < 2:
            raise _HttpResult((jsonify({"ok": False, "error": "need at least 2 elements to stack"}), 400))
        names = [n for n in names if isinstance(n, str)]
        missing = [n for n in names if not self._adapter.has(n)]
        if missing:
            raise _HttpResult((jsonify({"ok": False, "error": "unknown element(s): %s" % ", ".join(missing)}), 404))
        snap = self._adapter.snapshot()
        got = {}
        for n in names:
            g = self._xy_of(snap["elements"].get(n, {}).get("properties", {}))
            if g is None:
                raise _HttpResult((jsonify({"ok": False, "error": "%s has no position" % n}), 409))
            got[n] = g
        # order the elements by current y so the stack keeps their visual order
        ordered = sorted(names, key=lambda n: got[n][1])
        xs = [got[n][0] for n in ordered]
        ys = [got[n][1] for n in ordered]
        col_x = data.get("x")
        col_x = int(col_x) if isinstance(col_x, (int, float)) or (isinstance(col_x, str) and col_x.strip().lstrip("-").isdigit()) else min(xs)
        gap = data.get("gap")
        h = snap["screen"]["height"]
        y0 = min(ys)
        if gap not in (None, "") and str(gap).lstrip("-").isdigit():
            step = int(gap)
        else:
            # Default pitch: tall enough that the tallest row never overlaps the
            # next, with a readable floor. Anchored at the top-most element so
            # the column always visibly reflows into an even stack.
            heights = []
            for name in ordered:
                ex, ey, extra = got[name]
                heights.append((extra[1] - ey) if len(extra) >= 2 else 12)
            step = max(max(heights) + 2, 12)
        targets = [y0 + step * i for i in range(len(ordered))]
        self._push_history()
        changed = []
        for n, ny in zip(ordered, targets):
            x, y, extra = got[n]
            ny = max(0, min(ny, h - 1))
            new_xy = [col_x, ny] + ([col_x + (extra[0] - x), ny + (extra[1] - y)] if len(extra) >= 2 else [])
            self._apply_xy(n, new_xy)
            changed.append(n)
        self._save()
        self._adapter.redraw()
        return jsonify({"ok": True, "stacked": changed, "x": col_x})

    # ------------------------------------------------------------------ #
    # Minimal server-rendered recovery editor (harvest item 8)
    # ------------------------------------------------------------------ #
    def _recovery_rows(self):
        """Build a plain data structure for the recovery page.

        No JavaScript, no live preview - just the current editable string/int
        properties of each element, with every value HTML-escaped at render
        time by Jinja autoescaping.
        """
        snap = self._adapter.snapshot()
        configured = self._profile().get("edits", {})
        rows = []
        for name in sorted(snap["elements"].keys()):
            el = snap["elements"][name]
            fields = []
            for prop in el["editable"]:
                # Recovery editor keeps it simple: only scalar props it can
                # round-trip through a text input. xy is shown as "a,b[,c,d]".
                if prop in FONT_PROPS or prop in BOOL_PROPS:
                    continue
                value = el["properties"].get(prop)
                if isinstance(value, (list, tuple)):
                    value = ",".join(str(v) for v in value)
                fields.append({"prop": prop, "value": "" if value is None else str(value),
                               "edited": prop in configured.get(name, {})})
            rows.append({"name": name, "type": el["type"], "fields": fields})
        return rows

    def _recovery_update(self, request):
        """Apply a single element/property edit submitted by the recovery form.

        Form fields: ``element``, ``property``, ``value``. Runs through the same
        validated, transactional path as the JSON API (this is invoked from
        within _mutating_route, so it inherits rollback-on-failure).
        """
        form = getattr(request, "form", None)
        getter = form.get if form is not None else (lambda k, d=None: d)
        element = str(getter("element", "") or "").strip()
        prop = str(getter("property", "") or "").strip()
        value = getter("value", "")
        if not element or not prop:
            raise _HttpResult((self._recovery_html(message="element and property are required", ok=False), 400))
        if prop not in SAFE_PROPS:
            raise _HttpResult((self._recovery_html(message="property not editable: %s" % prop, ok=False), 400))
        if not self._adapter.has(element):
            raise _HttpResult((self._recovery_html(message="unknown element: %s" % element, ok=False), 404))
        widget = self._adapter.get_widget(element)
        if not hasattr(widget, prop):
            raise _HttpResult((self._recovery_html(message="%s has no property %s" % (element, prop), ok=False), 400))
        # Validate before mutating (raises ValueError -> rolled back as 500).
        self._adapter.normalize_property(widget, prop, value)
        self._push_history()
        profile = self._profile()
        if element in profile.setdefault("shapes", {}):
            profile["shapes"][element].setdefault("properties", {})[prop] = value
            self._adapter.add_shape(element, profile["shapes"][element])
        else:
            profile.setdefault("edits", {}).setdefault(element, {})[prop] = value
            self._adapter.apply_properties(element, {prop: value}, self._originals)
        self._save()
        self._adapter.redraw()
        return self._recovery_html(message="updated %s.%s" % (element, prop), ok=True)

    def _recovery_html(self, message=None, ok=True):
        if not self._is_ready():
            payload = self._readiness_payload()
            return render_template_string(RECOVERY_NOT_READY, version=self.__version__,
                                          phase=payload["phase"], message=payload["message"])
        width, height = self._adapter.dimensions()
        return render_template_string(
            RECOVERY_UI, version=self.__version__, rows=self._recovery_rows(),
            width=width, height=height, active_profile=self._layout.get("active_profile", "default"),
            message=message, ok=ok, last_error=self._last_error,
        )

    def on_webhook(self, path, request):
        try:
            path = (path or "").lstrip("/")
            if path.startswith("api/"):
                return self._route_api(path, request)
            if request.method == "GET" and path in ("", "/"):
                return render_template_string(WEB_UI, version=self.__version__)
            if path == "recovery" and request.method in ("GET", "POST"):
                if request.method == "POST":
                    # Route through the transactional mutation path.
                    return self._route_api("api/recovery/update", request)
                return self._recovery_html()
            abort(404)
        except Exception as exc:
            self._last_error = str(exc)
            LOG.exception("Tweak View NG webhook failure")
            return jsonify({"ok": False, "error": str(exc)}), 500


WEB_UI = r"""
<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no"><meta name="csrf_token" content="{{ csrf_token() }}"><title>Tweak View NG</title>
<style>:root{--bg:#0b0e10;--panel:#14191d;--card:#171d23;--line:#263039;--text:#d7e0e5;--dim:#83919a;--a:#5bd1ff;--ok:#79e28b;--bad:#ff6b78}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px system-ui,sans-serif;height:100vh;overflow:hidden}header{height:50px;display:flex;align-items:center;gap:8px;padding:0 12px;background:var(--panel);border-bottom:1px solid var(--line)}header b{color:var(--a);letter-spacing:.08em}.grow{flex:1}.muted{color:var(--dim);font-size:12px}button,select,input{background:#0d1114;color:var(--text);border:1px solid #34414b;border-radius:5px;padding:7px}button{cursor:pointer;transition:border-color .12s,background .12s}button:hover{border-color:var(--a)}main{display:grid;grid-template-columns:230px 1fr 310px;height:calc(100vh - 50px)}aside,.props{background:var(--panel);overflow:auto;padding:10px}.left{border-right:1px solid var(--line)}.props{border-left:1px solid var(--line);padding:10px 12px 24px}.props h3{margin:6px 0 8px;font-size:15px}#elements{list-style:none;padding:0;margin:8px 0}.el{padding:7px;border:1px solid transparent;border-radius:5px;cursor:pointer}.el:hover,.el.sel{border-color:var(--a);background:#101a20}.type{display:block;color:var(--dim);font-size:11px}.stage{overflow:auto;display:flex;align-items:center;justify-content:center;padding:18px}.frame{position:relative;border:1px solid #4b5b66;background:#fff;box-shadow:0 10px 35px #0008}.frame img{display:block;image-rendering:pixelated;max-width:none}.overlay{position:absolute;inset:0;pointer-events:auto}.box{position:absolute;border:1px dashed #00a7ff;background:#00a7ff14;min-width:5px;min-height:5px;cursor:move;user-select:none;-webkit-user-select:none;touch-action:none;opacity:.5;transition:opacity .12s,border-color .12s}.box:hover{opacity:.95}.box.edited{opacity:.78;border-color:var(--a)}.box.sel{border:2px solid #00a7ff;background:#00a7ff24;opacity:1}.row{display:grid;grid-template-columns:100px 1fr;gap:8px;align-items:center;margin:7px 0}.row label{color:var(--dim);font-size:12px}.row input,.row select{width:100%}.actions{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0}.danger{border-color:#6d3037}.ok{color:var(--ok)}.err{color:var(--bad)}#saved{min-width:66px;text-align:right}#saved.dirty{color:var(--bad)}#saved.ok{color:var(--ok)}
.card{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:11px 12px;margin:11px 0}
.card .ctitle{color:var(--a);font-size:11px;letter-spacing:.07em;font-weight:600;margin:0 0 8px;text-transform:uppercase;opacity:.9}
.pad-label{color:var(--dim);font-size:10px;margin:9px 0 4px}
.pad-label:first-of-type{margin-top:0}
.box.warn{border:2px solid var(--bad)!important;background:#ff6b7822!important;opacity:1!important}
.blabel{position:absolute;left:0;top:-13px;font-size:9px;line-height:1;color:var(--a);background:#0b0e10d8;padding:1px 3px;border-radius:3px;white-space:nowrap;pointer-events:none;max-width:120px;overflow:hidden;text-overflow:ellipsis;display:none}
.box.sel>.blabel,.box:hover>.blabel{display:block}
.zone{position:absolute;background:repeating-linear-gradient(45deg,#5bd1ff0f,#5bd1ff0f 6px,transparent 6px,transparent 12px);pointer-events:none;border-top:1px dashed #5bd1ff44;border-bottom:1px dashed #5bd1ff44}
.dot{color:var(--a);font-size:9px;margin-left:4px;vertical-align:middle}
button.on{border-color:var(--a);background:#101a20;color:var(--a)}
.align-grid{display:grid;grid-template-columns:1fr 1fr;gap:5px;margin:4px 0}.align-pad{display:grid;grid-template-columns:1fr 1fr 1fr;gap:5px;margin:4px 0}.align-pad button{font-size:12px;padding:6px}
.align-grid button{font-size:12px;padding:6px}
.nudge-pad{display:grid;grid-template-columns:repeat(3,1fr);gap:4px;max-width:168px;margin:4px 0}
.nudge-pad button{padding:6px;font-size:14px;line-height:1}
.nudge-pad span{display:flex;align-items:center;justify-content:center}
.nudge-dot{color:var(--dim);font-size:10px}
.hint{color:var(--dim);font-size:11px;margin:4px 0 2px}
#help{position:fixed;left:0;right:0;top:50px;z-index:18;background:#101a20;border-bottom:1px solid var(--a);color:var(--text);font-size:12px;line-height:1.5;padding:8px 14px;display:none}
#help b{color:var(--a)}
#helpbubble{position:fixed;z-index:30;max-width:250px;background:#0b0e10;border:1px solid var(--a);border-radius:7px;padding:8px 10px;font-size:12px;line-height:1.45;color:var(--text);box-shadow:0 12px 34px #000b;pointer-events:none;display:none}
body.help-on [data-help]{outline:1px dotted #5bd1ff66;outline-offset:2px}
body.help-on button,body.help-on [data-help]{cursor:help}
@media(prefers-reduced-motion:reduce){.box,button{transition:none}}
@media(max-width:850px){body{overflow-x:hidden;overflow-y:auto;height:auto}header{position:sticky;top:0;z-index:5;flex-wrap:wrap;height:auto;min-height:50px;padding:6px 10px;row-gap:4px}header .grow{flex-basis:100%;height:0}#help{position:static;top:auto}main{display:flex;flex-direction:column;height:auto}.left,.props{border:0;border-bottom:1px solid var(--line);max-height:38vh}.stage{min-height:45vh;justify-content:flex-start}.props{max-height:none}#elements{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px}.el{overflow:hidden;text-overflow:ellipsis}}
</style></head>
<body><header><b>TWEAK VIEW NG</b><span class="muted">{{ version }}</span><span class="grow"></span><span id="screen" class="muted"></span><span id="saved" class="muted" title="save state"></span><button id="helpBtn" onclick="toggleHelp()" title="help mode — hover any control for what it does" data-help="Turn help mode on or off. While it's on, hovering any control shows what it does.">?</button><button id="snapBtn" class="on" onclick="toggleSnap()" title="snap to lines/edges while dragging" data-help="When on, dragging snaps softly to the divider lines and screen edges.">Snap</button><button id="zoneBtn" class="on" onclick="toggleZones()" title="shade the top/bottom strips" data-help="Shade the top and bottom status strips so you can see what you're aligning into.">Zones</button><button onclick="undo()" data-help="Step back through your edits.">Undo</button><button onclick="redo()" data-help="Re-apply an edit you just undid.">Redo</button></header><main><aside class="left"><input id="search" placeholder="filter elements" style="width:100%" oninput="renderList()" data-help="Type to filter the element list by name."><div class="actions"><button onclick="addShape('line')" data-help="Add a custom line you can position and size — handy for dividers or underlines.">+ Line</button><button onclick="addShape('rect')" data-help="Add a custom rectangle outline — e.g. a box or badge around a value.">+ Rect</button><button onclick="addShape('ellipse')" data-help="Add a custom ellipse/circle outline — e.g. a status dot or ring.">+ Ellipse</button></div><ul id="elements"></ul></aside><section class="stage"><div id="frame" class="frame"><img id="preview" src="/ui"><div id="overlay" class="overlay"></div></div></section><section class="props"><div id="status" class="muted">loading…</div><h3 id="title">Select an element</h3><div id="editor"></div><div class="card"><div class="actions"><button onclick="apply()" data-help="Save the edited properties above to the selected element.">Apply</button><button onclick="revertEl()" data-help="Undo every change to the selected element, back to its default.">Revert element</button><button class="danger" onclick="resetAll()" data-help="Clear every change in the current profile and start from defaults.">Reset profile</button></div></div><div class="card"><div class="ctitle">Position</div><p class="hint">Drag a box, or select one and use the arrow keys (Shift = 10px).</p><div class="pad-label">Nudge 1px</div><div class="nudge-pad" data-help="Move the selected element one pixel per click — the touch-friendly version of the arrow keys, for fine adjustments."><span></span><button onclick="nudge(0,-1)" title="up 1px">↑</button><span></span><button onclick="nudge(-1,0)" title="left 1px">←</button><span class="nudge-dot">1px</span><button onclick="nudge(1,0)" title="right 1px">→</button><span></span><button onclick="nudge(0,1)" title="down 1px">↓</button><span></span></div><div class="pad-label">Align to an edge</div><div class="align-pad" data-help="Snap the selected element to an edge (or the center) of the strip it lives in — one big jump, not a nudge."><button onclick="alignEl('left')" title="snap to left">⇤ Left</button><button onclick="alignEl('hcenter')" title="center horizontally">↔ Center</button><button onclick="alignEl('right')" title="snap to right">Right ⇥</button><button onclick="alignEl('top')" title="snap to top of its area">⤒ Top</button><button onclick="alignEl('vcenter')" title="center vertically in its area">↕ Middle</button><button onclick="alignEl('bottom')" title="snap to bottom of its area">⤓ Bottom</button></div></div><div class="card"><div class="ctitle">Arrange</div><div class="align-grid" data-help="Line the selected element up with another one: Match X copies another element's horizontal position; Match Y copies its vertical position."><button onclick="matchCoord('x')" title="match X of another element">Match X of…</button><button onclick="matchCoord('y')" title="match Y of another element">Match Y of…</button></div><div class="actions"><button onclick="stackElements()" data-help="Pick several elements and space them evenly down one column — turns a messy strip into a clean, even stack.">≡ Stack…</button></div></div><div class="card"><div class="ctitle">Profile</div><div class="actions"><select id="profile" style="flex:1;min-width:90px" data-help="Switch between named layouts, e.g. a day arrangement and a night one."></select><button onclick="newProfile()" title="new profile" data-help="Create a new named layout.">New</button><button onclick="renameProfile()" title="rename this profile" data-help="Rename the current layout (the default one is protected).">Rename</button><button class="danger" onclick="deleteProfile()" title="delete this profile" data-help="Delete the current layout (the default one is protected).">Del</button></div><div class="actions"><button onclick="exportCfg()" data-help="Download the whole layout as a JSON file, to back up or share.">Export</button><button onclick="document.getElementById('importFile').click()" data-help="Load a layout from a JSON file.">Import</button><input id="importFile" type="file" accept="application/json" hidden onchange="importCfg(this)"></div></div></section></main><div id="help"><b>Help mode.</b> Hover any control for what it does. Quick keys: <b>drag</b> a box to move · <b>arrow keys</b> nudge 1px (<b>Shift</b> = 10px) · a <b>red box</b> warns it's off-screen or across a divider line. Click <b>?</b> again to exit.</div><div id="helpbubble"></div>
<script>const CSRF=document.querySelector('meta[name=csrf_token]').content;
let S=null,selected=null,drag=null;
let snap=true, overlayZones=true, warnOverlap=false;

const api=async(path,method='GET',body=null)=>{
  let o={method,headers:{'X-CSRFToken':CSRF}};
  if(body!==null){o.headers['Content-Type']='application/json';o.body=JSON.stringify(body)}
  let r=await fetch('/plugins/tweak_view_ng/'+path,o);
  let ct=r.headers.get('content-type')||'';
  if(!ct.includes('application/json')){
    let t=await r.text();
    if(r.status===400&&/csrf/i.test(t))throw Error('Session expired — reload the page (Ctrl-Shift-R) and try again.');
    if(r.status===401||r.status===403)throw Error('Not authorized — reload the page and sign in again.');
    throw Error('Server returned a non-JSON '+r.status+' response — try reloading the page.')
  }
  let j=await r.json();if(!r.ok)throw Error(j.error||r.statusText);return j
};

function msg(t,bad=false){let e=document.getElementById('status');e.textContent=t;e.className=bad?'err':'muted';if(!bad&&/✓/.test(t)){let sv=document.getElementById('saved');if(sv){sv.textContent='saved ✓';sv.className='ok'}}}

async function refresh(){
  try{
    S=await api('api/state');
    document.getElementById('screen').textContent=`${S.screen.width}×${S.screen.height} • Pwn ${S.pwnagotchi_version}`;
    renderList();renderEditor();renderProfiles();scale();
    msg(`undo ${S.history.undo} • redo ${S.history.redo}${S.pending_missing.length?' • pending '+S.pending_missing.join(', '):''}`)
  }catch(e){msg(e.message,true)}
}

// --- helpers for strip membership + "moved from default" ---
function lineY(name){let e=S&&S.elements[name];if(!e)return null;let xy=e.properties.xy;if(!xy)return null;if(!Array.isArray(xy))xy=String(xy).split(',').map(Number);return xy[1]}
function topLineY(){let y=lineY('line1');return y==null?Math.round(S.screen.height*0.12):y}
function botLineY(){let y=lineY('line2');return y==null?Math.round(S.screen.height*0.88):y}
function isEdited(n){return S&&S.configured&&S.configured.edits&&(n in S.configured.edits)}

function renderList(){
  if(!S)return;
  let q=document.getElementById('search').value.toLowerCase(),ul=document.getElementById('elements');
  ul.innerHTML='';
  Object.entries(S.elements).filter(([n])=>n.toLowerCase().includes(q)).forEach(([n,e])=>{
    let li=document.createElement('li');
    li.className='el'+(n===selected?' sel':'');
    li.innerHTML=`<b>${esc(n)}</b>${isEdited(n)?'<span class=dot title="moved from default">●</span>':''}<span class=type>${esc(e.type)}</span>`;
    li.setAttribute('data-help','Select this element, then drag it on the preview, nudge it, align it, or edit its properties on the right. A ● means it has been moved from its default.');
    li.onclick=()=>{selected=n;renderList();renderEditor();drawBoxes()};
    ul.appendChild(li)
  })
}

function esc(s){return String(s).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}

function renderEditor(){
  let ed=document.getElementById('editor'),t=document.getElementById('title');
  ed.innerHTML='';
  if(!S||!selected||!S.elements[selected]){t.textContent='Select an element';return}
  let e=S.elements[selected];
  t.innerHTML=esc(selected)+' · '+esc(e.type)+(isEdited(selected)?' <span class=dot title="moved from default">●</span>':'');
  e.editable.forEach(k=>{
    let v=e.properties[k],row=document.createElement('div');row.className='row';
    let label=document.createElement('label');label.textContent=k;
    let input;
    if(['font','text_font','label_font','alt_font'].includes(k)){
      input=document.createElement('select');
      S.fonts.forEach(f=>{let o=document.createElement('option');o.value=f;o.textContent=f;if(f===v)o.selected=true;input.appendChild(o)})
    }else if(k==='wrap'){input=document.createElement('input');input.type='checkbox';input.checked=!!v}
    else{input=document.createElement('input');input.value=Array.isArray(v)?v.join(','):v??'';if(k==='xy')input.dataset.xy='1'}
    input.id='p_'+k;row.append(label,input);ed.appendChild(row)
  })
}

function readProps(){let e=S.elements[selected],p={};e.editable.forEach(k=>{let i=document.getElementById('p_'+k);if(!i)return;p[k]=k==='wrap'?i.checked:i.value});return p}

async function apply(){if(!selected)return;try{await api('api/update','POST',{element:selected,properties:readProps()});await refresh();reloadPreview();msg('applied ✓')}catch(e){msg(e.message,true)}}
async function revertEl(){if(!selected)return;try{await api('api/revert','POST',{element:selected});await refresh();reloadPreview();msg('reverted ✓')}catch(e){msg(e.message,true)}}
async function resetAll(){if(!confirm('Reset the active profile?'))return;try{await api('api/reset','POST',{});await refresh();reloadPreview()}catch(e){msg(e.message,true)}}
async function undo(){try{await api('api/undo','POST',{});await refresh();reloadPreview()}catch(e){msg(e.message,true)}}
async function redo(){try{await api('api/redo','POST',{});await refresh();reloadPreview()}catch(e){msg(e.message,true)}}
function addShape(type){let name=prompt('Shape name');if(!name)return;api('api/add_shape','POST',{name,type,properties:{xy:[5,5,40,25],color:255,width:1}}).then(()=>{selected=name;refresh();reloadPreview()}).catch(e=>msg(e.message,true))}

// --- fine 1px nudge pad (touch-friendly twin of the arrow keys) ---
function nudge(dx,dy){
  if(!selected||!S||!S.elements[selected]){msg('select an element first',true);return}
  let xy=S.elements[selected].properties.xy;
  if(xy==null){msg('this element has no position to move',true);return}
  if(!Array.isArray(xy))xy=String(xy).split(',');
  let a=xy.map(Number);
  a[0]+=dx;a[1]+=dy;if(a.length>=4){a[2]+=dx;a[3]+=dy}
  let i=document.getElementById('p_xy');if(i)i.value=a.join(',');
  apply()
}

// --- auto-align (server does the math) ---
async function alignEl(edge){if(!selected){msg('select an element first',true);return}try{await api('api/align','POST',{element:selected,edge});await refresh();reloadPreview();msg('aligned '+edge+' ✓')}catch(e){msg(e.message,true)}}
function otherNames(){return S?Object.keys(S.elements).filter(n=>n!==selected).sort():[]}
async function matchCoord(axis){
  if(!selected){msg('select an element first',true);return}
  let opts=otherNames();if(!opts.length){msg('no other elements',true);return}
  let target=prompt('Match '+axis.toUpperCase()+' of which element?\n\n'+opts.join(', '));
  if(!target)return; target=target.trim();
  if(!S.elements[target]){msg('no element named '+target,true);return}
  try{await api('api/match','POST',{element:selected,target,axis});await refresh();reloadPreview();msg('matched '+axis.toUpperCase()+' of '+target+' ✓')}catch(e){msg(e.message,true)}
}
async function stackElements(){
  let all=S?Object.keys(S.elements).sort():[];
  let pick=prompt('Stack which elements down a column?\nComma-separated names (top-to-bottom order is auto):\n\n'+all.join(', '),selected||'');
  if(!pick)return;
  let names=pick.split(',').map(x=>x.trim()).filter(Boolean);
  if(names.length<2){msg('name at least 2 elements',true);return}
  let bad=names.filter(n=>!S.elements[n]);if(bad.length){msg('unknown: '+bad.join(', '),true);return}
  try{await api('api/stack','POST',{elements:names});await refresh();reloadPreview();msg('stacked '+names.length+' ✓')}catch(e){msg(e.message,true)}
}
async function renameProfile(){
  let cur=S&&S.active_profile;if(!cur)return;
  if(cur==='default'){msg("can't rename the default profile",true);return}
  let nn=prompt('Rename profile "'+cur+'" to:');if(!nn)return;
  try{await api('api/profile','POST',{op:'rename',name:cur,new:nn.trim()});selected=null;await refresh();reloadPreview();msg('renamed ✓')}catch(e){msg(e.message,true)}
}
async function deleteProfile(){
  let cur=S&&S.active_profile;if(!cur)return;
  if(cur==='default'){msg("can't delete the default profile",true);return}
  if(!confirm('Delete profile "'+cur+'"? This cannot be undone from here.'))return;
  try{await api('api/profile','POST',{op:'delete',name:cur});selected=null;await refresh();reloadPreview();msg('deleted ✓')}catch(e){msg(e.message,true)}
}
// --- help mode: the ? button turns on hover tooltips over every control ---
let helpMode=false;
function toggleHelp(){
  helpMode=!helpMode;
  document.getElementById('helpBtn').classList.toggle('on',helpMode);
  document.body.classList.toggle('help-on',helpMode);
  document.getElementById('help').style.display=helpMode?'block':'none';
  if(!helpMode)hideHelpBubble()
}
function showHelpBubble(el){
  let bub=document.getElementById('helpbubble');
  bub.textContent=el.getAttribute('data-help');
  bub.style.display='block';
  let r=el.getBoundingClientRect();
  let left=Math.max(8,Math.min(r.left,window.innerWidth-bub.offsetWidth-10));
  let top=r.bottom+6;
  if(top+bub.offsetHeight>window.innerHeight-6)top=Math.max(6,r.top-bub.offsetHeight-6);
  bub.style.left=left+'px';bub.style.top=top+'px'
}
function hideHelpBubble(){let b=document.getElementById('helpbubble');if(b)b.style.display='none'}
document.addEventListener('mouseover',ev=>{
  if(!helpMode)return;
  let el=ev.target.closest('[data-help]');
  if(el)showHelpBubble(el);else hideHelpBubble()
});
function markSaved(){let e=document.getElementById('saved');if(e){e.textContent='saved ✓';e.className='ok'}}

function renderProfiles(){
  let s=document.getElementById('profile');s.innerHTML='';
  S.profiles.forEach(n=>{let o=document.createElement('option');o.value=n;o.textContent=n;o.selected=n===S.active_profile;s.appendChild(o)});
  s.onchange=()=>api('api/profile','POST',{name:s.value}).then(()=>{selected=null;refresh();reloadPreview()}).catch(e=>msg(e.message,true))
}
function newProfile(){let n=prompt('New profile name');if(!n)return;api('api/profile','POST',{name:n}).then(()=>{selected=null;refresh();reloadPreview()}).catch(e=>msg(e.message,true))}
async function exportCfg(){let d=await api('api/export');let a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(d,null,2)],{type:'application/json'}));a.download='tweak_view_ng.json';a.click();URL.revokeObjectURL(a.href)}
function importCfg(inp){let f=inp.files[0];if(!f)return;let r=new FileReader();r.onload=()=>{try{let d=JSON.parse(r.result);api('api/import','POST',d).then(()=>{selected=null;refresh();reloadPreview()}).catch(e=>msg(e.message,true))}catch(e){msg('invalid JSON',true)}};r.readAsText(f)}
function reloadPreview(){let im=document.getElementById('preview');im.src='/ui?t='+Date.now()}

function toggleSnap(){snap=!snap;document.getElementById('snapBtn').classList.toggle('on',snap);msg('snap '+(snap?'on':'off'))}
function toggleZones(){overlayZones=!overlayZones;document.getElementById('zoneBtn').classList.toggle('on',overlayZones);drawBoxes()}

function scale(){
  if(!S)return;
  let im=document.getElementById('preview'),frame=document.getElementById('frame'),
    maxW=Math.max(250,document.querySelector('.stage').clientWidth-40),
    maxH=Math.max(150,document.querySelector('.stage').clientHeight-40),
    sc=Math.min(maxW/S.screen.width,maxH/S.screen.height,4);
  if(window.innerWidth<850)sc=Math.min((window.innerWidth-38)/S.screen.width,3);
  sc=Math.max(.5,sc);
  frame.style.width=(S.screen.width*sc)+'px';frame.style.height=(S.screen.height*sc)+'px';
  im.style.width='100%';im.style.height='100%';
  drawBoxes()
}

function boxRect(xy){let x=xy[0]||0,y=xy[1]||0,w=xy.length>=4?Math.max(4,(xy[2]-x)):18,h=xy.length>=4?Math.max(4,(xy[3]-y)):12;return {x,y,w,h}}

function drawBoxes(){
  if(drag)return;            // never rebuild the overlay mid-drag (would kill the live box)
  let ov=document.getElementById('overlay');ov.innerHTML='';
  if(!S)return;
  // safe-zone shading for top/bottom strips
  if(overlayZones){
    let ty=topLineY(),by=botLineY();
    let zt=document.createElement('div');zt.className='zone';zt.style.left='0';zt.style.top='0';zt.style.width='100%';zt.style.height=(ty/S.screen.height*100)+'%';ov.appendChild(zt);
    let zb=document.createElement('div');zb.className='zone';zb.style.left='0';zb.style.top=(by/S.screen.height*100)+'%';zb.style.width='100%';zb.style.height=((S.screen.height-by)/S.screen.height*100)+'%';ov.appendChild(zb)
  }
  Object.entries(S.elements).forEach(([n,e])=>{
    let xy=e.properties.xy;if(!xy)return;
    if(!Array.isArray(xy))xy=String(xy).split(',').map(Number);
    let r=boxRect(xy);
    let b=document.createElement('div');
    b.className='box'+(n===selected?' sel':'')+(isEdited(n)?' edited':'');
    b.style.left=(r.x/S.screen.width*100)+'%';b.style.top=(r.y/S.screen.height*100)+'%';
    b.style.width=(r.w/S.screen.width*100)+'%';b.style.height=(r.h/S.screen.height*100)+'%';
    b.title=n;
    let lbl=document.createElement('span');lbl.className='blabel';lbl.textContent=n;b.appendChild(lbl);
    b.onpointerdown=ev=>startDrag(ev,n,xy);
    b.onclick=()=>{selected=n;renderList();renderEditor();drawBoxes()};
    b.dataset.name=n;
    ov.appendChild(b)
  })
}

function applySnap(a){
  if(!snap)return a;
  let th=3;
  let edges=[0,S.screen.width-1];let ey=[0,S.screen.height-1,topLineY(),botLineY()];
  // snap x to screen edges
  edges.forEach(E=>{if(Math.abs(a[0]-E)<=th)a[0]=E});
  // snap y to edges + divider lines
  ey.forEach(E=>{if(Math.abs(a[1]-E)<=th)a[1]=E});
  return a
}

function crosses(a){
  // warn only on a real problem: pushed off a screen edge, or a box clearly
  // straddling a divider line (center on the far side), not merely touching a
  // line it legitimately sits against.
  let r=boxRect(a),ty=topLineY(),by=botLineY(),W=S.screen.width,H=S.screen.height;
  if(r.x<0||r.y<0||r.x+r.w>W||r.y+r.h>H)return true;
  let h=(a.length>=4?r.h:10),cy=r.y+h/2,tol=2;
  // straddling line1: top above it AND bottom well below it
  if(r.y<ty-tol && (r.y+h)>ty+tol)return true;
  if(r.y<by-tol && (r.y+h)>by+tol)return true;
  if(warnOverlap){
    for(let [n,e] of Object.entries(S.elements)){
      if(n===drag.n)continue;let oxy=e.properties.xy;if(!oxy)continue;
      if(!Array.isArray(oxy))oxy=String(oxy).split(',').map(Number);
      let o=boxRect(oxy);
      if(r.x< o.x+o.w && r.x+r.w> o.x && r.y< o.y+o.h && r.y+r.h> o.y)return true
    }
  }
  return false
}

function liveBox(a){
  if(!drag)return;
  let b=[...document.querySelectorAll('.box')].find(x=>x.dataset.name===drag.n);
  if(!b)return;
  let r=boxRect(a);
  b.style.left=(r.x/S.screen.width*100)+'%';b.style.top=(r.y/S.screen.height*100)+'%';
  b.classList.toggle('warn',crosses(a))
}

function startDrag(ev,n,xy){
  ev.preventDefault();
  selected=n;renderList();renderEditor();
  // mark the current box selected WITHOUT rebuilding the overlay (rebuilding
  // would detach the element being dragged and kill the drag).
  document.querySelectorAll('.box').forEach(b=>b.classList.toggle('sel',b.dataset.name===n));
  let frame=document.getElementById('frame').getBoundingClientRect();
  drag={id:ev.pointerId,n,xy:[...xy],sx:ev.clientX,sy:ev.clientY,fw:frame.width,fh:frame.height};
  let el=ev.currentTarget||ev.target;
  try{el.setPointerCapture(ev.pointerId)}catch(e){}
  el.onpointermove=moveDrag;el.onpointerup=endDrag;el.onpointercancel=endDrag
}

function moveDrag(ev){
  if(!drag)return;
  let dx=Math.round((ev.clientX-drag.sx)*S.screen.width/drag.fw),
      dy=Math.round((ev.clientY-drag.sy)*S.screen.height/drag.fh),
      a=[...drag.xy];
  a[0]+=dx;a[1]+=dy;if(a.length>=4){a[2]+=dx;a[3]+=dy}
  a=applySnap(a);
  drag.cur=a;
  let i=document.getElementById('p_xy');if(i)i.value=a.join(',');
  liveBox(a)   // Feature 1: box follows cursor live
}

function endDrag(ev){if(!drag)return;drag=null;document.querySelectorAll('.box.warn').forEach(b=>b.classList.remove('warn'));apply()}

// --- Feature: arrow-key nudge ---
window.addEventListener('keydown',ev=>{
  if(!selected||!S||!S.elements[selected])return;
  if(['INPUT','SELECT','TEXTAREA'].includes((ev.target.tagName||'')))return;
  let d={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[ev.key];
  if(!d)return;
  ev.preventDefault();
  let step=ev.shiftKey?10:1;
  let i=document.getElementById('p_xy');if(!i)return;
  let a=String(i.value).split(',').map(Number);
  a[0]+=d[0]*step;a[1]+=d[1]*step;if(a.length>=4){a[2]+=d[0]*step;a[3]+=d[1]*step}
  i.value=a.join(',');
  apply()
});

window.addEventListener('resize',scale);
document.getElementById('preview').onload=()=>{if(drag)return;scale();drawBoxes()};
refresh();
setInterval(()=>{if(!drag)reloadPreview()},7000);
</script></body></html>
"""


# Minimal, dependency-light, JavaScript-free recovery editor (harvest item 8).
# Jinja autoescaping renders every value as data, so hostile element names,
# types or values cannot inject markup. Plain <form> POSTs carry the CSRF token.
RECOVERY_UI = r"""
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tweak View NG - Recovery</title>
<style>
body{margin:0;background:#0b0e10;color:#d7e0e5;font:14px system-ui,sans-serif;padding:14px}
h1{font-size:18px;color:#5bd1ff;letter-spacing:.06em;margin:0 0 4px}
.sub{color:#83919a;font-size:12px;margin:0 0 14px}
.msg{padding:8px 10px;border-radius:5px;margin:0 0 14px;border:1px solid}
.msg.ok{border-color:#2e6b39;color:#79e28b;background:#0f1a12}
.msg.err{border-color:#6d3037;color:#ff6b78;background:#1a0f11}
table{border-collapse:collapse;width:100%;max-width:760px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #263039;vertical-align:top}
th{color:#83919a;font-weight:600;font-size:12px}
.el{color:#d7e0e5;font-weight:600}
.ty{color:#83919a;font-size:11px}
.edited{color:#5bd1ff}
form.inline{display:flex;gap:6px;align-items:center;margin:3px 0}
input[type=text]{background:#0d1114;color:#d7e0e5;border:1px solid #34414b;border-radius:4px;padding:5px;width:150px}
button{background:#0d1114;color:#d7e0e5;border:1px solid #34414b;border-radius:4px;padding:5px 10px;cursor:pointer}
button:hover{border-color:#5bd1ff}
.nav a{color:#5bd1ff;text-decoration:none;margin-right:12px}
code{color:#9fb0ba}
</style></head>
<body>
<h1>TWEAK VIEW NG - RECOVERY</h1>
<p class="sub">{{ version }} &middot; {{ width }}&times;{{ height }} &middot; profile <code>{{ active_profile }}</code> &middot; simple server-rendered fallback (no JavaScript)</p>
<p class="nav"><a href="/plugins/tweak_view_ng/">&larr; full editor</a></p>
{% if message %}<div class="msg {{ 'ok' if ok else 'err' }}">{{ message }}</div>{% endif %}
{% if last_error %}<div class="msg err">last error: {{ last_error }}</div>{% endif %}
<table>
<tr><th>element</th><th>property</th><th>value</th><th></th></tr>
{% for row in rows %}
  {% for f in row.fields %}
  <tr>
    <td>{% if loop.first %}<span class="el">{{ row.name }}</span><br><span class="ty">{{ row.type }}</span>{% endif %}</td>
    <td class="{{ 'edited' if f.edited else '' }}">{{ f.prop }}</td>
    <td colspan="2">
      <form class="inline" method="post" action="/plugins/tweak_view_ng/recovery">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <input type="hidden" name="element" value="{{ row.name }}">
        <input type="hidden" name="property" value="{{ f.prop }}">
        <input type="text" name="value" value="{{ f.value }}">
        <button type="submit">set</button>
      </form>
    </td>
  </tr>
  {% endfor %}
{% endfor %}
</table>
</body></html>
"""


RECOVERY_NOT_READY = r"""
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tweak View NG - Recovery</title>
<style>body{margin:0;background:#0b0e10;color:#d7e0e5;font:14px system-ui,sans-serif;padding:20px}
h1{font-size:18px;color:#5bd1ff}.p{color:#83919a}</style></head>
<body><h1>TWEAK VIEW NG - RECOVERY</h1>
<p class="p">{{ version }}</p>
<p>Not ready yet: <b>{{ message }}</b> (phase: <code>{{ phase }}</code>).</p>
<p class="p">The UI adapter is not available. Reload this page once the Pwnagotchi UI has finished starting.</p>
</body></html>
"""
