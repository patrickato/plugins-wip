"""
netmanager_ng.py - Network Manager for a pwnagotchi you own.

The "command center" page for the networks/targets you work with. One page,
phone-friendly (same pattern as badhid_ng's control page: token-gated,
bind_scope, on-screen status), backing a searchable list that scales to 50+
entries with full add / edit / delete, a "you are here / selected" marker, and
a per-row FIRE TEST button.

A "network" is any target you track, of three KINDS - the backbone stores all
three the same way; the per-kind FIRE action is wired on top of this:
  * wifi_join    - a WiFi network the pi can connect to (SSID/[BSSID]/[psk])
  * fleet        - a fleet agent you enrolled (url + token), driveable remotely
  * wifi_target  - a WiFi SSID/BSSID you're AUTHORIZED to run a wireless test on

BACKBONE SCOPE: store + CRUD + search + select + the page + a FIRE endpoint that
is a clear STUB per kind ("not wired yet"). The three real fire actions land on
top of this without changing the store or the page.

This fork's loader does NOT merge __defaults__, so every option is read through
DEFAULTS + the _opt()/_opt_*() helpers. Config section = this file's basename,
under [main.plugins.netmanager_ng].
"""

import hmac
import json
import logging
import os
import re
import secrets
import subprocess
import threading
import time

try:
    import pwnagotchi.plugins as plugins
except Exception:  # pragma: no cover - lets the pure layer import in tests
    class _P:  # minimal shim
        class Plugin:  # noqa: N801
            pass
    plugins = _P()

try:
    from flask import Response, jsonify, request  # noqa: F401
    from werkzeug.serving import make_server
except Exception:  # pragma: no cover
    Response = None
    jsonify = None
    request = None
    make_server = None

try:
    import pwnagotchi.ui.fonts as fonts
    from pwnagotchi.ui.components import LabeledValue
    from pwnagotchi.ui.view import BLACK
    _UI_AVAILABLE = True
except Exception:  # pragma: no cover
    fonts = None
    LabeledValue = None
    BLACK = 0
    _UI_AVAILABLE = False

ELEMENT_NAME = "netmgr"

KINDS = ("wifi_join", "fleet", "wifi_target")

DEFAULTS = {
    "enabled": False,
    # No working default - a blank/placeholder/<12-char token refuses to start.
    "auth_token": None,
    "bind_scope": "auto",          # auto | tailscale | localhost | lan
    "port": 8085,
    # Where the network list lives (0600; may hold fleet tokens).
    "store_path": "/etc/pwnagotchi/netmanager_ng/networks.json",
    # Per-fire network timeout (seconds) - e.g. the fleet reachability probe.
    "fire_timeout_seconds": 10,
    "ui_enabled": True,
    "ui_position_x": -40,
    "ui_position_y": 30,
}

PLACEHOLDER_TOKENS = frozenset({
    "", "changeme", "change_me", "change-me", "token", "default", "secret",
    "password", "admin", "root", "pwnagotchi", "netmanager", "test", "12345",
    "123456", "0000", "none", "null",
})

IPV4_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")


# ===========================================================================
# Pure / testable helpers - no flask, no pwnagotchi needed
# ===========================================================================

def _looks_like_placeholder(value):
    if value is None:
        return True
    return str(value).strip().lower() in PLACEHOLDER_TOKENS


def validate_token(token):
    if _looks_like_placeholder(token):
        return False, ("auth_token is missing/blank/placeholder - the Network "
                       "Manager server will NOT start. Set a long random token.")
    if len(str(token).strip()) < 12:
        return False, "auth_token is shorter than 12 characters - refusing to start."
    return True, "ok"


def token_matches(configured, presented):
    if configured is None or presented is None:
        return False
    return hmac.compare_digest(str(configured), str(presented))


def _detect_tailscale_ip():  # pragma: no cover
    for cmd in (["tailscale", "ip", "-4"],
                ["ip", "-4", "addr", "show", "tailscale0"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
            m = IPV4_RE.search(out or "")
            if m:
                return m.group(1)
        except Exception:
            pass
    return None


def resolve_bind_plan(bind_scope, port, detect_tailscale=None):
    """(bind_host, note, ok) - same semantics as badhid_ng/remoteexec_ng."""
    if detect_tailscale is None:
        detect_tailscale = _detect_tailscale_ip
    scope = (bind_scope or "auto").strip().lower()
    if scope == "localhost":
        return "127.0.0.1", f"localhost only - http://127.0.0.1:{port}/", True
    if scope == "lan":
        return "0.0.0.0", f"LAN/all interfaces (0.0.0.0:{port}) - EXPLICIT broad exposure", True
    if scope == "tailscale":
        ip = detect_tailscale()
        if ip:
            return ip, f"tailscale only - http://{ip}:{port}/", True
        return None, ("bind_scope='tailscale' but no tailscale interface found - "
                      "refusing to start (fail-safe)."), False
    if scope == "auto":
        ip = detect_tailscale()
        if ip:
            return ip, f"auto -> tailscale - http://{ip}:{port}/", True
        return "127.0.0.1", (f"auto -> no tailscale, localhost only - "
                             f"http://127.0.0.1:{port}/ (use an SSH tunnel)"), True
    return "127.0.0.1", f"unknown bind_scope {scope!r}, using localhost", True


# --- the network store (pure dict operations; the plugin wraps file I/O) -----

def new_id():
    return secrets.token_hex(4)


def _clean_fields(fields):
    out = {}
    for k, v in (fields or {}).items():
        k = str(k).strip()
        if k:
            out[k] = str(v)
    return out


def validate_entry(data):
    """Validate+normalize an add/update payload. Returns (entry, error)."""
    if not isinstance(data, dict):
        return None, "entry must be an object"
    name = (data.get("name") or "").strip()
    kind = (data.get("kind") or "").strip()
    if not name:
        return None, "name is required"
    if kind not in KINDS:
        return None, "kind must be one of: " + ", ".join(KINDS)
    entry = {
        "name": name,
        "kind": kind,
        "notes": (data.get("notes") or "").strip(),
        "fields": _clean_fields(data.get("fields")),
    }
    return entry, None


def empty_store():
    return {"version": 1, "selected": None, "networks": {}}


def normalize_store(obj):
    """Coerce arbitrary loaded JSON into a valid store shape (never raises)."""
    if not isinstance(obj, dict):
        return empty_store()
    nets = obj.get("networks")
    if not isinstance(nets, dict):
        nets = {}
    clean = {}
    for nid, entry in nets.items():
        e, err = validate_entry(entry if isinstance(entry, dict) else {})
        if err:
            continue
        e["added_at"] = entry.get("added_at") if isinstance(entry, dict) else None
        clean[str(nid)] = e
    selected = obj.get("selected")
    if selected not in clean:
        selected = None
    return {"version": 1, "selected": selected, "networks": clean}


def add_network(store, data):
    entry, err = validate_entry(data)
    if err:
        return None, err
    nid = new_id()
    while nid in store["networks"]:
        nid = new_id()
    entry["added_at"] = time.time()
    store["networks"][nid] = entry
    return nid, None


def update_network(store, nid, data):
    if nid not in store["networks"]:
        return "no such network"
    entry, err = validate_entry(data)
    if err:
        return err
    entry["added_at"] = store["networks"][nid].get("added_at")
    store["networks"][nid] = entry
    return None


def delete_network(store, nid):
    if nid not in store["networks"]:
        return "no such network"
    del store["networks"][nid]
    if store.get("selected") == nid:
        store["selected"] = None
    return None


def select_network(store, nid):
    if nid is not None and nid not in store["networks"]:
        return "no such network"
    store["selected"] = nid
    return None


def search_networks(store, q="", kind=""):
    """Return a sorted list of {id, ...entry} matching q (name/notes/fields) and
    kind. Case-insensitive substring. Sorted by name then id for stable 50+ UX."""
    q = (q or "").strip().lower()
    kind = (kind or "").strip()
    rows = []
    for nid, e in store.get("networks", {}).items():
        if kind and e.get("kind") != kind:
            continue
        if q:
            hay = " ".join([
                e.get("name", ""), e.get("notes", ""),
                " ".join(str(v) for v in (e.get("fields") or {}).values()),
                e.get("kind", ""),
            ]).lower()
            if q not in hay:
                continue
        row = dict(e)
        row["id"] = nid
        rows.append(row)
    rows.sort(key=lambda r: (r.get("name", "").lower(), r.get("id", "")))
    return rows


def fire_stub(entry):
    """FIRE for a kind that isn't wired yet - a clear per-kind 'not wired' result."""
    kind = (entry or {}).get("kind")
    msgs = {
        "wifi_join": "connect/switch not wired yet - coming in the connectivity slice",
        "wifi_target": "wireless test not wired yet - gated behind the authorized-target allowlist",
    }
    return {"ok": False, "stub": True, "kind": kind,
            "message": msgs.get(kind, "unknown kind")}


def _http_post_json(url, token, payload, timeout):  # pragma: no cover - real network
    """POST json with a Bearer token. Returns (status|None, text). None status
    means the request never completed (network error) - text carries why."""
    import urllib.request
    import urllib.error
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + str(token)})
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.getcode(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception:
            return e.code, ""
    except Exception as e:
        return None, str(e)


def fire_fleet(entry, post_fn=None, timeout=10, probe_task="uptime"):
    """FIRE a FLEET target = a safe reachability + auth probe: run a read-only
    task on the agent's remoteexec API with the stored token, and report. No
    side effects. post_fn(url, token, payload, timeout) -> (status|None, text)
    is injectable for tests. Remote BadHID payload-firing is a separate,
    heavier action (not this button)."""
    post_fn = post_fn or _http_post_json
    fields = (entry or {}).get("fields") or {}
    url = (fields.get("url") or "").strip().rstrip("/")
    token = fields.get("token") or ""
    if not url:
        return {"ok": False, "kind": "fleet", "message": "no agent url set for this fleet entry"}
    status, text = post_fn(url + "/run", token, {"task": probe_task}, timeout)
    if status is None:
        return {"ok": False, "kind": "fleet", "message": "unreachable: " + str(text)[:160]}
    if status == 401:
        return {"ok": False, "kind": "fleet", "message": "auth failed (401) - check the stored token"}
    try:
        data = json.loads(text)
    except Exception:
        data = {}
    if status == 200 and isinstance(data, dict) and data.get("ok"):
        out = (data.get("stdout") or "").strip().splitlines()
        return {"ok": True, "kind": "fleet",
                "message": "reachable & authed - %s: %s" % (probe_task, out[0] if out else "ok")}
    # Any other reply (unknown task, non-zero exit) still proves reach + auth.
    note = (data.get("error") if isinstance(data, dict) else None) or (str(text)[:120] if text else "?")
    return {"ok": True, "kind": "fleet",
            "message": "reachable & authed (probe '%s' note: %s)" % (probe_task, note)}


def fire_dispatch(entry, post_fn=None, timeout=10):
    """Route FIRE by kind. fleet is wired (safe probe); the others are stubs."""
    if (entry or {}).get("kind") == "fleet":
        return fire_fleet(entry, post_fn=post_fn, timeout=timeout)
    return fire_stub(entry)


# ===========================================================================
# The plugin
# ===========================================================================

class NetManagerNG(plugins.Plugin):
    __author__ = "built for this project's network-manager track"
    __version__ = "0.1.0"
    __license__ = "GPL3"
    __description__ = ("Phone-friendly Network Manager: a searchable, 50+-scale "
                       "list of your networks/targets with add/edit/delete, a "
                       "current/selected marker, and a per-row fire test "
                       "(backbone - fire actions wired on top).")

    def __init__(self):
        self._server = None
        self._server_thread = None
        self._bind_note = ""
        self._lock = threading.Lock()

    # --- option readers ---
    def _opt(self, key):
        try:
            return self.options.get(key, DEFAULTS.get(key))
        except Exception:
            return DEFAULTS.get(key)

    def _opt_int(self, key, default=0):
        try:
            return int(self._opt(key))
        except (TypeError, ValueError):
            return default

    def _opt_bool(self, key, default=False):
        val = self._opt(key)
        if isinstance(val, bool):
            return val
        if val is None:
            return default
        return str(val).strip().lower() in ("1", "true", "yes", "on")

    # --- store I/O (file side; pure ops live at module level) ---
    def _load(self):
        path = self._opt("store_path")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return normalize_store(json.load(fh))
        except FileNotFoundError:
            return empty_store()
        except Exception as exc:
            logging.warning("[netmanager_ng] store unreadable (%s) - starting empty", exc)
            return empty_store()

    def _save(self, store):
        path = self._opt("store_path")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except Exception:
            pass
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(store, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)  # may hold fleet tokens
        except Exception:
            pass

    # --- lifecycle ---
    def on_loaded(self):
        logging.info("[netmanager_ng] loading")
        self._start_server()

    def on_unload(self, ui):
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass
            self._server = None
        if _UI_AVAILABLE and ui is not None:
            try:
                with ui._lock:
                    ui.remove_element(ELEMENT_NAME)
            except Exception:
                pass
        logging.info("[netmanager_ng] unloaded")

    # --- server ---
    def _start_server(self):
        ok, msg = validate_token(self._opt("auth_token"))
        if not ok:
            logging.error("[netmanager_ng] %s", msg)
            return False
        if make_server is None:
            logging.error("[netmanager_ng] flask/werkzeug unavailable")
            return False
        port = self._opt_int("port", 8085)
        bind_host, note, bok = resolve_bind_plan(self._opt("bind_scope"), port)
        self._bind_note = note
        if not bok:
            logging.error("[netmanager_ng] %s", note)
            return False

        from flask import Flask
        app = Flask(__name__)
        app.add_url_rule("/", "root", self._http_root, methods=["GET"])
        app.add_url_rule("/api/state", "state", self._http_state, methods=["GET"])
        app.add_url_rule("/api/add", "add", self._http_add, methods=["POST"])
        app.add_url_rule("/api/update", "update", self._http_update, methods=["POST"])
        app.add_url_rule("/api/delete", "delete", self._http_delete, methods=["POST"])
        app.add_url_rule("/api/select", "select", self._http_select, methods=["POST"])
        app.add_url_rule("/api/fire", "fire", self._http_fire, methods=["POST"])
        try:
            self._server = make_server(bind_host, port, app, threaded=True)
        except Exception as exc:
            logging.error("[netmanager_ng] could not bind %s:%s - %s", bind_host, port, exc)
            return False
        self._server_thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="netmanager_ng-http")
        self._server_thread.start()
        logging.warning("[netmanager_ng] Network Manager up: %s", note)
        return True

    # --- auth ---
    def _authed(self, req):
        configured = self._opt("auth_token")
        presented = None
        try:
            auth = req.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                presented = auth[len("Bearer "):].strip()
            if presented is None:
                presented = req.headers.get("X-Auth-Token")
            if presented is None:
                presented = req.values.get("token")
        except Exception:
            presented = None
        return token_matches(configured, presented)

    def _json_body(self):  # pragma: no cover - needs flask req
        try:
            return request.get_json(silent=True) or {}
        except Exception:
            return {}

    def current_ssid(self):  # pragma: no cover - reads the radio
        """Best-effort SSID the pi's WiFi is associated with (read-only)."""
        for cmd in (["iwgetid", "-r"], ["iwgetid", "wlan0", "-r"]):
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=4).stdout.strip()
                if out:
                    return out
            except Exception:
                pass
        return None

    # --- HTTP handlers ---
    def _http_root(self):  # pragma: no cover
        if not self._authed(request):
            return Response("unauthorized", status=401)
        return Response(_PAGE, mimetype="text/html")

    def _http_state(self):  # pragma: no cover
        if not self._authed(request):
            return Response("unauthorized", status=401)
        with self._lock:
            store = self._load()
        counts = {}
        for e in store["networks"].values():
            counts[e["kind"]] = counts.get(e["kind"], 0) + 1
        return jsonify({
            "networks": search_networks(store),
            "selected": store.get("selected"),
            "current_ssid": self.current_ssid(),
            "counts": counts,
            "total": len(store["networks"]),
            "bind": self._bind_note,
            "kinds": list(KINDS),
        })

    def _mutate(self, fn):  # pragma: no cover - needs flask req
        if not self._authed(request):
            return Response("unauthorized", status=401)
        body = self._json_body()
        with self._lock:
            store = self._load()
            result, code = fn(store, body)
            if code == 200:
                self._save(store)
        return jsonify(result), code

    def _http_add(self):  # pragma: no cover
        def op(store, body):
            nid, err = add_network(store, body)
            if err:
                return {"ok": False, "error": err}, 400
            return {"ok": True, "id": nid}, 200
        return self._mutate(op)

    def _http_update(self):  # pragma: no cover
        def op(store, body):
            err = update_network(store, body.get("id"), body)
            if err:
                return {"ok": False, "error": err}, 400
            return {"ok": True}, 200
        return self._mutate(op)

    def _http_delete(self):  # pragma: no cover
        def op(store, body):
            err = delete_network(store, body.get("id"))
            if err:
                return {"ok": False, "error": err}, 400
            return {"ok": True}, 200
        return self._mutate(op)

    def _http_select(self):  # pragma: no cover
        def op(store, body):
            err = select_network(store, body.get("id"))
            if err:
                return {"ok": False, "error": err}, 400
            return {"ok": True, "selected": store.get("selected")}, 200
        return self._mutate(op)

    def _http_fire(self):  # pragma: no cover
        if not self._authed(request):
            return Response("unauthorized", status=401)
        body = self._json_body()
        with self._lock:
            store = self._load()
            nid = body.get("id")
            entry = store["networks"].get(nid)
        if entry is None:
            return jsonify({"ok": False, "error": "no such network"}), 400
        logging.warning("[netmanager_ng] FIRE requested on %s (%s)",
                        entry.get("name"), entry.get("kind"))
        result = fire_dispatch(entry, timeout=self._opt_int("fire_timeout_seconds", 10))
        logging.warning("[netmanager_ng] FIRE %s result: ok=%s %s",
                        entry.get("name"), result.get("ok"), result.get("message"))
        return jsonify(result), 200

    # --- on-screen status ---
    def on_ui_setup(self, ui):
        if not (_UI_AVAILABLE and self._opt_bool("ui_enabled", True)):
            return
        try:
            cx = self._opt_int("ui_position_x", -40)
            pos_x = ui.width() + cx if cx < 0 else cx
            pos_x = max(0, min(pos_x, max(0, ui.width() - 10)))
            pos_y = self._opt_int("ui_position_y", 30)
            ui.add_element(ELEMENT_NAME, LabeledValue(
                color=BLACK, label="net", value="0",
                position=(pos_x, pos_y),
                label_font=fonts.Bold, text_font=fonts.Medium))
        except Exception as exc:
            logging.error("[netmanager_ng] UI setup failed: %r", exc)

    def on_ui_update(self, ui):
        if not (_UI_AVAILABLE and self._opt_bool("ui_enabled", True)):
            return
        try:
            with self._lock:
                store = self._load()
            ui.set(ELEMENT_NAME, str(len(store["networks"])))
        except Exception:
            pass


# The page is static HTML+JS; it reads the token from its own ?token= URL and
# talks to /api/*. Kept dependency-free so it works on an offline pi.
_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Network Manager</title>
<style>
  :root { color-scheme: light dark; --b:#888; --sel:#1769aa; --fire:#b00; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, sans-serif; margin:0; padding:12px; max-width:860px; }
  h1 { font-size:1.25rem; margin:.2rem 0; }
  .muted { color:#888; font-size:.85rem; }
  .bar { position:sticky; top:0; background:Canvas; padding:8px 0; border-bottom:1px solid var(--b); }
  input, select, textarea, button { font:inherit; padding:7px 9px; margin:2px 0; }
  input[type=text], select, textarea { width:100%; }
  .row { border:1px solid var(--b); border-radius:8px; padding:8px 10px; margin:8px 0; }
  .row.sel { border-color:var(--sel); border-width:2px; }
  .row .top { display:flex; justify-content:space-between; align-items:center; gap:8px; }
  .name { font-weight:600; }
  .badge { font-size:.7rem; border:1px solid var(--b); border-radius:10px; padding:1px 7px; color:#888; }
  .fields { font-size:.8rem; color:#888; margin-top:3px; word-break:break-all; }
  .acts button { margin-left:4px; }
  .fire { color:#fff; background:var(--fire); border:none; border-radius:6px; }
  .here { color:var(--sel); font-weight:600; }
  .add { border:1px dashed var(--b); border-radius:8px; padding:10px; margin:10px 0; }
  .add .kf { display:none; }
  #result { white-space:pre-wrap; font-size:.85rem; border:1px solid var(--b); border-radius:6px; padding:8px; margin:8px 0; display:none; }
  .right { text-align:right; }
</style></head>
<body>
<h1>Network Manager</h1>
<div class="muted" id="status">loading…</div>

<div class="bar">
  <input type="text" id="q" placeholder="search 50+ networks…" oninput="render()">
  <select id="kfilter" onchange="render()">
    <option value="">all kinds</option>
    <option value="wifi_join">wifi I join</option>
    <option value="fleet">fleet targets</option>
    <option value="wifi_target">wifi attack targets</option>
  </select>
</div>

<div id="list"></div>
<div id="result"></div>

<div class="add">
  <b>Add a network</b>
  <input type="text" id="a_name" placeholder="name (required)">
  <select id="a_kind" onchange="kindFields()">
    <option value="wifi_join">wifi I join</option>
    <option value="fleet">fleet target (agent)</option>
    <option value="wifi_target">wifi attack target</option>
  </select>
  <div class="kf" data-k="wifi_join"><input type="text" id="f_ssid" placeholder="SSID"><input type="text" id="f_bssid" placeholder="BSSID (optional)"></div>
  <div class="kf" data-k="fleet"><input type="text" id="f_url" placeholder="agent URL e.g. http://10.0.0.5:8084"><input type="text" id="f_token" placeholder="agent token"><input type="text" id="f_badhid" placeholder="BadHID URL (optional) e.g. http://10.0.0.5:8083"><input type="text" id="f_btok" placeholder="BadHID token (optional)"></div>
  <div class="kf" data-k="wifi_target"><input type="text" id="f_tssid" placeholder="SSID"><input type="text" id="f_tbssid" placeholder="BSSID (optional)"></div>
  <input type="text" id="a_notes" placeholder="notes (optional)">
  <button onclick="addNet()">Add</button>
</div>

<script>
const TOK = new URLSearchParams(location.search).get("token") || "";
const H = {"Content-Type":"application/json","Authorization":"Bearer "+TOK};
let STATE = {networks:[], selected:null, current_ssid:null, counts:{}, total:0};
const KLABEL = {wifi_join:"wifi join", fleet:"fleet", wifi_target:"wifi target"};

async function api(path, body){
  const o = {headers:H}; if(body){o.method="POST"; o.body=JSON.stringify(body);}
  const r = await fetch(path, o);
  return r.json().catch(()=>({ok:false,error:"bad response ("+r.status+")"}));
}
function esc(s){ return (s||"").replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function fieldsStr(e){ return Object.entries(e.fields||{}).filter(([k,v])=>v).map(([k,v])=>k+"="+v).join("  "); }

async function load(){
  STATE = await api("/api/state");
  if(STATE.error){ document.getElementById("status").textContent = "auth error - check the token in your link"; return; }
  const c = STATE.counts||{};
  const on = STATE.current_ssid ? ("on <span class='here'>"+esc(STATE.current_ssid)+"</span>") : "not associated";
  document.getElementById("status").innerHTML =
    STATE.total+" networks ("+(c.wifi_join||0)+" join / "+(c.fleet||0)+" fleet / "+(c.wifi_target||0)+" target) · "+on;
  render();
}
function render(){
  const q = document.getElementById("q").value.toLowerCase();
  const kf = document.getElementById("kfilter").value;
  const list = document.getElementById("list");
  const rows = (STATE.networks||[]).filter(e=>{
    if(kf && e.kind!==kf) return false;
    if(q){ const hay=(e.name+" "+e.notes+" "+fieldsStr(e)+" "+e.kind).toLowerCase(); if(!hay.includes(q)) return false; }
    return true;
  });
  if(!rows.length){ list.innerHTML = "<p class='muted'>no matches.</p>"; return; }
  list.innerHTML = rows.map(e=>{
    const sel = e.id===STATE.selected;
    const isHere = e.kind==="wifi_join" && STATE.current_ssid && (e.fields.ssid||e.name)===STATE.current_ssid;
    return "<div class='row"+(sel?" sel":"")+"'><div class='top'>"+
      "<span><span class='name'>"+esc(e.name)+"</span> "+(isHere?"<span class='here'>• here</span>":"")+(sel?" <span class='here'>✓ selected</span>":"")+"</span>"+
      "<span class='badge'>"+KLABEL[e.kind]+"</span></div>"+
      (fieldsStr(e)?"<div class='fields'>"+esc(fieldsStr(e))+"</div>":"")+
      (e.notes?"<div class='fields'>“"+esc(e.notes)+"”</div>":"")+
      "<div class='right acts'>"+
      "<button onclick=\"sel('"+e.id+"')\">Select</button>"+
      "<button class='fire' onclick=\"fire('"+e.id+"')\">Fire Test</button>"+
      "<button onclick=\"delNet('"+e.id+"','"+esc(e.name).replace(/'/g,"")+"')\">Delete</button>"+
      "</div></div>";
  }).join("");
}
function kindFields(){
  const k = document.getElementById("a_kind").value;
  document.querySelectorAll(".add .kf").forEach(d=>{ d.style.display = d.dataset.k===k ? "block" : "none"; });
}
function gatherFields(){
  const k = document.getElementById("a_kind").value; const f={};
  if(k==="wifi_join"){ f.ssid=val("f_ssid"); f.bssid=val("f_bssid"); }
  if(k==="fleet"){ f.url=val("f_url"); f.token=val("f_token"); f.badhid_url=val("f_badhid"); f.badhid_token=val("f_btok"); }
  if(k==="wifi_target"){ f.ssid=val("f_tssid"); f.bssid=val("f_tbssid"); }
  return f;
}
function val(id){ return document.getElementById(id).value.trim(); }
async function addNet(){
  const name=val("a_name"); if(!name){ showResult("name is required"); return; }
  const r = await api("/api/add", {name, kind:document.getElementById("a_kind").value, notes:val("a_notes"), fields:gatherFields()});
  if(r.ok){ ["a_name","a_notes","f_ssid","f_bssid","f_url","f_token","f_badhid","f_btok","f_tssid","f_tbssid"].forEach(i=>document.getElementById(i).value=""); await load(); }
  else showResult("add failed: "+(r.error||"?"));
}
async function sel(id){ const r=await api("/api/select",{id}); if(r.ok) await load(); else showResult(r.error); }
async function delNet(id,name){ if(!confirm("Delete “"+name+"”?")) return; const r=await api("/api/delete",{id}); if(r.ok) await load(); else showResult(r.error); }
async function fire(id){ showResult("firing…"); const r=await api("/api/fire",{id}); const pfx = r.stub?"[not wired] ":(r.ok?"✓ ":"✗ "); showResult(pfx+(r.message||r.error||JSON.stringify(r))); }
function showResult(t){ const d=document.getElementById("result"); d.style.display="block"; d.textContent=t; }
kindFields(); load();
</script>
</body></html>
"""
