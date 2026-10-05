"""
test_badhid_ble_ng.py - sandbox tests for badhid-ble-suite.

The DuckyScript ENGINE is the same code as the wired suite, so these confirm it
came across intact (parser/keymap/reports), plus the arm model, the token gate,
and that the BLE transport FAILS SAFE (a fire with no connected host returns a
clear error, never a silent success). The BLE radio itself can't be tested in a
sandbox - that's the on-device bring-up (see BLE_DESIGN.md).

Run:  python3 tests/test_badhid_ble_ng.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))

import badhid_ble_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


def make_plugin(**opts):
    p = mod.BadHIDBLENG()
    o = dict(mod.DEFAULTS); o.update(opts)
    p.options = o
    return p


def test_engine_intact():
    # same engine as the wired suite
    acts = mod.parse_ducky("STRING hi\nGUI r\nCTRL ALT DELETE\n")
    check("parser works", len(acts) == 3)
    stream = mod.actions_to_reports(acts, modifier_settle_ms=40)
    check("stream leads with keys-up", stream[0] == ("report", mod.RELEASE))
    check("stream ends keys-up", stream[-1] == ("report", mod.RELEASE))
    check("keymap present", mod.CHAR_TO_HID["a"] == (0, 0x04))
    # modifier settle carried over
    check("modifier settle applied", ("delay", 40) in
          mod.actions_to_reports(mod.parse_ducky("GUI r\n"), modifier_settle_ms=40))


def test_token_and_bind():
    check("good token", mod.validate_token("a-long-enough-token")[0])
    check("placeholder rejected", not mod.validate_token("changeme")[0])
    h, n, ok = mod.resolve_bind_plan("localhost", 8085, lambda: None)
    check("bind localhost", h == "127.0.0.1" and ok)
    h, n, ok = mod.resolve_bind_plan("tailscale", 8085, lambda: None)
    check("tailscale refuses w/o iface", h is None and not ok)


def test_arm_model():
    p = make_plugin()
    check("starts disarmed", not p.is_armed())
    p._arm("t"); check("arms", p.is_armed())
    p._disarm("t"); check("disarms", not p.is_armed())


def test_fire_fails_safe_without_host():
    # with no paired/connected BLE host, a fire must refuse clearly (not silently ok)
    tmp = tempfile.mkdtemp()
    open(os.path.join(tmp, "p.duck"), "w").write("STRING hi\n")
    p = make_plugin(payloads_dir=tmp, arm_window_seconds=120)
    p._arm("t")
    ok, msg = p._fire("p.duck", "test")
    check("fire refused with no connected host", ok is False and "no paired host" in msg)


def test_transport_not_ready():
    t = mod.BLEHidTransport("TestKB")
    check("transport not connected initially", not t.connected())
    check("transport status not-ready", t.status() == "not-ready")
    try:
        t.send_report(mod.RELEASE)
        check("send_report raises when not ready", False)
    except RuntimeError:
        check("send_report raises when not ready", True)


def main():
    test_engine_intact()
    test_token_and_bind()
    test_arm_model()
    test_fire_fails_safe_without_host()
    test_transport_not_ready()
    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}"); sys.exit(1)
    print("all badhid_ble_ng sandbox tests passed")


if __name__ == "__main__":
    main()
