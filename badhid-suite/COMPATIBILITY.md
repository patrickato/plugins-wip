# badhid-suite — hardware compatibility

The **plugin** (`badhid_ng.py`: DuckyScript parser, HID reports, web UI,
arm/disarm, logging) is **board-independent** — it just writes to `/dev/hidg0`.
What varies by board is whether the Pi can present that HID device at all, which
needs a USB port that can act as a **device (OTG/peripheral)**. `enable_dwc2.sh`
auto-detects the board and does the right thing.

| Board | USB HID gadget | Device port | Notes |
|---|---|---|---|
| Pi Zero / Zero W / **Zero 2 W** | ✅ Yes | micro-USB **(the inner "USB" port, not "PWR")** | The classic BadUSB board |
| **Pi 4 / 4B** | ✅ Yes | USB-C | Needs `dtoverlay=dwc2,dr_mode=otg` (default is often host/none) |
| Pi 3 **A+** | ✅ Yes | micro-USB OTG | Less common board |
| Compute Module (CM3/4) | ✅ Usually | OTG via carrier | Depends on the carrier board wiring |
| **Pi 5** | ⚠️ Experimental | USB-C power port | USB2 peripheral exists but is newer/fiddlier; may need current firmware |
| Pi 3B / 3B+ | ❌ No | — | micro-USB is **power-only** (data behind an onboard USB hub chip) |
| Pi 400 | ❌ No | — | USB-C is power-only |
| Pi 1 / Pi 2 | ❌ No | — | No OTG |

**Bottom line for sharing:** anyone on a **Zero/Zero 2 W, Pi 4, or Pi 3A+** is
good to go with the one-time `enable_dwc2.sh` + reboot. Pi 5 may work (try it).
Pi 3B/3B+/400 can't do the keystroke part no matter what — `enable_dwc2.sh` will
say so plainly instead of leaving them guessing.

## Powering vs. data (field use — no laptop needed)

The gadget doesn't care how the Pi is powered; it only needs its **device port**
cabled to the target. So you can run it off a UPS/battery and keep the data port
free for the target:

- **Pi 4 field rig:** power from a **GPIO UPS HAT** (e.g. the Waveshare UPS 3S) or
  a power bank into the 5V GPIO pins → the **USB-C port is now a pure data link**.
  Cable: USB-C (Pi) → USB-A (target). Plug into the target, arm from your phone,
  fire. No laptop in the loop.
- **Pi Zero field rig:** power the **PWR** micro-USB from a bank/UPS, and use the
  **USB** (OTG) micro-USB → USB-A to the target. Two separate ports, so power and
  data are already independent.

**Cable note (worth knowing):** when the Pi is externally powered *and* plugged
into a target, both sides can put 5V on the cable. It's usually harmless, but a
**data-only / "charge-blocked" cable** (or a USB data-only adapter) keeps it
clean and avoids back-feeding the target's port. A true USB-A-to-USB-A data cable
works for the data link but is non-standard and especially prone to double-power
— prefer the Pi's native port (C or micro) to the target's USB-A, with power
pins blocked if you're also on a UPS.
