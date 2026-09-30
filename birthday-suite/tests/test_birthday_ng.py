import sys
import os
import json
import time
import datetime
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")

import pwnagotchi.plugins  # noqa: E402
import birthday_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


def make_plugin(**opts):
    p = mod.BirthdayNG()
    p.options = dict(opts)
    return p


# --- Registration -----------------------------------------------------------

check(
    "BirthdayNG registers as a real pwnagotchi.plugins.Plugin subclass",
    issubclass(mod.BirthdayNG, pwnagotchi.plugins.Plugin),
)

check(
    "__dependencies__ declares python-dateutil (not 'none')",
    "python-dateutil" in mod.BirthdayNG.__dependencies__["pip"],
)

# --- Missing options fall back to real defaults, not KeyError --------------

p = make_plugin()  # empty options, like a bare/partial config.toml
try:
    p.on_loaded_ok = True
    _ = p._opt("show_age")
    _ = p._opt("show_birthday")
    _ = p._opt("age_x_coord")
    _ = p._opt("age_y_coord")
    _ = p._opt("birthday_message")
    ok = True
except KeyError:
    ok = False
check("all options resolve via _opt() with no KeyError on an empty config", ok)
check("show_age defaults to True", p._opt("show_age") is True)
check("show_birthday defaults to False", p._opt("show_birthday") is False)

# --- on_ui_setup / on_ui_update / on_unload don't crash with empty options --

fake_ui = mock.Mock()
fake_ui._lock = mock.MagicMock()
try:
    p.on_ui_setup(fake_ui)
    setup_ok = True
except KeyError:
    setup_ok = False
check("on_ui_setup runs without KeyError on an empty config", setup_ok)

p.born_at = None
try:
    p.on_ui_update(fake_ui)
    update_ok = True
except KeyError:
    update_ok = False
check("on_ui_update runs without KeyError on an empty config", update_ok)
check("on_ui_update shows 'unknown' when born_at is None", fake_ui.set.call_args[0] == ("Age", "unknown"))

try:
    p.on_unload(fake_ui)
    unload_ok = True
except Exception:
    unload_ok = False
check("on_unload runs without crashing", unload_ok)

# --- load_data: real born_at present in brain.json --------------------------

sample_born = time.time() - (400 * 24 * 3600)  # ~13 months ago


def fake_open_factory(contents_by_path):
    real_open = open

    def _fake_open(path, *args, **kwargs):
        if path in contents_by_path:
            return mock.mock_open(read_data=contents_by_path[path])(path, *args, **kwargs)
        return real_open(path, *args, **kwargs)

    return _fake_open


with mock.patch("os.path.exists", side_effect=lambda p: p == mod.BRAIN_PATH), \
     mock.patch("builtins.open", side_effect=fake_open_factory(
         {mod.BRAIN_PATH: json.dumps({"born_at": sample_born})}
     )):
    p2 = make_plugin()
    p2.load_data(mod.BRAIN_PATH)

check("load_data reads a real born_at from brain.json", p2.born_at == sample_born)

# --- load_data: brain.json exists but has no born_at -> self-heals via
#     the plugin's own fallback file -----------------------------------------

written = {}


def fake_open_no_born_at(path, *args, **kwargs):
    if path == mod.BRAIN_PATH:
        return mock.mock_open(read_data=json.dumps({"some_other_key": 1}))(path, *args, **kwargs)
    if path == mod.FALLBACK_PATH:
        m = mock.mock_open()
        handle = m(path, *args, **kwargs)
        real_write = handle.write

        def capture_write(data):
            written["data"] = written.get("data", "") + data
            return real_write(data)

        handle.write = capture_write
        return handle
    raise FileNotFoundError(path)


with mock.patch("os.path.exists", side_effect=lambda p: p == mod.BRAIN_PATH), \
     mock.patch("builtins.open", side_effect=fake_open_no_born_at):
    p3 = make_plugin()
    before = time.time()
    p3.load_data(mod.BRAIN_PATH)
    after = time.time()

check(
    "brain.json without born_at falls back to a freshly-created timestamp",
    p3.born_at is not None and before <= p3.born_at <= after,
)
check(
    "the fallback timestamp was actually written to the plugin's own fallback file",
    "born_at" in written.get("data", ""),
)

# --- load_data: fallback file already exists -> its timestamp is reused,
#     not clobbered with a new "now" ----------------------------------------

existing_fallback_born_at = time.time() - (30 * 24 * 3600)


def fake_open_existing_fallback(path, *args, **kwargs):
    if path == mod.BRAIN_PATH:
        return mock.mock_open(read_data=json.dumps({"no_born_at_here": True}))(path, *args, **kwargs)
    if path == mod.FALLBACK_PATH:
        return mock.mock_open(read_data=json.dumps({"born_at": existing_fallback_born_at}))(path, *args, **kwargs)
    raise FileNotFoundError(path)


with mock.patch("os.path.exists", return_value=True), \
     mock.patch("builtins.open", side_effect=fake_open_existing_fallback):
    p4 = make_plugin()
    p4.load_data(mod.BRAIN_PATH)

check(
    "an existing fallback file's timestamp is reused rather than overwritten",
    p4.born_at == existing_fallback_born_at,
)

# --- load_data: brain.json genuinely gets a real born_at later -> it wins
#     over any existing fallback file ----------------------------------------

real_born_at_now = time.time() - (500 * 24 * 3600)


def fake_open_real_wins(path, *args, **kwargs):
    if path == mod.BRAIN_PATH:
        return mock.mock_open(read_data=json.dumps({"born_at": real_born_at_now}))(path, *args, **kwargs)
    if path == mod.FALLBACK_PATH:
        return mock.mock_open(read_data=json.dumps({"born_at": existing_fallback_born_at}))(path, *args, **kwargs)
    raise FileNotFoundError(path)


with mock.patch("os.path.exists", return_value=True), \
     mock.patch("builtins.open", side_effect=fake_open_real_wins):
    p5 = make_plugin()
    p5.load_data(mod.BRAIN_PATH)

check(
    "a real born_at in brain.json takes priority over an existing fallback file",
    p5.born_at == real_born_at_now,
)

# --- load_data: brain.json missing entirely -> falls back cleanly, no crash -

with mock.patch("os.path.exists", return_value=False), \
     mock.patch("builtins.open", mock.mock_open()):
    p6 = make_plugin()
    try:
        p6.load_data(mod.BRAIN_PATH)
        ok6 = True
    except Exception:
        ok6 = False

check("a missing brain.json is handled without raising", ok6)
check("a missing brain.json still results in a usable born_at via fallback", p6.born_at is not None)

# --- load_data: brain.json exists but is corrupt JSON -> handled, no crash --

with mock.patch("os.path.exists", side_effect=lambda p: p == mod.BRAIN_PATH), \
     mock.patch("builtins.open", mock.mock_open(read_data="{not valid json")):
    p7 = make_plugin()
    try:
        p7.load_data(mod.BRAIN_PATH)
        ok7 = True
    except Exception:
        ok7 = False
check("a corrupt brain.json is handled without raising", ok7)

# --- Age formatting -----------------------------------------------------

check("format_age: 0 years, 0 months, 0 days -> empty string", mod.BirthdayNG.format_age((0, 0, 0)) == "")
check("format_age: 1 year exactly -> singular 'Yr'", mod.BirthdayNG.format_age((1, 0, 0)) == "1Yr")
check("format_age: 2 years -> plural 'Yrs'", mod.BirthdayNG.format_age((2, 0, 0)) == "2Yrs")
check("format_age: under a year with days -> 'N days'", mod.BirthdayNG.format_age((0, 2, 5)) == "2m 5 days")
check("format_age: a year or more with days -> 'Nd'", mod.BirthdayNG.format_age((1, 2, 5)) == "1Yr 2m 5d")

# --- is_birthday_today --------------------------------------------------

p8 = make_plugin()
today = datetime.datetime.now()
p8.born_at = today.replace(year=today.year - 5).timestamp()
check("is_birthday_today is True when month+day match today", p8.is_birthday_today() is True)

p9 = make_plugin()
not_today = today + datetime.timedelta(days=10)
p9.born_at = not_today.replace(year=not_today.year - 5).timestamp()
check("is_birthday_today is False for a non-matching date", p9.is_birthday_today() is False)

p10 = make_plugin()
p10.born_at = None
check("is_birthday_today is False when born_at is None", p10.is_birthday_today() is False)

# --- birthday_message substitution on the matching date --------------------

p11 = make_plugin(show_age=True, birthday_message="Happy {age} Birthday!!")
p11.born_at = today.replace(year=today.year - 3).timestamp()
fake_ui2 = mock.Mock()
p11.on_ui_update(fake_ui2)
set_value = fake_ui2.set.call_args[0][1]
check(
    "on_ui_update shows the configured birthday_message (with {age} substituted) on the matching date",
    set_value.startswith("Happy") and "Birthday!!" in set_value and "{age}" not in set_value,
)

# --- show_birthday branch ---------------------------------------------------

p12 = make_plugin(show_age=False, show_birthday=True)
p12.born_at = datetime.datetime(2020, 6, 15).timestamp()
fake_ui3 = mock.Mock()
p12.on_ui_update(fake_ui3)
check(
    "on_ui_update (show_birthday) formats the birth date",
    fake_ui3.set.call_args[0] == ("Birthday", "Jun 15 '20"),
)


# --- ADDED: on-screen position options are honored --------------------------
class _PosUI:
    def __init__(self):
        self.elements = {}

    def add_element(self, name, widget):
        self.elements[name] = widget

    class _Lock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    @property
    def _lock(self):
        return _PosUI._Lock()


# Preferred convention-named position options land on the Age element.
_bp = make_plugin(position_x=20, position_y=30)
_bu = _PosUI()
_bp.on_ui_setup(_bu)
check(
    "birthday honors configured position_x/position_y on the Age element",
    _bu.elements["Age"].xy[:2] == (20, 30),
)

# Legacy age_x_coord / age_y_coord still work when position_x/y are unset.
_bpl = make_plugin(age_x_coord=7, age_y_coord=8)
_bul = _PosUI()
_bpl.on_ui_setup(_bul)
check(
    "birthday still honors legacy age_x_coord/age_y_coord when position_x/y unset",
    _bul.elements["Age"].xy[:2] == (7, 8),
)

# Default position is top-left (0, 0), matching the original.
_bpd = make_plugin()
_bud = _PosUI()
_bpd.on_ui_setup(_bud)
check(
    "birthday default position is (0, 0)",
    _bud.elements["Age"].xy[:2] == (0, 0),
)

# show_birthday branch places the Birthday element at the same resolved spot.
_bpb = make_plugin(show_age=False, show_birthday=True, position_x=11, position_y=12)
_bub = _PosUI()
_bpb.on_ui_setup(_bub)
check(
    "birthday honors position on the Birthday element too",
    _bub.elements["Birthday"].xy[:2] == (11, 12),
)


# --- ADDED: on_webhook returns a real body (never None -> Flask 500) --------
_bw = make_plugin(show_age=True)
_bw.born_at = time.time() - (400 * 24 * 3600)
_wh_body = _bw.on_webhook("/", None)
check("on_webhook returns a non-None body (avoids Flask 500 on index)", _wh_body is not None)
check("on_webhook body is HTML text", isinstance(_wh_body, str) and "<html" in _wh_body.lower())

_bw2 = make_plugin()
_bw2.born_at = None
check(
    "on_webhook returns a body even when born_at is unknown",
    _bw2.on_webhook("/", None) is not None,
)


print(f"\n{len(failures)} failure(s) out of test run")
if failures:
    sys.exit(1)
