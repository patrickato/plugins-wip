# DisplayVersionNG

**Status: work-in-progress (in `plugins-wip`).** Not yet moved to
`complete-plugins` - still needs a real-hardware pass before it's
considered done.

An extended rebuild of `display_version.py` (itsdarklikehell/
Teraskull) - adds the running pwnagotchi software version to the
display as a small UI element.

Target hardware: Raspberry Pi 4 + 3.5" TFT screen, jayofelony 64-bit
pwnagotchi image.

## Requirements & dependencies

- Python: none beyond the standard library. The original also
  declared an unused `scapy` pip dependency (never imported anywhere
  in the file) - dropped here, the same stray-dependency pattern
  found repeatedly elsewhere in this audit.

## What's changed vs. the original

No bugs were found in the original - this is about as simple a
plugin as this audit has covered (one element, one line of update
logic). This rebuild is a pure feature/cleanup addition, not a
bugfix one.

1. **Configurable position**, replacing the hardcoded `(185, 110)`
   (still the default).
2. **Dropped the unused `scapy` dependency declaration.**

## What's kept

- The one-line update logic (`f"v{pwnagotchi.__version__}"`),
  unchanged - there was nothing wrong with it.

## Install

1. Copy `display_version_ng.py` into your custom plugins folder.
2. Add the block from `config.toml` to `/etc/pwnagotchi/config.toml`.
3. If you were running the original `display_version.py`, disable/
   remove it first.
4. Restart pwnagotchi:
   ```
   sudo systemctl restart pwnagotchi
   ```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Position looks wrong on your screen | Set `position_x`/`position_y` explicitly |

## Still open / needs real-hardware testing

- Real on-screen position for the 3.5" TFT hasn't been checked yet.
