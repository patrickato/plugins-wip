# TweakViewNG

**Status: work-in-progress (in `plugins-wip`).** Not yet moved to
`complete-plugins` - still needs a real-hardware pass before it's
considered done.

**Still no guardrails on WHAT you change** - that part of the original
plugin's warning stands. What's fixed here is *when* your saved tweaks
apply and *how reliably* the live-edit webhook page actually works -
not what you're allowed to break with it.

A fix/rebuild of `tweak_view.py` (itsdarklikehell/Sniffleupagus) - lets
you reposition, relabel, and re-font any real UI element live, through
a webhook page, and remembers your changes across restarts.

Target hardware: Raspberry Pi 4 + 3.5" TFT screen, jayofelony 64-bit
pwnagotchi image.

## Requirements & dependencies

- Python: none beyond what pwnagotchi already provides (`flask`, which
  the framework's own web server already depends on).
- No config values are required to get something useful - the default
  `filename` works out of the box.

## What's fixed vs. the original

1. **Hook-ordering bug**: the original loaded its saved tweaks file in
   `on_ready`, but `on_ui_setup` (which applies tweaks) fires from
   `View`'s own constructor - confirmed by tracing the real startup
   order (`pwnagotchi/ui/view.py`'s `View.__init__` calls
   `plugins.on('ui_setup', self)` directly; `Agent.__init__` receives
   an already-built `view` as an argument, and `on_ready` doesn't fire
   until `Automata.set_ready()` is called later, well after the View
   exists). So the original's saved tweaks were never actually applied
   on the very first frame - only from the next UI refresh onward.
   This rebuild loads tweaks in `on_loaded` instead (confirmed to run
   before `on_ui_setup`, since plugin loading itself happens before
   the display/View is constructed), so tweaks apply immediately.
2. **A swallowed JSON-preview crash**: `res += "%sJSON\n" % prefix,
   json.dumps(j, sort_keys=True, indent=4)` in the original's
   `dump_item()` is actually `res += (a_string, a_string)` - a 2-tuple
   - which raises `TypeError: can only concatenate str (not "tuple")
   to str`. That exception was caught by the function's own broad
   `except`, so it never crashed anything, but it also meant a
   JSON-looking string value was *never* actually shown pretty-printed
   on the webhook page - fixed to build the string correctly.
3. **An undefined-variable bug masking save failures**: in
   `update_from_request()`, a failed config-file write logged
   `ret += "<li><b>Unable to save settings:</b> %s" % repr(err)` - but
   the function's actual local variable is `res`, not `ret`. That
   `NameError` was itself caught by an outer `except`, so instead of
   telling you the save failed, you'd see a confusing, unrelated
   traceback. Fixed to reference the real variable.
4. Minor cleanup: `self._logger.warn(...)` (deprecated since Python
   3.3, though it still worked) replaced with `.warning(...)`
   throughout; every value written into an HTML response is now
   `html.escape()`d (the original inconsistently escaped some fields
   but not others, e.g. `show_tweaks()`'s checkbox values).

## What's added

- **Load-time validation** (approved addition): every entry in the
  saved tweaks file is checked against the required `VSS.<element>.<attr>`
  shape at load time. A malformed entry is skipped with a specific,
  named warning in the log (`skipping malformed tweak key '...' in
  <path> - expected the form 'VSS.<element>.<attribute>'`) instead of
  either silently applying garbage or discarding the *entire* file
  over one bad line, which is what the original's blanket
  try/except-around-the-whole-load effectively did.
- **A hardened version of the original's live webhook editor**
  (approved addition) - the page itself already existed upstream
  (`GET /` lists every real UI element with editable fields; `POST
  update` applies your changes and saves them; `POST delete_mods`
  reverts and removes selected tweaks), so this isn't a new feature so
  much as the same one made to actually work correctly: the two real
  bugs above are fixed, a clear "not ready yet" message is shown
  before the agent reports in (the original didn't fully guard this
  path), and every value insertable into the page is now escaped.

## Install

1. Copy `tweak_view_ng.py` into your custom plugins folder
   (`custom_plugins` in `config.toml`, typically
   `/etc/pwnagotchi/custom-plugins/`).
2. Add the block from `config.toml` to `/etc/pwnagotchi/config.toml`.
3. If you were running the original `tweak_view.py`, disable/remove it
   first, and optionally point this plugin's `filename` at your old
   save file (`/etc/pwnagotchi/tweak_view.json` by default in the
   original) if you want to keep your existing tweaks - the file
   format is unchanged.
4. Restart pwnagotchi:
   ```
   sudo systemctl restart pwnagotchi
   ```
5. Visit `http://pwnagotchi.local:8080/plugins/tweak_view_ng/` to see
   every real UI element and edit its position/font/label live.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Page says "Not ready yet" | The plugin hasn't seen `on_ready` fire yet (very early in startup) - wait a few seconds and reload |
| A saved tweak seems to be ignored | Check `pwnagotchi.log` for a "skipping malformed tweak key" warning naming exactly which entry didn't parse |
| "Unable to save settings" appears after an edit | The `filename` path isn't writable by the pwnagotchi process - check permissions on the containing directory |
| A tweak you deleted still shows up after a restart | Deleting via the webhook page removes it from the live UI and the save file immediately - if it reappears, check you're not also running the original `tweak_view.py` pointed at the same file |

## Still open / needs real-hardware testing

- Visual correctness of the live-edit form on the real 3.5" TFT screen
  at typical browser widths hasn't been checked outside a sandbox.
- The underlying "no guardrails" nature of this plugin is unchanged by
  design - it will happily let you set a font/position that looks
  wrong or breaks layout; that's the original's intended (if risky)
  purpose, not something this rebuild tries to prevent.
