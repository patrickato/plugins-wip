# Wireless BadHID (BLE) — design & bring-up

This suite makes the pi a **Bluetooth HID keyboard**: no cable to the target.
It reuses the wired suite's DuckyScript engine verbatim (parser, US keymap,
8-byte boot-keyboard reports) — **only the transport changes**: reports go over
Bluetooth instead of `/dev/hidg0`.

The engine is proven and tested. The **BLE transport is the one part that needs
on-hardware bring-up** — there is no Bluetooth radio in a sandbox, so it is
isolated behind one method (`BLEHidTransport.send_report`), the same way the
wired suite isolated the `/dev/hidg0` write. This doc is the bring-up plan.

## Approach: BLE HID-over-GATT (HOG)

Present a **BLE peripheral** that advertises the standard **HID service
(0x1812)** so a modern phone or PC can pair and receive keystrokes:

- **Advertising:** name = `ble_device_name`, appearance = `0x03C1` (keyboard),
  service-complete list incl. 0x1812.
- **GATT services:**
  - **HID (0x1812):** Report Map (boot-keyboard descriptor — the same 8-byte
    layout the engine emits), HID Information, Protocol Mode, and a **Report**
    characteristic (Notify) that we push reports to; a boot-keyboard Input
    Report characteristic for hosts in boot protocol.
  - **Device Information (0x180A)** and **Battery (0x180F)** — hosts expect
    these on an HID peripheral.
- **Flow:** host pairs + bonds → subscribes to the Report characteristic →
  `send_report(8 bytes)` issues a GATT **notification** → host sees a keypress.
  Release = an all-zero report, exactly as the engine already emits.

The report bytes are **identical** to the wired path, so
`actions_to_reports()` output is reused with no change.

### Why HOG (and the classic alternative)

- **HOG/BLE** pairs with phones and current laptops, is the modern path, and is
  what "wireless" usually means today → the default here.
- **Classic Bluetooth HID** (SDP HID profile over L2CAP PSM 0x11/0x13) reaches
  older hosts and some that refuse BLE keyboards. It's a separate transport
  backend that can be added behind the same `send_report` interface if a target
  needs it. (Reference: the BlueZ input/HID-device emulator pattern.)

## Dependencies & BlueZ

- BlueZ ≥ 5.50 with D-Bus, and `python3-dbus` (+ GLib main loop). `setup_ble_hid.sh`
  installs these and checks the adapter.
- The controller (bettercap/pwnagotchi) also uses the BT adapter for its own BLE
  recon; **the radio is a shared, exclusive resource**. Running an HID peripheral
  and BLE scanning at once contends — plan to pause BT recon while the HID
  peripheral is advertising, or use a **second BT adapter** (a cheap USB BT
  dongle) dedicated to HID. This mirrors the wifi radio-contention rule elsewhere
  in this project.

## Bring-up plan (on the pi)

1. `sudo ./setup_ble_hid.sh` — install deps, confirm the adapter, (optionally)
   point a dedicated dongle at HID.
2. Implement the GATT HID application (the `HidApplication` the transport's
   `start()` references) using the BlueZ `example-gatt-server` + advertising
   pattern: register the services above, implement `notify_report()` to push to
   the Report characteristic, and set `subscribed` when the host enables
   notifications. Wire `BLEHidTransport.start/send_report/stop` to it.
3. Pair a device **you own** (phone/laptop), confirm it shows a "BadHID-KB"
   keyboard, open a text field.
4. `badhidctl`-style fire (arm → fire `hello_world.duck`) and watch it type over
   the air. Then `keymap_test.duck` to confirm every character, exactly like the
   wired bring-up.

Until step 2 is wired on-device, `start()` returns false with a clear reason and
a fire refuses loudly (`no paired host connected` / transport `not ready`) — it
never silently pretends to work.

## Safety — the pairing boundary

Wireless removes the cable, but adds a real gate: **a BLE HID keyboard can only
type into a device that has paired/bonded with this pi.** Pairing requires
someone to accept it on the target. So wireless BadHID is inherently scoped to
devices you can pair with — yours, or ones an authorized owner pairs for a test.
That pairing step is the boundary. The software still can't verify intent, so the
rule is unchanged: **only against hardware you own or are authorized to test.**
Disarmed + manual-fire by default, token auth, loud logging — same as wired. No
offensive payloads are shipped.
