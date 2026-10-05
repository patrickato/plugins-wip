# badhid-suite (`badhid_ng.py`)

A USB HID keystroke-injection ("BadUSB") **framework** for testing against
**your own authorized lab hardware**. A gadget-mode Pi becomes a USB keyboard
that types a payload *you* wrote, on manual trigger, behind a token, with a
loud audit log. This is the **A1+A2** slice of the
`BADUSB_AND_REMOTE_EXEC_IDEAS` backlog: the composite-gadget layer plus a
DuckyScript-subset runner.

## What it does / doesn't ship

- **Ships:** the HID gadget plumbing (a setup script), a DuckyScript-subset
  interpreter + US keymap, an arm/disarm model, token auth, `bind_scope`, a
  manual-fire web UI, and **three harmless demo payloads** (a Hello World, a
  sinister-*looking* but inert Notepad skull, and a rickroll).
- **Does not ship:** any offensive payload — no shells, credential grabbers,
  defender-disablers, persistence, or exfiltration. You author real payloads
  yourself.

## The safety line (read this)

- A USB keyboard **cannot tell which machine it is plugged into.** The
  arm/disarm model and `authorized_targets` list control *when* the injector
  is live and *record what you intended to test*; they are **not** a technical
  restriction on *which* host gets typed into. "Only my own gear" is operator
  discipline. The only real safeguard against hitting the wrong machine is
  **what you physically plug the device into.**
- Default state is **disarmed, manual-fire only, auth-required, loud.** It will
  not fire on a mere plug-in unless you deliberately set `fire_on_enumerate =
  true` *and* arm it.
- HID injection is legal on hardware you own. Keep it to your own gear or
  targets you have **written** authorization to test.

## Requirements

- Pi 4 on the **jayofelony 64-bit** image, in USB **gadget** mode (`dwc2`).
- `libcomposite` available (`modprobe libcomposite`).
- Flask + Werkzeug (already present on the pwnagotchi image; used for the UI).
- Root (writing `/dev/hidg0` and configuring the gadget need it).

## Install

1. **Copy the suite onto the pi** (your usual flow — Pi-side `git pull` of
   `plugins-wip`, or `scp`). Place `badhid_ng.py` in your custom-plugins dir
   (e.g. `/etc/pwnagotchi/custom-plugins/`), and put the `payloads/` contents
   in `payloads_dir` (default `/etc/pwnagotchi/badhid_ng/payloads/`):

   ```bash
   sudo mkdir -p /etc/pwnagotchi/badhid_ng/payloads
   sudo cp badhid-suite/payloads/*.duck /etc/pwnagotchi/badhid_ng/payloads/
   sudo cp badhid-suite/badhid_ng.py /etc/pwnagotchi/custom-plugins/
   sudo cp badhid-suite/setup_composite_gadget.sh /root/
   ```

2. **Add the config block** from `config.toml` to `/etc/pwnagotchi/config.toml`.
   Set a real `auth_token`:

   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(24))"
   ```

   Leave `enabled = false` until after step 3 if you want to stage it.

3. **Bring up the composite gadget — deliberately, with a fallback way in.**
   This reconfigures the live USB gadget and *can drop usb0*. Make sure you can
   still reach the pi another way first (SSH over Wi-Fi/Tailscale, or
   keyboard+HDMI):

   ```bash
   sudo /root/setup_composite_gadget.sh --status    # inspect first
   sudo /root/setup_composite_gadget.sh             # bring it up (5s abort window)
   ls -l /dev/hidg0                                  # should now exist
   ```

   To undo: `sudo /root/setup_composite_gadget.sh --teardown`.

4. **Enable the plugin** (`enabled = true`) and restart pwnagotchi. The log
   shows the control URL at WARNING, e.g.
   `[badhid_ng] control server up: auto -> tailscale - http://100.x.y.z:8083/`.

## Which Pis this works on

The **plugin** is board-independent. The **keystroke hardware** needs a USB port
that can act as a device, which not every Pi has — see **COMPATIBILITY.md** for
the full matrix. Short version: **Pi Zero / Zero 2 W, Pi 4, Pi 3A+** work; **Pi 5**
is experimental; **Pi 3B/3B+, Pi 400, Pi 1/2 can't** (power-only USB port).
`enable_dwc2.sh` auto-detects the board and tells you which case you're in.

## Powering it in the field (no laptop)

The gadget doesn't care how the Pi is powered — only that its **device port** is
cabled to the target. So you can run off a **UPS HAT or power bank** and use the
data port purely for the target:

- **Pi 4:** power via a GPIO **UPS HAT** (e.g. Waveshare UPS 3S) or 5V GPIO power
  bank → the USB-C port becomes a pure data link. Cable: USB-C (Pi) → USB-A
  (target). Plug in, arm from your phone, fire. No laptop needed.
- **Pi Zero:** power the **PWR** micro-USB from a bank, data via the **USB** (OTG)
  micro-USB → target.

A **data-only / charge-blocked cable** is the clean choice when you're also on a
UPS, so the Pi and target don't both push 5V down the line (usually harmless, but
tidy). See COMPATIBILITY.md for the cable details.

## Safe install & full restore (recommended)

Three helper scripts make the install reversible. Run them from inside this
`badhid-suite/` folder on the Pi:

- **`badhid_backup.sh`** — snapshots `config.toml` + `custom-plugins/` into
  `/etc/pwnagotchi/badhid_backups/` (tarball + a bare config copy). Handshakes
  are not touched or backed up (large, irrelevant).
- **`badhid_install.sh`** — the safe installer: backs up first, copies the
  plugin + payloads (additive — your other plugins are untouched), appends the
  `[main.plugins.badhid_ng]` block with a **freshly generated random token** and
  `enabled = false`, then **validates `config.toml` parses and auto-rolls-back
  the config from the backup if it doesn't.** It does *not* bring up the gadget
  or enable anything — those stay your deliberate steps.
- **`badhid_restore.sh`** — one-command undo: tears the runtime gadget down,
  removes `badhid_ng.py`, restores `config.toml` + `custom-plugins/` from the
  newest backup, restarts pwnagotchi. `--reboot` to reboot after.

```bash
cd ~/plugins-wip/badhid-suite
sudo ./badhid_install.sh        # backup + install, prints your token + undo cmd
# ...test (see below)...
sudo ./badhid_restore.sh        # full undo, if you want it gone
```

**Why this is safe to try:** your Pi is managed over **ethernet**, so losing
`usb0` can't lock you out; the gadget change is **runtime-only** (configfs), so
a plain `sudo reboot` already restores the original USB gadget; `config.toml` is
backed up and the edit is validated with auto-rollback; and the plugin is a
single added file. Worst-case total reset: `sudo ./badhid_restore.sh --reboot`.

## The demo payloads (all harmless)

The suite ships three proof-of-life payloads. None of them run a command,
download anything, change anything, or persist — they only type characters.
Point them only at a machine you own.

| File | What it does | OS |
|---|---|---|
| `hello_world.duck` | Types one line into the focused window. The quietest proof it works. | any |
| `spooky_skull.duck` | Opens Notepad and types an ASCII skull & crossbones with "I SEE YOU" / "I'M COMING FOR YOU!". Looks sinister, does **nothing** but type text you can close without saving. | Windows (adapt for mac/Linux) |
| `rickroll.duck` | Opens the classic video in the default browser. | Windows (adapt for mac/Linux) |

The two Windows demos use the **Win+R Run** box to launch Notepad / open the
URL. Each file's header REM block has the one-line tweak for macOS/Linux (they
just use that OS's launcher key instead of Win+R).

## Using it

The control page (reachable at the logged URL, with your token) shows arm
state, the HID device status, your payload list, and a fire form. Auth: send
the token as `Authorization: Bearer <token>`, header `X-Auth-Token`, or a
`token=` form field.

Typical flow against one of your own old PCs:

1. Plug the pi into the target PC's USB port.
2. On the control page: **ARM** (opens a 120s window; one-shot by default).
3. On the target, click into where it should type — an empty text editor for
   `hello_world.duck` (the Windows demos open their own window, so just leave
   the target at the desktop).
4. **FIRE** the payload. One-shot arming disarms it again automatically after.

Quick CLI smoke test (from somewhere that can reach the bound URL):

```bash
TOKEN=your-long-token
BASE=http://127.0.0.1:8083      # or the tailscale URL from the log
curl -s -X POST -H "Authorization: Bearer $TOKEN" $BASE/arm
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  --data "payload=hello_world.duck&target=my-old-laptop" $BASE/fire
```

## Writing your own payloads

Drop `*.duck` (or `*.txt`) files in `payloads_dir`. Supported commands:

| Command | Meaning |
|---|---|
| `REM ...` / `# ...` | comment |
| `STRING <text>` | type the literal text |
| `STRINGLN <text>` | type the text, then Enter |
| `ENTER`, `TAB`, `ESC`, `UP`, `DELETE`, `F5`, ... | a named key (see `NAMED_KEYS`) |
| `GUI r`, `CTRL ALT DELETE`, `CTRL c` | a modifier combo (GUI/CTRL/ALT/SHIFT) |
| `DELAY <ms>` | pause |
| `DEFAULTDELAY <ms>` | implicit pause between following lines |
| `REPEAT <n>` | repeat the previous line n times |

US keyboard layout only for now. Keep payloads pointed at your own equipment.

## Troubleshooting

- **Server won't start, log says token refused:** `auth_token` is blank,
  a placeholder, or under 12 chars. Set a real random token.
- **Fire fails: "HID device /dev/hidg0 missing":** the gadget isn't up — run
  `setup_composite_gadget.sh` (step 3).
- **Fire fails: permission denied:** the pwnagotchi process must be able to
  write `/dev/hidg0` (runs as root on the stock image).
- **Lost usb0 after the setup script:** that's the documented risk — reach the
  pi via your fallback path and run `--teardown`, then reconcile the script's
  addressing with how your image builds its gadget (see NOTES.md).
- **`bind_scope="tailscale"` refuses to start:** no tailscale interface was
  found — that's the fail-safe. Start tailscale or use `auto`/`localhost`.

## Tests

```bash
cd badhid-suite
python3 tests/test_badhid_ng.py
```

Sandbox tests cover the parser, keymap, report emitter, token/bind logic, the
arm state machine, the fire gate (device write mocked), and payload-path
safety. **Not** covered in sandbox (needs a real-hardware pass): the actual
`/dev/hidg0` write reaching a host, `setup_composite_gadget.sh` keeping usb0
alive, and the live server binding. Per repo rule, this suite does **not**
graduate to `complete-plugins` until that on-device pass is done.
