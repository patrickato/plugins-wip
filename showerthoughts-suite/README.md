# ShowerThoughtsNG

Displays random r/Showerthoughts headlines on-screen while the unit is
idle. Purely a personality/flair feature - no security or functional
value, just something fun to glance at.

## Why this is a from-scratch build, not a fix

No source for the original "Showerthoughts" plugin could be found
anywhere across the archives searched in this audit (a broad filesystem
search for the name turned up nothing). There was nothing to diff or
repair, so this is a new implementation of the same idea.

## How it works

- Pulls a batch of hot posts from r/Showerthoughts' public JSON endpoint
  (`https://www.reddit.com/r/Showerthoughts/hot.json`) whenever the unit
  comes online, throttled to once per `refresh_interval_seconds` so it
  never hammers reddit.
- Filters out stickied/pinned posts and anything below `min_score`
  (cuts out low-quality or since-removed posts that are still listed),
  and truncates anything longer than `max_length` for the small screen.
- Caches the fetched pool to disk, so a restart (or a stretch with no
  internet) still has thoughts to show instead of a blank element.
- Rotates to a new thought on-screen every `rotate_interval_seconds`,
  and optionally reacts immediately to personality-state changes
  (`reactive_states`, default: bored/lonely/sad) for a more "idle
  chatter" feel.

## A note on reddit's rate limits

Reddit's public JSON endpoints are unauthenticated and can be rate
limited (HTTP 429) fairly readily, especially if the User-Agent header
is missing or generic. This plugin always sends a descriptive
User-Agent and backs off cleanly on a 429 (logs it, keeps the existing
pool, tries again on the next refresh cycle) rather than erroring out -
but if you find it's getting rate limited often, raising
`refresh_interval_seconds` is the first thing to try.

## Configuration

See `config.toml`.

## Still open

- No real-device test of actual reddit delivery or on-screen rendering
  - the sandbox test suite exercises the plugin's own fetch/filter/
  cache/rotate logic against a mocked HTTP response, not a live request
  to reddit.
