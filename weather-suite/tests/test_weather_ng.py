import sys
import os
import time
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")

import pwnagotchi.plugins  # noqa: E402
import weather_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


def make_plugin(**opts):
    p = mod.WeatherNG()
    p.options = dict(opts)
    p.on_loaded()
    return p


class FakeUI:
    def __init__(self):
        self.elements = {}
        self.values = {}
        self._lock = mock.MagicMock()
        self._lock.__enter__ = mock.Mock(return_value=None)
        self._lock.__exit__ = mock.Mock(return_value=False)

    def add_element(self, key, elem):
        self.elements[key] = elem

    def remove_element(self, key):
        if key not in self.elements:
            raise KeyError(key)
        del self.elements[key]

    def set(self, key, value):
        self.values[key] = value


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def weather_payload(temp=10.0, description="clear sky", cond_id=800):
    return {
        "main": {"temp": temp},
        "weather": [{"description": description, "id": cond_id}],
    }


def forecast_payload(temp=9.0, description="light rain", cond_id=500):
    return {
        "list": [
            {"main": {"temp": temp}, "weather": [{"description": description, "id": cond_id}]}
        ]
    }


def wait_for_thread(thread, timeout=3.0):
    if thread:
        thread.join(timeout=timeout)


# --- Registration -----------------------------------------------------------

check(
    "WeatherNG registers as a real pwnagotchi.plugins.Plugin subclass",
    issubclass(mod.WeatherNG, pwnagotchi.plugins.Plugin),
)

# --- __dependencies__ was fixed (originally wrongly listed scapy) ----------

check("dependencies declare requests, not scapy", mod.WeatherNG.__dependencies__ == {"pip": ["requests"]})

# --- on_ui_setup adds the element at the configured position ---------------

p1 = make_plugin(position_x=42, position_y=99)
ui1 = FakeUI()
p1.on_ui_setup(ui1)
check("on_ui_setup adds the element", mod.ELEMENT_NAME in ui1.elements)
check("configured position is honored", ui1.elements[mod.ELEMENT_NAME].xy == (42, 99))

# --- No api_key configured: shows a clear message, never calls the API -----

p2 = make_plugin()  # no api_key
ui2 = FakeUI()
with mock.patch("requests.get") as mock_get2:
    p2.on_ui_update(ui2)
    time.sleep(0.2)
check("no api_key: requests.get is never called", not mock_get2.called)
check("no api_key: a clear on-screen message is shown", "api_key" in ui2.values[mod.ELEMENT_NAME])

# --- Successful fetch populates display with icon + temp + unit + desc -----

p3 = make_plugin(api_key="testkey", location="Leeuwarden", units="metric", refresh_interval_seconds=3600)
ui3 = FakeUI()
captured = {}


def fake_get3(url, timeout=None):
    captured.setdefault("urls", []).append(url)
    if "forecast" in url:
        return FakeResponse(forecast_payload(temp=9.0, description="light rain", cond_id=500))
    return FakeResponse(weather_payload(temp=10.0, description="clear sky", cond_id=800))


with mock.patch("requests.get", side_effect=fake_get3):
    p3.on_ui_update(ui3)
    wait_for_thread(p3._fetch_thread)

check("fetch called both weather and forecast URLs", any("forecast" in u for u in captured["urls"]) and any("data/2.5/weather" in u for u in captured["urls"]))
check("fetch used the configured api_key in the URL", all("testkey" in u for u in captured["urls"]))
check("fetch used units=metric in the URL", all("units=metric" in u for u in captured["urls"]))
display_text = p3._display_text
check("display text includes current temp", "10.0" in display_text)
check("display text includes current description", "clear sky" in display_text)
check("display text includes forecast temp", "9.0" in display_text)
check("display text includes forecast description", "light rain" in display_text)
check("display text includes the °C unit symbol", "°C" in display_text)
check("display text includes a clear-sky icon glyph (800)", mod.icon_for(800) in display_text)
check("_last_success was set after a successful fetch", p3._last_success is not None)

# --- Cached value is shown between fetches; throttle prevents re-fetch -----

ui3b = FakeUI()
with mock.patch("requests.get") as mock_get3b:
    p3.on_ui_update(ui3b)
check("on_ui_update within refresh_interval_seconds doesn't re-fetch", not mock_get3b.called)
check("cached display text is shown between fetches", ui3b.values[mod.ELEMENT_NAME] == display_text)

# --- Network exception is caught, logged, and surfaces a distinct message --

p4 = make_plugin(api_key="testkey", refresh_interval_seconds=0)
ui4 = FakeUI()
p4._last_success = time.time() - 120  # pretend it worked 2 minutes ago
import requests as _requests_mod  # noqa: E402

with mock.patch("requests.get", side_effect=_requests_mod.exceptions.ConnectionError("boom")):
    p4.on_ui_update(ui4)
    wait_for_thread(p4._fetch_thread)
check("a network exception doesn't crash on_ui_update", True)
check("network error message is shown", "network error" in p4._display_text)
check("network error message references staleness (last ok ... ago)", "last ok" in p4._display_text)

# --- Malformed/unexpected API response is caught without crashing ---------

p5 = make_plugin(api_key="testkey", refresh_interval_seconds=0)
ui5 = FakeUI()
with mock.patch("requests.get", return_value=FakeResponse({"unexpected": "shape"})):
    p5.on_ui_update(ui5)
    wait_for_thread(p5._fetch_thread)
check("a malformed API response doesn't crash", True)
check("malformed response shows a distinct 'bad API response' message", "bad API response" in p5._display_text)

# --- icon_for maps representative condition codes to distinct glyphs ------

check("icon_for(800) is clear/sun", mod.icon_for(800) == "☀")
check("icon_for(211) (thunderstorm) is distinct from clear", mod.icon_for(211) != mod.icon_for(800))
check("icon_for(502) (rain) is distinct from thunderstorm", mod.icon_for(502) != mod.icon_for(211))
check("icon_for(601) (snow) is distinct from rain", mod.icon_for(601) != mod.icon_for(502))
check("icon_for(unknown) returns a placeholder, not a crash", mod.icon_for(999) == "?")
check("icon_for(non-numeric) doesn't crash", mod.icon_for(None) == "?")

# --- units=imperial produces a °F symbol and is passed to the request ------

p6 = make_plugin(api_key="testkey", units="imperial", refresh_interval_seconds=3600)
ui6 = FakeUI()
urls6 = []


def fake_get6(url, timeout=None):
    urls6.append(url)
    if "forecast" in url:
        return FakeResponse(forecast_payload())
    return FakeResponse(weather_payload())


with mock.patch("requests.get", side_effect=fake_get6):
    p6.on_ui_update(ui6)
    wait_for_thread(p6._fetch_thread)
check("imperial units are passed through to the request URL", all("units=imperial" in u for u in urls6))
check("display text uses the °F unit symbol for imperial units", "°F" in p6._display_text)

# --- on_webhook returns display text and a last_updated value --------------

p7 = make_plugin(api_key="testkey")
p7._display_text = "some cached text"
p7._last_success = time.time() - 30
result7 = p7.on_webhook("/", mock.Mock())
check("on_webhook returns the current display text", result7["display"] == "some cached text")
check("on_webhook returns a last_updated value", "ago" in result7["last_updated"])

p7b = make_plugin(api_key="testkey")
result7b = p7b.on_webhook("/", mock.Mock())
check("on_webhook reports 'never' when nothing has succeeded yet", result7b["last_updated"] == "never")

# --- on_unload removes the element without crashing -------------------------

p8 = make_plugin(api_key="testkey")
ui8 = FakeUI()
p8.on_ui_setup(ui8)
try:
    p8.on_unload(ui8)
    ok = True
except Exception:
    ok = False
check("on_unload removes the element without crashing", ok and mod.ELEMENT_NAME not in ui8.elements)


print(f"\n{len(failures)} failure(s) out of test run")
if failures:
    sys.exit(1)
