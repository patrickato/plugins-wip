"""
test_netmanager_ng.py - sandbox tests for netmanager-suite (the backbone).

Covers (no hardware, no pwnagotchi needed - the plugin shims pwnagotchi.plugins):
  * token validation + bind-scope resolution
  * entry validation (name required, kind allowlist, field cleaning)
  * store CRUD: add / update / delete / select, incl. the select cascade on delete
  * search / filter (name, notes, fields, kind) for the 50+ list
  * normalize_store (coerces garbage; drops bad entries; fixes a dangling selected)
  * fire_stub dispatch per kind
  * a LIVE HTTP pass (auth 401, page serves, add/state/select/fire/delete,
    0600 store) when flask is importable

Run from this folder:  python3 tests/test_netmanager_ng.py
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import netmanager_ng as m  # noqa: E402

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


def test_token_and_bind():
    check("short token refused", m.validate_token("short")[0] is False)
    check("placeholder refused", m.validate_token("changeme")[0] is False)
    check("good token ok", m.validate_token("a-long-enough-token-123")[0] is True)
    check("localhost bind", m.resolve_bind_plan("localhost", 8085)[0] == "127.0.0.1")
    check("lan bind", m.resolve_bind_plan("lan", 8085)[0] == "0.0.0.0")
    check("tailscale w/o iface refuses",
          m.resolve_bind_plan("tailscale", 8085, detect_tailscale=lambda: None)[2] is False)
    check("auto -> localhost w/o tailscale",
          m.resolve_bind_plan("auto", 8085, detect_tailscale=lambda: None)[0] == "127.0.0.1")


def test_validate_entry():
    e, err = m.validate_entry({"name": "x", "kind": "wifi_join", "fields": {"ssid": "x", "": "drop"}})
    check("valid entry ok", err is None and e["kind"] == "wifi_join")
    check("blank field key dropped", "" not in e["fields"])
    check("name required", m.validate_entry({"name": "", "kind": "fleet"})[1] == "name is required")
    check("kind allowlisted", "kind must be" in (m.validate_entry({"name": "x", "kind": "nope"})[1] or ""))
    check("non-dict rejected", m.validate_entry("nope")[1] is not None)


def test_crud_and_cascade():
    s = m.empty_store()
    i1, _ = m.add_network(s, {"name": "Home", "kind": "wifi_join", "fields": {"ssid": "Home"}})
    i2, _ = m.add_network(s, {"name": "lab", "kind": "fleet", "fields": {"url": "http://x:8084", "token": "t"}})
    check("two added", len(s["networks"]) == 2)
    check("ids unique", i1 != i2)
    check("select sets selected", m.select_network(s, i2) is None and s["selected"] == i2)
    check("select unknown errors", m.select_network(s, "ghost") == "no such network")
    check("update ok", m.update_network(s, i1, {"name": "Home2", "kind": "wifi_join", "fields": {}}) is None)
    check("update applied", s["networks"][i1]["name"] == "Home2")
    check("update unknown errors", m.update_network(s, "ghost", {"name": "x", "kind": "fleet"}) == "no such network")
    check("delete clears selected", m.delete_network(s, i2) is None and s["selected"] is None)
    check("delete unknown errors", m.delete_network(s, "ghost") == "no such network")


def test_search():
    s = m.empty_store()
    m.add_network(s, {"name": "Home-AP", "kind": "wifi_join", "fields": {"ssid": "Home-AP"}})
    m.add_network(s, {"name": "lab-pi", "kind": "fleet", "fields": {"url": "http://10.0.0.5:8084"}})
    m.add_network(s, {"name": "NETGEAR77", "kind": "wifi_target", "fields": {"ssid": "NETGEAR77"}, "notes": "garage"})
    check("all listed", len(m.search_networks(s)) == 3)
    check("kind filter", len(m.search_networks(s, kind="fleet")) == 1)
    check("q matches name", m.search_networks(s, q="netgear")[0]["name"] == "NETGEAR77")
    check("q matches notes", len(m.search_networks(s, q="garage")) == 1)
    check("q matches field", len(m.search_networks(s, q="10.0.0.5")) == 1)
    check("q no match", len(m.search_networks(s, q="zzz")) == 0)
    # case-insensitive sort: home-ap < lab-pi < netgear77
    check("sorted by name (case-insensitive)",
          [r["name"] for r in m.search_networks(s)] == ["Home-AP", "lab-pi", "NETGEAR77"])


def test_normalize():
    n = m.normalize_store({"networks": {"a": {"name": "ok", "kind": "fleet", "fields": {}},
                                        "b": {"junk": 1}}, "selected": "a"})
    check("bad entry dropped", len(n["networks"]) == 1)
    check("good selected kept", n["selected"] == "a")
    n2 = m.normalize_store({"networks": {"a": {"name": "ok", "kind": "fleet"}}, "selected": "gone"})
    check("dangling selected cleared", n2["selected"] is None)
    check("garbage -> empty", m.normalize_store("nonsense") == m.empty_store())


def test_fire_stub():
    r = m.fire_stub({"kind": "mystery"})
    check("fire stub unknown kind", r["ok"] is False and r["stub"] is True)


def test_fire_wifi_join():
    e = {"kind": "wifi_join", "name": "Home", "fields": {"ssid": "Home"}}
    r = m.fire_wifi_join(e, current_ssid="Home")
    check("wifi_join on it -> ok", r["ok"] is True and "connected to 'Home'" in r["message"])
    r = m.fire_wifi_join(e, current_ssid="Other")
    check("wifi_join not on it -> not ok", r["ok"] is False and "not currently on 'Home'" in r["message"])
    r = m.fire_wifi_join(e, current_ssid=None)
    check("wifi_join none -> not associated", r["ok"] is False and "not associated" in r["message"])


def test_fire_wifi_target():
    e = {"kind": "wifi_target", "name": "NETGEAR", "fields": {"ssid": "NETGEAR", "bssid": "AA:BB:CC:DD:EE:FF"}}
    # empty allowlist -> REFUSED
    r = m.fire_wifi_target(e, allowlist=[])
    check("wifi_target empty allowlist refused", r["ok"] is False and r["authorized"] is False and "REFUSED" in r["message"])
    # bssid on allowlist (colon-insensitive) -> authorized, but no frames sent
    r = m.fire_wifi_target(e, allowlist=["aabbccddeeff"])
    check("wifi_target bssid authorized (no colons)", r["ok"] is True and r["authorized"] is True and "enforces the gate" in r["message"])
    # ssid on allowlist -> authorized
    r = m.fire_wifi_target(e, allowlist=["netgear"])
    check("wifi_target ssid authorized", r["ok"] is True and r["authorized"] is True)
    # a different target not on allowlist -> refused
    r = m.fire_wifi_target({"kind": "wifi_target", "name": "Other", "fields": {"bssid": "11:22:33:44:55:66"}}, allowlist=["aabbccddeeff"])
    check("wifi_target other refused", r["ok"] is False)
    # target_authorized direct
    check("target_authorized empty -> False", m.target_authorized("AA:BB:CC:DD:EE:FF", "x", []) is False)


def test_import_parsers():
    # handshake filename parsing
    check("hs ssid_bssid", m.parse_handshake_filename("Home_a1b2c3d4e5f6.pcap") == ("Home", "a1:b2:c3:d4:e5:f6"))
    check("hs .pcapng (jayofelony)", m.parse_handshake_filename("akocsis_c899b21b872b.pcapng") == ("akocsis", "c8:99:b2:1b:87:2b"))
    check("hs .pcapng numeric ssid", m.parse_handshake_filename("225101_000a998d5a91.pcapng") == ("225101", "00:0a:99:8d:5a:91"))
    check("hs .22000", m.parse_handshake_filename("ARLO_9c3dcf59ac14.22000") == ("ARLO", "9c:3d:cf:59:ac:14"))
    check("hs .gps.json ignored", m.parse_handshake_filename("ARLO_9c3dcf59ac14.gps.json") is None)
    check("hs ssid with underscores", m.parse_handshake_filename("My_Home_Net_001122334455.pcap") == ("My_Home_Net", "00:11:22:33:44:55"))
    check("hs cracked suffix", m.parse_handshake_filename("X_aabbccddeeff.pcap.cracked") == ("X", "aa:bb:cc:dd:ee:ff"))
    check("hs bssid only", m.parse_handshake_filename("aabbccddeeff.pcap") == ("", "aa:bb:cc:dd:ee:ff"))
    check("hs ssid only", m.parse_handshake_filename("JustName.pcap") == ("JustName", ""))
    check("non-handshake ignored", m.parse_handshake_filename("notes.txt") is None)
    check("empty ignored", m.parse_handshake_filename("") is None)
    # scan + dedup (same bssid twice -> one)
    rows = m.scan_handshakes(["A_001122334455.pcap", "A_001122334455.pcap.cracked", "B_aabbccddeeff.pcap", "readme.md"])
    check("scan dedups by bssid", len(rows) == 2)
    # fleet.json -> rows
    fr = m.fleet_rows({"pi-a": {"url": "http://1.2.3.4:8084", "token": "t", "badhid": {"url": "http://1.2.3.4:8083", "token": "b"}},
                       "pi-b": {"url": "http://5.6.7.8:8084", "token": "u"}, "junk": 5})
    check("fleet_rows count", len(fr) == 2)
    check("fleet_rows badhid carried", any(r["fields"].get("badhid_url") == "http://1.2.3.4:8083" for r in fr))

    # fleetctl wraps agents under an "agents" key - must unwrap, not treat
    # "agents" as a single agent label.
    wrapped = m.fleet_rows({"agents": {"pi-a": {"url": "http://1.2.3.4:8084", "token": "t"},
                                       "pi-b": {"url": "http://5.6.7.8:8084", "token": "u"}}})
    check("fleet_rows unwraps agents key", len(wrapped) == 2)
    check("fleet_rows no junk agents entry", all(r["name"] != "agents" for r in wrapped))
    check("fleet_rows unwrapped urls", sorted(r["fields"]["url"] for r in wrapped) ==
          ["http://1.2.3.4:8084", "http://5.6.7.8:8084"])


def test_resolve_handshakes_dir():
    import tempfile
    # configured path that exists -> used as-is
    real = {"/exists": True}
    check("resolve uses configured", m.resolve_handshakes_dir("/exists", isdir=lambda p: real.get(p, False)) == "/exists")
    # configured missing -> read from pwnagotchi config 'handshakes = "..."'
    cfgf = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False)
    cfgf.write('main.name = "x"\nhandshakes = "/etc/pwnagotchi/handshakes"\n'); cfgf.close()
    got = m.resolve_handshakes_dir("/nope", pwnagotchi_config=cfgf.name,
                                   isdir=lambda p: p == "/etc/pwnagotchi/handshakes")
    check("resolve reads pwnagotchi config", got == "/etc/pwnagotchi/handshakes")
    # nothing exists -> falls back to configured (clear error downstream)
    check("resolve falls back", m.resolve_handshakes_dir("/x", pwnagotchi_config="/no/such", isdir=lambda p: False) == "/x")


def test_import_merge():
    s = m.empty_store()
    a, sk = m.import_wifi_targets(s, m.scan_handshakes(["A_001122334455.pcap", "B_aabbccddeeff.pcap"]))
    check("import wifi added 2", a == 2 and sk == 0)
    # re-import same -> all skipped (dedup/additive)
    a2, sk2 = m.import_wifi_targets(s, m.scan_handshakes(["A_001122334455.pcap", "C_010203040506.pcap"]))
    check("re-import dedups", a2 == 1 and sk2 == 1)
    check("store has 3 targets", len(m.search_networks(s, kind="wifi_target")) == 3)
    # fleet import + dedup by url
    rows = m.fleet_rows({"x": {"url": "http://1.1.1.1:8084/", "token": "t"}})
    fa, fsk = m.import_fleet(s, rows)
    check("fleet import added", fa == 1)
    fa2, fsk2 = m.import_fleet(s, m.fleet_rows({"x2": {"url": "http://1.1.1.1:8084", "token": "t"}}))
    check("fleet dedup by url (trailing slash)", fa2 == 0 and fsk2 == 1)


def test_fire_fleet():
    import json as _json
    fleet = {"kind": "fleet", "fields": {"url": "http://10.0.0.5:8084/", "token": "tok"}}

    # success: agent runs the probe task, ok=true with stdout
    def ok_post(url, token, payload, timeout):
        check("fleet probe hits /run", url.endswith("/run"))
        check("fleet probe sends token", token == "tok")
        check("fleet probe task is uptime", payload.get("task") == "uptime")
        return 200, _json.dumps({"ok": True, "exit_code": 0, "stdout": "06:23 up 4:17\n"})
    r = m.fire_fleet(fleet, post_fn=ok_post)
    check("fleet success ok", r["ok"] is True and "reachable & authed" in r["message"] and "up 4:17" in r["message"])

    # auth failure -> 401
    r = m.fire_fleet(fleet, post_fn=lambda *a: (401, "unauthorized"))
    check("fleet 401 -> not ok, auth msg", r["ok"] is False and "auth failed" in r["message"])

    # unreachable -> status None
    r = m.fire_fleet(fleet, post_fn=lambda *a: (None, "Connection refused"))
    check("fleet unreachable -> not ok", r["ok"] is False and "unreachable" in r["message"])

    # reachable but task unknown (200 ok=false) still proves reach+auth
    r = m.fire_fleet(fleet, post_fn=lambda *a: (200, _json.dumps({"ok": False, "error": "unknown task 'uptime'"})))
    check("fleet unknown-task -> reachable & authed", r["ok"] is True and "reachable & authed" in r["message"])

    # no url set
    r = m.fire_fleet({"kind": "fleet", "fields": {}}, post_fn=lambda *a: (200, "{}"))
    check("fleet no-url refused", r["ok"] is False and "no agent url" in r["message"])

    # dispatch routes fleet to fire_fleet and others to stub
    rd = m.fire_dispatch(fleet, post_fn=lambda *a: (200, _json.dumps({"ok": True, "stdout": "x"})))
    check("dispatch fleet -> real", rd["ok"] is True and not rd.get("stub"))
    check("dispatch wifi_join -> real", m.fire_dispatch({"kind": "wifi_join", "name": "X"}, current_ssid=None).get("stub") is None)
    check("dispatch wifi_target -> gate", m.fire_dispatch({"kind": "wifi_target", "name": "Y"}, allowlist=[]).get("authorized") is False)


def test_live_http():
    try:
        import flask  # noqa: F401
    except Exception:
        check("live HTTP (skipped - no flask)", True)
        return
    import time
    import urllib.request
    import urllib.error
    d = tempfile.mkdtemp()
    p = m.NetManagerNG()
    p.options = dict(m.DEFAULTS)
    # set up import sources in the temp dir
    hs = os.path.join(d, "handshakes")
    os.makedirs(hs)
    for fn in ("Alpha_001122334455.pcap", "Bravo_aabbccddeeff.pcap", "ignore.txt"):
        open(os.path.join(hs, fn), "w").close()
    fj = os.path.join(d, "fleet.json")
    with open(fj, "w") as fh:
        json.dump({"agents": {"pi-a": {"url": "http://9.9.9.9:8084", "token": "t"}}}, fh)
    p.options.update({"auth_token": "test-token-123456", "bind_scope": "localhost",
                      "port": 8097, "store_path": os.path.join(d, "n.json"), "ui_enabled": False,
                      "handshakes_dir": hs, "fleet_json_path": fj})
    if not p._start_server():
        check("live HTTP: server started", False)
        return
    check("live HTTP: server started", True)
    time.sleep(0.4)
    base = "http://127.0.0.1:8097"

    def call(path, body=None, token="test-token-123456"):
        h = {"Authorization": "Bearer " + token} if token else {}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        req = urllib.request.Request(base + path, data=data, headers=h, method=("POST" if data else "GET"))
        try:
            r = urllib.request.urlopen(req, timeout=5)
            return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    try:
        st, _ = call("/api/state", token=None)
        check("live: no token -> 401", st == 401)
        st, body = call("/")
        check("live: page serves", st == 200 and "Network Manager" in body)
        st, b = call("/api/add", {"name": "lab", "kind": "fleet", "fields": {"url": "http://x:8084", "token": "z"}})
        nid = json.loads(b)["id"]
        check("live: add ok", st == 200)
        st, b = call("/api/state")
        check("live: state total 1", json.loads(b)["total"] == 1)
        st, b = call("/api/select", {"id": nid})
        check("live: select ok", json.loads(b).get("selected") == nid)
        # fleet fire runs a REAL probe; the dummy agent is unreachable -> ok:false, no stub
        st, b = call("/api/fire", {"id": nid})
        fr = json.loads(b)
        check("live: fleet fire runs real probe", fr.get("stub") is None and "unreachable" in (fr.get("message") or ""))
        # wifi_join fires a real association check (no SSID in sandbox -> not on it)
        st, b = call("/api/add", {"name": "Home", "kind": "wifi_join", "fields": {"ssid": "Home"}})
        wid = json.loads(b)["id"]
        st, b = call("/api/fire", {"id": wid})
        wr = json.loads(b)
        check("live: wifi_join real check", wr.get("stub") is None and "not currently on" in (wr.get("message") or ""))
        # wifi_target fires the GATE -> refused (empty allowlist)
        st, b = call("/api/add", {"name": "T", "kind": "wifi_target", "fields": {"bssid": "99:88:77:66:55:44"}})
        tid = json.loads(b)["id"]
        st, b = call("/api/fire", {"id": tid})
        tr = json.loads(b)
        check("live: wifi_target refused by gate", tr.get("ok") is False and "REFUSED" in (tr.get("message") or ""))
        # bulk import from handshakes (2 valid) and fleet.json (1)
        st, b = call("/api/import", {"source": "handshakes"})
        ir = json.loads(b)
        check("live: import handshakes +2", ir.get("ok") and ir.get("added") == 2)
        st, b = call("/api/import", {"source": "handshakes"})
        check("live: re-import handshakes skips", json.loads(b).get("added") == 0)
        st, b = call("/api/import", {"source": "fleet"})
        check("live: import fleet +1", json.loads(b).get("added") == 1)
        st, b = call("/api/import", {"source": "bogus"})
        check("live: bad import source 400", st == 400)
        st, b = call("/api/delete", {"id": nid})
        check("live: delete ok", json.loads(b).get("ok") is True)
        import stat
        mode = stat.S_IMODE(os.stat(os.path.join(d, "n.json")).st_mode)
        check("live: store 0600", mode == 0o600)
    finally:
        try:
            p._server.shutdown()
        except Exception:
            pass


def main():
    test_token_and_bind()
    test_validate_entry()
    test_crud_and_cascade()
    test_search()
    test_normalize()
    test_import_parsers()
    test_resolve_handshakes_dir()
    test_import_merge()
    test_fire_stub()
    test_fire_wifi_join()
    test_fire_wifi_target()
    test_fire_fleet()
    test_live_http()
    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("all netmanager_ng sandbox tests passed")


if __name__ == "__main__":
    main()
