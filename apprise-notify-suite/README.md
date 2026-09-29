# AppriseNotifyNG

Sends push notifications for real pwnagotchi events through
[Apprise](https://github.com/caronc/apprise), which fans a single
notification out to dozens of destination services - Discord, ntfy,
Pushover, email, SMS gateways, Matrix, Slack, and many more - from one
list of URLs. One plugin, many destinations, instead of a separate
plugin per service.

## Why this exists

The original `apprise-notify.py` tried to wire up essentially every
callback name that has ever existed across pwnagotchi forks - about 30
methods, nearly all of which reference an undefined `title` variable.
Since `Plugin.__init_subclass__` on this fork instantiates a plugin's
class the moment it's imported, with no try/except around that call,
this crashed on the very first line it ever executed (`__init__`
itself), which risked taking down plugin loading more broadly than a
typical "this one hook is broken" bug.

This rebuild only wires up hooks that are actually real on this fork
(confirmed by grepping every `plugins.on(...)` call site in the real
`pwnagotchi/agent.py`, `automata.py`, `epoch.py`, and `grid.py`), builds
the Apprise object from `config.toml` instead of a hardcoded
`/home/pi/apprise-config.yml`, and sends notifications from a background
worker thread with a queue and a configurable cooldown, matching the
pattern already proven out in `Discord v3.0.1`.

## What this adds over a plain "notify on everything" plugin

- **Configurable event list** (`events`) instead of ~30 hardcoded
  hooks, most of which never fired anyway on this fork.
- **Background worker + queue**, so a slow/unreachable notification
  service can never block the main pwnagotchi loop (the original blocked
  every single hook with a `time.sleep(1)`).
- **`min_interval_seconds` cooldown** so a burst of `peer_detected` or
  `handshake` events doesn't hammer your notification service(s) - events
  queue up and get sent at a steady pace instead of being lost or
  flooding out all at once.
- **Real screenshot attachment** via `agent.view().image()` (confirmed
  real - `agent.view()` actually returns the live `Display` object,
  which subclasses `View` and adds `.image()`), toggled with
  `attach_screenshot`.
- **`config_path` support** for anyone who wants a full Apprise YAML
  config instead of (or alongside) a flat url list.

## Configuration

See `config.toml` - the `urls` (or `config_path`) and `events` list are
the two things you'll actually want to set. Apprise's own README has the
full URL syntax for every supported service.

## Still open

- No real-device test of actually delivering to a live Discord/ntfy/etc.
  endpoint - the sandbox test suite exercises the plugin's own logic
  against a stub of the `apprise` library (which isn't installable in
  this sandbox - no network access to PyPI), not the real library's
  wire format. Worth a real end-to-end smoke test with your own webhook
  URL once installed.
