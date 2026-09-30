# FortuneThoughtsNG

Displays a rotating short text message on screen while the unit is
idle - a local fortune, the output of a shell command, or a live
r/Showerthoughts post, depending on how you configure `content_source`.

This suite is a merge of `fortune-cookie-suite` (rebuilt from
`fortune_cookie.py`) and `showerthoughts-suite` (a from-scratch build -
no original "Showerthoughts" plugin source was ever locatable). The
post-cluster-review pass found both were small, closely-related "rotate
a short text message on screen while idle" plugins worth combining into
one rather than maintaining two nearly parallel implementations. See
`NOTES.md` for the full writeup of what was preserved from each
original and where to find it in the new file.

## What you get

- **One on-screen element** that rotates its text every
  `rotate_interval_seconds` (default 45s), replacing both originals'
  separate elements - there is exactly one now, not two.
- **`content_source`** picks where that text comes from:
  - `"local"` (default) - the configured `fortunes` list, or
    `fortune_command` if you've set one (command takes priority over the
    list). No network access at all.
  - `"command"` - always run `fortune_command`, falling back to the
    `fortunes` list on any failure.
  - `"reddit"` - always use the live r/Showerthoughts pool, falling back
    to the `fortunes` list if the pool is currently empty.
  - `"auto"` - prefer the reddit pool if it has content, otherwise
    `fortune_command` if configured, otherwise the `fortunes` list.
- **`orientation`** - `"vertical"` shows a `Fortune:` label above the
  message; `"horizontal"` (default) is just the message on one line.
- **Reactive rotation** - `reactive_states` (default
  `["bored", "lonely", "sad"]`) triggers an immediate rotation on those
  personality states, regardless of `content_source`.
- **A small status page** through pwnagotchi's own built-in web UI
  webhook (no separate server) showing the current `content_source`,
  pool size, currently-displayed message, and last reddit refresh time.

## Install

1. Copy `fortune_thoughts_ng.py` into your custom plugins folder.
2. Add the block from `config.toml` to `/etc/pwnagotchi/config.toml`.
3. If you were running `fortune-cookie-suite` or `showerthoughts-suite`,
   disable/remove those first (`enabled = false` under their own
   `[main.plugins.fortune_cookie_ng]` / `[main.plugins.showerthoughts_ng]`
   sections, or delete the files) - this plugin replaces both entirely.
4. Restart pwnagotchi:
   ```
   sudo systemctl restart pwnagotchi
   ```

## Configuration walkthrough

See `config.toml` for every option with inline comments. The short
version:

- Leave `content_source` at `"local"` (the default) for a
  zero-network-access, always-on-by-default experience using the
  built-in `fortunes` list.
- Set `fortune_command = "fortune"` (or any shell command that prints a
  line of text) to pull from the real `fortune` Unix utility instead,
  with `content_source` left at `"local"` or set to `"command"`.
- Set `content_source = "reddit"` or `"auto"` to pull live
  r/Showerthoughts posts - only then does the plugin start its
  background fetch thread on `on_internet_available`.
- `position_x`/`position_y` explicitly override the on-screen position
  when both are set; otherwise a per-hardware table (waveshare v1/v2/v3,
  waveshare144lcd, inky, waveshare27inch, and a sane default) is used.

## Original config preserved

See `NOTES.md`'s "Original config preserved" section - fortune-cookie's
real original config is preserved verbatim as `config.original.toml`;
showerthoughts never had one to preserve.

## Still open

- No real-device test against a live e-paper display, an actually
  installed `fortune` binary, or a live network call to reddit - see
  `NOTES.md`'s "Known limitations" section.
