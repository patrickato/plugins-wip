# DiscordNG

Sends captured handshakes (with an optional WiGLE-based location lookup)
and session reports to a Discord webhook. Rebuilt from `Discord v3.0.1`
(WPA2's `discord.py`, which itself has no real bugs beyond one small
attribute-name mismatch) - already the best-engineered plugin found in
this whole cluster: a threaded worker queue so Discord calls never block
the main loop, an expiring WiGLE location cache, HTTP retry/backoff, and
thread-safe deduplication of duplicate handshake events.

## What was actually broken

Just one real bug: the "previous session" report reads
`getattr(last_session, 'deauths', 0)` to show a deauth count, but the
real attribute on `LastSession` (confirmed in the cloned framework's
`pwnagotchi/log.py`) is `deauthed`, not `deauths`. Since it used
`getattr` with a default, this never crashed - it just silently always
returned 0, so the Deauths field never appeared in a session report even
when there were plenty. Fixed here.

## What this rebuild adds

- **`disable_wigle_lookup`** - skip the WiGLE dependency entirely if you
  don't want it, without needing to omit the API key.
- **`attachment_mode`** (`"file"` / `"json_only"`) - choose whether
  handshake reports upload the actual capture file or just send the
  embed/text.
- **`include_session_stats`** - toggle the "previous session" report on
  or off independently of the "online" notification.
- **`cache_file`** override, instead of the original's fixed location
  alongside the handshakes directory.

## Configuration

See `config.toml`. Only `webhook_url` is required; everything else has
a sensible default.

## Still open

- No real-device test of an actual Discord delivery (webhook, file
  upload, embed rendering) - the sandbox test suite exercises the
  plugin's own logic with the real `requests`/`urllib3` libraries but a
  mocked HTTP transport, not a live Discord endpoint.
