import sys
import os
import json
import tempfile
import threading
import time
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")

import pwnagotchi.plugins  # noqa: E402
import showerthoughts_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


TMPDIR = tempfile.mkdtemp(prefix="showerthoughts_ng_test_")


def make_plugin(**opts):
    p = mod.ShowerThoughtsNG()
    opts.setdefault("cache_file", os.path.join(TMPDIR, f"cache_{time.time_ns()}.json"))
    p.options = dict(opts)
    p.on_loaded()
    return p


class FakeUI:
    def __init__(self, width=250, height=122):
        self.elements = {}
        self.values = {}
        self._width = width
        self._height = height

    def width(self):
        return self._width

    def height(self):
        return self._height

    def add_element(self, key, elem):
        self.elements[key] = elem

    def remove_element(self, key):
        if key not in self.elements:
            raise KeyError(key)
        del self.elements[key]

    def set(self, key, value):
        self.values[key] = value


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def reddit_payload(titles_and_scores):
    return {
        "data": {
            "children": [
                {"data": {"title": t, "score": s, "stickied": False}}
                for t, s in titles_and_scores
            ]
        }
    }


def wait_for_thread(thread, timeout=3.0):
    if thread:
        thread.join(timeout=timeout)


# --- Registration ---------------------------------------------------------

check(
    "ShowerThoughtsNG registers as a real pwnagotchi.plugins.Plugin subclass",
    issubclass(mod.ShowerThoughtsNG, pwnagotchi.plugins.Plugin),
)

# --- on_ui_setup adds the element at a default position --------------------

p = make_plugin()
ui = FakeUI()
p.on_ui_setup(ui)
check("on_ui_setup adds the element", mod.ELEMENT_NAME in ui.elements)
check(
    "default position is bottom-left, derived from ui.height()",
    ui.elements[mod.ELEMENT_NAME].xy == (0, ui.height() - 10),
)
p.on_unload(ui)
check("on_unload removes the element", mod.ELEMENT_NAME not in ui.elements)

# --- Configured position is honored ----------------------------------------

p2 = make_plugin(position_x=15, position_y=20)
ui2 = FakeUI()
p2.on_ui_setup(ui2)
check("configured position_x/position_y is used verbatim", ui2.elements[mod.ELEMENT_NAME].xy == (15, 20))

# --- on_unload is safe even if on_ui_setup never ran ------------------------

p3 = make_plugin()
try:
    p3.on_unload(FakeUI())
    ok = True
except Exception:
    ok = False
check("on_unload doesn't crash if on_ui_setup never ran", ok)

# --- Fetch: real requests.get call parsed correctly (params, headers, url) -

p4 = make_plugin(subreddit="Showerthoughts", fetch_limit=50, min_score=10, max_length=1000)
captured = {}


def fake_get(url, params=None, headers=None, timeout=None):
    captured["url"] = url
    captured["params"] = params
    captured["headers"] = headers
    return FakeResponse(
        200,
        reddit_payload(
            [
                ("A good thought with high score", 500),
                ("A low-score thought that should be filtered", 1),
            ]
        ),
    )


with mock.patch("requests.get", side_effect=fake_get):
    p4.on_internet_available(mock.Mock())
    wait_for_thread(p4._fetch_thread)

check("fetch hit the correct subreddit URL", captured["url"] == "https://www.reddit.com/r/Showerthoughts/hot.json")
check("fetch sent a real (non-empty, descriptive) User-Agent header", "User-Agent" in captured["headers"] and len(captured["headers"]["User-Agent"]) > 10)
check("min_score filtering kept the high-score thought", any("high score" in t for t in p4._pool))
check("min_score filtering dropped the low-score thought", not any("should be filtered" in t for t in p4._pool))

# --- Fetch persists to the cache file --------------------------------------

check("fetched thoughts were saved to the cache file", os.path.exists(p4._cache_file()))
with open(p4._cache_file()) as f:
    saved = json.load(f)
check("cache file content matches the in-memory pool", saved == p4._pool)

# --- Cache round-trips on a fresh plugin instance ---------------------------

cache_path = p4._cache_file()
p5 = mod.ShowerThoughtsNG()
p5.options = {"cache_file": cache_path}
p5.on_loaded()
check("a fresh plugin instance loads the same cache from disk", p5._pool == saved)
check("on_loaded picks a current thought from the loaded cache", p5._current in saved)

# --- 429 rate limit doesn't crash and leaves the pool untouched -----------

p6 = make_plugin()
p6._pool = ["existing thought"]
with mock.patch("requests.get", return_value=FakeResponse(429)):
    p6.on_internet_available(mock.Mock())
    wait_for_thread(p6._fetch_thread)
check("a 429 response doesn't crash and doesn't clear the existing pool", p6._pool == ["existing thought"])

# --- Network exception doesn't crash ---------------------------------------

p7 = make_plugin()
with mock.patch("requests.get", side_effect=OSError("network unreachable")):
    p7.on_internet_available(mock.Mock())
    wait_for_thread(p7._fetch_thread)
check("a network exception during fetch doesn't crash", True)

# --- Malformed JSON response doesn't crash ---------------------------------

p8 = make_plugin()
with mock.patch("requests.get", return_value=FakeResponse(200, {"unexpected": "shape"})):
    p8.on_internet_available(mock.Mock())
    wait_for_thread(p8._fetch_thread)
check("a malformed reddit response doesn't crash", True)

# --- refresh_interval_seconds throttles repeated calls ----------------------

p9 = make_plugin(refresh_interval_seconds=3600)
calls = []


def counting_get(*a, **kw):
    calls.append(1)
    return FakeResponse(200, reddit_payload([("Thought one", 100)]))


with mock.patch("requests.get", side_effect=counting_get):
    p9.on_internet_available(mock.Mock())
    wait_for_thread(p9._fetch_thread)
    p9.on_internet_available(mock.Mock())  # immediately again - should be throttled
    wait_for_thread(p9._fetch_thread)
check("a second on_internet_available call within refresh_interval_seconds doesn't re-fetch", len(calls) == 1)

# --- Rotation: on_ui_update rotates after rotate_interval_seconds ----------

p10 = make_plugin(rotate_interval_seconds=0)
p10._pool = ["thought A", "thought B", "thought C"]
p10._current = "thought A"
p10._last_rotate = 0  # force immediate rotation eligibility
ui10 = FakeUI()
with mock.patch("random.choice", return_value="thought B"):
    p10.on_ui_update(ui10)
check("on_ui_update rotates to a new thought once the interval has elapsed", ui10.values[mod.ELEMENT_NAME] == "thought B")

# --- Rotation is throttled: doesn't rotate before the interval elapses -----

p11 = make_plugin(rotate_interval_seconds=3600)
p11._pool = ["thought A", "thought B"]
p11._current = "thought A"
p11._last_rotate = time.time()  # just rotated
ui11 = FakeUI()
p11.on_ui_update(ui11)
check("on_ui_update does not rotate before rotate_interval_seconds elapses", ui11.values[mod.ELEMENT_NAME] == "thought A")

# --- Empty pool: on_ui_update doesn't crash, shows empty string ------------

p12 = make_plugin(rotate_interval_seconds=0)
p12._pool = []
p12._current = ""
ui12 = FakeUI()
try:
    p12.on_ui_update(ui12)
    ok = True
except Exception:
    ok = False
check("on_ui_update with an empty pool doesn't crash", ok)

# --- Reactive states trigger an immediate rotation --------------------------

p13 = make_plugin(reactive_states=["bored"])
p13._pool = ["thought X", "thought Y"]
p13._current = "thought X"
with mock.patch("random.choice", return_value="thought Y"):
    p13.on_bored(mock.Mock())
check("on_bored triggers an immediate rotation when 'bored' is in reactive_states", p13._current == "thought Y")

p14 = make_plugin(reactive_states=["bored"])
p14._pool = ["thought X", "thought Y"]
p14._current = "thought X"
with mock.patch("random.choice", return_value="thought Y"):
    p14.on_sad(mock.Mock())  # 'sad' not in reactive_states
check("on_sad does not rotate when 'sad' isn't in reactive_states", p14._current == "thought X")

# --- Long titles are truncated to max_length --------------------------------

p15 = make_plugin(max_length=20)
long_title = "This is a very long shower thought that exceeds the configured max length"
with mock.patch("requests.get", return_value=FakeResponse(200, reddit_payload([(long_title, 100)]))):
    p15.on_internet_available(mock.Mock())
    wait_for_thread(p15._fetch_thread)
check("long titles are truncated to max_length", len(p15._pool[0]) <= 20)
check("truncated titles end with an ellipsis", p15._pool[0].endswith("…"))

# --- Stickied posts are excluded --------------------------------------------

p16 = make_plugin()
payload = {
    "data": {
        "children": [
            {"data": {"title": "Pinned announcement", "score": 999, "stickied": True}},
            {"data": {"title": "A real shower thought", "score": 100, "stickied": False}},
        ]
    }
}
with mock.patch("requests.get", return_value=FakeResponse(200, payload)):
    p16.on_internet_available(mock.Mock())
    wait_for_thread(p16._fetch_thread)
check("stickied posts are excluded from the pool", "Pinned announcement" not in p16._pool)
check("non-stickied posts are included", "A real shower thought" in p16._pool)

# --- webhook handler doesn't crash ------------------------------------------

p17 = make_plugin()
try:
    p17.on_webhook("/", mock.Mock())
    ok = True
except Exception:
    ok = False
check("on_webhook doesn't crash", ok)


print(f"\n{len(failures)} failure(s) out of test run")
if failures:
    sys.exit(1)
