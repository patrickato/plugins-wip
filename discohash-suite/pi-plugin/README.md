# DiscoHashNG (pi-side plugin)

Runs **on the pwnagotchi itself**. Converts new handshakes to hashcat
22000 hashes and posts them (with GPS, if available) to a private Discord
channel via webhook, using this fork's real handshake directory and file
format.

Rewrite of the original `DiscoHash` plugin by flamebarke/v0yager - see
`../NOTES.md` for exactly what was broken in the original and what
changed here.

## Requirements

- Hardware: any pwnagotchi running the jayofelony 64-bit image (verified
  against a Pi 4 + 3.5" TFT setup)
- System package: `hcxtools` (provides `hcxpcapngtool` and `hcxhashtool`)
- Python package: `requests`
- A Discord server you control, with a webhook URL for one channel (see
  `../SETUP.md`)

## Install

1. Install the system dependency:
   ```
   sudo apt install hcxtools
   ```
2. Copy `discohash_ng.py` into your custom plugins folder (matches
   `custom_plugins` in your `config.toml`, typically
   `/etc/pwnagotchi/custom-plugins/`).
3. Add the block from `config.toml` to
   `/etc/pwnagotchi/config.toml`, and fill in `webhook_url` (see
   `../SETUP.md` step 6 for how to get one).
4. Add `discohash_ng` to your `main.plugins.enabled` list if your config
   uses one, or confirm `enabled = true` is set in the plugin's own
   section (either is fine on this fork - the block above sets it per-plugin).
5. Restart pwnagotchi:
   ```
   sudo systemctl restart pwnagotchi
   ```
6. Check it loaded cleanly:
   ```
   tail -f /etc/pwnagotchi/log/pwnagotchi.log | grep DiscoHashNG
   ```
   You should see `[DiscoHashNG] plugin loaded`. If you see the
   `webhook_url is not set` warning instead, double check step 3.

## How it behaves

- **Live captures:** every new handshake is processed immediately via the
  `on_handshake` hook - no need to wait for an epoch boundary.
- **Backlog catch-up:** once per epoch, *while internet is available*, it
  also sweeps the whole handshake folder for anything it hasn't posted
  yet (covers handshakes captured while offline, or ones that existed
  before you installed the plugin).
- **No duplicates, ever:** every successfully-posted filename is recorded
  in `state_file` (default `/etc/pwnagotchi/discohash_ng.posted.json`).
  Reboots, plugin reloads, and re-scans will never re-post the same
  handshake.
- **Retries only on failure:** if a Discord post fails (bad network,
  Discord outage), it retries up to `retry_attempts` times, waiting
  `retry_delay` seconds between tries. A handshake is only marked as
  "posted" once a post actually succeeds - a successful first attempt
  never triggers a retry or delay at all.
- **Not enough packets for a hash yet:** if `hcxpcapngtool` can't produce
  a `.22000` file from a given capture, that file is simply left alone
  (not marked posted) and retried automatically the next time it's seen -
  no error, no crash.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Nothing ever posts | `webhook_url` missing/wrong in `config.toml`, or Discord webhook was deleted/regenerated |
| `hcxpcapngtool failed` in the log | `hcxtools` not installed - `sudo apt install hcxtools` |
| Same-looking hash posted twice | Check `state_file` is writable - if the plugin can't save state, it'll try again after every restart |
| GPS fields say "N/A" | Normal if you don't have a `.gps.json`/`.geo.json` for that handshake (no GPS plugin running, or no fix at capture time) - not a bug |
