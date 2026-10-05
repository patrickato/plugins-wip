# badhid-ble-suite (`badhid_ble_ng.py`)

**Wireless BadHID.** The pi presents itself as a **Bluetooth (BLE) HID keyboard**
and types a payload you wrote into a device that has **paired** with it — no
cable to the target. It reuses the wired suite's exact DuckyScript engine
(parser, US keymap, 8-byte reports) and the same arm/auth/logging model; only
the transport is Bluetooth.

```
wired  (badhid_ng)     : DuckyScript -> reports -> /dev/hidg0 (USB cable)
wireless (badhid_ble)  : DuckyScript -> reports -> Bluetooth HID (paired, no cable)
```

## Status (read this)

- **Engine: proven & tested** — it's the same code as the wired suite (parser,
  keymap, reports incl. the stuck-modifier fix), with its own test suite here.
- **BLE transport: needs on-device bring-up.** There's no Bluetooth radio in a
  sandbox, so the transport is isolated behind one method (`send_report`) — the
  same way the wired suite isolated the `/dev/hidg0` write. Until it's wired on
  the pi, a fire **refuses loudly** (`no paired host connected`) rather than
  pretending. **BLE_DESIGN.md** is the full bring-up plan; `setup_ble_hid.sh`
  installs the deps.

This is why the version is `0.1.0-pre`: everything above the transport is done
and tested; the Bluetooth bring-up is the remaining live step (exactly the
workflow that shook out the wired suite's real bugs on hardware).

## The safety line (+ the pairing boundary)

Wireless drops the cable but adds a real gate: a BLE HID keyboard can **only**
type into a device that has **paired/bonded** with this pi, which requires
someone to accept the pairing on the target. So wireless BadHID is inherently
scoped to devices you can pair with — yours, or ones an authorized owner pairs
for a test. The software still can't verify intent, so the rule is unchanged:
**only against hardware you own or are authorized to test.** Disarmed +
manual-fire by default, token auth, loud logging. No offensive payloads shipped.

## Bring-up (summary — full detail in BLE_DESIGN.md)

```bash
sudo ./setup_ble_hid.sh          # install BlueZ + python dbus deps, check adapter
# then (on-device, see BLE_DESIGN.md):
#  - wire the GATT HID application into BLEHidTransport.start()/send_report()
#  - pair a device you own, open a text field
#  - add the config block, set a token + enabled=true, restart pwnagotchi
#  - arm + fire hello_world.duck, watch it type over the air
```

Radio note: BLE recon (bettercap) and an HID peripheral share the one adapter.
Pause BT recon while advertising, or dedicate a second USB BT dongle to HID
(same radio-contention discipline as the wifi side of this project).

## Config

Add the `config.toml` block to `/etc/pwnagotchi/config.toml`. Key options:
`auth_token` (required), `ble_device_name` (what the target sees), `bind_scope`,
`payloads_dir` (defaults to the wired suite's folder, so your whole payload
library works over BLE), the arm model, and the timing options carried over from
the wired suite. See `config.toml` comments.

## Control

Same shape as the wired suite — a token-gated HTTP API (`/arm`, `/disarm`,
`/fire`), so `badhidctl.sh`-style control and the phone workflow apply. A fire
checks that a host is actually paired/connected first.

## Tests

```bash
python3 tests/test_badhid_ble_ng.py
```
Confirms the engine came across intact (parser/keymap/reports/modifier-settle),
the token + bind gates, the arm model, and that a fire **fails safe** with no
connected host (the transport raises rather than silently succeeding). The BLE
radio path is the on-device bring-up (BLE_DESIGN.md).
