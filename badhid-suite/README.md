# badhid-suite (`badhid_ng.py`)

Turn a gadget-capable Raspberry Pi into a **USB keyboard** that types a payload
*you* wrote — on demand, behind a token, with a loud log and an on-screen
status. A "BadUSB / BadHID" lab tool for testing against **hardware you own or
are authorized to test**. It's the A1+A2 slice of the
`BADUSB_AND_REMOTE_EXEC_IDEAS` backlog: the USB-gadget layer + a
DuckyScript-subset runner.

New to this? Read **"How it works in one minute"** then **"Quickstart"**. The
`badhid_doctor.sh` script will hold your hand the whole way.

---

## How it works in one minute

- A Pi with a USB **device-capable** port can pretend to be a keyboard. When you
  plug that port into a computer, the computer thinks a keyboard was attached.
- This plugin reads a **payload** (a little script in DuckyScript syntax) and
  "types" it on that fake keyboard — into whatever window has focus.
- You control it from a **web page or a one-line command**, over your network
  (ethernet / Wi-Fi / Tailscale). So you can trigger it from your phone while
  the Pi is plugged into the target.
- It is **disarmed by default**. Nothing types until you **arm** it, and it
  re-locks itself after firing. Every arm and fire is logged.

Two honest limits up front (more in **Limitations**):
1. **Not every Pi can do this** — the USB port has to act as a *device*. Pi
   Zero/Zero 2 W, Pi 4, Pi 3A+ can; Pi 3B/3B+, Pi 400 can't. See
   **COMPATIBILITY.md**. `enable_dwc2.sh`/`badhid_doctor.sh` tell you which you
   have.
2. **A keyboard can't tell which computer it's plugged into.** The arm model and
   `authorized_targets` control *when* it fires and *log your intent*; they
   can't stop it typing into the wrong machine. The only real safeguard is
   **what you physically plug it into.**

This suite ships the framework + **three harmless demos** and **no** offensive
payloads. You write your own, for your own gear.

---

## Quickstart (Pi 4, the common case)

Everything runs on the Pi. You stay reachable over ethernet/Wi-Fi the whole
time, so you can't lock yourself out.

```bash
# 0) get the files onto the pi
cd ~/plugins-wip && git pull || git clone https://github.com/patrickato/plugins-wip ~/plugins-wip
cd ~/plugins-wip/badhid-suite

# 1) safe install (backs up first, generates a token, enabled=false, auto-rollback)
sudo ./badhid_install.sh

# 2) is the Pi's USB port ready? the doctor tells you the next step
sudo ./badhid_doctor.sh
#    on a fresh Pi 4 it'll say: run enable_dwc2.sh && reboot

# 3) enable gadget mode (one-time), then reboot (ethernet survives it)
sudo ./enable_dwc2.sh
sudo reboot

# --- after it comes back, SSH in again ---
cd ~/plugins-wip/badhid-suite
sudo ./badhid_doctor.sh                       # should now say: bring the gadget up
sudo ./setup_composite_gadget.sh --hid-only   # creates /dev/hidg0
sudo ./badhid_doctor.sh                        # should be all green except "enable the plugin"

# 4) turn the plugin on: open the config, find [main.plugins.badhid_ng],
#    change its  enabled = false  to  enabled = true  (that block is at the
#    very end if badhid_install.sh added it). Then restart.
sudo nano /etc/pwnagotchi/config.toml
sudo systemctl restart pwnagotchi

# 5) fire a harmless demo at the machine the Pi is plugged into
sudo ./badhidctl.sh arm
sudo ./badhidctl.sh fire hello_world.duck
```

If anything's unclear at any point: **`sudo ./badhid_doctor.sh`** prints exactly
what to do next.

> Not on a Pi 4? The same steps work on a Pi Zero/Zero 2 W or Pi 3A+ — step 3
> still applies (`enable_dwc2.sh` auto-detects your board). On a Pi 3B/3B+/400 it
> will tell you the board can't do HID and stop.

---

## The helper scripts (what each one is for)

| Script | What it does | Reversible? |
|---|---|---|
| `badhid_install.sh` | Backup + install plugin/payloads + add config block (random token, `enabled=false`), with config auto-rollback | `badhid_restore.sh` |
| `badhid_doctor.sh` | **Read-only** health check of the whole chain; prints the one next step | n/a |
| `enable_dwc2.sh` | Put the USB port in gadget mode (`dr_mode=otg`); board-aware; **needs reboot** | `enable_dwc2.sh --revert` |
| `setup_composite_gadget.sh` | Bring up the USB keyboard gadget (`/dev/hidg0`). `--hid-only` (keyboard only) or default (keyboard + USB net). Runtime only — a reboot clears it | `--teardown` |
| `badhidctl.sh` | Friendly control: `status / list / arm / disarm / fire` (reads your token automatically) | n/a |
| `badhid_backup.sh` | Snapshot config + plugins anytime | n/a |
| `badhid_restore.sh` | Full undo: remove plugin, restore config, tear gadget down (`--reboot` option) | n/a |

---

## The demo payloads (all harmless — type-only, no commands)

| File | What it does | OS |
|---|---|---|
| `hello_world.duck` | Types one line into the focused window. The quietest proof it works. | any |
| `spooky_skull.duck` | Opens Notepad and types an ASCII skull & crossbones with "I SEE YOU" / "I'M COMING FOR YOU!". Looks sinister, does **nothing** but type text you can close without saving. | Windows (adapt for mac/Linux) |
| `rickroll.duck` | Opens the classic video in the default browser. | Windows (adapt for mac/Linux) |

The two Windows demos use **Win+R (Run)** to launch Notepad / open the URL. Each
file's header has the one-line tweak for macOS/Linux.

---

## Controlling it

### Easiest: the CLI wrapper
```bash
sudo ./badhidctl.sh status            # arm state, HID device, bound URL
sudo ./badhidctl.sh list              # available payloads
sudo ./badhidctl.sh arm
sudo ./badhidctl.sh fire hello_world.duck   my-old-laptop   # (payload, optional label)
sudo ./badhidctl.sh disarm
```
It reads your token from `/etc/pwnagotchi/badhid_ng/auth_token.txt` automatically.

### The web page (trigger from your phone)
After enabling the plugin, the log prints the control URL, e.g.
`[badhid_ng] control server up: auto -> tailscale - http://100.x.y.z:8083/`.
Open it and pass the token (the page asks via `Authorization: Bearer`; easiest is
the CLI, or a browser extension that sets the header). The page shows arm state,
the HID device, your payloads, and ARM / DISARM / FIRE buttons.

### On the Pi's screen
With `ui_enabled = true` a small **`BadHID`** indicator shows on the TFT:
`off` → `ready` (gadget up) → `ARMED` (live) → `no-dev` (gadget not up).

---

## Ways to run it (pick what fits)

- **Tethered to a laptop (simplest first test):** the Pi is powered + plugged
  into the laptop you're at. That laptop is the target — fire a demo into a
  Notepad window on it.
- **Field rig, no laptop:** power the Pi from a **UPS HAT or power bank** and run
  a single **data cable** from the Pi's device port to the target. See
  **"Powering it in the field"** below. Trigger from your phone.
- **Gadget flavor:** `--hid-only` (target sees just a keyboard — best when you
  manage the Pi over ethernet/Wi-Fi) or the default composite (keyboard + a USB
  network link, if you want usb0 too).
- **Exposure:** `bind_scope = auto` (Tailscale if present, else localhost),
  `tailscale`, `localhost`, or `lan`. The URL is always logged.
- **Trigger model:** manual fire (default), a timed arm window
  (`arm_window_seconds`), one-shot vs repeat (`arm_one_shot`), or fire the moment
  a host enumerates (`fire_on_enumerate`, off by default).

### Powering it in the field (no laptop)
The gadget doesn't care how the Pi is powered — only that its **device port** is
cabled to the target:
- **Pi 4:** power via a GPIO **UPS HAT** (e.g. Waveshare UPS 3S) or 5V power bank
  → the USB-C port becomes a pure data link. Cable: USB-C (Pi) → USB-A (target).
- **Pi Zero:** power the **PWR** micro-USB; data via the **USB** (OTG) micro-USB.

Use a **data-only / charge-blocked cable** when you're also on a UPS so both
ends don't push 5V down the line. More in **COMPATIBILITY.md**.

---

## Writing your own payloads

Drop `*.duck` (or `*.txt`) files in `payloads_dir`
(`/etc/pwnagotchi/badhid_ng/payloads`). Supported commands:

| Command | Meaning |
|---|---|
| `REM ...` / `# ...` | comment |
| `STRING <text>` | type the literal text |
| `STRINGLN <text>` | type the text, then Enter |
| `ENTER`, `TAB`, `ESC`, `UP`, `DELETE`, `F5`, ... | a named key |
| `GUI r`, `CTRL ALT DELETE`, `CTRL c` | a modifier combo (GUI/CTRL/ALT/SHIFT) |
| `DELAY <ms>` | pause |
| `DEFAULTDELAY <ms>` | implicit pause between following lines |
| `REPEAT <n>` | repeat the previous line n times |

US keyboard layout for now. Keep payloads pointed at your own equipment.

---

## Configuration reference

Every option lives under `[main.plugins.badhid_ng]` (see `config.toml` for the
commented block). Highlights:

- `auth_token` — **required**, ≥12 chars, no placeholders, or the server refuses
  to start.
- `hid_device` (`/dev/hidg0`), `bind_scope`, `port` (8083).
- `payloads_dir`, `default_payload`.
- `arm_window_seconds` (120), `arm_one_shot` (true), `fire_on_enumerate` (false).
- `inter_key_delay_ms` (5) — raise if a fast host drops characters.
- `write_timeout_seconds` (10) — a fire aborts with a clear error if no host is
  reading (e.g. not plugged into a powered/awake target), instead of hanging.
- `authorized_targets` (advisory log only — see Limitations), `ui_*`.

---

## Limitations (read before sharing)

- **Board support is hardware-bound.** Pi Zero/Zero 2 W, Pi 4, Pi 3A+: yes. Pi 5:
  experimental. Pi 3B/3B+, Pi 400, Pi 1/2: no device port, so no HID. See
  **COMPATIBILITY.md**.
- **The allowlist is advisory, not a guard.** A USB keyboard can't verify the
  host, so `authorized_targets` is an audit/intent record only. Physical control
  of what you plug into is the real safeguard.
- **US keyboard layout only** right now. Non-US layouts can mistype symbols.
- **Focus matters.** It types into whatever window is focused. The Windows demos
  open their own window (Run → Notepad/browser); a bare `STRING` demo needs you
  to click into a text field first.
- **One payload at a time, synchronous.** A long payload (many DELAYs) holds the
  request until it finishes.
- **This is a lab tool.** It ships no offensive payloads and won't be extended
  into turnkey malware. Keep it to gear you own or are authorized to test.

---

## Troubleshooting

Run **`sudo ./badhid_doctor.sh`** first — it pinpoints the broken link. Common ones:

| Symptom | Cause | Fix |
|---|---|---|
| `no UDC found` from the gadget script | USB port not in gadget mode (often `dr_mode=host`) | `sudo ./enable_dwc2.sh && sudo reboot` |
| gadget script: `/dev/hidg0 missing` after bind | legacy `g_ether` grabbed the controller | the script auto-unbinds it; re-run; or `sudo ./setup_composite_gadget.sh --hid-only` |
| server won't start, log: token refused | `auth_token` blank/placeholder/<12 chars | set a long random token in config, restart |
| fire error: "HID device not accepting input" | Pi isn't plugged into a powered, awake, enumerated target | plug into the target; wake it; check the cable |
| characters dropped/garbled on the target | host too slow for the type speed | raise `inter_key_delay_ms` (e.g. 10–20) |
| wrong symbols typed | non-US keyboard layout on the target | US layout only for now |
| lost `usb0` after the gadget came up | expected if you used composite on an ethernet-managed Pi | you manage over ethernet; or `--teardown`, or reboot |
| want it all gone | — | `sudo ./badhid_restore.sh` (add `--reboot` to fully clear the gadget) |

---

## Tests

```bash
cd badhid-suite
python3 tests/test_badhid_ng.py
```
Sandbox tests cover the parser, keymap, report emitter, token/bind logic, the
arm state machine, the fire gate (device write mocked), payload-path safety, the
UI hooks, and that every shipped payload parses. **Not** covered in sandbox
(needs a real-hardware pass): the actual `/dev/hidg0` write reaching a host,
`enable_dwc2.sh` + `setup_composite_gadget.sh` on real hardware, and the live
server. Per repo rule, this suite doesn't graduate to `complete-plugins` until
that on-device pass is done.
