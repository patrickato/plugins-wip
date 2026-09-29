import sys
import os
import json
import threading
import time
import tempfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")

import pwnagotchi.plugins  # noqa: E402
import discord_ng as mod  # noqa: E402
from requests import RequestException  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


TMPDIR = tempfile.mkdtemp(prefix="discord_ng_test_")


def make_plugin(**opts):
    p = mod.DiscordNG()
    opts.setdefault("webhook_url", "https://discord.com/api/webhooks/x/y")
    opts.setdefault("cache_file", os.path.join(TMPDIR, f"cache_{time.time_ns()}.json"))
    p.options = dict(opts)
    p.on_loaded()
    return p


def drain(plugin, timeout=5.0):
    joined = threading.Event()

    def _join():
        plugin._event_queue.join()
        joined.set()

    t = threading.Thread(target=_join, daemon=True)
    t.start()
    joined.wait(timeout=timeout)


class FakeResponse:
    def __init__(self, status_code=204, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json


class FakeAgent:
    def __init__(self, name="testchi", last_session=None):
        self._name = name
        self.last_session = last_session

    def config(self):
        return {"main": {"name": self._name}}


class FakeLastSession:
    def __init__(self, handshakes=5, epochs=10, duration="0:10:00", deauthed=3):
        self.handshakes = handshakes
        self.epochs = epochs
        self.duration = duration
        self.deauthed = deauthed


# --- Registration ---------------------------------------------------------

check(
    "DiscordNG registers as a real pwnagotchi.plugins.Plugin subclass",
    issubclass(mod.DiscordNG, pwnagotchi.plugins.Plugin),
)

# --- on_loaded with no webhook_url configured -----------------------------

p = mod.DiscordNG()
p.options = {"cache_file": os.path.join(TMPDIR, "no_webhook.json")}
p.on_loaded()
check("on_loaded with no webhook_url does not start a worker thread crash", True)
p.on_unload(mock.Mock())

# --- on_loaded starts the worker thread -----------------------------------

p2 = make_plugin()
check("worker thread started", p2._worker_thread is not None and p2._worker_thread.is_alive())
p2.on_unload(mock.Mock())

# --- Handshake sends a Discord payload with location omitted (no wigle key) -

p3 = make_plugin(disable_wigle_lookup=True, attachment_mode="json_only")
sent = []


def fake_post(url, **kwargs):
    sent.append(kwargs)
    return FakeResponse(status_code=204)


with mock.patch.object(p3.http_session, "post", side_effect=fake_post):
    ap = {"mac": "aa:bb:cc:dd:ee:ff", "hostname": "TestAP", "channel": 6}
    client = {"mac": "11:22:33:44:55:66"}
    p3.on_handshake(FakeAgent(), "/tmp/handshake.pcap", ap, client)
    drain(p3)

check("handshake with disable_wigle_lookup sends exactly one payload", len(sent) == 1)
payload = sent[0].get("json", {})
check(
    "handshake payload embed mentions the AP name",
    "TestAP" in json.dumps(payload),
)
check(
    "handshake payload notes location lookups are disabled",
    "disabled" in json.dumps(payload).lower(),
)
p3.on_unload(mock.Mock())

# --- Duplicate handshake is deduped ----------------------------------------

p4 = make_plugin(disable_wigle_lookup=True)
sent4 = []
with mock.patch.object(p4.http_session, "post", side_effect=lambda url, **kw: (sent4.append(kw), FakeResponse())[1]):
    ap = {"mac": "aa:bb:cc:dd:ee:ff", "hostname": "DupAP", "channel": 1}
    client = {"mac": "11:22:33:44:55:66"}
    p4.on_handshake(FakeAgent(), "/tmp/dup.pcap", ap, client)
    p4.on_handshake(FakeAgent(), "/tmp/dup.pcap", ap, client)  # exact duplicate
    drain(p4)
check("duplicate handshake is deduped (only 1 payload sent for 2 identical events)", len(sent4) == 1)
p4.on_unload(mock.Mock())

# --- attachment_mode="file" attaches the real handshake file ----------------

real_pcap = os.path.join(TMPDIR, "real.pcap")
with open(real_pcap, "wb") as f:
    f.write(b"fake pcap bytes")

p5 = make_plugin(disable_wigle_lookup=True, attachment_mode="file")
sent5 = []
with mock.patch.object(p5.http_session, "post", side_effect=lambda url, **kw: (sent5.append(kw), FakeResponse())[1]):
    ap = {"mac": "aa:bb:cc:dd:ee:ff", "hostname": "FileAP", "channel": 1}
    client = {"mac": "11:22:33:44:55:66"}
    p5.on_handshake(FakeAgent(), real_pcap, ap, client)
    drain(p5)
check("attachment_mode='file' sends multipart 'files' kwarg", "files" in sent5[0])
p5.on_unload(mock.Mock())

# --- attachment_mode="json_only" never attaches even if the file exists -----

p6 = make_plugin(disable_wigle_lookup=True, attachment_mode="json_only")
sent6 = []
with mock.patch.object(p6.http_session, "post", side_effect=lambda url, **kw: (sent6.append(kw), FakeResponse())[1]):
    ap = {"mac": "aa:bb:cc:dd:ee:ff", "hostname": "JsonAP", "channel": 1}
    client = {"mac": "11:22:33:44:55:66"}
    p6.on_handshake(FakeAgent(), real_pcap, ap, client)
    drain(p6)
check("attachment_mode='json_only' never sends 'files' even if the file exists", "files" not in sent6[0])
p6.on_unload(mock.Mock())

# --- WiGLE cache round-trips to disk ----------------------------------------

cache_path = os.path.join(TMPDIR, "wigle_roundtrip.json")
p7 = mod.DiscordNG()
p7.options = {"webhook_url": "https://x", "wigle_api_key": "key123", "cache_file": cache_path}
p7.on_loaded()
p7.wigle_cache["aa:bb:cc:dd:ee:ff"] = mod.CachedLocation(lat="1.23", lon="4.56", timestamp=time.time())
p7._cache_dirty = True
p7._save_wigle_cache()
check("cache file was written to disk", os.path.exists(cache_path))

p7b = mod.DiscordNG()
p7b.options = {"webhook_url": "https://x", "wigle_api_key": "key123", "cache_file": cache_path}
p7b.on_loaded()
check("cache round-trips: loaded entry matches what was saved", "aa:bb:cc:dd:ee:ff" in p7b.wigle_cache)
check("loaded cache entry has correct lat/lon", p7b.wigle_cache["aa:bb:cc:dd:ee:ff"].lat == "1.23")
p7.on_unload(mock.Mock())
p7b.on_unload(mock.Mock())

# --- Expired cache entries are skipped on load ------------------------------

p8 = mod.DiscordNG()
p8.options = {"webhook_url": "https://x", "cache_file": os.path.join(TMPDIR, "expired.json")}
p8.on_loaded()
old_ts = time.time() - (31 * 86400)
with open(p8._cache_file, "w") as f:
    json.dump({"expired:mac": {"lat": "9", "lon": "9", "timestamp": old_ts}}, f)
p8._load_wigle_cache()
check("expired (31-day-old) cache entries are not loaded", "expired:mac" not in p8.wigle_cache)
p8.on_unload(mock.Mock())

# --- disable_wigle_lookup skips the API entirely even with a key set -------

p9 = make_plugin(wigle_api_key="realkey", disable_wigle_lookup=True)
with mock.patch.object(p9.http_session, "get", side_effect=AssertionError("should never call WiGLE")):
    loc = p9._get_location_from_wigle("aa:bb:cc:dd:ee:ff")
check("disable_wigle_lookup=True skips the WiGLE API call entirely", loc is None)
p9.on_unload(mock.Mock())

# --- Previous-session report uses the real 'deauthed' attribute (fixed) ----

p10 = make_plugin(include_session_stats=True)
last = FakeLastSession(handshakes=7, epochs=20, duration="1:00:00", deauthed=42)
agent10 = FakeAgent(last_session=last)
sent10 = []
with mock.patch.object(p10.http_session, "post", side_effect=lambda url, **kw: (sent10.append(kw), FakeResponse())[1]):
    p10.on_ready(agent10)
    drain(p10)
# on_ready queues 2 notifications: online + previous session report
all_text = "".join(json.dumps(s.get("json", {})) for s in sent10)
check("previous-session report includes exactly the real deauthed count (42)", "42" in all_text)
check("two notifications were sent for on_ready (online + session report)", len(sent10) == 2)
p10.on_unload(mock.Mock())

# --- include_session_stats=False skips the second report --------------------

p11 = make_plugin(include_session_stats=False)
sent11 = []
with mock.patch.object(p11.http_session, "post", side_effect=lambda url, **kw: (sent11.append(kw), FakeResponse())[1]):
    p11.on_ready(FakeAgent(last_session=FakeLastSession()))
    drain(p11)
check("include_session_stats=False sends only the online notification", len(sent11) == 1)
p11.on_unload(mock.Mock())

# --- No duration -> no session report even with include_session_stats=True -

p12 = make_plugin(include_session_stats=True)
sent12 = []
with mock.patch.object(p12.http_session, "post", side_effect=lambda url, **kw: (sent12.append(kw), FakeResponse())[1]):
    p12.on_ready(FakeAgent(last_session=FakeLastSession(duration="0:00:00")))
    drain(p12)
check("a zero-duration previous session produces no session report", len(sent12) == 1)
p12.on_unload(mock.Mock())

# --- HTTP error doesn't crash the worker ------------------------------------

p13 = make_plugin(disable_wigle_lookup=True)
with mock.patch.object(p13.http_session, "post", side_effect=RequestException("network down")):
    ap = {"mac": "aa:bb:cc:dd:ee:ff", "hostname": "ErrAP", "channel": 1}
    p13.on_handshake(FakeAgent(), "/tmp/err.pcap", ap, {"mac": "11:22:33:44:55:66"})
    drain(p13)
check("worker survives an HTTP request exception", p13._worker_thread.is_alive())
p13.on_unload(mock.Mock())

# --- Rate limit (429) response is handled without crashing ------------------

p14 = make_plugin(disable_wigle_lookup=True)
with mock.patch.object(
    p14.http_session, "post", return_value=FakeResponse(status_code=429, json_data={"retry_after": 5})
):
    ap = {"mac": "aa:bb:cc:dd:ee:ff", "hostname": "RateAP", "channel": 1}
    p14.on_handshake(FakeAgent(), "/tmp/rate.pcap", ap, {"mac": "11:22:33:44:55:66"})
    drain(p14)
check("a 429 rate-limit response is handled without crashing", p14._worker_thread.is_alive())
p14.on_unload(mock.Mock())

# --- Clean shutdown: worker thread stops, cleanup is idempotent -----------

p15 = make_plugin()
p15.on_unload(mock.Mock())
check("worker thread stopped after on_unload", not p15._worker_thread.is_alive())
try:
    p15._on_exit_cleanup()  # idempotent - should be a no-op, no crash
    ok = True
except Exception:
    ok = False
check("_on_exit_cleanup is idempotent (safe to call twice)", ok)

# --- on_unload safe even if on_loaded never ran -----------------------------

p16 = mod.DiscordNG()
try:
    p16.on_unload(mock.Mock())
    ok = True
except Exception:
    ok = False
check("on_unload doesn't crash if on_loaded never ran", ok)

# --- webhook handler doesn't crash ------------------------------------------

p17 = make_plugin()
try:
    p17.on_webhook("/", mock.Mock())
    ok = True
except Exception:
    ok = False
check("on_webhook doesn't crash", ok)
p17.on_unload(mock.Mock())


print(f"\n{len(failures)} failure(s) out of test run")
if failures:
    sys.exit(1)
