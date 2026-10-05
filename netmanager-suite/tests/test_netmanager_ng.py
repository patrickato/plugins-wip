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
    # the two unwired kinds still stub; fleet is wired (tested separately)
    for k in ("wifi_join", "wifi_target"):
        r = m.fire_stub({"kind": k})
        check(f"fire stub {k}: ok False + message", r["ok"] is False and r["stub"] is True and bool(r["message"]))


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
    check("dispatch wifi_join -> stub", m.fire_dispatch({"kind": "wifi_join"}).get("stub") is True)


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
    p.options.update({"auth_token": "test-token-123456", "bind_scope": "localhost",
                      "port": 8097, "store_path": os.path.join(d, "n.json"), "ui_enabled": False})
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
        # a wifi_join entry still fires as a stub over HTTP
        st, b = call("/api/add", {"name": "Home", "kind": "wifi_join", "fields": {"ssid": "Home"}})
        wid = json.loads(b)["id"]
        st, b = call("/api/fire", {"id": wid})
        check("live: wifi_join fire is stub", json.loads(b).get("stub") is True)
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
    test_fire_stub()
    test_fire_fleet()
    test_live_http()
    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("all netmanager_ng sandbox tests passed")


if __name__ == "__main__":
    main()
