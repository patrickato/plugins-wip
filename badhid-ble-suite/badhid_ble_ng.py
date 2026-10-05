"""
badhid_ble_ng.py - pwnagotchi plugin (runs ON the pi)

WIRELESS BadHID: present the pi as a *Bluetooth* HID keyboard so it can type
into a paired device with NO cable to the target. It reuses the exact same
DuckyScript engine as the wired badhid_ng (parser, US keymap, 8-byte boot-
keyboard reports) - only the TRANSPORT changes: instead of writing reports to
/dev/hidg0 (USB gadget), it sends them over a Bluetooth HID connection.

  wired  badhid_ng     : DuckyScript -> reports -> /dev/hidg0 (USB)
  wireless badhid_ble  : DuckyScript -> reports -> Bluetooth HID (this file)

STATUS: the engine + the arm/auth/parse/fire structure are the same proven,
tested code as the wired suite. The BLE *transport* (BlueZ) is the part that
MUST be brought up and debugged on real hardware - there is no Bluetooth radio
in a sandbox to validate against. The transport is written out concretely
below and clearly marked; treat on-device bring-up as the real test (exactly
how the wired gadget shook out its bugs).

THE SAFETY LINE (same as wired, plus the pairing boundary):

  * A Bluetooth HID keyboard can only type into a device that has PAIRED with
    this pi. Pairing requires someone to accept it on the target - so wireless
    BadHID is inherently scoped to devices you can pair with (yours, or ones an
    authorized owner pairs for a test). That pairing step IS the boundary; the
    software still cannot verify intent, so the same rule holds: only against
    hardware you own or are authorized to test.
  * Disarmed + manual-fire by default, token auth, loud logging - identical to
    the wired suite. Ships no offensive payloads.

Framework facts: same as badhid_ng - [main.plugins.badhid_ble_ng] section,
DEFAULTS + _opt* readers, real hooks, server in a daemon thread.

Transport choice: BLE HID-over-GATT (HOG) - advertises a BLE keyboard (HID
service 0x1812) that modern phones and PCs can pair with. The classic
Bluetooth HID profile is an alternative for older hosts; see NOTES.md.
"""

import hmac
import logging
import os
import re
import threading
import time

import pwnagotchi.plugins as plugins

try:
    from flask import Response, render_template_string, request  # noqa: F401
    from werkzeug.serving import make_server
except Exception:  # pragma: no cover
    Response = None
    render_template_string = None
    make_server = None


# --- HID keyboard usage table (USB HID Usage Tables, Keyboard/Keypad page) ---

# Modifier bitmask (report byte 0)
MOD_LCTRL = 0x01
MOD_LSHIFT = 0x02
MOD_LALT = 0x04
MOD_LGUI = 0x08

MODIFIER_WORDS = {
    "CTRL": MOD_LCTRL, "CONTROL": MOD_LCTRL,
    "SHIFT": MOD_LSHIFT,
    "ALT": MOD_LALT, "OPTION": MOD_LALT,
    "GUI": MOD_LGUI, "WINDOWS": MOD_LGUI, "WIN": MOD_LGUI,
    "COMMAND": MOD_LGUI, "CMD": MOD_LGUI, "META": MOD_LGUI, "SUPER": MOD_LGUI,
}

# Named (non-character) keys -> usage id
NAMED_KEYS = {
    "ENTER": 0x28, "RETURN": 0x28,
    "ESCAPE": 0x29, "ESC": 0x29,
    "BACKSPACE": 0x2A, "BKSP": 0x2A,
    "TAB": 0x2B,
    "SPACE": 0x2C,
    "CAPSLOCK": 0x39,
    "PRINTSCREEN": 0x46, "PRINTSCRN": 0x46, "PRTSCR": 0x46,
    "SCROLLLOCK": 0x47,
    "PAUSE": 0x48, "BREAK": 0x48,
    "INSERT": 0x49, "INS": 0x49,
    "HOME": 0x4A,
    "PAGEUP": 0x4B,
    "DELETE": 0x4C, "DEL": 0x4C,
    "END": 0x4D,
    "PAGEDOWN": 0x4E,
    "RIGHT": 0x4F, "RIGHTARROW": 0x4F,
    "LEFT": 0x50, "LEFTARROW": 0x50,
    "DOWN": 0x51, "DOWNARROW": 0x51,
    "UP": 0x52, "UPARROW": 0x52,
    "NUMLOCK": 0x53,
    "MENU": 0x65, "APP": 0x65,
    "F1": 0x3A, "F2": 0x3B, "F3": 0x3C, "F4": 0x3D, "F5": 0x3E, "F6": 0x3F,
    "F7": 0x40, "F8": 0x41, "F9": 0x42, "F10": 0x43, "F11": 0x44, "F12": 0x45,
}


def _build_char_map():
    """char -> (modifier_mask, usage_id) for printable US-ASCII."""
    m = {}
    # letters
    for i in range(26):
        lower = chr(ord("a") + i)
        usage = 0x04 + i
        m[lower] = (0, usage)
        m[lower.upper()] = (MOD_LSHIFT, usage)
    # top-row digits 1..9,0 and their shifted symbols
    digits = "1234567890"
    digit_usages = [0x1E, 0x1F, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27]
    shifted = ")!@#$%^&*("  # index 0 -> '0' is special; handled below
    for ch, usage in zip(digits, digit_usages):
        m[ch] = (0, usage)
    shift_for_digit = {
        "1": "!", "2": "@", "3": "#", "4": "$", "5": "%",
        "6": "^", "7": "&", "8": "*", "9": "(", "0": ")",
    }
    for digit, sym in shift_for_digit.items():
        m[sym] = (MOD_LSHIFT, m[digit][1])
    # the rest of the printable punctuation
    punct = {
        "-": (0, 0x2D), "_": (MOD_LSHIFT, 0x2D),
        "=": (0, 0x2E), "+": (MOD_LSHIFT, 0x2E),
        "[": (0, 0x2F), "{": (MOD_LSHIFT, 0x2F),
        "]": (0, 0x30), "}": (MOD_LSHIFT, 0x30),
        "\\": (0, 0x31), "|": (MOD_LSHIFT, 0x31),
        ";": (0, 0x33), ":": (MOD_LSHIFT, 0x33),
        "'": (0, 0x34), "\"": (MOD_LSHIFT, 0x34),
        "`": (0, 0x35), "~": (MOD_LSHIFT, 0x35),
        ",": (0, 0x36), "<": (MOD_LSHIFT, 0x36),
        ".": (0, 0x37), ">": (MOD_LSHIFT, 0x37),
        "/": (0, 0x38), "?": (MOD_LSHIFT, 0x38),
        " ": (0, 0x2C),
    }
    m.update(punct)
    return m


CHAR_TO_HID = _build_char_map()

# A single empty (all-keys-up) 8-byte report.
RELEASE = bytes(8)


class DuckyParseError(ValueError):
    """Raised with a 1-based line number for a payload that won't parse."""


def _report(modifier, usage):
    """An 8-byte HID keyboard report: [mods, reserved, k1..k6]."""
    return bytes([modifier & 0xFF, 0x00, usage & 0xFF, 0, 0, 0, 0, 0])


def char_to_reports(ch):
    """A printable char -> [keydown_report, RELEASE], or [] if unmappable.

    A '\\n' maps to ENTER. Unknown chars are skipped (caller decides whether
    to warn); this keeps one odd glyph from aborting a whole payload.
    """
    if ch == "\n":
        return [_report(0, NAMED_KEYS["ENTER"]), RELEASE]
    if ch == "\t":
        return [_report(0, NAMED_KEYS["TAB"]), RELEASE]
    pair = CHAR_TO_HID.get(ch)
    if pair is None:
        return []
    mod, usage = pair
    return [_report(mod, usage), RELEASE]


def _combo_to_report(tokens, line_no):
    """Turn a key-combo line's tokens (e.g. ['CTRL','ALT','DELETE'] or
    ['GUI','r'] or ['ENTER']) into a single (modifier, usage) report."""
    mod = 0
    key_usage = None
    for tok in tokens:
        up = tok.upper()
        if up in MODIFIER_WORDS:
            mod |= MODIFIER_WORDS[up]
            continue
        if key_usage is not None:
            raise DuckyParseError(
                f"line {line_no}: more than one non-modifier key in a combo "
                f"({tokens!r})"
            )
        if up in NAMED_KEYS:
            key_usage = NAMED_KEYS[up]
        elif len(tok) == 1 and tok in CHAR_TO_HID:
            ch_mod, ch_usage = CHAR_TO_HID[tok]
            mod |= ch_mod
            key_usage = ch_usage
        else:
            raise DuckyParseError(f"line {line_no}: unknown key {tok!r}")
    if key_usage is None:
        # modifiers-only (e.g. just 'GUI') - tap the modifier with no key
        return _report(mod, 0)
    return _report(mod, key_usage)


def parse_ducky(text, max_actions=None):
    """Parse a DuckyScript subset into a list of action dicts.

    Supported: REM/# comments, STRING, STRINGLN, ENTER/named keys, modifier
    combos (GUI r / CTRL ALT DELETE), DELAY <ms>, DEFAULTDELAY <ms>,
    DEFAULT_DELAY <ms>, REPEAT <n> (repeat previous line n times).

    Actions are dicts, one of:
      {"type": "string", "text": "..."}        # type literal text
      {"type": "key", "report": b"..."}         # one key/combo press+release
      {"type": "delay", "ms": N}                # sleep
      {"type": "defaultdelay", "ms": N}         # set inter-line delay
    Raises DuckyParseError (with a line number) on anything it can't parse.
    """
    actions = []
    last_line_tokens = None  # for REPEAT
    lines = text.splitlines()
    for idx, raw in enumerate(lines, start=1):
        line = raw.rstrip("\r\n")
        stripped = line.strip()
        if stripped == "" or stripped.startswith("#"):
            continue
        parts = stripped.split(" ", 1)
        cmd = parts[0].upper()
        rest = parts[1] if len(parts) > 1 else ""

        if cmd == "REM":
            continue
        if cmd == "REPEAT":
            try:
                n = int(rest.strip())
            except ValueError:
                raise DuckyParseError(f"line {idx}: REPEAT needs an integer")
            if last_line_tokens is None:
                raise DuckyParseError(f"line {idx}: REPEAT with no previous line")
            for _ in range(n):
                _apply_line(last_line_tokens, idx, actions)
            continue

        tokens = (cmd, rest)
        _apply_line(tokens, idx, actions)
        last_line_tokens = tokens

        if max_actions is not None and len(actions) > max_actions:
            raise DuckyParseError(
                f"line {idx}: payload exceeds max_actions ({max_actions})"
            )
    return actions


def _apply_line(tokens, line_no, actions):
    cmd, rest = tokens
    if cmd == "STRING":
        actions.append({"type": "string", "text": rest})
    elif cmd == "STRINGLN":
        actions.append({"type": "string", "text": rest})
        actions.append({"type": "key", "report": _report(0, NAMED_KEYS["ENTER"])})
    elif cmd in ("DELAY",):
        actions.append({"type": "delay", "ms": _int_arg(rest, line_no, "DELAY")})
    elif cmd in ("DEFAULTDELAY", "DEFAULT_DELAY"):
        actions.append({"type": "defaultdelay",
                        "ms": _int_arg(rest, line_no, cmd)})
    else:
        # a named key or a modifier combo: re-join the whole line's tokens
        combo = ([cmd] + rest.split()) if rest else [cmd]
        actions.append({"type": "key", "report": _combo_to_report(combo, line_no)})


def _int_arg(rest, line_no, name):
    try:
        return int(rest.strip())
    except ValueError:
        raise DuckyParseError(f"line {line_no}: {name} needs an integer (ms)")


def actions_to_reports(actions, default_delay_ms=0, modifier_settle_ms=40):
    """Flatten parsed actions into an ordered list of ('report', bytes) and
    ('delay', ms) tuples ready to stream to the HID device. Pure - no I/O.

    Two reliability measures bake in here (learned from a real-hardware test
    where a modifier key stuck and turned a payload into Win+<key> chaos):
      * a leading all-keys-up report + settle, so a payload never starts with a
        modifier left held from a previous fire or a half-enumerated gadget;
      * an explicit settle delay AFTER any key report that carried a modifier
        (Ctrl/Alt/Shift/GUI), so the host definitely processes the release
        before the next key - this is what stops a "stuck Windows key".
    """
    out = [("report", RELEASE), ("delay", max(30, modifier_settle_ms))]
    for act in actions:
        t = act["type"]
        if t == "string":
            for ch in act["text"]:
                for rep in char_to_reports(ch):
                    out.append(("report", rep))
            if default_delay_ms:
                out.append(("delay", default_delay_ms))
        elif t == "key":
            rep = act["report"]
            out.append(("report", rep))
            out.append(("report", RELEASE))
            # rep[0] is the modifier byte; if any modifier was held, give the
            # host extra time to register the release before the next key.
            if rep[0] != 0 and modifier_settle_ms:
                out.append(("delay", modifier_settle_ms))
            elif default_delay_ms:
                out.append(("delay", default_delay_ms))
        elif t == "delay":
            out.append(("delay", act["ms"]))
        elif t == "defaultdelay":
            default_delay_ms = act["ms"]
    # always end keys-up
    out.append(("report", RELEASE))
    return out

# ---------------------------------------------------------------------------
# token / exposure helpers (same contract as badhid_ng / web2ssh_ng)
# ---------------------------------------------------------------------------
PLACEHOLDER_TOKENS = frozenset({
    "", "changeme", "change_me", "token", "default", "secret", "password",
    "admin", "root", "pwnagotchi", "badhid", "test", "12345", "123456",
    "0000", "none", "null",
})
IPV4_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")


def _looks_like_placeholder(value):
    if value is None:
        return True
    return str(value).strip().lower() in PLACEHOLDER_TOKENS


def validate_token(token):
    if _looks_like_placeholder(token):
        return False, ("auth_token is missing/blank/placeholder - the BLE "
                       "control server will NOT start. Set a long random token.")
    if len(str(token).strip()) < 12:
        return False, "auth_token is shorter than 12 characters - refusing to start."
    return True, "ok"


def token_matches(configured, presented):
    if configured is None or presented is None:
        return False
    return hmac.compare_digest(str(configured), str(presented))


def resolve_bind_plan(bind_scope, port, detect_tailscale=None):
    """(bind_host, note, ok) - identical contract to badhid_ng."""
    def _detect():  # pragma: no cover
        import subprocess
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
    detect_tailscale = detect_tailscale or _detect
    scope = (bind_scope or "auto").strip().lower()
    if scope == "localhost":
        return "127.0.0.1", f"localhost only - http://127.0.0.1:{port}/", True
    if scope == "lan":
        return "0.0.0.0", f"LAN (0.0.0.0:{port}) - EXPLICIT broad exposure", True
    if scope == "tailscale":
        ip = detect_tailscale()
        if ip:
            return ip, f"tailscale only - http://{ip}:{port}/", True
        return None, "bind_scope='tailscale' but no tailscale iface - refusing (fail-safe).", False
    if scope == "auto":
        ip = detect_tailscale()
        if ip:
            return ip, f"auto -> tailscale - http://{ip}:{port}/", True
        return "127.0.0.1", f"auto -> localhost only - http://127.0.0.1:{port}/", True
    return "127.0.0.1", f"unknown bind_scope {scope!r}, using localhost", True


# NOTE: auth_token defaults to None on purpose - no working default.
DEFAULTS = {
    "enabled": False,
    "auth_token": None,
    "bind_scope": "auto",
    "port": 8085,                 # control API (distinct from wired badhid 8083)
    "payloads_dir": "/etc/pwnagotchi/badhid_ng/payloads",   # shares the wired suite's payloads
    "default_payload": "hello_world.duck",
    # BLE identity the target will see when pairing.
    "ble_device_name": "BadHID-KB",
    # arm model - identical to the wired suite.
    "arm_window_seconds": 120,
    "arm_one_shot": True,
    "fire_on_connect": False,      # fire the default payload when a host connects (armed only)
    # typing timing + the stuck-modifier fix carried over from the wired suite.
    "inter_key_delay_ms": 12,
    "default_delay_ms": 0,
    "modifier_settle_ms": 40,
    "max_actions": 20000,
    "allow_quickfire": True,
    "ui_enabled": True,
    "ui_position_x": -55,
    "ui_position_y": 20,
}


# ===========================================================================
# BLE HID transport  (the ONE part that needs on-hardware bring-up)
# ===========================================================================
#
# Everything above this line is the same tested engine as the wired suite.
# Below is the transport: it must present the pi as a BLE HID keyboard
# (HID-over-GATT, service 0x1812) and push 8-byte boot-keyboard reports over
# the HID Report characteristic to a paired host.
#
# This CANNOT be validated without a real Bluetooth radio, so it is isolated
# behind one small interface (`send_report`) exactly like the wired suite put
# the /dev/hidg0 write behind `_write_stream`. On-device bring-up is the test.
#
# Reference approach (see BLE_DESIGN.md for the full write-up):
#   * BlueZ >= 5.50 with D-Bus. python3-dbus + the BlueZ GATT server pattern
#     (org.bluez.GattManager1 / LEAdvertisingManager1), registering:
#       - HID Service (0x1812) with Report Map (boot keyboard), HID Info,
#         Protocol Mode, and a Report characteristic (notify) we write to.
#       - Device Information + Battery services (hosts expect them).
#   * Advertise as `ble_device_name`, appearance = keyboard (0x03C1).
#   * On connect+pair+subscribe, `send_report(bytes8)` -> notify the Report
#     characteristic. The 8-byte report format is IDENTICAL to the wired one
#     (modifier, reserved, 6 keycodes), so the engine's output is reused as-is.
#
# The class below detects whether the stack is usable and, until the GATT
# server is wired on-device, reports a clear "not ready" instead of pretending.


class BLEHidTransport:
    """Isolated BLE HID keyboard transport. send_report() is the single
    integration point the engine drives - mirror of the wired _write_stream."""

    def __init__(self, device_name="BadHID-KB"):
        self.device_name = device_name
        self._ready = False
        self._reason = "not started"
        self._server = None  # the GATT application object, once brought up

    def available(self):
        """True if the BlueZ/D-Bus Python stack is importable. Does not prove a
        radio or a paired host - just that the deps exist."""
        try:
            import dbus  # noqa: F401
            return True
        except Exception as exc:
            self._reason = f"python dbus/BlueZ not available: {exc}"
            return False

    def start(self):  # pragma: no cover - needs a real BT radio
        """Bring up the BLE HID peripheral and begin advertising.

        BRING-UP SURFACE: wire the BlueZ GATT HID server here (see BLE_DESIGN.md
        and setup_ble_hid.sh). Until that's done on-device this returns False
        with a clear reason, so a fire fails loudly instead of silently."""
        if not self.available():
            self._ready = False
            return False
        # --- on-device: construct + register the GATT HID application here ---
        # from .ble_gatt import HidApplication  # the GATT server (bring-up)
        # self._server = HidApplication(self.device_name); self._server.register()
        self._ready = False
        self._reason = ("BLE GATT HID server not yet wired on this device - run "
                        "setup_ble_hid.sh and complete the bring-up (see "
                        "BLE_DESIGN.md). The engine is ready; only this transport "
                        "needs hardware.")
        logging.warning("[badhid_ble_ng] %s", self._reason)
        return False

    def connected(self):  # pragma: no cover
        """True when a host is paired, connected, and subscribed to reports."""
        return bool(self._ready and self._server and getattr(self._server, "subscribed", False))

    def send_report(self, report_bytes):  # pragma: no cover - needs radio+host
        """Notify one 8-byte HID report to the paired host. Raises if not ready,
        so a fire reports a clear error (same discipline as the wired write)."""
        if not self._ready or self._server is None:
            raise RuntimeError(self._reason or "BLE HID transport not ready")
        self._server.notify_report(bytes(report_bytes))

    def stop(self):  # pragma: no cover
        if self._server is not None:
            try:
                self._server.unregister()
            except Exception:
                pass
        self._server = None
        self._ready = False

    def status(self):
        if self._ready and self.connected():
            return "connected"
        if self._ready:
            return "advertising"
        return "not-ready"


# ===========================================================================
# The plugin
# ===========================================================================

class BadHIDBLENG(plugins.Plugin):
    __author__ = "built for this project's wireless BadHID (BLE) track"
    __version__ = "0.1.0-pre"   # engine proven; BLE transport needs hardware bring-up
    __license__ = "GPL3"
    __description__ = ("Wireless BadHID: present the pi as a Bluetooth (BLE) HID "
                       "keyboard and type a payload you wrote into a PAIRED "
                       "device. Reuses the wired suite's DuckyScript engine; "
                       "BLE transport needs on-device bring-up.")

    def __init__(self):
        self._server = None
        self._server_thread = None
        self._lock = threading.Lock()
        self._armed_until = 0.0
        self._armed_forever = False
        self._fire_budget = 0
        self._last_fire = None
        self._bind_note = ""
        self._ble = BLEHidTransport()

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
        v = self._opt(key)
        if isinstance(v, bool):
            return v
        if v is None:
            return default
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    # --- lifecycle ---
    def on_loaded(self):
        logging.info("[badhid_ble_ng] loading")
        self._ble.device_name = self._opt("ble_device_name") or "BadHID-KB"
        if not self._ble.available():
            logging.warning("[badhid_ble_ng] BLE stack not available: %s "
                            "(install deps - see setup_ble_hid.sh)", self._ble._reason)
        else:
            self._ble.start()  # logs its own bring-up status
        self._start_control_server()
        logging.warning("[badhid_ble_ng] loaded (DISARMED). BLE status: %s",
                        self._ble.status())

    def on_unload(self, ui):
        self._disarm("unload")
        self._ble.stop()
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass
            self._server = None
        logging.info("[badhid_ble_ng] unloaded")

    # --- arm / disarm (identical model to the wired suite) ---
    def _arm(self, source):
        window = self._opt_int("arm_window_seconds", 120)
        with self._lock:
            if window <= 0:
                self._armed_forever = True
                self._armed_until = 0.0
            else:
                self._armed_forever = False
                self._armed_until = time.time() + window
            self._fire_budget = 1 if self._opt_bool("arm_one_shot", True) else 10**9
        logging.warning("[badhid_ble_ng] ARMED via %s", source)

    def _disarm(self, reason):
        with self._lock:
            was = self._is_armed_locked()
            self._armed_forever = False
            self._armed_until = 0.0
            self._fire_budget = 0
        if was:
            logging.warning("[badhid_ble_ng] DISARMED (%s)", reason)

    def _is_armed_locked(self):
        if self._fire_budget <= 0:
            return False
        if self._armed_forever:
            return True
        return time.time() < self._armed_until

    def is_armed(self):
        with self._lock:
            return self._is_armed_locked()

    # --- payloads (shared with the wired suite's payloads_dir) ---
    def list_payloads(self):
        d = self._opt("payloads_dir")
        try:
            return sorted(f for f in os.listdir(d)
                          if f.lower().endswith((".duck", ".txt")) and not f.startswith("."))
        except Exception:
            return []

    def _payload_path(self, name):
        if not name or "/" in name or "\\" in name or str(name).startswith("."):
            return None
        if not str(name).lower().endswith((".duck", ".txt")):
            return None
        d = os.path.abspath(self._opt("payloads_dir"))
        path = os.path.abspath(os.path.join(d, name))
        if os.path.commonpath([d, path]) != d:
            return None
        return path if os.path.isfile(path) else None

    # --- firing (same shape as wired; transport is BLE) ---
    def _fire(self, payload_name, source, target_label=None):
        if not self.is_armed():
            logging.warning("[badhid_ble_ng] fire REFUSED (disarmed): %s", payload_name)
            return False, "disarmed - arm first"
        if not self._ble.connected():
            return False, (f"no paired host connected (BLE status: {self._ble.status()}). "
                           "Pair a device you own first.")
        path = self._payload_path(payload_name)
        if path is None:
            return False, f"payload {payload_name!r} not found/allowed"
        try:
            text = open(path, "r", encoding="utf-8", errors="replace").read()
            actions = parse_ducky(text, max_actions=self._opt_int("max_actions", 20000))
        except DuckyParseError as exc:
            return False, f"parse error: {exc}"
        except Exception as exc:
            return False, f"could not read payload: {exc}"
        stream = actions_to_reports(
            actions, default_delay_ms=self._opt_int("default_delay_ms", 0),
            modifier_settle_ms=self._opt_int("modifier_settle_ms", 40))
        logging.warning("[badhid_ble_ng] FIRE payload=%s source=%s target=%s (BLE)",
                        payload_name, source, target_label or "(none)")
        try:
            self._send_stream(stream)
        except Exception as exc:
            return False, f"BLE send failed: {exc}"
        with self._lock:
            self._fire_budget -= 1
            self._last_fire = (payload_name, target_label, time.time())
        if self._opt_bool("arm_one_shot", True):
            self._disarm("one-shot fire complete")
        return True, f"fired {payload_name} ({len(stream)} events) over BLE"

    def _send_stream(self, stream):  # pragma: no cover - needs radio+host
        key_delay = self._opt_int("inter_key_delay_ms", 12) / 1000.0
        for kind, val in stream:
            if kind == "report":
                self._ble.send_report(val)
                if key_delay:
                    time.sleep(key_delay)
            elif kind == "delay":
                time.sleep(max(0, val) / 1000.0)
        self._ble.send_report(RELEASE)

    # --- control server (token + bind_scope) ---
    def _start_control_server(self):
        ok, msg = validate_token(self._opt("auth_token"))
        if not ok:
            logging.error("[badhid_ble_ng] %s", msg)
            return False
        if make_server is None:
            logging.error("[badhid_ble_ng] flask unavailable")
            return False
        port = self._opt_int("port", 8085)
        bind_host, note, bok = resolve_bind_plan(self._opt("bind_scope"), port)
        self._bind_note = note
        if not bok:
            logging.error("[badhid_ble_ng] %s", note)
            return False
        from flask import Flask
        app = Flask(__name__)
        app.add_url_rule("/", "root", self._http_root, methods=["GET"])
        app.add_url_rule("/arm", "arm", self._http_arm, methods=["POST"])
        app.add_url_rule("/disarm", "disarm", self._http_disarm, methods=["POST"])
        app.add_url_rule("/fire", "fire", self._http_fire, methods=["POST"])
        try:
            self._server = make_server(bind_host, port, app, threaded=True)
        except Exception as exc:
            logging.error("[badhid_ble_ng] could not bind %s:%s - %s", bind_host, port, exc)
            return False
        self._server_thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="badhid_ble_ng-http")
        self._server_thread.start()
        logging.warning("[badhid_ble_ng] control server up: %s", note)
        return True

    def _authed(self, req):
        presented = None
        try:
            auth = req.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                presented = auth[len("Bearer "):].strip()
            if presented is None:
                presented = req.headers.get("X-Auth-Token") or req.values.get("token")
        except Exception:
            presented = None
        return token_matches(self._opt("auth_token"), presented)

    def _http_root(self):  # pragma: no cover
        if not self._authed(request):
            return Response("unauthorized", status=401)
        return (f"BadHID BLE - armed={self.is_armed()} - BLE={self._ble.status()} - "
                f"payloads={len(self.list_payloads())} - bound: {self._bind_note}\n")

    def _http_arm(self):  # pragma: no cover
        if not self._authed(request):
            return Response("unauthorized", status=401)
        self._arm("web"); return Response("armed\n")

    def _http_disarm(self):  # pragma: no cover
        if not self._authed(request):
            return Response("unauthorized", status=401)
        self._disarm("web"); return Response("disarmed\n")

    def _http_fire(self):  # pragma: no cover
        if not self._authed(request):
            return Response("unauthorized", status=401)
        name = request.values.get("payload") or self._opt("default_payload")
        ok, msg = self._fire(name, "web", request.values.get("target"))
        return Response(msg + "\n", status=(200 if ok else 409))
