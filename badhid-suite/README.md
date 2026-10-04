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
  manual-fire web UI, and **one harmless demo payload** (`demo_hello.duck`,
  which just echoes a line).
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
   sudo cp badhid-suite/payloads/demo_hello.duck /etc/pwnagotchi/badhid_ng/payloads/
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

## Using it

The control page (reachable at the logged URL, with your token) shows arm
state, the HID device status, your payload list, and a fire form. Auth: send
the token as `Authorization: Bearer <token>`, header `X-Auth-Token`, or a
`token=` form field.

Typical flow against one of your own old PCs:

1. Plug the pi into the target PC's USB port.
2. On the control page: **ARM** (opens a 120s window; one-shot by default).
3. Click into a window on the target you want to type into.
4. **FIRE** `demo_hello.duck`. It types the benign echo line. One-shot arming
   disarms it again automatically.

Quick CLI smoke test (from somewhere that can reach the bound URL):

```bash
TOKEN=your-long-token
BASE=http://127.0.0.1:8083      # or the tailscale URL from the log
curl -s -X POST -H "Authorization: Bearer $TOKEN" $BASE/arm
curl -s -X POST -H "Authorization: Bearer $TOKEN" --data "payload=demo_hello.duck&target=my-old-laptop" $BASE/fire
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
