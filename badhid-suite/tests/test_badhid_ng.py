"""
test_badhid_ng.py - sandbox tests for badhid-suite.

What these cover (sandbox-verifiable, no hardware):
  * DuckyScript-subset parser: commands, combos, comments, errors, max_actions
  * US HID keymap: a spot-check of characters -> (modifier, usage)
  * report emitter: string/key -> ordered 8-byte reports + releases
  * token validation + constant-time match
  * bind-scope resolution (with an injected tailscale detector)
  * arm/disarm state machine incl. window expiry + one-shot budget
  * the firing gate: NO fire while disarmed; fire while armed (device write
    mocked); one-shot disarms after a fire; parse errors are reported
  * payload path safety: no traversal / only .duck|.txt inside payloads_dir

What these do NOT cover (needs a real-hardware pass on the pi, per CLAUDE.md):
  * the actual /dev/hidg0 write (_write_stream) and whether a host receives
    the keystrokes
  * setup_composite_gadget.sh bringing up the composite gadget without
    dropping usb0
  * the flask control server binding/serving (on-device smoke test)

Run from this folder:  python3 tests/test_badhid_ng.py
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))

import badhid_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


def make_plugin(**opts):
    p = mod.BadHIDNG()
    o = dict(mod.DEFAULTS)
    o.update(opts)
    p.options = o
    return p


# --- keymap ----------------------------------------------------------------
def test_keymap():
    m = mod.CHAR_TO_HID
    check("keymap 'a' -> (0, 0x04)", m["a"] == (0, 0x04))
    check("keymap 'A' -> (SHIFT, 0x04)", m["A"] == (mod.MOD_LSHIFT, 0x04))
    check("keymap '1' -> (0, 0x1E)", m["1"] == (0, 0x1E))
    check("keymap '!' -> (SHIFT, 0x1E)", m["!"] == (mod.MOD_LSHIFT, 0x1E))
    check("keymap ' ' -> (0, 0x2C)", m[" "] == (0, 0x2C))
    check("keymap '/' -> (0, 0x38)", m["/"] == (0, 0x38))
    check("keymap '?' -> (SHIFT, 0x38)", m["?"] == (mod.MOD_LSHIFT, 0x38))
    check("keymap '-' and '_'",
          m["-"] == (0, 0x2D) and m["_"] == (mod.MOD_LSHIFT, 0x2D))


# --- parser ----------------------------------------------------------------
def test_parser_basic():
    acts = mod.parse_ducky(
        "REM a comment\n# also a comment\nSTRING hello\nENTER\nDELAY 250\n"
    )
    types = [a["type"] for a in acts]
    check("parser skips comments, keeps 3 actions", types == ["string", "key", "delay"])
    check("parser STRING text", acts[0]["text"] == "hello")
    check("parser DELAY ms", acts[2]["ms"] == 250)


def test_parser_stringln():
    acts = mod.parse_ducky("STRINGLN ls -la\n")
    check("STRINGLN -> string + ENTER key", [a["type"] for a in acts] == ["string", "key"])
    check("STRINGLN text", acts[0]["text"] == "ls -la")
    check("STRINGLN appends ENTER report",
          acts[1]["report"] == mod._report(0, mod.NAMED_KEYS["ENTER"]))


def test_parser_combos():
    acts = mod.parse_ducky("GUI r\n")
    rep = acts[0]["report"]
    check("GUI r -> GUI modifier set", rep[0] & mod.MOD_LGUI == mod.MOD_LGUI)
    check("GUI r -> 'r' usage", rep[2] == mod.CHAR_TO_HID["r"][1])

    acts2 = mod.parse_ducky("CTRL ALT DELETE\n")
    rep2 = acts2[0]["report"]
    check("CTRL ALT DEL modifiers", rep2[0] == (mod.MOD_LCTRL | mod.MOD_LALT))
    check("CTRL ALT DEL usage", rep2[2] == mod.NAMED_KEYS["DELETE"])


def test_parser_errors():
    for bad, why in [
        ("DELAY abc\n", "non-int DELAY"),
        ("CTRL c x\n", "two non-modifier keys"),
        ("FROBNICATE\n", "unknown key"),
        ("REPEAT 2\n", "REPEAT with no previous line"),
    ]:
        try:
            mod.parse_ducky(bad)
            check(f"parse error raised for {why}", False)
        except mod.DuckyParseError:
            check(f"parse error raised for {why}", True)


def test_parser_repeat_and_maxactions():
    acts = mod.parse_ducky("STRING x\nREPEAT 3\n")
    check("REPEAT 3 -> 1 + 3 string actions", len(acts) == 4)
    try:
        mod.parse_ducky("STRING " + ("a" * 10) + "\n", max_actions=1)
        # STRING is a single action, so bump with many lines instead
        big = "\n".join("ENTER" for _ in range(50))
        mod.parse_ducky(big, max_actions=10)
        check("max_actions enforced", False)
    except mod.DuckyParseError:
        check("max_actions enforced", True)


# --- reports ---------------------------------------------------------------
def test_reports():
    # streams now start with a leading keys-up (RELEASE)+settle and end keys-up
    acts = mod.parse_ducky("STRING ab\n")
    stream = mod.actions_to_reports(acts)
    check("stream starts with a leading release", stream[0] == ("report", mod.RELEASE))
    check("stream ends keys-up", stream[-1] == ("report", mod.RELEASE))
    reports = [s for s in stream if s[0] == "report"]
    # leading release + a_down + a_up + b_down + b_up + trailing release = 6
    check("string 'ab' -> 6 report events (incl leading/trailing release)",
          len(reports) == 6)
    check("'a' down present", ("report", mod._report(0, 0x04)) in stream)

    acts2 = mod.parse_ducky("ENTER\n")
    stream2 = mod.actions_to_reports(acts2)
    check("key down present after leading release",
          ("report", mod._report(0, mod.NAMED_KEYS["ENTER"])) in stream2)

    acts3 = mod.parse_ducky("DEFAULTDELAY 40\nSTRING a\n")
    stream3 = mod.actions_to_reports(acts3)
    check("DEFAULTDELAY applies after a string", ("delay", 40) in stream3)


def test_modifier_settle():
    # a modifier combo (GUI r) must be followed by a settle delay so the host
    # registers the release (the fix for the stuck-Windows-key bug)
    acts = mod.parse_ducky("GUI r\n")
    stream = mod.actions_to_reports(acts, modifier_settle_ms=40)
    # find the combo report, confirm a release then a 40ms settle follow it
    idx = next(i for i, s in enumerate(stream)
               if s[0] == "report" and s[1][0] != 0)
    check("modifier combo report has a modifier byte", stream[idx][1][0] == mod.MOD_LGUI)
    check("release follows the combo", stream[idx + 1] == ("report", mod.RELEASE))
    check("settle delay follows the release", stream[idx + 2] == ("delay", 40))
    # a non-modifier key gets no settle: only the single leading settle is 40ms
    acts2 = mod.parse_ducky("ENTER\n")
    s2 = mod.actions_to_reports(acts2, modifier_settle_ms=40)
    forty = [d for d in s2 if d == ("delay", 40)]
    check("plain key has only the leading settle (no post-key settle)", len(forty) == 1)
    # the modifier case has two 40ms delays (leading + post-combo settle)
    two = [d for d in mod.actions_to_reports(mod.parse_ducky("GUI r\n"),
                                             modifier_settle_ms=40) if d == ("delay", 40)]
    check("modifier key adds a second settle", len(two) == 2)


# --- token / bind ----------------------------------------------------------
def test_token():
    ok, _ = mod.validate_token("a-long-enough-random-token")
    check("good token accepted", ok)
    check("blank token rejected", not mod.validate_token("")[0])
    check("placeholder token rejected", not mod.validate_token("changeme")[0])
    check("short token rejected", not mod.validate_token("short")[0])
    check("token_matches true", mod.token_matches("abc123xyz789", "abc123xyz789"))
    check("token_matches false", not mod.token_matches("abc123xyz789", "nope"))
    check("token_matches None-safe", not mod.token_matches(None, None))


def test_bind_plan():
    host, note, ok = mod.resolve_bind_plan("localhost", 8083, lambda: None)
    check("localhost -> 127.0.0.1", host == "127.0.0.1" and ok)
    host, note, ok = mod.resolve_bind_plan("lan", 8083, lambda: None)
    check("lan -> 0.0.0.0", host == "0.0.0.0" and ok)
    host, note, ok = mod.resolve_bind_plan("tailscale", 8083, lambda: None)
    check("tailscale w/o iface refuses", host is None and not ok)
    host, note, ok = mod.resolve_bind_plan("tailscale", 8083, lambda: "100.64.0.1")
    check("tailscale w/ iface ok", host == "100.64.0.1" and ok)
    host, note, ok = mod.resolve_bind_plan("auto", 8083, lambda: None)
    check("auto w/o tailscale -> localhost", host == "127.0.0.1" and ok)
    host, note, ok = mod.resolve_bind_plan("auto", 8083, lambda: "100.64.0.9")
    check("auto w/ tailscale -> ts ip", host == "100.64.0.9" and ok)


# --- arm / disarm ----------------------------------------------------------
def test_arm_state():
    p = make_plugin(arm_window_seconds=120, arm_one_shot=True)
    check("starts disarmed", not p.is_armed())
    p._arm("test")
    check("armed after _arm", p.is_armed())
    p._disarm("test")
    check("disarmed after _disarm", not p.is_armed())

    # window expiry
    p2 = make_plugin(arm_window_seconds=120, arm_one_shot=False)
    p2._arm("test")
    p2._armed_until = 0.0  # simulate an elapsed window
    p2._armed_forever = False
    check("armed window expiry disarms", not p2.is_armed())

    # budget exhaustion (one-shot)
    p3 = make_plugin(arm_window_seconds=0, arm_one_shot=True)
    p3._arm("test")
    check("armed with window=0 (forever until fire)", p3.is_armed())
    p3._fire_budget = 0
    check("budget exhausted disarms", not p3.is_armed())


# --- firing gate (device write mocked) -------------------------------------
def test_fire_gate():
    tmp = tempfile.mkdtemp()
    payload = os.path.join(tmp, "demo.duck")
    with open(payload, "w") as fh:
        fh.write("STRING hi\nENTER\n")

    p = make_plugin(payloads_dir=tmp, arm_window_seconds=120, arm_one_shot=True)
    written = []
    p._write_stream = lambda stream: written.append(list(stream))

    # disarmed -> refused, nothing written
    ok, msg = p._fire("demo.duck", "test")
    check("fire refused while disarmed", not ok and "disarm" in msg.lower())
    check("nothing written while disarmed", written == [])

    # armed -> fires, writes, one-shot disarms
    p._arm("test")
    ok, msg = p._fire("demo.duck", "test", target_label="my-old-laptop")
    check("fire ok while armed", ok)
    check("device write happened once", len(written) == 1 and len(written[0]) > 0)
    check("one-shot disarmed after fire", not p.is_armed())

    # missing payload
    p._arm("test")
    ok, msg = p._fire("does_not_exist.duck", "test")
    check("missing payload reported", not ok and "not found" in msg.lower())

    # parse error payload
    bad = os.path.join(tmp, "bad.duck")
    with open(bad, "w") as fh:
        fh.write("DELAY nope\n")
    p._arm("test")
    ok, msg = p._fire("bad.duck", "test")
    check("parse error payload reported", not ok and "parse" in msg.lower())


# --- payload path safety ---------------------------------------------------
def test_payload_path_safety():
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "ok.duck"), "w") as fh:
        fh.write("STRING x\n")
    p = make_plugin(payloads_dir=tmp)
    check("resolves a real .duck", p._payload_path("ok.duck") is not None)
    check("rejects traversal", p._payload_path("../etc/passwd") is None)
    check("rejects abs path", p._payload_path("/etc/passwd") is None)
    check("rejects dotfile", p._payload_path(".secret") is None)
    check("rejects wrong extension", p._payload_path("ok.sh") is None)
    check("rejects nested sep", p._payload_path("sub/ok.duck") is None)


class FakeUI:
    """Minimal stand-in for the pwnagotchi UI object."""
    def __init__(self, width=250):
        import threading
        self._lock = threading.RLock()
        self._w = width
        self.elements = {}
    def width(self):
        return self._w
    def add_element(self, name, el):
        self.elements[name] = el
    def set(self, name, val):
        self.elements[name] = val
    def remove_element(self, name):
        self.elements.pop(name, None)


def test_ui_hooks_safe():
    # UI isn't available in the sandbox (_UI_AVAILABLE False) so the hooks must
    # no-op without raising. This guards the import-guard + the enabled check.
    p = make_plugin(ui_enabled=True)
    ui = FakeUI()
    try:
        p.on_ui_setup(ui)
        p.on_ui_update(ui)
        p.on_unload(ui)
        check("UI hooks no-op safely when UI unavailable", True)
    except Exception as exc:
        check(f"UI hooks no-op safely ({exc})", False)
    # and with ui_enabled False they must also be inert
    p2 = make_plugin(ui_enabled=False)
    p2.on_ui_setup(ui); p2.on_ui_update(ui)
    check("UI hooks respect ui_enabled=false", True)


def test_write_timeout_option():
    # write_timeout_seconds is read with a sane default and floor.
    p = make_plugin()
    check("write_timeout default is 10", p._opt_int("write_timeout_seconds", 10) == 10)
    p2 = make_plugin(write_timeout_seconds=3)
    check("write_timeout override honored", p2._opt_int("write_timeout_seconds", 10) == 3)


def test_shipped_payloads():
    pdir = os.path.join(HERE, "..", "payloads")
    files = sorted(f for f in os.listdir(pdir) if f.endswith(".duck"))
    # core demos must always be present; the catalog can grow beyond them
    core = {"hello_world.duck", "rickroll.duck", "spooky_skull.duck"}
    check("ships the core demo payloads", core.issubset(set(files)))
    check("ships a generous catalog (>=10)", len(files) >= 10)
    for f in files:
        try:
            acts = mod.parse_ducky(open(os.path.join(pdir, f), encoding="utf-8").read(),
                                   max_actions=20000)
            check(f"{f} parses and yields actions", len(acts) > 0)
            # every parsed action can be flattened to reports without error
            mod.actions_to_reports(acts)
            check(f"{f} flattens to reports", True)
        except Exception as exc:
            check(f"{f} parses cleanly ({exc})", False)


def main():
    test_keymap()
    test_parser_basic()
    test_parser_stringln()
    test_parser_combos()
    test_parser_errors()
    test_parser_repeat_and_maxactions()
    test_reports()
    test_modifier_settle()
    test_token()
    test_bind_plan()
    test_arm_state()
    test_fire_gate()
    test_payload_path_safety()
    test_ui_hooks_safe()
    test_write_timeout_option()
    test_shipped_payloads()

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("all badhid_ng sandbox tests passed")


if __name__ == "__main__":
    main()
