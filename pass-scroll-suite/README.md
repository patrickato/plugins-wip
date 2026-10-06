# PassScroll

**Status: work-in-progress (in `plugins-wip`).** New plugin (not a rebuild of an
upstream). Logic is sound but it has **not** had a real-hardware pass or automated
tests yet - see `NOTES.md`.

Shows the most recent **N** cracked passwords on screen, rotating through them,
instead of surfacing only the single last crack. It reads from multiple sources,
auto-detects their format, dedupes, and keeps the newest N.

Target hardware: Raspberry Pi 4 + 3.5" MPI3501 TFT, jayofelony 64-bit image.

## Requirements & dependencies

- Python: standard library only (`os`, `glob`, `time`, `binascii`, `re`). No pip
  installs, no external deps.
- No config values are strictly required - the defaults read the plugins-wip
  data-bus canonical cracked-password paths, so with crack-house-suite / wpa-sec
  already writing there it works unmodified.

## Sources & formats

Reads every path in `sources` (globs allowed) and auto-parses three formats per
line:

- **hashcat 22000/2500 potfile** - `<hash>:<password>`, ESSID decoded from the
  hash's 6th `*`-field.
- **wpa-sec style** - `bssid:station:essid:password`.
- **plain** - `essid:password` (and `.cracked` sidecars in that shape).

Defaults (the data-bus canonical producers):

- `/etc/pwnagotchi/handshakes/crack_house_ng.potfile` (crack-house-suite)
- `/etc/pwnagotchi/handshakes/wpa-sec.cracked.potfile`
- `/etc/pwnagotchi/handshakes/my.potfile`
- `/etc/pwnagotchi/handshakes/OnlineHashCrack.cracked`
- `/etc/pwnagotchi/handshakes/*.cracked`

## Options

| Option | Default | What it does |
| --- | --- | --- |
| `sources` | data-bus paths above | files/globs to read cracks from |
| `max_entries` | 7 | how many recent cracks to rotate (5-10 sensible) |
| `scroll_interval` | 5 | seconds per entry |
| `refresh_interval` | 60 | seconds between re-reading the files |
| `show_ssid` | true | `SSID: pass` vs just `pass` |
| `mask` | false | show password as `****` |
| `max_len` | 22 | truncate the on-screen string |
| `position` | `[5, 95]` | `(x, y)` on your TFT - adjust to taste |
| `label` | `"pwd"` | label drawn before the value |

## Install (options - pick one)

1. **Drop-in (scp):** copy `passscroll.py` to `/etc/pwnagotchi/custom-plugins/`,
   add the block from `config.toml` to `/etc/pwnagotchi/config.toml`, then
   `sudo systemctl restart pwnagotchi`.
2. **Manual (editor):** paste `passscroll.py` into a new file under your
   `custom_plugins` dir; add the config block; restart.
3. **git sparse-checkout** of just `pass-scroll-suite/` from this repo, then copy
   the `.py` into place.

(A one-line `install.sh` and the jayofelony `custom_plugin_repos` archive method
will be added when/if this graduates to `complete-plugins`.)

## Notes

Displaying cracked passwords is your own data on your own device; `mask` is there
if a screen might be shoulder-surfed. Nothing is uploaded or transmitted - it only
reads local files.
