# badhid-suite — design & safety notes

Status: **new build**, A1+A2 slice of `BADUSB_AND_REMOTE_EXEC_IDEAS`
(in `patrickato/test-plugins`). Sandbox-green; **not** hardware-tested yet, so
it does not graduate to `complete-plugins` (CLAUDE.md workflow rule 1).

This is not a rebuild of an upstream community plugin — there's no original to
preserve. It's a from-scratch framework, so these notes are design rationale +
the safety argument rather than a bug writeup.

## Why this is a lab instrument and not malware

The whole suite is deliberately split so the part I built is dual-use
infrastructure and the part that would make it an attack *weapon* is left to
the operator and kept off the repo:

- **Shipped:** the USB-gadget plumbing, a DuckyScript-subset interpreter + US
  HID keymap, the arm/disarm model, token auth, `bind_scope`, the manual-fire
  UI, loud logging, and **three harmless demo payloads** (hello_world, an
  inert Notepad skull gag, and a rickroll) — all type-only, no commands.
- **Not shipped, by design:** every actual offensive payload (shells,
  credential capture, AV/defender tampering, persistence, exfiltration). Those
  are what turn keystroke injection into a compromise; the operator writes
  them, for their own authorized targets, and they stay out of this repo.

That split is the same line the deauth gate (`wifiJtest`) and
`crack-pipeline-suite` draw: build the capability + the safety gates for gear
you own, don't ship turnkey means to compromise systems you don't control.

## The honest limit of the "allowlist" here (important)

The `authorized_networks` convention (CONVENTIONS.md) works for radio/cracking
plugins because the code sees the BSSID/SSID it's about to act on and can
refuse. **A HID keyboard cannot do that** — USB HID is one-way output; the
device has no idea what host enumerated it. So `authorized_targets` here is
**advisory only**: it's logged on every fire as a record of intent, and the
empty-list warning still fires at load, but it is *not* a technical restriction
and the code never pretends it is (see the module docstring and the warnings in
`on_loaded`/`_fire`).

What *is* a real control:

- **Disarmed by default**, with an explicit arm step and an auto-expiring
  window (`arm_window_seconds`) + one-shot budget (`arm_one_shot`). Nothing can
  fire unless someone armed it, and it re-locks on its own.
- **No fire-on-plug-in** unless `fire_on_enumerate=true` *and* armed — off by
  default, so charging the pi or plugging it into your laptop does nothing.
- **Token auth** (≥12 chars, no placeholders) + least-exposed `bind_scope`,
  so the control surface isn't open on the LAN by accident.
- **Loud audit log** at WARNING on every arm/disarm/fire, naming the payload
  and the recorded target, with the reminder that the list is intent-only.

The remaining risk — plugging into the wrong machine — is physical and lives
with the operator. The README says so plainly rather than implying the config
removes it.

## A1 — the composite gadget (the genuinely tricky part)

pwnagotchi already owns the USB port in gadget mode to give you `usb0`. To add
a keyboard you need a **composite** gadget (ECM ethernet + HID together) via
`libcomposite`/configfs — you can't just bolt an HID gadget on without losing
`usb0`. P4wnP1 A.L.O.A. is the reference implementation.

Decisions:

- The **plugin never reconfigures the gadget.** `manage_gadget` is unsupported
  on purpose — a plugin silently rebuilding the live gadget during
  `on_loaded()` is exactly how you brick your own management link. Instead,
  `setup_composite_gadget.sh` is run **by hand**, with a 5-second abort window
  and a `--status`/`--teardown`, and the plugin just writes to the resulting
  `/dev/hidg0`.
- The HID function uses the standard 8-byte **boot-keyboard** report descriptor
  (modifier byte, reserved, 6 key slots), which is what the keymap/report code
  emits.
- The script's ECM MACs are locally-administered (`02:...`) and configurable at
  the top. If your image already builds its gadget via a systemd unit or
  `/boot` config, reconcile with that before running — this is the item most
  likely to need a per-image tweak, and the most likely cause of a dropped
  `usb0`. **This is the #1 thing to verify on the real-hardware pass.**

## A2 — the runner

- **Parser** (`parse_ducky`): a DuckyScript subset — REM/#, STRING, STRINGLN,
  named keys, GUI/CTRL/ALT/SHIFT combos, DELAY, DEFAULTDELAY, REPEAT. Raises
  `DuckyParseError` with a line number rather than half-typing a broken
  payload. `max_actions` caps a runaway file.
- **Keymap** (`CHAR_TO_HID`): US layout, built programmatically for letters,
  digits + shifted symbols, and punctuation. Non-US layouts are a future item.
- **Reports** (`actions_to_reports`): pure transform to an ordered list of
  `("report", 8-byte)` / `("delay", ms)` events. Keeping it pure is what makes
  the whole runner unit-testable with no device.
- **Device write** (`_write_stream`): the *only* part that needs real hardware,
  isolated behind one method so tests mock it and the real-hardware pass has a
  single obvious thing to verify.

## Config / fork conventions followed

- Section `[main.plugins.badhid_ng]` = file basename; `DEFAULTS` + `_opt*`
  readers (no `__defaults__` merge); real hooks only (`on_loaded(self)`,
  `on_unload(self, ui)`, `on_webhook(self, path, request)`); server via
  `make_server` in a daemon thread so `on_loaded` never blocks; `bind_scope`
  with the URL logged and shown — same as web2ssh_ng/handshaker.
- `>>> USER INPUT REQUIRED <<<` marker on `auth_token`, which refuses
  placeholders/short values exactly like web2ssh_ng's credential gate.

## Real-hardware pass checklist (before graduating)

1. `setup_composite_gadget.sh` brings up `/dev/hidg0` **without dropping usb0**
   on your actual image (have a Wi-Fi/Tailscale fallback up). Tune MACs/
   addressing if needed; note what you changed.
2. Control server binds at the expected `bind_scope` URL; token auth rejects a
   wrong/absent token.
3. Arm → fire `hello_world.duck` into a text editor on one of your own old PCs;
   confirm the line appears and one-shot disarm re-locks. Then try
   `spooky_skull.duck` and `rickroll.duck` (Windows) to confirm Win+R launches.
4. A combo payload (e.g. `GUI r` then `STRINGLN notepad`) behaves on that host.
5. `fire_on_enumerate` behaves only when explicitly enabled **and** armed.

## Deliberately out of scope here

- Non-US keyboard layouts.
- Any offensive payload library (operator-authored, off-repo).
- The remote-exec / multi-device coordination tracks (B1–B3 in the ideas doc) —
  separate slices, separate build decisions.
