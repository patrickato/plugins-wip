import sys
import os
import json
import tempfile
import time
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")

import pwnagotchi.plugins  # noqa: E402
import spam_peers_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


TMPDIR = tempfile.mkdtemp(prefix="spam_peers_ng_test_")


def make_plugin(**opts):
    p = mod.SpamPeersNG()
    opts.setdefault("state_file", os.path.join(TMPDIR, f"state_{time.time_ns()}.json"))
    p.options = dict(opts)
    return p


class FakePeer:
    def __init__(self, adv):
        self.adv = adv


# --- Registration -----------------------------------------------------------

check(
    "SpamPeersNG registers as a real pwnagotchi.plugins.Plugin subclass",
    issubclass(mod.SpamPeersNG, pwnagotchi.plugins.Plugin),
)

# --- __init__ never touches the filesystem (the original bug: unguarded
#     os.listdir("/root/peers") in __init__ crashed plugin *loading*
#     entirely on a fresh install where that dir doesn't exist) -------------

with mock.patch("os.path.isdir", side_effect=AssertionError("isdir called during __init__")), \
     mock.patch("os.listdir", side_effect=AssertionError("listdir called during __init__")):
    try:
        p_init = mod.SpamPeersNG()
        ok = True
    except Exception:
        ok = False
check("__init__ does no filesystem I/O (deferred to on_loaded)", ok)

# --- on_loaded safely skips a missing /root/peers directory -----------------

with mock.patch("os.path.isdir", return_value=False):
    p1 = make_plugin()
    try:
        p1.on_loaded()
        ok = True
    except FileNotFoundError:
        ok = False
check("on_loaded doesn't crash when /root/peers doesn't exist", ok)
check("greeted starts empty when /root/peers doesn't exist and no state file", p1.greeted == {})

# --- on_loaded preloads from /root/peers, tolerating a malformed entry -----

peers_dir = os.path.join(TMPDIR, "root_peers")
os.makedirs(peers_dir, exist_ok=True)
with open(os.path.join(peers_dir, "good-peer.json"), "w") as f:
    json.dump({"advertisement": {"name": "GoodPeer"}}, f)
with open(os.path.join(peers_dir, "bad-peer.json"), "w") as f:
    f.write("not valid json{{{")

# Point the loader at a real temp dir by monkeypatching the hardcoded
# /root/peers path via os.path.isdir/os.listdir + real file reads.
real_isdir = os.path.isdir
real_listdir = os.listdir


def fake_isdir(path):
    if path == "/root/peers":
        return True
    return real_isdir(path)


def fake_listdir(path):
    if path == "/root/peers":
        return real_listdir(peers_dir)
    return real_listdir(path)


import builtins as _builtins_mod
_builtin_open = _builtins_mod.open


def fake_open(path, *args, **kwargs):
    if str(path).startswith("/root/peers/"):
        path = os.path.join(peers_dir, os.path.basename(str(path)))
    return _builtin_open(path, *args, **kwargs)

with mock.patch("os.path.isdir", side_effect=fake_isdir), \
     mock.patch("os.listdir", side_effect=fake_listdir), \
     mock.patch("builtins.open", side_effect=fake_open):
    p2 = make_plugin()
    try:
        p2.on_loaded()
        ok = True
    except Exception:
        ok = False

check("on_loaded doesn't crash on a malformed peer file in /root/peers", ok)
check("on_loaded preloads the good peer as already-greeted", p2.greeted.get("good-peer") is True)
check("the malformed peer file is skipped, not crashing the whole load", "bad-peer" not in p2.greeted)

# --- on_peer_detected with a normal identity: greets and persists ----------

p3 = make_plugin(min_delay_seconds=0, max_delay_seconds=0)
with mock.patch("os.path.isdir", return_value=False):
    p3.on_loaded()

with mock.patch("pwnagotchi.grid.send_message") as mock_send:
    p3.on_peer_detected(mock.Mock(), FakePeer({"identity": "peer-123"}))
    time.sleep(0.3)  # let the (0-delay) timer thread fire

check("on_peer_detected sends a greeting to a new peer", mock_send.called)
check("greeted peer is recorded with a timestamp", isinstance(p3.greeted.get("peer-123"), float))
check("greeted state was persisted to disk", os.path.exists(p3._state_file()))
with open(p3._state_file()) as f:
    saved = json.load(f)
check("persisted state contains the greeted peer", "peer-123" in saved)

# --- on_peer_detected with peer.adv == {} (malformed advertisement) --------
# The real Peer class documents self.adv can be {} on a malformed advert -
# the original's raw peer.adv['identity'] would KeyError here.

p4 = make_plugin()
with mock.patch("os.path.isdir", return_value=False):
    p4.on_loaded()
with mock.patch("pwnagotchi.grid.send_message") as mock_send4:
    try:
        p4.on_peer_detected(mock.Mock(), FakePeer({}))
        ok = True
    except KeyError:
        ok = False
check("on_peer_detected doesn't KeyError on an empty (malformed) advertisement", ok)
check("no greeting is sent for a peer with no identity", not mock_send4.called)

# --- Cooldown: a peer within the window is not re-greeted -------------------

p5 = make_plugin(regreet_after_hours=168, min_delay_seconds=0, max_delay_seconds=0)
with mock.patch("os.path.isdir", return_value=False):
    p5.on_loaded()
p5.greeted["peer-cooldown"] = time.time()  # just greeted

with mock.patch("pwnagotchi.grid.send_message") as mock_send5:
    p5.on_peer_detected(mock.Mock(), FakePeer({"identity": "peer-cooldown"}))
    time.sleep(0.2)
check("a peer greeted moments ago is not re-greeted within the cooldown", not mock_send5.called)

# --- Cooldown: a peer past the window IS re-greeted --------------------------

p6 = make_plugin(regreet_after_hours=1, min_delay_seconds=0, max_delay_seconds=0)  # 1 hour cooldown
with mock.patch("os.path.isdir", return_value=False):
    p6.on_loaded()
p6.greeted["peer-stale"] = time.time() - (2 * 3600)  # greeted 2 hours ago

with mock.patch("pwnagotchi.grid.send_message") as mock_send6:
    p6.on_peer_detected(mock.Mock(), FakePeer({"identity": "peer-stale"}))
    time.sleep(0.2)
check("a peer greeted past the cooldown window is re-greeted", mock_send6.called)

# --- regreet_after_hours = 0 means never re-greet ---------------------------

p7 = make_plugin(regreet_after_hours=0, min_delay_seconds=0, max_delay_seconds=0)
with mock.patch("os.path.isdir", return_value=False):
    p7.on_loaded()
p7.greeted["peer-forever"] = time.time() - (365 * 24 * 3600)  # greeted a year ago

with mock.patch("pwnagotchi.grid.send_message") as mock_send7:
    p7.on_peer_detected(mock.Mock(), FakePeer({"identity": "peer-forever"}))
    time.sleep(0.2)
check("regreet_after_hours=0 never re-greets, even a year later", not mock_send7.called)

# --- State round-trips through a fresh plugin instance -----------------------

state_path = p3._state_file()
p8 = mod.SpamPeersNG()
p8.options = {"state_file": state_path}
with mock.patch("os.path.isdir", return_value=False):
    p8.on_loaded()
check("a fresh plugin instance loads the same greeted state from disk", "peer-123" in p8.greeted)

# --- known_peers from config are merged in as pre-greeted -------------------

p9 = make_plugin(known_peers=["my-other-unit"])
with mock.patch("os.path.isdir", return_value=False):
    p9.on_loaded()
check("known_peers from config are merged in as already-greeted", "my-other-unit" in p9.greeted)

with mock.patch("pwnagotchi.grid.send_message") as mock_send9:
    p9.on_peer_detected(mock.Mock(), FakePeer({"identity": "my-other-unit"}))
    time.sleep(0.2)
check("a peer listed in known_peers is never greeted", not mock_send9.called)

# --- Jitter delay falls within the configured range -------------------------

p10 = make_plugin(min_delay_seconds=2, max_delay_seconds=5)
with mock.patch("os.path.isdir", return_value=False):
    p10.on_loaded()

captured_delay = {}
real_timer = mod.threading.Timer


def capturing_timer(delay, func, args=None, kwargs=None):
    captured_delay["delay"] = delay
    return real_timer(0, func, args=args, kwargs=kwargs)


with mock.patch("spam_peers_ng.threading.Timer", side_effect=capturing_timer), \
     mock.patch("pwnagotchi.grid.send_message"):
    p10.on_peer_detected(mock.Mock(), FakePeer({"identity": "peer-jitter"}))
    time.sleep(0.2)

check(
    "jitter delay falls within min_delay_seconds/max_delay_seconds",
    2 <= captured_delay.get("delay", -1) <= 5,
)

# --- messages config is honored ---------------------------------------------

p11 = make_plugin(messages=["only message"], min_delay_seconds=0, max_delay_seconds=0)
with mock.patch("os.path.isdir", return_value=False):
    p11.on_loaded()
check("configured messages list is used", p11.messages == ["only message"])

with mock.patch("pwnagotchi.grid.send_message") as mock_send11:
    p11.on_peer_detected(mock.Mock(), FakePeer({"identity": "peer-msg"}))
    time.sleep(0.2)

check("the configured message is what gets sent", mock_send11.call_args[0][1] == "only message")


print(f"\n{len(failures)} failure(s) out of test run")
if failures:
    sys.exit(1)
