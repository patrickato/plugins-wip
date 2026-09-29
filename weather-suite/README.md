# WeatherNG

Shows current weather + a short forecast on the pwnagotchi screen, pulled
from OpenWeatherMap. Rebuilt from `Weather.py` (`WeatherForecast`).

## What was actually broken

- A live OpenWeatherMap API key was hardcoded directly in the source
  (`api_key = "3d34..."`), along with a hardcoded location
  (`"Leeuwarden"`), and there were **zero** config options - unusable
  for anyone but the original author, and the embedded key was exposed
  to anyone who read the file.
- `__dependencies__` declared `"pip": ["scapy"]`, which this plugin
  never uses - the actual runtime dependency is `requests` (`toml`/
  `pyyaml` are already framework dependencies).
- A bare `except:` in `on_ui_update` swallowed every possible error
  (network failure, bad JSON, missing key, rate limiting - anything)
  indiscriminately, always showing the same generic "Error getting
  weather forecast" with no way to tell what actually went wrong.
- The live API call happened directly inside `on_ui_update`, which runs
  on every screen render tick - a busy/idle unit could hit the live API
  far more often than needed (and OpenWeatherMap's free tier is rate
  limited).

## What this rebuild adds

- Real config options: `api_key` (**required**, no default - see
  Configuration below), `location`, `units`, `refresh_interval_seconds`,
  `position_x`/`position_y`.
- Fixed `__dependencies__` to `{"pip": ["requests"]}`.
- The fetch now runs on a background thread, throttled by
  `refresh_interval_seconds`; `on_ui_update` only ever displays the
  cached result, so it never blocks or hammers the API on a render tick.
- The bare `except:` is replaced with specific handling:
  `requests.exceptions.RequestException` for network problems, and
  `KeyError`/`IndexError`/`TypeError`/`ValueError` for a malformed or
  unexpected API response - each logs what actually happened and shows
  a distinct on-screen message (`"Weather: network error"` vs
  `"Weather: bad API response"`) instead of one generic string.
- **Condition icon**: a small unicode glyph (sun/cloud/rain/snow/etc.)
  mapped from OpenWeatherMap's numeric condition code, shown alongside
  the temperature and description.
- **Last-updated tracking**: the time of the last *successful* fetch is
  tracked internally. If fetches start failing (e.g. the API is down),
  the on-screen error message includes how long ago the last good
  reading was (`"(last ok 42m ago)"`), and `on_webhook` reports it too
  (`last_updated`), so it's possible to tell stale data from live data
  instead of silently showing old numbers with no indication.
- **Explicit units toggle**: `units` (`metric`/`imperial`/`standard`) is
  wired straight through to OpenWeatherMap's own `units` parameter, and
  the displayed unit symbol (`°C`/`°F`/`K`) follows it - no more
  hardcoded metric.

## Configuration

See `config.toml`. You must supply your own `api_key`:

1. Create a free account at <https://openweathermap.org/api>.
2. Under "My API keys", copy the default key (or generate a new one).
3. A newly-created key can take up to a couple of hours to activate -
   if you get 401 errors immediately after creating it, wait and retry.
4. Set `main.plugins.weather_ng.api_key` in `config.toml` to that key.

Without a configured `api_key`, the plugin loads fine but displays
"Weather: no api_key configured" and never calls the API.

## Still open

- No real-device test against the live OpenWeatherMap API - the test
  suite uses the real `requests` library with only the HTTP transport
  (`requests.get`) mocked, so request construction and response parsing
  are exercised for real.
- The forecast only ever shows the single next forecast entry (~3 hours
  out), matching the original's behavior - a multi-entry forecast strip
  would need more screen space than a `LabeledValue` easily gives.
