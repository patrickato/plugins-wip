# Notes: AppriseNotifyNG

## Why this one

`apprise-notify.py` was found to crash immediately at plugin-load time:
`__init__` references an undefined `title` variable (present in every
single method in the file, ~30 of them), and since
`Plugin.__init_subclass__` calls `cls()` with no try/except wrapping it,
this is a load-time crash, not a "just this one hook breaks" bug - a
more severe class of failure than most bugs found in this audit.

## Bugs found in the original

- `__init__` (and every other method) references `title`, which is
  never assigned anywhere in the file - guaranteed `NameError`.
- Every method also builds `outputfile = "/tmp/" + short + ".wav"` and
  attaches it to a notification, but nothing anywhere in the file ever
  creates that `.wav` file - the attachment would always be a path to a
  file that doesn't exist.
- The Apprise object and its config source are built once at **module
  import time**, hardcoded to `/home/pi/apprise-config.yml` - not
  configurable via `config.toml`, and `self.options`/`__defaults__` are
  declared but never actually read anywhere in the file.
- Every method blocks with `time.sleep(1)` between two `apobj.notify()`
  calls, directly in whichever real-time hook triggered it (`on_ready`,
  `on_handshake`, etc.) - this stalls the calling hook for at least a
  second, every time, with no queue or background thread.
- Roughly two-thirds of the ~30 hooks it implements aren't real hooks on
  this fork at all (`on_ai_ready`, `on_ai_policy`,
  `on_ai_training_start/step/end`, `on_ai_best_reward`,
  `on_ai_worst_reward`, `on_config_changed`, `on_free_channel`,
  `on_wait`, `on_cracked`) - confirmed via a full grep of every
  `plugins.on(...)` call site in the real cloned framework. They're
  dead code that never fires, on top of being individually broken.
  `on_unread_messages` isn't even the right hook name (real hook is
  `on_unread_inbox`, called with a single argument, not the five the
  original expected).

## What this rebuild keeps, drops, and adds

- **Keeps**: the core idea - one Apprise object, fed from config,
  notifying on real pwnagotchi events.
- **Drops**: the ~20 dead/nonexistent hooks; the per-hook blocking
  `time.sleep`; the nonexistent `.wav` attachment; the hardcoded
  `/home/pi/apprise-config.yml` path.
- **Adds**: a configurable `events` list restricted to hooks confirmed
  real on this fork; a background worker thread + queue (Discord
  v3.0.1's proven pattern) so notifications never block the calling
  hook; a `min_interval_seconds` cooldown so event bursts don't flood
  out all at once; real screenshot attachment via the actually-working
  `agent.view().image()` call (confirmed real - `Display` subclasses
  `View` and adds `.image()`, even though `View` itself doesn't have
  it); `config_path` support for a full Apprise YAML config as an
  alternative to a flat url list.

## A correction to earlier findings in this audit

While researching this plugin I confirmed `agent.view().image()` **is**
a real, working call - `agent.view()` returns the live `Display`
instance (not a bare `View`), and `Display` subclasses `View` and adds
`.image()`, `.clear()`, and the various `is_waveshareXXX()` hardware
checks. Earlier in this same cluster review I'd flagged `display.image()`
calls in `twitter.py`/`slack.py`/`mastodon.py` as broken based on `View`
alone lacking that method - that was incomplete: `.image()` itself is
fine on the real object. `twitter.py`'s crash is still real (missing
`os` import, and a genuinely nonexistent `.block_update()` call before
it ever reaches `.image()`); `slack.py`'s and `mastodon.py`'s crash
points are elsewhere in their code than originally stated (self.option/
self.channel typos in slack.py; an unvalidated empty `instance_url`
reaching `Mastodon.create_app()` in mastodon.py). None of this changes
their disposition - all three were already dropped by user decision
before this correction was found - just noting it for the record since
it affects how the earlier findings table should be read.

## Testing

21 tests in `tests/test_apprise_notify_ng.py`, all passing against the
real cloned `jayofelony/pwnagotchi` framework (the `apprise` pip package
itself is stubbed in `tests/stub_deps/apprise.py` - this sandbox has no
network access to install it, but the stub matches Apprise's public
`Apprise()`/`.add()`/`.notify()`/`AppriseConfig()` shape closely enough
to exercise all of this plugin's own logic). Covers: real plugin
registration; graceful handling of a missing apprise import; building
the Apprise object from configured `urls` and/or `config_path`; only
notifying for events actually in the configured `events` list; an
unknown/non-real event name in config not crashing load; a real
handshake event producing a body that includes the AP hostname; the
`attach_screenshot` toggle both ways; the worker thread surviving a
`notify()` exception; the `min_interval_seconds` cooldown actually
delaying and not dropping a second rapid event; a full queue not
raising; `on_unload` being safe even if `on_loaded` never ran; and the
webhook handler not crashing.

## Still open

- No real end-to-end test against a live notification service (Discord
  webhook, ntfy topic, etc.) - see README's "Still open" section.

## Original config preserved

A real original config file for `apprise-notify.py` was found (exact match) at
`itsdarklikehell/pwnagotchi-plugins/configs/apprise-notify.toml`. Preserved verbatim in this suite's own folder as
`config.original.toml`, per the project's standing config-preservation requirement,
for reference/troubleshooting if the rebuilt `config.toml` above ever
needs comparing against the source.
