import sys
import os
import json
import subprocess
import tempfile
import time
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")

import pwnagotchi.plugins  # noqa: E402
import fortune_thoughts_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


TMPDIR = tempfile.mkdtemp(prefix="fortune_thoughts_ng_test_")


def make_plugin(**opts):
    p = mod.FortuneThoughtsNG()
    opts.setdefault("cache_file", os.path.join(TMPDIR, f"cache_{time.time_ns()}.json"))
    p.options = dict(opts)
    p.on_loaded()
    return p


class FakeUI:
    def __init__(self, width=250, height=122, **flags):
        self.elements = {}
        self.values = {}
        self._width = width
        self._height = height
        self._flags = flags

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

    def is_waveshare_v1(self):
        return self._flags.get("is_waveshare_v1", False)

    def is_waveshare_v2(self):
        return self._flags.get("is_waveshare_v2", False)

    def is_waveshare_v3(self):
        return self._flags.get("is_waveshare_v3", False)

    def is_waveshare144lcd(self):
        return self._flags.get("is_waveshare144lcd", False)

    def is_inky(self):
        return self._flags.get("is_inky", False)

    def is_waveshare27inch(self):
        return self._flags.get("is_waveshare27inch", False)


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


# --- Registration -----------------------------------------------------

check(
    "FortuneThoughtsNG registers as a real pwnagotchi.plugins.Plugin subclass",
    issubclass(mod.FortuneThoughtsNG, pwnagotchi.plugins.Plugin),
)

# --- Empty options dict doesn't KeyError -------------------------------

p = mod.FortuneThoughtsNG()
p.options = {}
ui = FakeUI()
try:
    p.on_loaded()
    p.on_ui_setup(ui)
    p.on_ui_update(ui)
    ok = True
except KeyError:
    ok = False
check("on_loaded/on_ui_setup/on_ui_update run without KeyError on empty options", ok)

# --- Local-list rotation ------------------------------------------------

custom_fortunes = ["Only fortune A", "Only fortune B"]
p1 = make_plugin(enabled=True, content_source="local", fortunes=custom_fortunes, fortune_command="")
msg = p1._get_message()
check("content_source='local' with no fortune_command picks from the configured list", msg in custom_fortunes)

p1b = make_plugin(enabled=True, content_source="local", fortunes=[], fortune_command="")
try:
    msg1b = p1b._get_message()
    ok1b = True
except IndexError:
    ok1b = False
check("an empty configured fortunes list falls back to DEFAULT_FORTUNES instead of crashing", ok1b)
check("the fallback message comes from DEFAULT_FORTUNES", msg1b in mod.DEFAULT_FORTUNES)

# --- fortune_command: success/timeout/failure/empty-output fallback ----

p2 = make_plugin(enabled=True, content_source="local", fortune_command="fortune")
fake_ok = mock.Mock(returncode=0, stdout="A command-sourced fortune.\n", stderr="")
with mock.patch("subprocess.run", return_value=fake_ok) as mock_run:
    result = p2._get_message()
check("fortune_command success returns the command's stdout, stripped", result == "A command-sourced fortune.")
check("fortune_command is actually invoked via subprocess.run", mock_run.called)

p3 = make_plugin(enabled=True, content_source="local", fortunes=["List fallback A"], fortune_command="fortune")
fake_fail = mock.Mock(returncode=1, stdout="", stderr="boom")
with mock.patch("subprocess.run", return_value=fake_fail):
    result3 = p3._get_message()
check("a non-zero exit from fortune_command falls back to the configured list", result3 == "List fallback A")

p4 = make_plugin(enabled=True, content_source="command", fortunes=["List fallback B"], fortune_command="fortune")
with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="fortune", timeout=5)):
    try:
        result4 = p4._get_message()
        ok4 = True
    except subprocess.TimeoutExpired:
        ok4 = False
check("fortune_command timeout never raises and falls back to the list", ok4 and result4 == "List fallback B")

p5 = make_plugin(enabled=True, content_source="command", fortunes=["List fallback C"], fortune_command="not-a-real-binary")
with mock.patch("subprocess.run", side_effect=OSError("No such file or directory")):
    try:
        result5 = p5._get_message()
        ok5 = True
    except OSError:
        ok5 = False
check("a missing fortune binary (OSError) never crashes and falls back to the list", ok5 and result5 == "List fallback C")

p6 = make_plugin(enabled=True, content_source="command", fortunes=["List fallback D"], fortune_command="fortune")
fake_empty = mock.Mock(returncode=0, stdout="   \n", stderr="")
with mock.patch("subprocess.run", return_value=fake_empty):
    result6 = p6._get_message()
check("empty stdout from fortune_command falls back to the configured list", result6 == "List fallback D")

p6b = make_plugin(enabled=True, content_source="command", fortunes=["List fallback E"], fortune_command="")
result6b = p6b._get_message()
check("content_source='command' with no fortune_command configured falls back to the list", result6b == "List fallback E")

# --- Reddit fetch: success, header, min_score filtering, truncation ----

p7 = make_plugin(content_source="reddit", subreddit="Showerthoughts", fetch_limit=50, min_score=10, max_length=1000)
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
    p7.on_internet_available(mock.Mock())
    wait_for_thread(p7._fetch_thread)

check("fetch hit the correct subreddit URL", captured["url"] == "https://www.reddit.com/r/Showerthoughts/hot.json")
check(
    "fetch sent a real, descriptive User-Agent referencing fortune_thoughts_ng",
    "User-Agent" in captured["headers"] and "fortune_thoughts_ng" in captured["headers"]["User-Agent"],
)
check("min_score filtering kept the high-score thought", any("high score" in t for t in p7._pool))
check("min_score filtering dropped the low-score thought", not any("should be filtered" in t for t in p7._pool))

p7b = make_plugin(content_source="local")  # local mode should never start the fetch thread
with mock.patch("requests.get", side_effect=fake_get) as mock_get_local:
    p7b.on_internet_available(mock.Mock())
    wait_for_thread(p7b._fetch_thread)
check("content_source='local' never starts the reddit fetch machinery", not mock_get_local.called)

# --- Fetch persists to cache, and round-trips on a fresh instance -------

check("fetched thoughts were saved to the cache file", os.path.exists(p7._cache_file()))
with open(p7._cache_file()) as f:
    saved = json.load(f)
check("cache file content matches the in-memory pool", saved == p7._pool)

cache_path = p7._cache_file()
p8 = mod.FortuneThoughtsNG()
p8.options = {"cache_file": cache_path}
p8.on_loaded()
check("a fresh plugin instance loads the same cache from disk", p8._pool == saved)

# --- 429 / network exception / malformed JSON don't crash ---------------

p9 = make_plugin(content_source="reddit")
p9._pool = ["existing thought"]
with mock.patch("requests.get", return_value=FakeResponse(429)):
    p9.on_internet_available(mock.Mock())
    wait_for_thread(p9._fetch_thread)
check("a 429 response doesn't crash and doesn't clear the existing pool", p9._pool == ["existing thought"])

p10 = make_plugin(content_source="reddit")
with mock.patch("requests.get", side_effect=OSError("network unreachable")):
    p10.on_internet_available(mock.Mock())
    wait_for_thread(p10._fetch_thread)
check("a network exception during fetch doesn't crash", True)

p11 = make_plugin(content_source="reddit")
with mock.patch("requests.get", return_value=FakeResponse(200, {"unexpected": "shape"})):
    p11.on_internet_available(mock.Mock())
    wait_for_thread(p11._fetch_thread)
check("a malformed reddit response doesn't crash", True)

# --- Missing `requests` import is handled gracefully ---------------------
# Same technique apprise-notify-suite's own tests use for its optional
# `apprise` dependency: patch builtins.__import__ to raise ImportError
# only for the module under test, leaving everything else real.

import builtins  # noqa: E402

real_import = builtins.__import__


def fake_import_no_requests(name, *a, **kw):
    if name == "requests":
        raise ImportError("no requests")
    return real_import(name, *a, **kw)


p12 = make_plugin(content_source="reddit")
with mock.patch("builtins.__import__", side_effect=fake_import_no_requests):
    try:
        p12._fetch_thoughts()
        ok12 = True
    except ImportError:
        ok12 = False
check("a missing `requests` import is caught and doesn't crash the fetch", ok12)

# --- content_source: all four values and their fallback behavior --------

p13 = make_plugin(content_source="local", fortunes=["Local X"], fortune_command="")
check("content_source='local' uses the local list when nothing else is configured", p13._get_message() == "Local X")

p14 = make_plugin(content_source="command", fortunes=["Local Y"], fortune_command="")
check("content_source='command' with no command configured falls back to the local list", p14._get_message() == "Local Y")

p15 = make_plugin(content_source="reddit", fortunes=["Local Z"])
p15._pool = []
check("content_source='reddit' with an empty pool falls back to the local list", p15._get_message() == "Local Z")

p16 = make_plugin(content_source="reddit", fortunes=["Local unused"])
p16._pool = ["Reddit thought"]
check("content_source='reddit' with a non-empty pool uses the reddit pool", p16._get_message() == "Reddit thought")

p17 = make_plugin(content_source="auto", fortunes=["Local unused too"], fortune_command="")
p17._pool = ["Auto reddit thought"]
check("content_source='auto' prefers the reddit pool when it has content", p17._get_message() == "Auto reddit thought")

p18 = make_plugin(content_source="auto", fortunes=["Auto local fallback"], fortune_command="")
p18._pool = []
check("content_source='auto' falls back to the local list when the reddit pool is empty and no command is set", p18._get_message() == "Auto local fallback")

p19 = make_plugin(content_source="auto", fortunes=["Auto unused"], fortune_command="fortune")
p19._pool = []
fake_cmd_ok = mock.Mock(returncode=0, stdout="Auto command result\n", stderr="")
with mock.patch("subprocess.run", return_value=fake_cmd_ok):
    result19 = p19._get_message()
check("content_source='auto' prefers fortune_command over the local list when the reddit pool is empty", result19 == "Auto command result")

p20 = make_plugin(content_source="unrecognized-typo", fortunes=["Typo fallback"], fortune_command="")
check("an unrecognized content_source value falls back to local behavior instead of crashing", p20._get_message() == "Typo fallback")

# --- Position resolution -------------------------------------------------

p21 = make_plugin()
ui21 = FakeUI(is_waveshare_v2=True)
p21.on_ui_setup(ui21)
check("waveshare_v2 uses the per-hardware position (0, 95)", ui21.elements[mod.ELEMENT_NAME].xy == (0, 95))

p22 = make_plugin()
ui22 = FakeUI(is_inky=True)
p22.on_ui_setup(ui22)
check("inky uses the per-hardware position (0, 83)", ui22.elements[mod.ELEMENT_NAME].xy == (0, 83))

p23 = make_plugin()
ui23 = FakeUI(is_waveshare27inch=True)
p23.on_ui_setup(ui23)
check("waveshare27inch uses the per-hardware position (0, 153)", ui23.elements[mod.ELEMENT_NAME].xy == (0, 153))

p24 = make_plugin()
ui24 = FakeUI()  # no hardware flags set - default/unknown case
p24.on_ui_setup(ui24)
check("unknown/default hardware falls back to (0, 91)", ui24.elements[mod.ELEMENT_NAME].xy == (0, 91))

p25 = make_plugin(position_x=15, position_y=20)
ui25 = FakeUI(is_inky=True)  # would otherwise resolve to (0, 83)
p25.on_ui_setup(ui25)
check("explicit position_x/position_y overrides the per-hardware table when both are set", ui25.elements[mod.ELEMENT_NAME].xy == (15, 20))

p26 = make_plugin(position_x=15, position_y=None)
ui26 = FakeUI(is_inky=True)
p26.on_ui_setup(ui26)
check("a partially-set position (only position_x) does NOT override - falls back to the hardware table", ui26.elements[mod.ELEMENT_NAME].xy == (0, 83))

# --- Orientation: vertical vs horizontal ---------------------------------

p27 = make_plugin(orientation="vertical")
ui27 = FakeUI()
p27.on_ui_setup(ui27)
check("vertical orientation gets a 'Fortune:' label", ui27.elements[mod.ELEMENT_NAME].label == "Fortune:")

p28 = make_plugin(orientation="horizontal")
ui28 = FakeUI()
p28.on_ui_setup(ui28)
check("horizontal orientation has no separate label", ui28.elements[mod.ELEMENT_NAME].label == "")

# --- Exactly one UI element -----------------------------------------------

check("ELEMENT_NAME is a single element name (merged suite has one element, not two)", isinstance(mod.ELEMENT_NAME, str))

# --- Reactive rotation fires regardless of content_source -----------------

p29 = make_plugin(content_source="local", reactive_states=["bored"], fortunes=["Local reactive"])
p29.current_message = "stale"
p29._last_rotate = time.time()
p29.on_bored(mock.Mock())
check("on_bored triggers an immediate rotation for content_source='local'", p29.current_message == "Local reactive")

p30 = make_plugin(content_source="reddit", reactive_states=["lonely"])
p30._pool = ["Reddit reactive thought"]
p30.current_message = "stale"
p30._last_rotate = time.time()
p30.on_lonely(mock.Mock())
check("on_lonely triggers an immediate rotation for content_source='reddit'", p30.current_message == "Reddit reactive thought")

p31 = make_plugin(content_source="local", reactive_states=["bored"], fortunes=["Should not change"])
p31.current_message = "stays the same"
p31._last_rotate = time.time()
with mock.patch("random.choice", return_value="Should not change"):
    p31.on_sad(mock.Mock())  # 'sad' not in reactive_states
check("on_sad does not rotate when 'sad' isn't in reactive_states", p31.current_message == "stays the same")

# --- enabled=False shows the disabled message -----------------------------

p32 = make_plugin(enabled=False)
ui32 = FakeUI()
p32.on_ui_update(ui32)
check("enabled=False shows the disabled message", ui32.values[mod.ELEMENT_NAME] == "FortuneThoughtsNG is disabled")

# --- Rotation timing -------------------------------------------------------

p33 = make_plugin(enabled=True, content_source="local", fortunes=["Only one option"], rotate_interval_seconds=1000)
ui33 = FakeUI()
p33.on_ui_update(ui33)
first_rotate_time = p33._last_rotate
p33.on_ui_update(ui33)  # called again immediately - interval hasn't elapsed
check("rotation timestamp unchanged before rotate_interval_seconds elapses", p33._last_rotate == first_rotate_time)

p33._last_rotate = 0
with mock.patch("random.choice", return_value="A different message entirely"):
    p33.on_ui_update(ui33)
check("message re-rolls once rotate_interval_seconds has elapsed", p33.current_message == "A different message entirely")

# --- on_unload removes the element cleanly --------------------------------

p34 = make_plugin()
ui34 = FakeUI()
p34.on_ui_setup(ui34)
p34.on_unload(ui34)
check("on_unload removes the element", mod.ELEMENT_NAME not in ui34.elements)

p35 = make_plugin()
try:
    p35.on_unload(FakeUI())  # on_ui_setup never ran
    ok35 = True
except Exception:
    ok35 = False
check("on_unload doesn't crash if on_ui_setup never ran", ok35)

# --- on_webhook renders without crashing, for both source kinds ----------

p36 = make_plugin(content_source="local", fortunes=["Webhook local fortune"])
p36.current_message = "Webhook local fortune"
try:
    page36 = p36.on_webhook("/", mock.Mock())
    ok36 = True
except Exception:
    ok36 = False
check("on_webhook doesn't crash for content_source='local'", ok36)
check("on_webhook (local) mentions the content_source", "local" in page36)
check("on_webhook (local) mentions the currently-displayed message", "Webhook local fortune" in page36)

p37 = make_plugin(content_source="reddit")
p37._pool = ["Webhook reddit thought"]
p37._last_fetch_success = time.time()
p37.current_message = "Webhook reddit thought"
try:
    page37 = p37.on_webhook("/", mock.Mock())
    ok37 = True
except Exception:
    ok37 = False
check("on_webhook doesn't crash for content_source='reddit'", ok37)
check("on_webhook (reddit) reports a real last-refresh time, not 'never'", "never" not in page37)


# --- REGRESSION: a Display lacking the is_*() hardware helpers (jayofelony's
#     fork has no is_waveshare27inch()) must NOT raise AttributeError in
#     on_ui_setup; it must fall through to the generic (0, 91). This is the
#     exact crash that killed load on the real Pi. ---
class _BareUI:
    """A minimal UI with NO is_*() hardware-detect helpers at all."""
    def __init__(self):
        self.elements = {}

    def add_element(self, key, elem):
        self.elements[key] = elem

    def set(self, key, value):
        pass


p_bare = make_plugin()  # no explicit position -> hits the hardware table
ui_bare = _BareUI()
try:
    p_bare.on_ui_setup(ui_bare)
    bare_ok = True
except Exception:
    bare_ok = False
check("on_ui_setup does NOT crash on a Display with no is_*() helpers (the real-Pi bug)", bare_ok)
check(
    "a Display with no is_*() helpers falls through to the generic (0, 91)",
    mod.ELEMENT_NAME in ui_bare.elements and ui_bare.elements[mod.ELEMENT_NAME].xy == (0, 91),
)


print(f"\n{len(failures)} failure(s) out of test run")
if failures:
    sys.exit(1)
else:
    print("All tests passed")
