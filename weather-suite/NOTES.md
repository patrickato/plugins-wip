# Notes: WeatherNG

## Bugs found

- Hardcoded OpenWeatherMap API key (`api_key = "3d34a7f2abb93ca1fd5a5e4aa28db151"`)
  and hardcoded location (`"Leeuwarden"`) baked directly into the
  source, with zero config options - unusable for anyone but the
  original author, and the embedded key was exposed to anyone reading
  the file.
- `__dependencies__` declared `{"pip": ["scapy"]}` - wrong; the plugin
  actually needs `requests` (`toml`/`pyyaml` already ship with the
  framework). `scapy` is unrelated and unused here.
- A bare `except:` in `on_ui_update` caught literally everything
  (network errors, JSON errors, missing-key errors, rate limiting) and
  always showed the same "Error getting weather forecast" string, with
  nothing logged and no way to distinguish causes.
- The live weather + forecast API calls happened directly inside
  `on_ui_update`, which the framework calls on every UI render tick -
  no throttle at all, so a busy unit could call the live API far more
  often than useful, risking the free tier's rate limit.

## What this build does

- Config-driven `api_key` (required, no default - explicitly documented
  as `>>> USER INPUT REQUIRED <<<` in `config.toml`), `location`,
  `units`, `refresh_interval_seconds`, `position_x`/`position_y`.
- Fixed `__dependencies__` to `{"pip": ["requests"]}`.
- Fetch moved to a background thread (`_fetch_weather`, daemon,
  triggered from `on_ui_update` but throttled by
  `refresh_interval_seconds` and an "already running" check);
  `on_ui_update` itself only ever reads the cached `_display_text`
  under a lock - it never blocks on network I/O.
- Bare `except:` replaced with `requests.exceptions.RequestException`
  (network) and `KeyError`/`IndexError`/`TypeError`/`ValueError`
  (parsing) branches, each logging the real exception and setting a
  distinct on-screen message.
- `icon_for(owm_code)` maps OpenWeatherMap's numeric condition code to a
  small unicode glyph (sun/cloud/rain/snow/thunder/fog), grouped by the
  code's leading digit per OWM's own condition-code documentation.
- `_last_success` timestamp tracked on every successful fetch; surfaced
  both in the on-screen error message (`"(last ok Nm ago)"`) and via
  `on_webhook`'s `last_updated` field, so staleness is visible rather
  than silent.
- `_unit_symbol()` maps `units` to the displayed symbol (`°C`/`°F`/`K`),
  keeping the OpenWeatherMap `units` query param and the displayed
  symbol in sync instead of hardcoding `°C`.

## Testing

`tests/test_weather_ng.py`, run against the real cloned
`jayofelony/pwnagotchi` framework and the real `requests` library, with
only `requests.get` (the HTTP transport) mocked - matching the
showerthoughts-suite test pattern. Covers: real plugin registration;
`on_ui_setup` adding the UI element at the configured position;
`on_ui_update` with no `api_key` configured shows the
"no api_key configured" message and never calls `requests.get`; a
successful fetch populates `_display_text` with icon + temp + unit
symbol + description for both current and forecast, and sets
`_last_success`; `on_ui_update` only ever displays the cached value
between fetches (a second call within `refresh_interval_seconds`
doesn't re-fetch); a `RequestException` during fetch is caught, logged,
and produces a "network error" message referencing how long ago the
last successful reading was; a malformed/unexpected JSON response
(missing keys) is caught without crashing and produces a "bad API
response" message; `icon_for` maps representative condition codes
(800 clear, 2xx thunderstorm, 5xx rain, 6xx snow) to distinct glyphs and
returns `"?"` for an unrecognized code; `units=imperial` produces a °F
symbol and is passed through to the request URL; `on_webhook` returns
both the current display text and a `last_updated` value; and
`on_unload` removes the UI element without crashing.

## Still open

- See README's "Still open" section (no live-API test; forecast only
  shows the single next entry).

## Original config preserved

A real original config file for `Weather.py` was found (exact match) at
`itsdarklikehell/pwnagotchi-plugins/configs/Weather.toml`. Preserved verbatim in this suite's own folder as
`config.original.toml`, per the project's standing config-preservation requirement,
for reference/troubleshooting if the rebuilt `config.toml` above ever
needs comparing against the source.
