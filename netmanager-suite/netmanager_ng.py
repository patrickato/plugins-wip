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

# The wifi_target capture backend caps deauth frames hard: this is a
# handshake-capture nudge, never an open-ended jam. Values above are clamped.
MAX_DEAUTH = 64

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
    # NON-NEGOTIABLE GATE for wifi_target "fire": a wireless test is REFUSED
    # unless the target's BSSID or SSID is on this explicit allowlist. EMPTY by
    # default (so nothing can be fired at). Only add networks you own or are
    # authorized to test. netmanager enforces this gate; it does not itself send
    # deauth/attack frames - the actual test backend is wired on your own lab
    # hardware (see README).
    "authorized_targets": [],
    # Bulk-import sources (so you never hand-type 50+). Importing is deduped and
    # additive - it never deletes or overwrites what's already there.
    "handshakes_dir": "/etc/pwnagotchi/handshakes",                       # -> wifi_target rows
    "fleet_json_path": "/home/pi/.config/fleetctl/fleet.json",     # -> fleet rows
    # --- wifi_target capture backend (the authorized-path "real test") ---
    # OFF by default. Even for an authorized target, NOTHING runs unless BOTH
    # capture_backend_enabled is true AND capture_iface names a SECOND adapter
    # (never the pwnagotchi radio). It runs standard tools (airodump-ng capture
    # locked to the one authorized BSSID, + an optional bounded, targeted
    # aireplay-ng deauth) and drops the handshake into handshakes_dir.
    "capture_backend_enabled": False,   # master switch (2nd gate on top of the allowlist)
    "capture_iface": "",                # e.g. "wlan1" - run netmanager_wifi_probe.sh to find it
    "capture_seconds": 25,              # how long airodump captures
    "deauth_count": 0,                  # 0 = PASSIVE capture only (no frames sent). >0 = bounded deauth
    "capture_out_dir": "",              # where the .pcapng lands (empty -> handshakes_dir)
    # interfaces the backend refuses to use (the pwnagotchi/bettercap radio):
    "builtin_ifaces": ["wlan0", "wlan0mon", "mon0"],
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


# --- bulk import (pure parsers; the plugin reads the files) -----------------

_HS_SUFFIXES = (".pcapng", ".pcap.cracked", ".pcap", ".cap", ".pmkid", ".22000", ".hc22000", ".2500", ".hccapx")
_BSSID_TAIL_RE = re.compile(r"_([0-9a-fA-F]{12})$")
_BSSID_FULL_RE = re.compile(r"^[0-9a-fA-F]{12}$")


def _fmt_bssid(hex12):
    h = hex12.lower()
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


def parse_handshake_filename(name):
    """A pwnagotchi handshake filename -> (ssid, bssid) or None. Handles
    '<ssid>_<12hex>.pcap' (ssid may contain underscores), a bare '<12hex>', and
    an ssid-only name. Returns None for files that aren't handshakes."""
    base = os.path.basename(str(name or "")).strip()
    if not base:
        return None
    low = base.lower()
    matched = False
    for suf in _HS_SUFFIXES:
        if low.endswith(suf):
            base = base[: len(base) - len(suf)]
            matched = True
            break
    if not matched:
        return None  # only import recognized handshake files
    m = _BSSID_TAIL_RE.search(base)
    if m:
        return (base[: m.start()], _fmt_bssid(m.group(1)))
    if _BSSID_FULL_RE.match(base):
        return ("", _fmt_bssid(base))
    if base:
        return (base, "")
    return None


def scan_handshakes(filenames):
    """List of filenames -> deduped [{ssid, bssid}] (dedup by bssid, else ssid)."""
    seen, out = set(), []
    for fn in filenames or []:
        r = parse_handshake_filename(fn)
        if not r:
            continue
        ssid, bssid = r
        key = bssid.lower() if bssid else ("ssid:" + ssid.lower())
        if not key or key == "ssid:" or key in seen:
            continue
        seen.add(key)
        out.append({"ssid": ssid, "bssid": bssid})
    return out


_HS_CFG_RE = re.compile(r'^\s*(?:main\.)?(?:bettercap\.)?handshakes\s*=\s*"([^"]+)"')


def resolve_handshakes_dir(configured, pwnagotchi_config="/etc/pwnagotchi/config.toml",
                           isdir=None):
    """Find the real handshakes dir so import 'just works' across images:
    the configured path if it exists, else the pwnagotchi config's
    `handshakes = "..."`, else common locations. isdir is injectable for tests."""
    isdir = isdir or os.path.isdir
    if configured and isdir(configured):
        return configured
    try:
        with open(pwnagotchi_config, "r", encoding="utf-8") as fh:
            for line in fh:
                m = _HS_CFG_RE.match(line)
                if m and isdir(m.group(1)):
                    return m.group(1)
    except Exception:
        pass
    for d in ("/etc/pwnagotchi/handshakes", "/root/handshakes", "/home/pi/handshakes"):
        if isdir(d):
            return d
    return configured  # fall back (listdir will then give a clear error)


def fleet_rows(fleet_data):
    """fleetctl fleet.json -> fleet entries.

    fleetctl wraps its agents under an "agents" key:
    {"agents": {label: {url, token, badhid:{url,token}}}}. Unwrap that so we
    iterate the agent labels, not the wrapper. A bare {label: {...}} map (older
    / hand-written) still works.
    """
    if isinstance(fleet_data, dict) and isinstance(fleet_data.get("agents"), dict):
        fleet_data = fleet_data["agents"]
    out = []
    for label, a in (fleet_data or {}).items():
        if not isinstance(a, dict):
            continue
        fields = {"url": a.get("url", ""), "token": a.get("token", "")}
        bh = a.get("badhid") or {}
        if isinstance(bh, dict):
            if bh.get("url"):
                fields["badhid_url"] = bh["url"]
            if bh.get("token"):
                fields["badhid_token"] = bh["token"]
        out.append({"name": str(label), "kind": "fleet", "fields": fields})
    return out


def _dedup_key(kind, name, fields):
    f = fields or {}
    if kind == "wifi_target":
        return (f.get("bssid") or "").lower() or ("ssid:" + (f.get("ssid") or name or "").lower())
    if kind == "fleet":
        return (f.get("url") or "").rstrip("/")
    return "name:" + (name or "").lower()


def _existing_keys(store, kind):
    keys = set()
    for e in store.get("networks", {}).values():
        if e.get("kind") == kind:
            k = _dedup_key(kind, e.get("name"), e.get("fields"))
            if k:
                keys.add(k)
    return keys


def import_wifi_targets(store, rows):
    """Merge scan_handshakes() rows as wifi_target entries. Additive + deduped."""
    existing = _existing_keys(store, "wifi_target")
    added = skipped = 0
    for r in rows or []:
        fields = {"ssid": r.get("ssid", ""), "bssid": r.get("bssid", "")}
        name = r.get("ssid") or r.get("bssid") or "unknown"
        key = _dedup_key("wifi_target", name, fields)
        if key in existing:
            skipped += 1
            continue
        existing.add(key)
        add_network(store, {"name": name, "kind": "wifi_target", "fields": fields})
        added += 1
    return added, skipped


def import_fleet(store, rows):
    """Merge fleet_rows() as fleet entries. Additive + deduped (by url)."""
    existing = _existing_keys(store, "fleet")
    added = skipped = 0
    for r in rows or []:
        key = _dedup_key("fleet", r.get("name"), r.get("fields"))
        if key and key in existing:
            skipped += 1
            continue
        if key:
            existing.add(key)
        add_network(store, r)
        added += 1
    return added, skipped


def fire_stub(entry):
    """FIRE for an unknown kind - a clear 'unknown' result."""
    return {"ok": False, "stub": True, "kind": (entry or {}).get("kind"),
            "message": "unknown kind"}


def fire_wifi_join(entry, current_ssid=None):
    """FIRE a WIFI_JOIN target = a safe, read-only connectivity test: is the pi
    associated with this SSID right now? Does NOT switch the radio - the
    built-in adapter is busy with pwnagotchi, so connecting needs a second USB
    WiFi adapter (a documented, hardware step)."""
    fields = (entry or {}).get("fields") or {}
    ssid = (fields.get("ssid") or entry.get("name") or "").strip()
    cur = (current_ssid or "").strip()
    if ssid and cur and ssid == cur:
        return {"ok": True, "kind": "wifi_join", "message": "connected to '%s' now" % ssid}
    where = ("on '%s'" % cur) if cur else "not associated with any network"
    return {"ok": False, "kind": "wifi_join",
            "message": "not currently on '%s' (%s). Switching needs a 2nd WiFi "
                       "adapter - the built-in radio is busy with pwnagotchi "
                       "(see README)." % (ssid or "?", where)}


def _norm_mac(s):
    return re.sub(r"[:\-\s]", "", str(s or "").strip().lower())


def target_authorized(bssid, ssid, allowlist):
    """True only if this target's BSSID (colon-insensitive) or SSID (exact,
    case-insensitive) is on the explicit allowlist. Empty allowlist => False."""
    al = [str(x).strip() for x in (allowlist or []) if str(x).strip()]
    if not al:
        return False
    raw = {x.lower() for x in al}
    macs = {_norm_mac(x) for x in al}
    if bssid and (bssid.strip().lower() in raw or _norm_mac(bssid) in macs):
        return True
    if ssid and ssid.strip().lower() in raw:
        return True
    return False


def fire_wifi_target(entry, allowlist):
    """FIRE a WIFI_TARGET = the GATE. A wireless test is REFUSED unless the
    target is on the explicit authorized_targets allowlist (empty by default).
    netmanager ENFORCES authorization here; it does not send deauth/attack
    frames itself - the authorized path is an integration point for a backend
    you run on your own authorized lab hardware."""
    fields = (entry or {}).get("fields") or {}
    bssid = (fields.get("bssid") or "").strip()
    ssid = (fields.get("ssid") or entry.get("name") or "").strip()
    who = bssid or ssid or "?"
    if not target_authorized(bssid, ssid, allowlist):
        return {"ok": False, "kind": "wifi_target", "authorized": False,
                "authorize_hint": who,
                "message": "REFUSED - '%s' is not on your authorized_targets "
                           "allowlist (empty by default). To authorize it, run on "
                           "the pi: sudo netmanagerctl.sh authorize %s - then try "
                           "again. Only networks you own or are authorized to "
                           "test." % (who, who)}
    return {"ok": True, "kind": "wifi_target", "authorized": True,
            "message": "authorized ✓ ('%s') - wire your wireless-test backend on "
                       "your lab hardware (deauth/capture). netmanager enforces "
                       "the gate; it does not send frames itself." % who}


def _safe_name(s):
    """pwnagotchi-ish filename component: keep it tame for the filesystem."""
    s = re.sub(r"[^A-Za-z0-9_.-]", "", str(s or "").strip())
    return s[:48]


def plan_capture(entry, cfg):
    """PURE: decide whether/how the wifi_target capture backend runs, and build
    the exact commands. Returns (plan, reason). plan is None when it won't run,
    with a human reason - and a None plan NEVER sends anything.

    Safety properties enforced here (all unit-tested):
      * backend must be explicitly enabled (2nd switch on top of the allowlist),
      * capture_iface must be set AND must NOT be the pwnagotchi/bettercap radio,
      * a BSSID is required (capture is LOCKED to that one BSSID; no broad sweep),
      * deauth is OFF unless deauth_count>0, is TARGETED at that BSSID, and is
        hard-clamped to MAX_DEAUTH (a capture nudge, never a flood)."""
    cfg = cfg or {}
    if not cfg.get("capture_backend_enabled"):
        return None, "capture backend is off (capture_backend_enabled=false) - gate passed, nothing run"
    iface = str(cfg.get("capture_iface") or "").strip()
    if not iface:
        return None, "no capture_iface set - run netmanager_wifi_probe.sh to pick a 2nd adapter"
    builtin = {str(x).strip().lower() for x in (cfg.get("builtin_ifaces") or [])}
    if iface.lower() in builtin:
        return None, ("capture_iface '%s' is the pwnagotchi radio - use a SECOND USB "
                      "adapter, never the engine's interface" % iface)
    fields = (entry or {}).get("fields") or {}
    bssid = (fields.get("bssid") or "").strip()
    ssid = (fields.get("ssid") or (entry or {}).get("name") or "").strip()
    if not bssid:
        return None, "this target has no BSSID - the capture is locked to a BSSID, so add one first"
    bssid12 = _norm_mac(bssid)
    if not re.fullmatch(r"[0-9a-f]{12}", bssid12):
        return None, "BSSID '%s' is not a valid MAC" % bssid
    channel = str(fields.get("channel") or "").strip()
    try:
        seconds = max(5, int(cfg.get("capture_seconds") or 25))
    except (TypeError, ValueError):
        seconds = 25
    try:
        deauth = int(cfg.get("deauth_count") or 0)
    except (TypeError, ValueError):
        deauth = 0
    deauth = max(0, min(deauth, MAX_DEAUTH))  # clamp: capture nudge, not a jam
    out_dir = str(cfg.get("capture_out_dir") or cfg.get("handshakes_dir") or ".").strip()
    out_base = (_safe_name(ssid) + "_" + bssid12) if ssid else bssid12
    out_path = os.path.join(out_dir, out_base)

    bssid_fmt = _fmt_bssid(bssid12)
    capture = ["airodump-ng", "--bssid", bssid_fmt, "-w", out_path,
               "--output-format", "pcapng"]
    if channel:
        capture += ["-c", channel]
    capture += [iface]
    cmds = {
        "channel": (["iw", "dev", iface, "set", "channel", channel] if channel else None),
        "capture": capture,
        # targeted at the one AP (-a BSSID); NO broadcast-to-all deauth, and only if asked
        "deauth": (["aireplay-ng", "--deauth", str(deauth), "-a", bssid_fmt, iface]
                   if deauth > 0 else None),
    }
    plan = {"iface": iface, "bssid": bssid_fmt, "ssid": ssid, "channel": channel or None,
            "seconds": seconds, "deauth_count": deauth, "out_dir": out_dir,
            "out_basename": out_base, "out_path": out_path, "cmds": cmds}
    return plan, "ok"


def run_capture_backend(entry, cfg, execute=None):
    """Run the authorized-path capture for a wifi_target. `execute(plan)` does the
    real hardware work (airodump + optional bounded deauth) and returns a dict
    like {captured: bool, handshake: bool, file: str, detail: str}; it is
    injectable so the gating + command build are testable without a radio.

    When the backend isn't enabled/ready, returns the gate-only authorized result
    (never an error, never sends anything)."""
    plan, reason = plan_capture(entry, cfg)
    who = plan["bssid"] if plan else ((entry or {}).get("fields", {}).get("bssid")
                                      or (entry or {}).get("name") or "?")
    if plan is None:
        return {"ok": True, "kind": "wifi_target", "authorized": True, "ran": False,
                "message": "authorized ✓ ('%s') - %s" % (who, reason)}
    if execute is None:  # pragma: no cover - real hardware path
        execute = _execute_capture
    try:
        res = execute(plan) or {}
    except Exception as exc:  # never let the backend crash the fire handler
        return {"ok": False, "kind": "wifi_target", "authorized": True, "ran": True,
                "message": "capture backend errored on '%s': %s" % (who, str(exc)[:160])}
    captured = bool(res.get("captured"))
    hs = bool(res.get("handshake"))
    nudge = (" (deauth x%d)" % plan["deauth_count"]) if plan["deauth_count"] else " (passive)"
    if hs:
        msg = "✓ handshake captured from '%s'%s -> %s" % (who, nudge, res.get("file") or plan["out_path"])
    elif captured:
        msg = "ran on '%s'%s - capture written, no handshake yet -> %s" % (who, nudge, res.get("file") or plan["out_path"])
    else:
        msg = "ran on '%s'%s - no capture (%s)" % (who, nudge, res.get("detail") or "nothing seen")
    return {"ok": True, "kind": "wifi_target", "authorized": True, "ran": True,
            "captured": captured, "handshake": hs, "file": res.get("file"),
            "message": msg}


def _execute_capture(plan):  # pragma: no cover - real hardware (needs a 2nd monitor adapter)
    """Standard-tooling capture: lock airodump-ng to the one BSSID, optionally a
    bounded targeted aireplay-ng deauth, then check the capture for a handshake.
    Runs only after plan_capture authorized it. Needs airodump-ng/aireplay-ng
    (aircrack-ng) and the 2nd adapter already in monitor mode."""
    import glob
    import shutil
    import subprocess as sp
    cmds = plan["cmds"]
    iface = plan["iface"]
    os.makedirs(plan["out_dir"], exist_ok=True)
    # Put THIS adapter in monitor mode via iw (keeps the name - airmon-ng would
    # rename wlan1 -> wlan1mon and break capture_iface). Harmless if already monitor.
    for c in (["ip", "link", "set", iface, "down"],
              ["iw", "dev", iface, "set", "type", "monitor"],
              ["ip", "link", "set", iface, "up"]):
        sp.run(c, capture_output=True, timeout=15)
    if cmds.get("channel"):
        sp.run(cmds["channel"], capture_output=True, timeout=15)
    # airodump in the background for the capture window
    cap = sp.Popen(cmds["capture"], stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    try:
        if cmds.get("deauth"):
            sp.run(cmds["deauth"], capture_output=True, timeout=min(plan["seconds"], 30))
        time.sleep(plan["seconds"])
    finally:
        cap.terminate()
        try:
            cap.wait(timeout=5)
        except Exception:
            cap.kill()
    # airodump appends -01.cap/.pcapng; find the newest for this basename
    hits = sorted(glob.glob(plan["out_path"] + "*.pcapng") + glob.glob(plan["out_path"] + "*.cap"),
                  key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0)
    capfile = hits[-1] if hits else None
    # airodump appends a "-NN" session counter (dad24_..d2-02.cap); normalize to
    # the pwnagotchi-style "<ssid>_<bssid>.<ext>" so the rest of the bus (netmanager
    # re-import, crack-house) recognizes it.
    if capfile:
        ext = os.path.splitext(capfile)[1]
        norm = plan["out_path"] + ext
        if os.path.abspath(capfile) != os.path.abspath(norm):
            try:
                os.replace(capfile, norm)
                capfile = norm
            except Exception:
                pass
    handshake = False
    if capfile:
        # best-effort: hcxpcapngtool emits a non-empty .22000 iff there's a usable hash
        if shutil.which("hcxpcapngtool"):
            out22000 = capfile.rsplit(".", 1)[0] + ".22000"
            sp.run(["hcxpcapngtool", "-o", out22000, capfile], capture_output=True, timeout=30)
            handshake = os.path.exists(out22000) and os.path.getsize(out22000) > 0
        elif shutil.which("aircrack-ng"):
            r = sp.run(["aircrack-ng", capfile], capture_output=True, text=True, timeout=30)
            handshake = "1 handshake" in (r.stdout or "") or "WPA (" in (r.stdout or "")
    return {"captured": bool(capfile), "handshake": handshake, "file": capfile,
            "detail": "no cap file produced" if not capfile else ""}


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


def fire_dispatch(entry, post_fn=None, timeout=10, current_ssid=None, allowlist=None,
                  capture_cfg=None, capture_execute=None):
    """Route FIRE by kind:
      fleet       -> safe reachability+auth probe
      wifi_join   -> safe read-only association check
      wifi_target -> the authorized-target allowlist GATE. If (and only if) the
                     target passes the gate AND the capture backend is enabled +
                     configured, run the authorized capture; otherwise return the
                     gate-only authorized result (no frames)."""
    kind = (entry or {}).get("kind")
    if kind == "fleet":
        return fire_fleet(entry, post_fn=post_fn, timeout=timeout)
    if kind == "wifi_join":
        return fire_wifi_join(entry, current_ssid=current_ssid)
    if kind == "wifi_target":
        gate = fire_wifi_target(entry, allowlist=allowlist)
        if not gate.get("authorized"):
            return gate  # REFUSED - the allowlist gate said no
        if capture_cfg and capture_cfg.get("capture_backend_enabled"):
            return run_capture_backend(entry, capture_cfg, execute=capture_execute)
        return gate  # authorized, backend off -> gate-only result
    return fire_stub(entry)


# ===========================================================================
# The plugin
# ===========================================================================

class NetManagerNG(plugins.Plugin):
    __author__ = "built for this project's network-manager track"
    __version__ = "0.4.0"
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
        app.add_url_rule("/api/import", "import", self._http_import, methods=["POST"])
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

    def _http_import(self):  # pragma: no cover - reads files + needs flask req
        if not self._authed(request):
            return Response("unauthorized", status=401)
        source = (self._json_body().get("source") or "").strip().lower()
        if source not in ("handshakes", "fleet"):
            return jsonify({"ok": False, "error": "source must be 'handshakes' or 'fleet'"}), 400
        with self._lock:
            store = self._load()
            try:
                if source == "handshakes":
                    path = resolve_handshakes_dir(self._opt("handshakes_dir"))
                    try:
                        files = os.listdir(path)
                    except Exception as exc:
                        return jsonify({"ok": False, "error": "can't read %s: %s" % (path, exc)}), 400
                    added, skipped = import_wifi_targets(store, scan_handshakes(files))
                else:
                    path = self._opt("fleet_json_path")
                    try:
                        with open(path, "r", encoding="utf-8") as fh:
                            data = json.load(fh)
                    except Exception as exc:
                        return jsonify({"ok": False, "error": "can't read %s: %s" % (path, exc)}), 400
                    added, skipped = import_fleet(store, fleet_rows(data))
            except Exception as exc:
                return jsonify({"ok": False, "error": "import failed: %s" % exc}), 400
            if added:
                self._save(store)
        logging.warning("[netmanager_ng] IMPORT %s: +%d added, %d skipped (from %s)",
                        source, added, skipped, path)
        return jsonify({"ok": True, "source": source, "added": added,
                        "skipped": skipped, "total": len(store["networks"])}), 200

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
        capture_cfg = {
            "capture_backend_enabled": self._opt_bool("capture_backend_enabled", False),
            "capture_iface": self._opt("capture_iface"),
            "capture_seconds": self._opt_int("capture_seconds", 25),
            "deauth_count": self._opt_int("deauth_count", 0),
            "capture_out_dir": self._opt("capture_out_dir"),
            "handshakes_dir": resolve_handshakes_dir(self._opt("handshakes_dir")),
            "builtin_ifaces": self._opt("builtin_ifaces") or DEFAULTS["builtin_ifaces"],
        }
        result = fire_dispatch(
            entry,
            timeout=self._opt_int("fire_timeout_seconds", 10),
            current_ssid=self.current_ssid(),
            allowlist=self._opt("authorized_targets") or [],
            capture_cfg=capture_cfg)
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
  :root { color-scheme: light dark; --b:#888; --sel:#1769aa; --fire:#b00;
          --ok:#1a7f37; --err:#b00020; --info:#333; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, sans-serif; margin:0; padding:12px 12px 90px; max-width:860px; }
  h1 { font-size:1.25rem; margin:.2rem 0; }
  .muted { color:#888; font-size:.85rem; }
  .bar { position:sticky; top:0; z-index:5; background:Canvas; padding:8px 0; border-bottom:1px solid var(--b); }
  input, select, textarea, button { font:inherit; padding:7px 9px; margin:2px 0; }
  input[type=text], select, textarea { width:100%; }
  button { cursor:pointer; border-radius:6px; border:1px solid var(--b); background:transparent; }
  .row { border:1px solid var(--b); border-radius:8px; padding:8px 10px; margin:8px 0; }
  .row.sel { border-color:var(--sel); border-width:2px; }
  .row .top { display:flex; justify-content:space-between; align-items:center; gap:8px; }
  .name { font-weight:600; }
  .badge { font-size:.7rem; border:1px solid var(--b); border-radius:10px; padding:1px 7px; color:#888; }
  .fields { font-size:.8rem; color:#888; margin-top:3px; word-break:break-all; }
  .acts button { margin-left:4px; }
  .fire { color:#fff; background:var(--fire); border:none; }
  .here { color:var(--sel); font-weight:600; }
  .add { border:1px dashed var(--b); border-radius:8px; padding:10px; margin:10px 0; }
  .add .kf { display:none; }
  .right { text-align:right; }
  /* first-run welcome + help */
  .welcome { border:2px solid var(--sel); border-radius:10px; padding:14px; margin:10px 0; }
  .welcome h2 { margin:.1rem 0 .4rem; font-size:1.05rem; }
  .big { display:block; width:100%; text-align:center; padding:12px; margin:6px 0;
         font-weight:600; border:1px solid var(--sel); }
  .big.primary { background:var(--sel); color:#fff; border-color:var(--sel); }
  details.help { border:1px solid var(--b); border-radius:8px; padding:6px 10px; margin:8px 0; }
  details.help summary { cursor:pointer; font-weight:600; }
  details.help table { width:100%; border-collapse:collapse; font-size:.82rem; margin-top:6px; }
  details.help td { border-top:1px solid var(--b); padding:5px 4px; vertical-align:top; }
  details.help .k { font-weight:600; white-space:nowrap; }
  /* floating toast - always visible, wherever you've scrolled */
  #toast { position:fixed; left:50%; bottom:16px; transform:translateX(-50%) translateY(140%);
           width:min(92vw,560px); z-index:50; border-radius:10px; padding:12px 40px 12px 14px;
           color:#fff; background:var(--info); box-shadow:0 4px 18px rgba(0,0,0,.35);
           font-size:.9rem; white-space:pre-wrap; word-break:break-word;
           opacity:0; transition:transform .2s ease, opacity .2s ease; pointer-events:none; }
  #toast.show { transform:translateX(-50%) translateY(0); opacity:1; pointer-events:auto; }
  #toast.ok { background:var(--ok); } #toast.err { background:var(--err); }
  #toast .x { position:absolute; top:6px; right:10px; cursor:pointer; font-size:1.1rem;
              line-height:1; opacity:.85; background:none; border:none; color:#fff; }
  #toast .hint { display:block; margin-top:6px; font-size:.8rem; opacity:.92; }
</style></head>
<body>
<h1>Network Manager</h1>
<div class="muted" id="status">loading…</div>

<details class="help">
  <summary>New here? What is this &amp; how do I use it</summary>
  <p class="muted" style="margin:.4rem 0;">Your phone command-center for the networks you work with.
     Fastest start: <b>Bulk import</b> (below) pulls your networks in automatically — no typing.
     Then use <b>search</b> at the top, <b>Select</b> to mark the one you're working on, and
     <b>Fire Test</b> to check it. Three kinds of network:</p>
  <table>
    <tr><td class="k">wifi I join</td><td>a WiFi the pi connects to. <b>Fire Test</b> = is the pi on it right now? (read-only)</td></tr>
    <tr><td class="k">fleet</td><td>another agent you enrolled (url + token). <b>Fire Test</b> = is it reachable &amp; is the token good? (read-only)</td></tr>
    <tr><td class="k">wifi target</td><td>a WiFi you're <b>authorized</b> to test. <b>Fire Test</b> is <b>REFUSED</b> unless you've added it to the <code>authorized_targets</code> allowlist in config (empty by default — only networks you own/are allowed to test).</td></tr>
  </table>
</details>

<div class="bar">
  <input type="text" id="q" placeholder="search your networks…" oninput="render()">
  <select id="kfilter" onchange="render()">
    <option value="">all kinds</option>
    <option value="wifi_join">wifi I join</option>
    <option value="fleet">fleet targets</option>
    <option value="wifi_target">wifi attack targets</option>
  </select>
</div>

<div id="welcome" class="welcome" style="display:none">
  <h2>👋 Your network list is empty — let's fill it</h2>
  <p class="muted">The quick way (no typing): pull your networks straight off the pi. Both are
     additive &amp; deduped, so they're safe to tap more than once.</p>
  <button class="big primary" onclick="doImport('handshakes')">① Import wifi targets from handshakes</button>
  <button class="big" onclick="doImport('fleet')">② Import agents from fleet.json</button>
  <p class="muted">Nothing to import yet? Open <b>Add a network manually</b> below.</p>
</div>

<div id="list"></div>

<details class="add" id="addbox">
  <summary><b>Add a network manually</b></summary>
  <div id="editbanner" class="muted" style="display:none"></div>
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
  <button id="savebtn" onclick="saveNet()">Add</button>
  <button id="cancelbtn" onclick="cancelEdit()" style="display:none">Cancel edit</button>
</details>

<details class="add" id="importbox">
  <summary><b>Bulk import</b> <span class="muted">(additive + deduped — safe to re-run)</span></summary>
  <button onclick="doImport('handshakes')">Import wifi targets from handshakes</button>
  <button onclick="doImport('fleet')">Import agents from fleet.json</button>
</details>

<div id="toast" role="status" aria-live="polite"><button class="x" onclick="hideToast()" aria-label="dismiss">×</button><span id="toastmsg"></span></div>

<script>
const TOK = new URLSearchParams(location.search).get("token") || "";
const H = {"Content-Type":"application/json","Authorization":"Bearer "+TOK};
const CTL = "/etc/pwnagotchi/netmanager_ng/netmanagerctl.sh";  // the authorize helper
let STATE = {networks:[], selected:null, current_ssid:null, counts:{}, total:0};
const KLABEL = {wifi_join:"wifi join", fleet:"fleet", wifi_target:"wifi target"};

async function api(path, body){
  const o = {headers:H}; if(body){o.method="POST"; o.body=JSON.stringify(body);}
  const r = await fetch(path, o);
  return r.json().catch(()=>({ok:false,error:"bad response ("+r.status+")"}));
}
function esc(s){ return (s||"").replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function fieldsStr(e){ return Object.entries(e.fields||{}).filter(([k,v])=>v).map(([k,v])=>k+"="+v).join("  "); }

/* floating toast: type = ok | err | info. errors/refusals stay until dismissed;
   ok/info auto-hide. hint is an optional 2nd line (e.g. how to authorize). */
let _toastT=null;
function toast(msg, type, hint){
  if(_toastT){ clearTimeout(_toastT); _toastT=null; }
  const t=document.getElementById("toast");
  document.getElementById("toastmsg").innerHTML = esc(msg) + (hint?("<span class='hint'>"+esc(hint)+"</span>"):"");
  t.className = "show " + (type||"info");
  if(type!=="err"){ _toastT = setTimeout(hideToast, 5000); }
}
function hideToast(){ document.getElementById("toast").className=""; }

async function load(){
  STATE = await api("/api/state");
  if(STATE.error){ document.getElementById("status").textContent = "auth error — check the token in your link"; toast("Auth error — the token in your link is missing or wrong.","err"); return; }
  const c = STATE.counts||{};
  const on = STATE.current_ssid ? ("on <span class='here'>"+esc(STATE.current_ssid)+"</span>") : "not associated";
  document.getElementById("status").innerHTML =
    STATE.total+" networks ("+(c.wifi_join||0)+" join / "+(c.fleet||0)+" fleet / "+(c.wifi_target||0)+" target) · "+on;
  document.getElementById("welcome").style.display = (STATE.total===0) ? "block" : "none";
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
  if(!rows.length){ list.innerHTML = STATE.total ? "<p class='muted'>no matches.</p>" : ""; return; }
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
      "<button onclick=\"editNet('"+e.id+"')\">Edit</button>"+
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
function setv(id,v){ document.getElementById(id).value = v||""; }
function entryById(id){ return (STATE.networks||[]).find(e=>e.id===id) || null; }

const FORM_IDS = ["a_name","a_notes","f_ssid","f_bssid","f_url","f_token","f_badhid","f_btok","f_tssid","f_tbssid"];
let EDIT_ID = null;
function clearForm(){ FORM_IDS.forEach(i=>setv(i,"")); }
function cancelEdit(){
  EDIT_ID = null; clearForm();
  document.getElementById("savebtn").textContent = "Add";
  document.getElementById("cancelbtn").style.display = "none";
  document.getElementById("editbanner").style.display = "none";
}
function editNet(id){
  const e = entryById(id); if(!e){ toast("can't find that one — reloading.","err"); load(); return; }
  EDIT_ID = id;
  const f = e.fields||{};
  setv("a_name", e.name); document.getElementById("a_kind").value = e.kind; kindFields();
  if(e.kind==="wifi_join"){ setv("f_ssid",f.ssid); setv("f_bssid",f.bssid); }
  if(e.kind==="fleet"){ setv("f_url",f.url); setv("f_token",f.token); setv("f_badhid",f.badhid_url); setv("f_btok",f.badhid_token); }
  if(e.kind==="wifi_target"){ setv("f_tssid",f.ssid); setv("f_tbssid",f.bssid); }
  setv("a_notes", e.notes);
  const b = document.getElementById("editbanner");
  b.textContent = "Editing “"+e.name+"” — change what you need, then Save. Tip: changing the kind is allowed.";
  b.style.display = "block";
  document.getElementById("savebtn").textContent = "Save changes";
  document.getElementById("cancelbtn").style.display = "";
  document.getElementById("addbox").open = true;
  document.getElementById("addbox").scrollIntoView({behavior:"smooth", block:"center"});
}
async function saveNet(){
  const name=val("a_name"); if(!name){ toast("Name is required.","err"); return; }
  const body = {name, kind:document.getElementById("a_kind").value, notes:val("a_notes"), fields:gatherFields()};
  const editing = EDIT_ID;
  if(editing) body.id = editing;
  const r = await api(editing ? "/api/update" : "/api/add", body);
  if(r.ok){ cancelEdit(); document.getElementById("addbox").open=false; toast((editing?"Updated “":"Added “")+name+"”.","ok"); await load(); }
  else toast((editing?"Update":"Add")+" failed: "+(r.error||"?"),"err");
}
async function sel(id){ const r=await api("/api/select",{id}); if(r.ok){ const e=entryById(id); toast("Selected “"+((e&&e.name)||"")+"”.","ok"); await load(); } else toast(r.error||"select failed","err"); }
async function delNet(id,name){ if(!confirm("Delete “"+name+"”?")) return; const r=await api("/api/delete",{id}); if(r.ok){ toast("Deleted “"+name+"”.","ok"); await load(); } else toast(r.error||"delete failed","err"); }
async function fire(id){
  const e=entryById(id); const label=(e&&e.name)||"";
  toast("Firing test at “"+label+"”…","info");
  const r=await api("/api/fire",{id});
  const msg = (r.message||r.error||JSON.stringify(r));
  if(r.stub){ toast(label+": not wired — "+msg,"info"); return; }
  if(r.ok){ toast("✓ "+label+": "+msg,"ok"); return; }
  // a refused wifi_target gets an actionable "how to authorize" hint: the exact
  // one-command helper, with this target's identifier already filled in.
  if(e && e.kind==="wifi_target" && r.authorized===false){
    const who = (e.fields&&(e.fields.bssid||e.fields.ssid)) || e.name || "?";
    toast("✗ "+label+" — REFUSED (not authorized to test it yet).",
          "err",
          "To allow it, run this on the pi (SSH), then tap Fire Test again:\n\n"+
          "sudo "+CTL+" authorize "+who+"\n\n"+
          "It adds the target, restarts pwnagotchi, and is reversible (… deauthorize "+who+"). "+
          "Only authorize networks you own or are explicitly allowed to test.");
    return;
  }
  toast("✗ "+label+": "+msg,"err");
}
async function doImport(source){
  toast("Importing from "+source+"…","info");
  const r=await api("/api/import",{source});
  if(r.ok){ toast("Imported from "+source+": +"+r.added+" added, "+r.skipped+" already there ("+r.total+" total).","ok"); await load(); }
  else toast("Import failed: "+(r.error||"?"),"err");
}
kindFields(); load();
</script>
</body></html>
"""
