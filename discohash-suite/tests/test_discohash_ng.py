import sys
import os
import json
import tempfile
import shutil
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "mock_pwnagotchi"))
sys.path.insert(0, os.path.join(HERE, "..", "pi-plugin"))

import discohash_ng  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


tmpdir = tempfile.mkdtemp()
handshake_dir = os.path.join(tmpdir, "handshakes")
os.makedirs(handshake_dir)
state_file = os.path.join(tmpdir, "state.json")

plugin = discohash_ng.DiscoHashNG()
plugin.options = {
    "webhook_url": "https://discord.example/webhook",
    "handshake_dir": handshake_dir,
    "state_file": state_file,
    "retry_attempts": 3,
    "retry_delay": 0,  # no real sleeping in tests
}

# --- Test 1: state load/save round trip ---
plugin._load_state()
check("fresh state loads empty", plugin.posted == set())
plugin.posted.add("foo.pcapng")
plugin._save_state()
plugin2 = discohash_ng.DiscoHashNG()
plugin2.options = plugin.options
plugin2._load_state()
check("state persists across instances", plugin2.posted == {"foo.pcapng"})

# --- Test 2: .pcap files are ignored entirely (the original bug, now fixed) ---
bad_file = os.path.join(handshake_dir, "network.pcap")
open(bad_file, "w").close()
with mock.patch.object(discohash_ng, "subprocess") as msub:
    plugin._process_one(bad_file)
    check(".pcap file never triggers hcxpcapngtool", not msub.run.called)

# --- Test 3: correct .pcapng handling, hash-write failure leaves it unposted ---
good_file = os.path.join(handshake_dir, "network.pcapng")
open(good_file, "w").close()
with mock.patch.object(discohash_ng, "subprocess") as msub:
    msub.run.return_value = mock.Mock()
    # simulate hcxpcapngtool "succeeding" but producing no .22000 (not enough packets)
    plugin._process_one(good_file)
    check(
        "hash-write failure (no .22000 produced) leaves file unposted",
        "network.pcapng" not in plugin.posted,
    )
    check("hcxpcapngtool was actually invoked for a .pcapng file", msub.run.called)

# --- Test 4: full happy path - hash written, GPS present, Discord post succeeds first try ---
hash_path = good_file[: -len(".pcapng")] + ".22000"
with open(hash_path, "w") as f:
    f.write("WPA*02*aabbcc...*deadbeef\n")
gps_path = good_file[: -len(".pcapng")] + ".gps.json"
with open(gps_path, "w") as f:
    json.dump({"Latitude": 42.1, "Longitude": -71.2}, f)


class FakeResp:
    status_code = 204
    text = ""


sleep_calls = []
with mock.patch.object(discohash_ng, "subprocess") as msub, \
     mock.patch.object(discohash_ng, "requests") as mreq, \
     mock.patch.object(discohash_ng.time, "sleep", side_effect=lambda s: sleep_calls.append(s)):
    msub.run.return_value = mock.Mock(stdout=b"analysis output")
    mreq.post.return_value = FakeResp()
    plugin._process_one(good_file)

    check("successful post marks file as posted", "network.pcapng" in plugin.posted)
    check("successful first attempt never sleeps/retries", sleep_calls == [])
    posted_payload = mreq.post.call_args.kwargs.get("json") or mreq.post.call_args[1].get("json")
    fields = posted_payload["embeds"][0]["fields"]
    field_names = [f["name"] for f in fields]
    check("GPS location field included when coords present", "Location" in field_names)
    check("webhook posted exactly once on success", mreq.post.call_count == 1)

# --- Test 5: already-posted file is skipped entirely (no subprocess/requests calls) ---
with mock.patch.object(discohash_ng, "subprocess") as msub, \
     mock.patch.object(discohash_ng, "requests") as mreq:
    plugin._process_one(good_file)
    check("already-posted file triggers no work at all", not msub.run.called and not mreq.post.called)

# --- Test 6: retry-on-failure - fails twice, succeeds on 3rd attempt, sleeps exactly twice ---
plugin.posted.discard("network.pcapng")
attempt_count = {"n": 0}


class FlakyResp:
    def __init__(self, ok):
        self.status_code = 204 if ok else 500
        self.text = "" if ok else "server error"


def flaky_post(*args, **kwargs):
    attempt_count["n"] += 1
    return FlakyResp(attempt_count["n"] >= 3)


sleep_calls2 = []
with mock.patch.object(discohash_ng, "subprocess") as msub, \
     mock.patch.object(discohash_ng, "requests") as mreq, \
     mock.patch.object(discohash_ng.time, "sleep", side_effect=lambda s: sleep_calls2.append(s)):
    msub.run.return_value = mock.Mock(stdout=b"analysis output")
    mreq.post.side_effect = flaky_post
    plugin._process_one(good_file)
    check("eventually-successful post (3rd try) still marks as posted", "network.pcapng" in plugin.posted)
    check("retried exactly twice before success (3 total attempts)", attempt_count["n"] == 3)
    check("slept exactly twice (once per failure, never after success)", len(sleep_calls2) == 2)

# --- Test 7: permanent failure never marks as posted, respects retry_attempts cap ---
plugin.posted.discard("network.pcapng")
attempt_count2 = {"n": 0}


def always_fail_post(*args, **kwargs):
    attempt_count2["n"] += 1
    return FlakyResp(False)


sleep_calls3 = []
with mock.patch.object(discohash_ng, "subprocess") as msub, \
     mock.patch.object(discohash_ng, "requests") as mreq, \
     mock.patch.object(discohash_ng.time, "sleep", side_effect=lambda s: sleep_calls3.append(s)):
    msub.run.return_value = mock.Mock(stdout=b"analysis output")
    mreq.post.side_effect = always_fail_post
    plugin._process_one(good_file)
    check("permanent failure never marks file as posted", "network.pcapng" not in plugin.posted)
    check("stopped exactly at retry_attempts=3 (no infinite loop)", attempt_count2["n"] == 3)

# --- Test 8: GPS fallback to .geo.json when .gps.json absent ---
os.remove(gps_path)
geo_path = good_file[: -len(".pcapng")] + ".geo.json"
with open(geo_path, "w") as f:
    json.dump({"location": {"lat": 10.0, "lng": 20.0}}, f)
lat, lon, url = plugin._get_coords(good_file[: -len(".pcapng")])
check("geo.json fallback used when gps.json missing", (lat, lon) == (10.0, 20.0) and url is not None)

# --- Test 9: no GPS files at all -> N/A, no crash ---
os.remove(geo_path)
lat, lon, url = plugin._get_coords(good_file[: -len(".pcapng")])
check("no GPS files -> graceful N/A, no exception", (lat, lon, url) == ("N/A", "N/A", None))

# --- Test 10: backlog scan on a wrong/missing handshake_dir doesn't crash ---
plugin.options["handshake_dir"] = "/does/not/exist"
try:
    plugin._scan_backlog()
    check("missing handshake_dir handled without crashing", True)
except Exception as e:
    check(f"missing handshake_dir handled without crashing (raised {e})", False)

shutil.rmtree(tmpdir)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("ALL TESTS PASSED")
