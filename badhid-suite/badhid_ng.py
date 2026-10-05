"""
badhid_ng.py - pwnagotchi plugin (runs ON the pi)

BadHIDNG is a USB HID keystroke-injection ("BadUSB") *framework* for
testing against the operator's OWN authorized lab hardware. It turns a
gadget-mode Pi into a USB keyboard that can type a payload the operator
wrote, on manual trigger, behind authentication, with a loud audit log.

It was built from the A1+A2 slice of this project's
`BADUSB_AND_REMOTE_EXEC_IDEAS` backlog doc:

  * A1 - the composite-gadget layer: present an HID keyboard
    (`/dev/hidg0`) ALONGSIDE the existing ethernet gadget so the pi
    keeps its `usb0` management link. Because reconfiguring the live
    gadget can kill `usb0` (your lifeline to the device), this plugin
    does NOT touch the gadget itself by default - it writes to an
    existing `/dev/hidg0` if one is present. A deliberately-run,
    heavily-warned `setup_composite_gadget.sh` (shipped alongside this
    file) is how you bring the composite gadget up; the plugin only
    uses it.
  * A2 - a DuckyScript-subset runner with a manual-fire web UI, an
    arm/disarm model, token auth, and `bind_scope`.

THE SAFETY LINE (non-negotiable - this is a lab instrument, not malware):

  * This file ships the FRAMEWORK and the SAFETY SCAFFOLDING, plus ONE
    harmless demo payloads (`payloads/*.duck`: a Hello World, a
    sinister-looking-but-inert Notepad skull, and a rickroll). It ships NO
    offensive payloads - no
    shells, credential grabbers, defender-disablers, persistence, or
    exfiltration. You author any real payloads yourself, in the
    `payloads_dir`, and point them only at hardware you own or have
    written authorization to test. That line is what keeps this
    defensible.
  * The `authorized_targets` list and the arm/disarm model are REAL
    controls over *when* the injector is live (so it never fires on a
    mere plug-in, and never silently). They are NOT, and cannot be, a
    control over *which machine* gets typed into: a USB keyboard is
    physically unable to tell what host it is plugged into. "Only my
    own gear" is therefore operator discipline, not something this code
    can enforce - the list is an audit/intent aid, logged on every
    fire, nothing more. The NOTES.md says this plainly too; do not read
    the allowlist as a technical guarantee.
  * Legality note: HID injection is perfectly legal against your own
    equipment. The entire liability is in *what you physically plug the
    device into* - which is exactly why the default is disarmed,
    manual-only, auth-required, and loud.

Framework facts this plugin relies on (verified against the
jayofelony/pwnagotchi fork's `pwnagotchi/plugins/__init__.py`, the same
file every other suite in this repo is built against):
  - `load_from_file()` registers a plugin under its FILE basename,
    case-sensitive: this file is `badhid_ng.py`, so its config section
    MUST be `[main.plugins.badhid_ng]` - not the class name, not
    anything else, or the loader never lists it as "enabled" and it
    silently never loads.
  - `plugins.load()` does `plugin.options =
    config['main']['plugins'][name]` verbatim and NEVER merges
    `__defaults__`. Every option is read through `_opt()/_opt_int()/
    _opt_bool()` against the module-level `DEFAULTS` dict below, never
    bare `self.options[...]`.
  - `Plugin.__init_subclass__` constructs with `cls()` (zero args), so
    this plugin takes no `__init__` arguments.
  - Real hooks only: `on_loaded(self)` (called SYNCHRONOUSLY during
    load - it must never block, so the web server runs in a background
    thread), `on_unload(self, ui)`, `on_webhook(self, path, request)`.

Naming: snake_case file `badhid_ng.py` -> section
`[main.plugins.badhid_ng]`, matching sigstr_ng/web2ssh_ng/etc. (not the
MadHatterNG CamelCase one-off).
"""

import hmac
import logging
import os
import re
import threading
import time

import pwnagotchi.plugins as plugins

try:
    from flask import Response, jsonify, render_template_string, request  # noqa: F401
    from werkzeug.serving import make_server
except Exception:  # pragma: no cover - flask/werkzeug always present on-device
    Response = None
    jsonify = None
    render_template_string = None
    make_server = None

# On-screen status element (optional; absent in the test sandbox). Guarded so
# the module imports without the full pwnagotchi.ui package present.
try:
    import pwnagotchi.ui.fonts as fonts
    from pwnagotchi.ui.components import LabeledValue
    from pwnagotchi.ui.view import BLACK
    _UI_AVAILABLE = True
except Exception:  # pragma: no cover - ui always present on-device
    fonts = None
    LabeledValue = None
    BLACK = 0
    _UI_AVAILABLE = False

ELEMENT_NAME = "badhid"


# ---------------------------------------------------------------------------
# Options (read via _opt* against this dict - the fork never merges __defaults__)
# ---------------------------------------------------------------------------
# NOTE: auth_token defaults to None ON PURPOSE. There is NO working default
# token anywhere in this file - a missing/blank/obviously-default token means
# the web control surface refuses to start, exactly like web2ssh_ng refuses a
# default credential pair.
DEFAULTS = {
    "enabled": False,

    # >>> USER INPUT REQUIRED <<< - no working default is shipped. A missing,
    # blank, or obviously-default value means the control server refuses to
    # start (see _looks_like_placeholder / validate_token).
    "auth_token": None,

    # Where your payloads live. Only *.duck / *.txt files here are listable
    # and fireable. This suite ships three harmless demos here.
    "payloads_dir": "/etc/pwnagotchi/badhid_ng/payloads",

    # The HID gadget device the composite gadget exposes. The plugin only
    # WRITES to this; it does not create it. Run setup_composite_gadget.sh
    # (shipped with this suite) deliberately to bring the gadget up.
    "hid_device": "/dev/hidg0",

    # The plugin NEVER reconfigures the USB gadget itself by default, because
    # doing so can drop your usb0 management link. Leave this false; the
    # gadget is managed by the setup script you run by hand.
    "manage_gadget": False,

    # Control-server exposure. Same semantics as web2ssh_ng/handshaker: one of
    # "auto" (tailscale IP if present else localhost-only), "tailscale"
    # (require tailscale or refuse to start), "localhost", or "lan" (0.0.0.0,
    # explicit opt-in). The exact URL is always logged and shown on the page.
    "bind_scope": "auto",
    "port": 8083,

    # Advisory audit list of targets you intend to test (labels, BSSIDs,
    # hostnames - free text). EMPTY BY DEFAULT. This is logged on every fire
    # as a record of intent; it is NOT and cannot be a technical restriction
    # on which host gets typed into (a USB keyboard can't know). See the
    # module docstring + NOTES.md.
    "authorized_targets": [],

    # Arming. The injector only fires while ARMED, and is DISARMED by default.
    # arm_window_seconds: when you arm, how long the armed window lasts before
    # auto-disarm (0 = until you disarm or one fire, per arm_one_shot).
    "arm_window_seconds": 120,
    # arm_one_shot: if true (default), the first successful fire disarms again.
    "arm_one_shot": True,
    # fire_on_enumerate: if true AND armed, fire the default payload as soon as
    # a host enumerates the gadget. Default FALSE - manual fire only. This is
    # the one that turns it into a "fires when plugged in" device, so it is
    # off unless you deliberately turn it on AND arm.
    "fire_on_enumerate": False,
    # The payload fired by fire_on_enumerate / the UI "fire default" button.
    "default_payload": "hello_world.duck",

    # Typing timing. inter_key_delay_ms slows typing so fast hosts don't drop
    # keystrokes; default_delay_ms is the implicit DELAY between payload lines.
    # (12ms is a safe default; drop to 5 for speed on a host that keeps up.)
    "inter_key_delay_ms": 12,
    "default_delay_ms": 0,
    # Extra pause after a key that used a modifier (Ctrl/Alt/Shift/GUI), so the
    # host registers the release before the next key - prevents a "stuck
    # Windows key" turning a payload into Win+<key> shortcuts.
    "modifier_settle_ms": 40,
    # Hard cap on a single payload's parsed action count, so a runaway/huge
    # file can't type forever.
    "max_actions": 20000,
    # If no host reads the HID gadget within this many seconds (e.g. the pi
    # isn't plugged into a powered/awake target), a fire aborts with a clear
    # error instead of hanging forever.
    "write_timeout_seconds": 10,

    # One-tap "quick fire" from the web page: a single button that arms AND
    # fires a payload in one action (convenience for a phone). It still needs
    # the auth token. Set false to force the deliberate two-step arm-then-fire.
    "allow_quickfire": True,

    # Allow a token-authenticated client (e.g. the fleet controller, B3) to
    # upload a payload to payloads_dir via POST /stage. The upload is path-safe
    # (payloads_dir only) and parse-validated before it's written. Set false to
    # refuse remote staging entirely (payloads then only arrive via the pi's
    # filesystem).
    "allow_remote_stage": True,

    # On-screen one-glyph status (optional; monochrome-safe for the TFT).
    "ui_enabled": True,
    "ui_position_x": -55,
    "ui_position_y": 10,
}

# Values that are clearly "left the placeholder in" rather than a real chosen
# token. Format/heuristic check only - its job is to close off a server coming
# up with a guessable token, not to score token strength.
PLACEHOLDER_TOKENS = frozenset({
    "", "changeme", "change_me", "change-me", "token", "default", "secret",
    "password", "admin", "root", "pwnagotchi", "badhid", "test", "12345",
    "123456", "0000", "none", "null",
})

IPV4_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")


# ===========================================================================
# Pure / testable helpers  (no device, no network, no framework needed)
# ===========================================================================

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


# --- token / exposure helpers ---------------------------------------------

def _looks_like_placeholder(value):
    if value is None:
        return True
    return str(value).strip().lower() in PLACEHOLDER_TOKENS


def validate_token(token):
    """(ok, message). The single choke point every configured token passes
    before the control server is allowed to start."""
    if _looks_like_placeholder(token):
        return False, (
            "auth_token is missing, blank, or an obvious placeholder - "
            "the BadHID control server will NOT start. Set a real random "
            "token in [main.plugins.badhid_ng].auth_token."
        )
    if len(str(token).strip()) < 12:
        return False, (
            "auth_token is shorter than 12 characters - refusing to start. "
            "Use a long random token."
        )
    return True, "ok"


def token_matches(configured, presented):
    """Constant-time compare; both may be None."""
    if configured is None or presented is None:
        return False
    return hmac.compare_digest(str(configured), str(presented))


def _detect_tailscale_ip():  # pragma: no cover - environment dependent
    """Best-effort tailscale IPv4, or None. Isolated so resolve_bind_plan is
    testable with an injected detector."""
    try:
        import subprocess
        out = subprocess.run(
            ["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5
        ).stdout
        m = IPV4_RE.search(out or "")
        if m:
            return m.group(1)
    except Exception:
        pass
    try:
        import subprocess
        out = subprocess.run(
            ["ip", "-4", "addr", "show", "tailscale0"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        m = IPV4_RE.search(out or "")
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def resolve_bind_plan(bind_scope, port, detect_tailscale=None):
    """(bind_host, note, ok). Mirrors web2ssh_ng/handshaker semantics.

    - auto:      tailscale IP if detected, else 127.0.0.1 (ok=True either way)
    - tailscale: tailscale IP, or ok=False (refuse to start) if none
    - localhost: 127.0.0.1
    - lan:       0.0.0.0 (explicit, logged opt-in)
    Unknown scope -> treated as localhost with a note.
    """
    if detect_tailscale is None:
        detect_tailscale = _detect_tailscale_ip
    scope = (bind_scope or "auto").strip().lower()

    if scope == "localhost":
        return "127.0.0.1", f"localhost only - http://127.0.0.1:{port}/", True
    if scope == "lan":
        return "0.0.0.0", (
            f"LAN/all interfaces (0.0.0.0:{port}) - EXPLICIT broad exposure"
        ), True
    if scope == "tailscale":
        ip = detect_tailscale()
        if ip:
            return ip, f"tailscale only - http://{ip}:{port}/", True
        return None, (
            "bind_scope='tailscale' but no tailscale interface found - "
            "refusing to start (fail-safe, no silent fallback)."
        ), False
    if scope == "auto":
        ip = detect_tailscale()
        if ip:
            return ip, f"auto -> tailscale - http://{ip}:{port}/", True
        return "127.0.0.1", (
            f"auto -> no tailscale, localhost only - http://127.0.0.1:{port}/ "
            "(reach it via an SSH tunnel)"
        ), True
    return "127.0.0.1", f"unknown bind_scope {scope!r}, using localhost", True


# --- gadget setup script (generated; the plugin never runs it itself) ------

def gadget_setup_script_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "setup_composite_gadget.sh")


# ===========================================================================
# The plugin
# ===========================================================================

class BadHIDNG(plugins.Plugin):
    __author__ = "built for this project's offensive-tooling backlog (A1+A2)"
    __version__ = "0.2.0"
    __license__ = "GPL3"
    __description__ = (
        "USB HID keystroke-injection framework for your own authorized lab "
        "hardware: composite-gadget HID, DuckyScript-subset runner, "
        "arm/disarm + token auth + bind_scope, loud audit log. Ships no "
        "offensive payloads."
    )

    def __init__(self):
        self._server = None
        self._server_thread = None
        self._lock = threading.Lock()
        self._armed_until = 0.0      # epoch seconds; 0 = disarmed
        self._armed_forever = False  # arm_window_seconds == 0 and armed
        self._fire_budget = 0        # remaining fires while armed (one_shot)
        self._last_fire = None       # (payload, target, ts) for the UI/log
        self._bind_note = ""
        self._bind_url = ""

    # --- option readers (fork never merges __defaults__) ---
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

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def on_loaded(self):
        logging.info("[badhid_ng] loading")

        # Audit list is advisory only - say so loudly if it's empty, like the
        # authorized_networks convention does.
        targets = self._opt("authorized_targets") or []
        if not targets:
            logging.warning(
                "[badhid_ng] authorized_targets is EMPTY - this is only an "
                "audit/intent record (a USB keyboard cannot verify the host "
                "it types into); it does not restrict anything. Default state "
                "is DISARMED + manual-fire only."
            )
        else:
            logging.info("[badhid_ng] authorized_targets (advisory): %s",
                         ", ".join(str(t) for t in targets))

        self._ensure_payloads_dir()

        if self._opt_bool("manage_gadget"):
            logging.warning(
                "[badhid_ng] manage_gadget=true is NOT supported by this "
                "plugin on purpose - reconfiguring the live USB gadget can "
                "drop your usb0 link. Run setup_composite_gadget.sh by hand "
                "instead (%s). Ignoring and continuing.",
                gadget_setup_script_path(),
            )

        hid = self._opt("hid_device")
        if not os.path.exists(hid):
            logging.warning(
                "[badhid_ng] HID device %s not present - the composite gadget "
                "isn't up yet. The control UI will still run, but a fire will "
                "fail until you run setup_composite_gadget.sh. See README.",
                hid,
            )

        ok = self._start_control_server()
        if ok:
            logging.warning(
                "[badhid_ng] control server up: %s (DISARMED). Arming, and "
                "every fire, are logged here at WARNING.", self._bind_url or self._bind_note
            )

    def on_unload(self, ui):
        self._disarm("plugin unload")
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
        logging.info("[badhid_ng] unloaded")

    # ------------------------------------------------------------------
    # on-screen status (optional; monochrome-safe for the small TFT)
    # ------------------------------------------------------------------
    def on_ui_setup(self, ui):
        if not (_UI_AVAILABLE and self._opt_bool("ui_enabled", True)):
            return
        try:
            cx = self._opt_int("ui_position_x", -55)
            # negative x = that many px in from the right edge (repo convention)
            pos_x = ui.width() + cx if cx < 0 else cx
            pos_x = max(0, min(pos_x, max(0, ui.width() - 10)))
            pos_y = self._opt_int("ui_position_y", 10)
            ui.add_element(ELEMENT_NAME, LabeledValue(
                color=BLACK,
                label="BadHID",
                value="off",
                position=(pos_x, pos_y),
                label_font=fonts.Bold,
                text_font=fonts.Medium,
            ))
        except Exception as exc:
            logging.error("[badhid_ng] UI setup failed: %r", exc)

    def on_ui_update(self, ui):
        if not (_UI_AVAILABLE and self._opt_bool("ui_enabled", True)):
            return
        try:
            if self.is_armed():
                text = "ARMED"
            elif not os.path.exists(self._opt("hid_device")):
                text = "no-dev"
            else:
                text = "ready"
            ui.set(ELEMENT_NAME, text)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # arm / disarm
    # ------------------------------------------------------------------
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
        logging.warning(
            "[badhid_ng] ARMED via %s (window=%ss, one_shot=%s). Fires are "
            "now possible until disarm/expiry.",
            source, window, self._opt_bool("arm_one_shot", True),
        )

    def _disarm(self, reason):
        with self._lock:
            was = self._is_armed_locked()
            self._armed_forever = False
            self._armed_until = 0.0
            self._fire_budget = 0
        if was:
            logging.warning("[badhid_ng] DISARMED (%s)", reason)

    def _is_armed_locked(self):
        if self._fire_budget <= 0:
            return False
        if self._armed_forever:
            return True
        return time.time() < self._armed_until

    def is_armed(self):
        with self._lock:
            return self._is_armed_locked()

    # ------------------------------------------------------------------
    # firing
    # ------------------------------------------------------------------
    def _fire(self, payload_name, source, target_label=None):
        """Parse + stream a payload. Returns (ok, message). Only fires while
        armed; records and loudly logs every attempt."""
        if not self.is_armed():
            logging.warning("[badhid_ng] fire REFUSED (disarmed): %s via %s",
                            payload_name, source)
            return False, "disarmed - arm first"

        path = self._payload_path(payload_name)
        if path is None:
            return False, f"payload {payload_name!r} not found/allowed"

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            actions = parse_ducky(text, max_actions=self._opt_int("max_actions", 20000))
        except DuckyParseError as exc:
            logging.warning("[badhid_ng] fire parse error (%s): %s",
                            payload_name, exc)
            return False, f"parse error: {exc}"
        except Exception as exc:
            return False, f"could not read payload: {exc}"

        stream = actions_to_reports(
            actions,
            default_delay_ms=self._opt_int("default_delay_ms", 0),
            modifier_settle_ms=self._opt_int("modifier_settle_ms", 40))

        targets = self._opt("authorized_targets") or []
        logging.warning(
            "[badhid_ng] FIRE payload=%s source=%s recorded_target=%s "
            "advisory_authorized_list=%s  (reminder: the list is intent only; "
            "verify by hand what this device is physically plugged into)",
            payload_name, source, target_label or "(none given)",
            (", ".join(str(t) for t in targets) or "EMPTY"),
        )

        try:
            self._write_stream(stream)
        except FileNotFoundError:
            return False, (f"HID device {self._opt('hid_device')} missing - "
                           "run setup_composite_gadget.sh first")
        except PermissionError:
            return False, "permission denied writing HID device (run as root)"
        except Exception as exc:
            return False, f"write failed: {exc}"

        with self._lock:
            self._fire_budget -= 1
            self._last_fire = (payload_name, target_label, time.time())
        if self._opt_bool("arm_one_shot", True):
            self._disarm("one-shot fire complete")
        return True, f"fired {payload_name} ({len(stream)} events)"

    def _write_stream(self, stream):  # pragma: no cover - needs real /dev/hidg0
        """Stream reports to the HID device. Isolated so tests can mock it and
        so the device write is the ONLY part that needs real hardware.

        Opens /dev/hidg0 NON-BLOCKING with a per-write deadline. If no host is
        reading the gadget (e.g. the pi isn't plugged into a powered, enumerated
        target), a plain blocking write would hang forever; instead each report
        waits up to write_timeout_seconds for the device to become writable and
        then raises a clear TimeoutError, so a fire while unplugged fails fast
        with a useful message instead of wedging the request thread."""
        import errno
        import select

        hid = self._opt("hid_device")
        key_delay = self._opt_int("inter_key_delay_ms", 5) / 1000.0
        deadline = max(1, self._opt_int("write_timeout_seconds", 10))

        fd = os.open(hid, os.O_WRONLY | os.O_NONBLOCK)
        try:
            def _put(buf):
                remaining = buf
                end = time.time() + deadline
                while remaining:
                    timeout = end - time.time()
                    if timeout <= 0:
                        raise TimeoutError(
                            "HID device not accepting input - is the pi plugged "
                            "into a powered, awake target that has enumerated the "
                            "keyboard? (no host read the gadget within "
                            f"{deadline}s)")
                    _, wr, _ = select.select([], [fd], [], timeout)
                    if not wr:
                        continue
                    try:
                        n = os.write(fd, remaining)
                        remaining = remaining[n:]
                    except OSError as exc:
                        if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                            continue
                        raise

            for kind, val in stream:
                if kind == "report":
                    _put(val)
                    if key_delay:
                        time.sleep(key_delay)
                elif kind == "delay":
                    time.sleep(max(0, val) / 1000.0)
            _put(RELEASE)
        finally:
            os.close(fd)

    # ------------------------------------------------------------------
    # payload directory
    # ------------------------------------------------------------------
    def _ensure_payloads_dir(self):
        d = self._opt("payloads_dir")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception as exc:
            logging.warning("[badhid_ng] could not create payloads_dir %s: %s",
                            d, exc)

    def list_payloads(self):
        d = self._opt("payloads_dir")
        try:
            names = sorted(
                f for f in os.listdir(d)
                if f.lower().endswith((".duck", ".txt")) and not f.startswith(".")
            )
        except Exception:
            names = []
        return names

    def _payload_path(self, name):
        """Resolve a payload name safely inside payloads_dir (no traversal)."""
        p = self._stage_target(name)
        return p if (p and os.path.isfile(p)) else None

    def _stage_target(self, name):
        """Safe absolute path for a payload NAME inside payloads_dir (the file
        need not exist yet - used for staging). None if the name is unsafe."""
        if not name or "/" in name or "\\" in name or str(name).startswith("."):
            return None
        if not str(name).lower().endswith((".duck", ".txt")):
            return None
        d = os.path.abspath(self._opt("payloads_dir"))
        path = os.path.abspath(os.path.join(d, name))
        if os.path.commonpath([d, path]) != d:
            return None
        return path

    def stage_payload(self, name, content):
        """Validate + write an uploaded payload into payloads_dir. Returns
        (result_dict, http_status). Gated by allow_remote_stage; path-safe;
        parse-validated. Pure enough to unit-test (only writes a file)."""
        if not self._opt_bool("allow_remote_stage", True):
            return {"ok": False, "error": "remote staging disabled (allow_remote_stage=false)"}, 403
        path = self._stage_target(name)
        if path is None:
            return {"ok": False, "error": "invalid payload name (payloads_dir only, .duck/.txt)"}, 400
        if content is None or not str(content).strip():
            return {"ok": False, "error": "empty payload content"}, 400
        try:
            parse_ducky(str(content), max_actions=self._opt_int("max_actions", 20000))
        except DuckyParseError as exc:
            return {"ok": False, "error": f"payload won't parse: {exc}"}, 400
        try:
            self._ensure_payloads_dir()
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(str(content))
        except Exception as exc:
            return {"ok": False, "error": f"write failed: {exc}"}, 500
        logging.warning("[badhid_ng] STAGED payload %s (%d bytes)",
                        os.path.basename(path), len(str(content)))
        return {"ok": True, "name": os.path.basename(path)}, 200

    # ------------------------------------------------------------------
    # control server
    # ------------------------------------------------------------------
    def _start_control_server(self):
        token = self._opt("auth_token")
        ok, msg = validate_token(token)
        if not ok:
            logging.error("[badhid_ng] %s", msg)
            return False

        if make_server is None:
            logging.error("[badhid_ng] flask/werkzeug unavailable - no UI")
            return False

        port = self._opt_int("port", 8083)
        bind_host, note, bok = resolve_bind_plan(self._opt("bind_scope"), port)
        self._bind_note = note
        if not bok:
            logging.error("[badhid_ng] %s", note)
            return False
        self._bind_url = note

        from flask import Flask
        app = Flask(__name__)
        app.add_url_rule("/", "root", self._http_root, methods=["GET"])
        app.add_url_rule("/arm", "arm", self._http_arm, methods=["POST"])
        app.add_url_rule("/disarm", "disarm", self._http_disarm, methods=["POST"])
        app.add_url_rule("/fire", "fire", self._http_fire, methods=["POST"])
        app.add_url_rule("/quickfire", "quickfire", self._http_quickfire, methods=["POST"])
        app.add_url_rule("/stage", "stage", self._http_stage, methods=["POST"])

        try:
            self._server = make_server(bind_host, port, app, threaded=True)
        except Exception as exc:
            logging.error("[badhid_ng] could not bind %s:%s - %s",
                          bind_host, port, exc)
            return False
        self._server_thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="badhid_ng-http")
        self._server_thread.start()
        logging.warning("[badhid_ng] control server bound: %s", note)
        return True

    # Also accept pwnagotchi's native on_webhook so it works mounted under the
    # built-in web UI as /plugins/badhid_ng/... (in addition to the standalone
    # bound server above).
    def on_webhook(self, path, request):  # pragma: no cover - needs flask req
        if not self._authed(request):
            return Response("unauthorized", status=401)
        p = (path or "").strip("/")
        if p in ("", "index"):
            return self._http_root()
        if p == "arm" and request.method == "POST":
            return self._http_arm()
        if p == "disarm" and request.method == "POST":
            return self._http_disarm()
        if p == "fire" and request.method == "POST":
            return self._http_fire()
        if p == "quickfire" and request.method == "POST":
            return self._http_quickfire()
        if p == "stage" and request.method == "POST":
            return self._http_stage()
        return Response("not found", status=404)

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

    # --- handlers (small; render via render_template_string) ---
    def _token_for_page(self):
        # the token the page uses to build its form links (from the request)
        try:
            t = request.values.get("token")
            if t is None:
                auth = request.headers.get("Authorization", "")
                if auth.startswith("Bearer "):
                    t = auth[len("Bearer "):].strip()
            return t or ""
        except Exception:
            return ""

    def _render(self, flash=None):
        armed = self.is_armed()
        hid = self._opt("hid_device")
        return render_template_string(
            _PAGE, armed=armed, payloads=self.list_payloads(), bind=self._bind_note,
            hid=hid, hid_ready=os.path.exists(hid), last=self._last_fire,
            targets=self._opt("authorized_targets") or [],
            default_payload=self._opt("default_payload"),
            token=self._token_for_page(),
            allow_quickfire=self._opt_bool("allow_quickfire", True),
            flash=flash,
        )

    def _http_root(self):  # pragma: no cover - flask rendering
        if not self._authed(request):
            return Response("unauthorized", status=401)
        return self._render()

    def _wants_html(self):
        # a browser form post -> re-render the page; a CLI/curl -> plain text
        try:
            acc = request.headers.get("Accept", "")
            return "text/html" in acc
        except Exception:
            return False

    def _reply(self, msg, ok, flash):
        if self._wants_html():
            return self._render(flash=flash)
        return Response(msg + "\n", status=(200 if ok else 409))

    def _http_arm(self):  # pragma: no cover
        if not self._authed(request):
            return Response("unauthorized", status=401)
        self._arm("web UI")
        return self._reply("armed", True, "Armed.")

    def _http_disarm(self):  # pragma: no cover
        if not self._authed(request):
            return Response("unauthorized", status=401)
        self._disarm("web UI")
        return self._reply("disarmed", True, "Disarmed.")

    def _http_fire(self):  # pragma: no cover
        if not self._authed(request):
            return Response("unauthorized", status=401)
        name = request.values.get("payload") or self._opt("default_payload")
        target = request.values.get("target")
        ok, msg = self._fire(name, "web UI", target_label=target)
        return self._reply(msg, ok, msg)

    def _http_stage(self):  # pragma: no cover
        # upload a payload to payloads_dir (path-safe + parse-validated, gated)
        if not self._authed(request):
            return Response("unauthorized", status=401)
        body = {}
        try:
            if request.is_json:
                body = request.get_json(silent=True) or {}
        except Exception:
            body = {}
        name = request.values.get("name") or body.get("name")
        content = request.values.get("content") or body.get("content")
        res, status = self.stage_payload(name, content)
        return jsonify(res), status

    def _http_quickfire(self):  # pragma: no cover
        # one-tap: arm + fire in a single action (still token-gated)
        if not self._authed(request):
            return Response("unauthorized", status=401)
        if not self._opt_bool("allow_quickfire", True):
            return self._reply("quickfire disabled (allow_quickfire=false)", False,
                               "One-tap fire is disabled in config.")
        name = request.values.get("payload") or self._opt("default_payload")
        target = request.values.get("target") or "web one-tap"
        self._arm("web UI one-tap")
        ok, msg = self._fire(name, "web UI one-tap", target_label=target)
        return self._reply(msg, ok, msg)


# Mobile-friendly, JS-free control page. The token travels in a hidden field on
# every form (and in ?token= for the page link), so one tap works from a phone.
_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BadHID control</title>
<style>
 :root{color-scheme:light dark}
 body{font-family:system-ui,-apple-system,sans-serif;max-width:32em;margin:0 auto;padding:16px;line-height:1.4}
 h2{margin:.2em 0}
 .state{font-size:1.4em;font-weight:700;padding:.3em .6em;border-radius:8px;display:inline-block}
 .armed{background:#b00020;color:#fff}
 .idle{background:#e0e0e0;color:#222}
 .flash{background:#fff7d6;border:1px solid #e3cf6b;padding:.5em .7em;border-radius:8px;margin:.6em 0;word-break:break-word}
 button{font-size:1.05em;padding:.7em 1em;border-radius:10px;border:1px solid #888;background:#f4f4f4;cursor:pointer}
 button:active{transform:translateY(1px)}
 .row{display:flex;gap:.5em;align-items:center;justify-content:space-between;border-bottom:1px solid #ccc;padding:.45em 0}
 .name{font-family:monospace}
 .fire{background:#1565c0;color:#fff;border-color:#0d47a1}
 .arm{background:#2e7d32;color:#fff;border-color:#1b5e20}
 .disarm{background:#616161;color:#fff}
 form{margin:0}
 small{color:#777}
 @media (prefers-color-scheme:dark){.idle{background:#333;color:#eee}.flash{background:#3a360f;border-color:#6b5e2b}button{background:#2a2a2a;color:#eee;border-color:#555}}
</style></head><body>
<h2>BadHID control</h2>
<p><span class="state {{ 'armed' if armed else 'idle' }}">{{ 'ARMED' if armed else 'DISARMED' }}</span></p>
{% if flash %}<div class="flash">{{ flash }}</div>{% endif %}
<p><small>HID: {{ 'ready' if hid_ready else 'NOT PRESENT - run setup_composite_gadget.sh' }} &middot; {{ bind }}</small></p>
{% if last %}<p><small>Last fire: {{ last[0] }} (target: {{ last[1] or 'none' }})</small></p>{% endif %}

<div style="display:flex;gap:.5em;margin:.6em 0">
  <form method="post" action="arm?token={{ token|urlencode }}"><input type="hidden" name="token" value="{{ token }}"><button class="arm">ARM</button></form>
  <form method="post" action="disarm?token={{ token|urlencode }}"><input type="hidden" name="token" value="{{ token }}"><button class="disarm">DISARM</button></form>
</div>

<h3>Tap to fire</h3>
<p><small>{% if allow_quickfire %}One tap arms &amp; fires. {% else %}Two-step: ARM first, then Fire. {% endif %}Only plug into hardware you own or are authorized to test.</small></p>
{% for p in payloads %}
<div class="row">
  <span class="name">{{ p }}</span>
  <form method="post" action="{{ 'quickfire' if allow_quickfire else 'fire' }}?token={{ token|urlencode }}">
    <input type="hidden" name="token" value="{{ token }}">
    <input type="hidden" name="payload" value="{{ p }}">
    <input type="hidden" name="target" value="web">
    <button class="fire">{{ 'FIRE' if allow_quickfire else 'FIRE (armed)' }}</button>
  </form>
</div>
{% endfor %}
{% if not payloads %}<p>(no payloads in payloads_dir)</p>{% endif %}

<p><small>Advisory authorized_targets: {{ targets|join(', ') if targets else 'EMPTY (audit-only, not a technical restriction - a USB keyboard cannot verify the host it types into)' }}</small></p>
</body></html>"""
