# Notes: TweakViewNG

## Why this one, and what "fix" means here

`tweak_view.py` is unusual among the Cluster 34 fix candidates: it's
not broken in the sense of failing to load or crashing outright - it's
a genuinely functional, already-featureful live UI editor (position,
font, label, label-spacing, max-length, per-element) with a working
webhook page and a save/revert/delete flow. The issues found are real
but narrower: one timing bug that delays when saved tweaks first
apply, and two swallowed exceptions in the webhook page that silently
broke a preview feature and masked error reporting. This rebuild fixes
those three things and hardens the webhook page's escaping, without
changing the plugin's fundamental shape or its explicit "no
guardrails" design.

## Bug 1: hook-ordering (tweaks load too late)

The original's `on_ready(self, agent)` is where `self._conf_file` gets
set and the saved tweaks JSON is loaded. But `on_ui_setup` - which
calls `update_elements()`, the only thing that actually *applies*
tweaks - fires earlier, from `View.__init__` itself
(`pwnagotchi/ui/view.py` line ~103: `plugins.on('ui_setup', self)`).
Tracing the real startup order: `Agent.__init__(self, view, config,
keypair)` (`pwnagotchi/agent.py`) receives an already-constructed
`view` object as an argument - meaning the View, and therefore
`on_ui_setup`, has already run by the time an `Agent` even exists.
`on_ready` only fires later, when something calls
`Automata.set_ready()` (`pwnagotchi/automata.py`), well after Agent
construction. So the original's very first `on_ui_setup` call always
ran against an empty `self._tweaks = {}` (set in `__init__`) - it
wasn't a crash (the loop over an empty dict is just a no-op), but
none of your saved tweaks applied on that first frame; they only took
effect from the next `on_ui_update` after `on_ready` finally fired.

Fix: load tweaks in `on_loaded` instead. Plugin loading itself
(`pwnagotchi/plugins/__init__.py`'s `load()`, which fires the `loaded`
event) happens as part of early startup, before the display/View is
constructed at all - so `on_loaded` is guaranteed to run before
`on_ui_setup` can. Tweaks are now in `self._tweaks` before the first
`update_elements()` call.

## Bug 2: swallowed JSON-preview crash

In `dump_item()`, the original had:

```python
try:
    j = json.loads(item)
    res += "%sJSON\n" % prefix, json.dumps(j, sort_keys=True, indent=4)
except Exception as inst:
    res += "%s%s = '%s'\n" % (prefix, name, item)
else:
    ...
```

The bare comma on the `res +=` line makes the right-hand side a
2-tuple: `("%sJSON\n" % prefix, json.dumps(...))`. `res += a_tuple`
where `res` is a `str` raises `TypeError: can only concatenate str
(not "tuple") to str`. Since this happens inside the `try:` block,
it's caught by the function's own `except Exception as inst:` clause -
so nothing crashed, but the intended feature (pretty-printing a
JSON-looking string value from a real UI element) never actually
worked; every such value silently fell through to the fallback
plain-text branch. Fixed to build the string with two statements
instead of a tuple.

## Bug 3: undefined-variable bug masking save failures

In `update_from_request()`, the function's local variable is `res`
throughout - except in one error branch:

```python
except Exception as err:
    ret += "<li><b>Unable to save settings:</b> %s" % repr(err)
```

`ret` was never defined in this function (it's a variable name reused
from a different method, `on_webhook`). This raises `NameError: name
'ret' is not defined`, itself caught by the *outer*
`except Exception as err:` a few lines down - so instead of the
intended "Unable to save settings: <real error>" message, a save
failure showed an unrelated `NameError` traceback that gave no hint
what actually went wrong. Fixed to reference `res`.

## What was verified as NOT a bug

The original's use of `{{ csrf_token() }}` inside strings passed to
`render_template_string()`, and `request.form`'s CSRF handling, looked
suspicious at first glance (a literal-looking Jinja tag inside an
f-string-built HTML blob) but is actually the **correct, framework-
blessed pattern** on this fork: `pwnagotchi/ui/web/server.py` imports
and applies `flask_wtf.csrf.CSRFProtect(app)` on the real web server,
which is exactly what makes `csrf_token()` resolve as a Jinja global.
Multiple bundled default plugins (`webcfg.py`, `auto_backup.py`,
`logtail.py`, `pisugarx.py`) use the identical pattern. No change
needed here - the test suite registers the same Jinja global by hand
on its bare test Flask app to exercise this code path correctly
without pulling in the full real web server.

## Testing

27 tests in `tests/test_tweak_view_ng.py`, all passing against the
real cloned `jayofelony/pwnagotchi` framework: real plugin
registration, the `VSS.<element>.<attr>` key parser, missing/malformed/
non-dict saved-file handling with specific per-entry warnings, the
core ordering fix (a tweak loaded in `on_loaded` applies on the very
first `on_ui_setup` call), `update_elements` applying xy/font/label
tweaks and cleanly skipping unknown elements/attributes,
`dump_item` correctly pretty-printing a JSON-looking string (fix #2)
and handling plain strings/ints/bools/lists, `update_from_request`
recording and saving a valid change and cleanly reporting a save
failure without a `NameError` (fix #3), the webhook page rendering
both before and after the agent is ready, and the delete-mods flow
reverting and removing a tracked tweak.

## Still open

- No real-hardware verification yet of the live-edit page's usability
  on an actual 3.5" TFT browser session - see README's "Still open"
  section.

## Original config preserved

A real original config file for `tweak_view.py` was found (exact match) at
`itsdarklikehell/pwnagotchi-plugins/tweak_view.json`. Preserved verbatim in this suite's own folder as
`config.original.json`, per the project's standing config-preservation requirement,
for reference/troubleshooting if the rebuilt `config.toml` above ever
needs comparing against the source.
