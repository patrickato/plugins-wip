# badhid-ble-suite — design & safety notes

Status: **new build**, the wireless slice of the BadUSB track. The DuckyScript
**engine is the same tested code as the wired suite** (copied verbatim from
`badhid_ng.py`'s pure section); the **BLE transport is a scaffold that needs
on-hardware bring-up** (no BT radio in a sandbox). Version `0.1.0-pre`
accordingly. Does not graduate to `complete-plugins` until a real on-device BLE
pass.

## What's done vs. what needs hardware

- **Done & tested here:** the engine (parser, US keymap, 8-byte reports incl.
  the leading/trailing keys-up + `modifier_settle_ms` fix), token + `bind_scope`
  gates, the arm/disarm model, the control API, payload listing (shares the
  wired `payloads_dir`), and the **fail-safe**: with no paired/connected host a
  fire refuses (`no paired host connected`) and the transport's `send_report`
  raises rather than silently succeeding (`test_fire_fails_safe_without_host`,
  `test_transport_not_ready`).
- **Needs on-device bring-up:** `BLEHidTransport.start()/send_report()` — the
  BlueZ GATT HID application (advertise HID 0x1812, push reports via the Report
  characteristic). Isolated behind `send_report` exactly like the wired suite's
  `/dev/hidg0` write, so the bring-up surface is one small class. Plan in
  BLE_DESIGN.md; deps via `setup_ble_hid.sh`.

Why scaffold-not-fake: shipping ~300 lines of untested D-Bus GATT code blind
would give false confidence. The engine is real and tested; the transport is
honestly marked and structured so on-device bring-up is a contained, well-
documented step — the same way the wired gadget's real issues only surfaced on
hardware.

## Transport choice

BLE HID-over-GATT (HOG) as the default (phones + modern PCs). Classic Bluetooth
HID (older hosts) is a second backend behind the same `send_report` interface if
a target needs it. See BLE_DESIGN.md.

## Safety — the pairing boundary

The cable is gone, but pairing replaces it: a BLE HID keyboard can only type into
a device that has paired/bonded with the pi, which someone must accept on the
target. So this is inherently scoped to devices you can pair with. The software
can't verify intent, so the rule is unchanged (own/authorized hardware only),
and the same disarmed-by-default + token + loud-log discipline applies. No
offensive payloads shipped.

## Radio contention

The BT adapter is a single exclusive resource shared with bettercap/pwnagotchi's
BLE recon. Running an HID peripheral and BLE scanning at once contends — pause BT
recon while advertising, or dedicate a second USB BT dongle to HID. Same rule as
the wifi radio elsewhere in this project.

## Out of scope (later)

- The classic-Bluetooth-HID backend (older hosts).
- On-screen status element (stubbed via ui_* options; wire after the transport).
- Graduation after a real BLE pass on the pi + a paired target.
