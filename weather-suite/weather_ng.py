import logging
import threading
import time

import pwnagotchi.plugins as plugins
import pwnagotchi.ui.components as components
import pwnagotchi.ui.view as view
import pwnagotchi.ui.fonts as fonts

# Rebuilt from Weather.py (WeatherForecast). Shows current + short-term
# forecast from OpenWeatherMap on-screen.

ELEMENT_NAME = "weather_ng"

# OpenWeatherMap condition-code first digit -> a small glyph. See
# https://openweathermap.org/weather-conditions for the full code list;
# codes are grouped by their leading digit (2xx=thunderstorm, 3xx=drizzle,
# 5xx=rain, 6xx=snow, 7xx=atmosphere/haze-fog-etc, 800=clear, 80x=clouds).
_ICONS = {
    2: "⛈",   # thunder cloud+lightning
    3: "\U0001F326",  # sun behind small cloud (drizzle stand-in)
    5: "\U0001F327",  # cloud with rain
    6: "❄",   # snowflake
    7: "\U0001F32B",  # fog
    8: "☁",   # cloud (800 clear is special-cased below)
}


def icon_for(owm_code):
    try:
        code = int(owm_code)
    except (TypeError, ValueError):
        return "?"
    if code == 800:
        return "☀"  # sun
    return _ICONS.get(code // 100, "?")


class WeatherNG(plugins.Plugin):
    __author__ = "rebuilt from itsdarklikehell/Bauke Molenaar's Weather.py (WeatherForecast)"
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = "Displays current weather + short forecast on the pwnagotchi screen, from OpenWeatherMap."
    __dependencies__ = {
        "pip": ["requests"],
    }

    DEFAULTS = {
        "enabled": False,
        # >>> USER INPUT REQUIRED <<< - get a free key at openweathermap.org
        # (see README "Configuration"). No default: the plugin refuses to
        # fetch without one, instead of silently using the original
        # author's baked-in key.
        "api_key": None,
        "location": "Leeuwarden",
        # "metric" (C), "imperial" (F), or "standard" (Kelvin) - passed
        # straight through to OpenWeatherMap's own `units` parameter.
        "units": "metric",
        "refresh_interval_seconds": 600,
        "position_x": 120,
        "position_y": 80,
    }

    def __init__(self):
        self._lock = threading.Lock()
        self._last_refresh = 0.0
        self._last_success = None  # unix timestamp of last successful fetch
        self._display_text = "Weather: waiting for first update"
        self._fetch_thread = None

    def _opt(self, key):
        return self.options.get(key, self.DEFAULTS[key])

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_loaded(self):
        logging.info("[WeatherNG] plugin loaded")
        if not self._opt("api_key"):
            logging.warning(
                "[WeatherNG] no api_key configured - set "
                "main.plugins.weather_ng.api_key in config.toml (see README)"
            )

    def on_ui_setup(self, ui):
        ui.add_element(
            ELEMENT_NAME,
            components.LabeledValue(
                color=view.BLACK,
                label="",
                value="",
                position=(self._opt("position_x"), self._opt("position_y")),
                label_font=fonts.Small,
                text_font=fonts.Small,
            ),
        )

    def on_unload(self, ui):
        with ui._lock:
            try:
                ui.remove_element(ELEMENT_NAME)
                logging.info("[WeatherNG] plugin unloaded")
            except KeyError:
                pass
            except Exception as e:
                logging.error("[WeatherNG] unload: %s" % e)

    # ------------------------------------------------------------------
    # Fetching (background thread, cached - never a live call from
    # on_ui_update, which runs on every render tick)
    # ------------------------------------------------------------------

    def on_ui_update(self, ui):
        self._maybe_start_fetch()
        with self._lock:
            ui.set(ELEMENT_NAME, self._display_text)

    def _maybe_start_fetch(self):
        if not self._opt("api_key"):
            with self._lock:
                self._display_text = "Weather: no api_key configured"
            return

        now = time.time()
        if now - self._last_refresh < self._opt("refresh_interval_seconds"):
            return
        if self._fetch_thread and self._fetch_thread.is_alive():
            return

        self._last_refresh = now
        self._fetch_thread = threading.Thread(
            target=self._fetch_weather, daemon=True, name="WeatherNGFetch"
        )
        self._fetch_thread.start()

    def _fetch_weather(self):
        try:
            import requests
        except ImportError as e:
            logging.error("[WeatherNG] requests not available: %s", e)
            self._set_error("Weather: requests not installed")
            return

        api_key = self._opt("api_key")
        location = self._opt("location")
        units = self._opt("units")
        weather_url = (
            "https://api.openweathermap.org/data/2.5/weather"
            "?q=%s&units=%s&appid=%s" % (location, units, api_key)
        )
        forecast_url = (
            "https://api.openweathermap.org/data/2.5/forecast"
            "?q=%s&units=%s&appid=%s" % (location, units, api_key)
        )

        try:
            weather_response = requests.get(weather_url, timeout=10).json()
            forecast_response = requests.get(forecast_url, timeout=10).json()

            current_temp = weather_response["main"]["temp"]
            current_description = weather_response["weather"][0]["description"]
            current_icon = icon_for(weather_response["weather"][0].get("id"))

            forecast_list = forecast_response["list"]
            forecast_temp = forecast_list[0]["main"]["temp"]
            forecast_description = forecast_list[0]["weather"][0]["description"]

            unit_symbol = self._unit_symbol(units)
            text = "%s %s%s %s\n%s%s %s" % (
                current_icon,
                current_temp,
                unit_symbol,
                current_description,
                forecast_temp,
                unit_symbol,
                forecast_description,
            )

            with self._lock:
                self._display_text = text
                self._last_success = time.time()
            logging.info("[WeatherNG] refreshed weather for %s" % location)

        except requests.exceptions.RequestException as e:
            logging.error("[WeatherNG] network error fetching weather: %s" % e)
            self._set_error("Weather: network error")
        except (KeyError, IndexError, TypeError, ValueError) as e:
            logging.error("[WeatherNG] couldn't parse weather response: %s" % e)
            self._set_error("Weather: bad API response")

    @staticmethod
    def _unit_symbol(units):
        return {"metric": "°C", "imperial": "°F", "standard": "K"}.get(units, "")

    def _set_error(self, message):
        stale_suffix = ""
        with self._lock:
            if self._last_success:
                age_minutes = int((time.time() - self._last_success) / 60)
                stale_suffix = " (last ok %dm ago)" % age_minutes
            self._display_text = message + stale_suffix

    def _last_updated_text(self):
        with self._lock:
            if not self._last_success:
                return "never"
            age_seconds = int(time.time() - self._last_success)
            return "%ds ago" % age_seconds

    def on_webhook(self, path, request):
        logging.info("[WeatherNG] webhook pressed")
        # NOTE: _last_updated_text() takes self._lock itself, so it's
        # called outside any lock held here to avoid deadlocking on the
        # (non-reentrant) Lock.
        with self._lock:
            display_text = self._display_text
        return {
            "display": display_text,
            "last_updated": self._last_updated_text(),
        }
