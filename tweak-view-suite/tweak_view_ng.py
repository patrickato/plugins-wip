import logging
import time
import html
import json

import pwnagotchi.plugins as plugins
from pwnagotchi.ui.components import LabeledValue, Text, Line
import pwnagotchi.ui.fonts as fonts

from flask import abort
from flask import render_template_string


DEFAULT_CONF_FILE = "/etc/pwnagotchi/tweak_view_ng.json"


def parse_tweak_key(tag):
    """A tweak key is always "VSS.<element>.<attr>". Returns the
    (element, attr) pair, or None if the key doesn't have that shape -
    used both at load time (to validate the saved file) and when
    applying a tweak (to skip anything malformed instead of raising)."""
    parts = tag.split(".")
    if len(parts) != 3 or parts[0] != "VSS" or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


class TweakViewNG(plugins.Plugin):
    __author__ = "fixed/rebuilt by this project's plugin audit, from itsdarklikehell/Sniffleupagus's tweak_view.py"
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = (
        "Live-edit UI element position/fonts/labels via a webhook page. "
        "Still no guardrails on WHAT you change - be careful - but the "
        "loading, saving, and JSON-preview bugs from the original are fixed."
    )
    __name__ = "TweakViewNG"
    __help__ = __description__

    myFonts = {
        "Small": fonts.Small,
        "BoldSmall": fonts.BoldSmall,
        "Medium": fonts.Medium,
        "Bold": fonts.Bold,
        "BoldBig": fonts.BoldBig,
        "Huge": fonts.Huge,
    }

    def __init__(self):
        self._agent = None
        self._logger = logging.getLogger(__name__)
        self._tweaks = {}
        self._untweak = {}
        self._conf_file = DEFAULT_CONF_FILE

    # ---- loading -----------------------------------------------------

    def on_loaded(self):
        # FIX: the original loaded its saved tweaks in on_ready, but
        # on_ui_setup (which applies them) fires from View's own
        # constructor - BEFORE Agent (and therefore on_ready) ever
        # runs. Loading here instead means tweaks are already in
        # self._tweaks by the time on_ui_setup runs, so they apply on
        # the very first frame instead of being silently skipped for
        # one refresh cycle at boot.
        self._conf_file = self.options.get("filename", DEFAULT_CONF_FILE)
        self._tweaks = self._load_and_validate(self._conf_file)
        logging.info("[TweakViewNG] plugin loaded, %d tweak(s) loaded from %s",
                     len(self._tweaks), self._conf_file)

    def _load_and_validate(self, path):
        try:
            with open(path, "r") as f:
                raw = json.load(f)
        except FileNotFoundError:
            self._logger.info("[TweakViewNG] no saved tweaks file at %s yet", path)
            return {}
        except Exception as err:
            self._logger.warning("[TweakViewNG] could not read/parse %s: %r", path, err)
            return {}

        if not isinstance(raw, dict):
            self._logger.warning(
                "[TweakViewNG] %s does not contain a JSON object at the top "
                "level (got %s) - ignoring the whole file",
                path, type(raw).__name__,
            )
            return {}

        # ADDED: per-entry validation with a specific, actionable warning
        # for each bad entry, instead of either applying garbage or
        # discarding the entire file over one bad line.
        clean = {}
        for tag, value in raw.items():
            if parse_tweak_key(tag) is None:
                self._logger.warning(
                    "[TweakViewNG] skipping malformed tweak key %r in %s - "
                    "expected the form 'VSS.<element>.<attribute>'",
                    tag, path,
                )
                continue
            clean[tag] = value
            self._logger.info("[TweakViewNG] loaded tweak %s -> %r", tag, value)
        return clean

    def _save(self):
        with open(self._conf_file, "w") as f:
            f.write(json.dumps(self._tweaks, indent=4))

    # ---- ui lifecycle --------------------------------------------------

    def on_ready(self, agent):
        self._agent = agent

    def on_unload(self, ui):
        try:
            for tag, value in self._untweak.items():
                parsed = parse_tweak_key(tag)
                if parsed is None:
                    continue
                element, key = parsed
                self._logger.info("[TweakViewNG] reverting %s to %r", tag, value)
                try:
                    if element in ui._state._state and hasattr(ui._state._state[element], key):
                        setattr(ui._state._state[element], key, value)
                except Exception as err:
                    self._logger.warning("[TweakViewNG] on_unload revert %s: %r", tag, err)
        except Exception as err:
            self._logger.warning("[TweakViewNG] on_unload: %r", err)

    def on_ui_setup(self, ui):
        try:
            self.update_elements(ui)
        except Exception as err:
            self._logger.warning("[TweakViewNG] ui setup: %r", err)

    def on_ui_update(self, ui):
        self.update_elements(ui)

    def update_elements(self, ui):
        try:
            state = ui._state._state
            for tag, value in self._tweaks.items():
                parsed = parse_tweak_key(tag)
                if parsed is None:
                    continue  # already warned about at load time
                element, key = parsed
                try:
                    if element not in state or key not in dir(state[element]):
                        continue
                    if tag not in self._untweak:
                        self._untweak[tag] = getattr(state[element], key)
                        self._logger.info("[TweakViewNG] saved for unload: %s = %r", tag, self._untweak[tag])

                    if key == "xy":
                        new_xy = [int(float(x.strip())) for x in value.split(",")]
                        if state[element].xy != new_xy:
                            state[element].xy = new_xy
                    elif key in ("font", "text_font", "label_font"):
                        if value in self.myFonts:
                            setattr(state[element], key, self.myFonts[value])
                    elif key == "label":
                        state[element].label = value
                    elif key == "label_spacing":
                        state[element].label_spacing = int(value)
                    elif key == "max_length":
                        state[element].max_length = int(value)
                except Exception as err:
                    self._logger.warning("[TweakViewNG] tweak failed for key %s: %r", tag, err)
        except Exception as err:
            self._logger.warning("[TweakViewNG] ui update: %r", err)

    # ---- webhook UI ------------------------------------------------

    def show_tweaks(self, request):
        res = (
            '<form method=POST action="%s/delete_mods"><input id="csrf_token" name="csrf_token" type="hidden" value="{{ csrf_token() }}">\n'
            % request.path
        )
        res += "<ul>\n"
        for tw, val in self._tweaks.items():
            res += '<li><input type=checkbox name=delete_me id="%s" value="%s"> %s: %s\n' % (
                html.escape(tw), html.escape(tw), html.escape(tw), html.escape(repr(val)),
            )
            if tw in self._untweak:
                res += "(orig: %s)\n" % html.escape(repr(self._untweak[tw]))
        res += "</ul>"
        res += '<input type=submit value="Delete Selected Mods"></form>'
        return res

    def dump_item(self, name, item, prefix=""):
        res = ""
        if type(item) is int or type(item) is float:
            res += '%s: <input type=text name="%s%s" value="%s"><br>' % (
                html.escape(name), html.escape(prefix), html.escape(name), html.escape(str(item)),
            )
        elif type(item) is str:
            if item.startswith("{"):
                try:
                    parsed = json.loads(item)
                except Exception:
                    res += '%s: <input type=text name="%s%s" value="%s"><br>' % (
                        html.escape(name), html.escape(prefix), html.escape(name), html.escape(item),
                    )
                else:
                    # FIX: the original did `res += "...", json.dumps(...)`
                    # - a bare comma made that a 2-tuple, so `str += tuple`
                    # raised TypeError every time (silently swallowed by
                    # its own broad except, which meant a parseable JSON
                    # string value was NEVER actually shown pretty-printed).
                    res += "%s%s JSON:<pre>%s</pre>" % (
                        html.escape(prefix), html.escape(name),
                        html.escape(json.dumps(parsed, sort_keys=True, indent=4)),
                    )
            else:
                res += '%s: <input type=text name="%s%s" value="%s"><br>' % (
                    html.escape(name), html.escape(prefix), html.escape(name), html.escape(item),
                )
        elif type(item) is bool:
            res += "%s%s is %s<br>" % (html.escape(prefix), html.escape(name), item)
        elif type(item) is list:
            res += "%s[%s]<br>" % (html.escape(prefix), html.escape(name))
            for i, key in enumerate(item, start=1):
                res += self.dump_item("{%i}" % i, key, "  %s %s" % (" " * len(prefix), name)) + "<br>"
            if prefix == "":
                res += "%s[%s END]<br>" % (html.escape(prefix), html.escape(name))
        elif type(item) is dict:
            for key in item:
                res += "<li>" + self.dump_item(key, item[key], "%s%s" % (prefix, name)) + "\n"
            res += "</ul>"
        elif type(item) in (Text, LabeledValue, Line):
            res += "<b>%s:</b> %s\n<ul>" % (html.escape(type(item).__name__), html.escape(name))
            try:
                for key in dir(item):
                    if key.startswith("__"):
                        continue
                    val = getattr(item, key)
                    if key == "draw":
                        continue
                    elif key == "value":
                        res += '<li>%s.%s = "%s"\n' % (html.escape(name), html.escape(key), html.escape(str(val)))
                    elif key == "xy":
                        res += '<li>%s.xy: <input type=text name="%s.%s.xy" value="%s">' % (
                            html.escape(name), html.escape(prefix), html.escape(name),
                            html.escape(",".join(map(str, val))),
                        )
                    elif key in ("label", "label_spacing") or type(val) in (int, str):
                        res += '<li>%s.%s: <input type=text name="%s.%s.%s" value="%s"><br>' % (
                            html.escape(name), html.escape(key), html.escape(prefix),
                            html.escape(name), html.escape(key), html.escape(str(val)),
                        )
                    elif "font" in key:
                        tag = "%s.%s.%s" % (prefix, name, key)
                        res += '<li>%s.%s: <select id="%s" name="%s">\n' % (
                            html.escape(name), html.escape(key), html.escape(tag), html.escape(tag),
                        )
                        for label, f in self.myFonts.items():
                            selected = " selected" if val == f else ""
                            res += '  <option value="%s"%s>%s</option>' % (label, selected, label)
                        res += "</select>"
            except Exception as inst:
                res += "*%s] Error processing %s: %s<br>\n" % (html.escape(prefix), html.escape(name), html.escape(repr(inst)))
            res += "</ul>"
        return res

    def update_from_request(self, request):
        res = "<ul>"
        changed = False
        try:
            view = self._agent.view()
            for k, val in request.form.items():
                if not k.startswith("VSS."):
                    continue
                parsed = parse_tweak_key(k)
                if parsed is None:
                    res += "<li>skipping malformed field %s\n" % html.escape(k)
                    continue
                element, attr = parsed
                if element not in view._state._state or attr not in dir(view._state._state[element]):
                    res += "<li>%s has no attribute %s, skipping\n" % (html.escape(element), html.escape(attr))
                    continue
                oldval = getattr(view._state._state[element], attr)

                if "font" in attr:
                    if val in self.myFonts and oldval != self.myFonts[val]:
                        self._tweaks[k] = val
                        changed = True
                        res += "<li>%s.%s -> %s\n" % (html.escape(element), html.escape(attr), html.escape(val))
                elif type(oldval) in (list, tuple):
                    try:
                        new_vals = [x.strip() for x in val.split(",")]
                        if [str(v) for v in oldval] != new_vals:
                            self._tweaks[k] = val
                            changed = True
                            res += "<li>%s.%s -> %s\n" % (html.escape(element), html.escape(attr), html.escape(val))
                    except Exception as err:
                        res += "<li>couldn't parse %s for %s.%s: %s\n" % (html.escape(val), html.escape(element), html.escape(attr), html.escape(repr(err)))
                elif type(oldval) is int:
                    try:
                        if oldval != int(float(val)):
                            self._tweaks[k] = int(float(val))
                            changed = True
                            res += "<li>%s.%s -> %s\n" % (html.escape(element), html.escape(attr), html.escape(val))
                    except ValueError:
                        res += "<li>%s is not a number for %s.%s\n" % (html.escape(val), html.escape(element), html.escape(attr))
                elif type(oldval) is str:
                    if oldval != str(val):
                        self._tweaks[k] = val
                        changed = True
                        res += "<li>%s.%s -> %s\n" % (html.escape(element), html.escape(attr), html.escape(val))

            if changed:
                try:
                    self._save()
                    res += "<li>saved to %s\n" % html.escape(self._conf_file)
                except Exception as err:
                    # FIX: the original referenced an undefined `ret`
                    # variable here instead of `res` - a NameError that
                    # masked the real "unable to save" message with a
                    # confusing, unrelated traceback whenever a save
                    # actually failed (e.g. a permissions problem).
                    res += "<li><b>Unable to save settings:</b> %s\n" % html.escape(repr(err))
        except Exception as err:
            res += "<li><b>update from request err:</b> %s\n" % html.escape(repr(err))
        res += "</ul>"
        return res

    def on_webhook(self, path, request):
        try:
            if request.method == "GET":
                if path in ("", "/"):
                    ret = '<html><head><title>TweakViewNG</title><meta name="csrf_token" content="{{ csrf_token() }}"></head>'
                    ret += "<body><h1>Tweak View NG</h1>"
                    ret += '<img src="/ui?%s"><p>' % int(time.time())
                    if self._agent:
                        view = self._agent.view()
                        ret += '<h2>Available View Elements</h2><form method=post><input id="csrf_token" name="csrf_token" type="hidden" value="{{ csrf_token() }}">'
                        ret += self.dump_item("VSS", view._state._state)
                        ret += '<input type=submit name=submit value="Update View"></form><p>'
                    else:
                        ret += "<p>Not ready yet - the agent hasn't reported in.</p>"
                    ret += "<h2>Current Mods</h2>%s" % self.show_tweaks(request)
                    ret += "</body></html>"
                    return render_template_string(ret)
                abort(404)

            elif request.method == "POST":
                if path == "update":
                    body = self.update_from_request(request)
                    title, heading = "TweakViewNG - Updated", "Tweak View Update"
                elif path == "delete_mods":
                    body = self._handle_delete(request)
                    title, heading = "TweakViewNG - Updated", "Tweak View Update"
                else:
                    body = "<p>Unknown action: %s</p>" % html.escape(path)
                    title, heading = "TweakViewNG", "Tweak View"

                ret = '<html><head><title>%s</title><meta name="csrf_token" content="{{ csrf_token() }}"></head>' % title
                ret += "<body><h1>%s</h1>" % heading
                ret += '<img src="/ui?%s"><p>' % int(time.time())
                ret += body
                ret += "<h2>Current Mods</h2>%s" % self.show_tweaks(request)
                ret += "</body></html>"
                return render_template_string(ret)

            ret = '<html><head><title>TweakViewNG</title></head><body><h1>Tweak View NG</h1></body></html>'
            return render_template_string(ret)

        except Exception as err:
            self._logger.warning("[TweakViewNG] webhook err: %r", err)
            return (
                "<html><head><title>oops</title></head><body><code>%s</code></body></html>"
                % html.escape(repr(err))
            )

    def _handle_delete(self, request):
        if "delete_me" not in request.form:
            return "<p>nothing selected</p>"
        res = "<h2>Delete Mods</h2><ul>\n"
        changed = False
        for tag in request.form.getlist("delete_me"):
            if tag in self._untweak and self._agent:
                try:
                    view = self._agent.view()
                    parsed = parse_tweak_key(tag)
                    if parsed:
                        element, key = parsed
                        if element in view._state._state and hasattr(view._state._state[element], key):
                            setattr(view._state._state[element], key, self._untweak[tag])
                            res += "<li>reverted %s to %r\n" % (html.escape(tag), self._untweak[tag])
                    del self._untweak[tag]
                except Exception as err:
                    res += "<li>revert %s failed: %s\n" % (html.escape(tag), html.escape(repr(err)))
            if tag in self._tweaks:
                del self._tweaks[tag]
                res += "<li>removed mod %s\n" % html.escape(tag)
                changed = True
        if changed:
            try:
                self._save()
                res += "<li>saved mods\n"
            except Exception as err:
                res += "<li><b>Unable to save settings:</b> %s\n" % html.escape(repr(err))
        res += "</ul>\n"
        return res
