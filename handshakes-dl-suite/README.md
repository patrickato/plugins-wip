# HandshakesDLNG

**Status: work-in-progress (in `plugins-wip`).** Not yet moved to
`complete-plugins` - still needs real-hardware testing before it's
considered done.

A rebuild of `handshakes-dl-hashie.py`, which itself made the plainer
`handshakes-dl.py` redundant (see
`test-plugins:plugin-upgrade-proposals/cluster-29-handshakes-dl/NOTES.md`
for that history). Adds a page to the pwnagotchi's web UI for browsing
and downloading captured handshakes, their converted hash files, and any
GPS data - with file size/date, a newest-first cap, and a
download-all-as-ZIP button.

Target hardware: Raspberry Pi 4 + 3.5" TFT screen, jayofelony 64-bit
pwnagotchi image.

## Requirements & dependencies

- Hardware: any pwnagotchi running the jayofelony 64-bit image (verified
  against a Pi 4 + 3.5" TFT setup)
- No extra apt or pip packages needed - only the Python standard library
  (`glob`, `io`, `os`, `zipfile`) plus `Flask`, which pwnagotchi's web UI
  already depends on. Nothing new to install.
- No secrets, tokens, or external accounts needed at all - unlike the
  DiscoHash Suite, there's no user-input-required section here beyond the
  one config option below, since this plugin only reads files already on
  the pi and doesn't talk to anything external.

## What's fixed vs. the original

1. **The `.pcap`-vs-`.pcapng` bug** - the original's glob filter and
   filename-length math both assumed a 5-character `.pcap` extension.
   This fork only ever writes `.pcapng` (7 characters), so the page
   always came back empty. Fixed throughout.
2. **A second, independently-confirmed bug**: the original calls
   `send_from_directory(directory=dir, filename=path, ...)`. Current
   Flask (verified against Flask 3.1.3, and this fork's own
   `pyproject.toml` pins an unversioned `flask` dependency, so a fresh
   install pulls whatever's current) renamed that parameter from
   `filename` to `path` - the old call raises `TypeError` immediately on
   a modern install, independent of the extension bug. Fixed to use the
   current parameter name.

## What's added

- **File size and capture date** shown per row, not just a bare filename.
- **GPS badge/link** - if a `.gps.json`/`.geo.json` exists next to a
  capture (written by whatever GPS plugin you're running), it shows up as
  its own downloadable entry, same as the existing hash-format links.
- **Sorted newest-first** by default, instead of whatever order the
  filesystem happened to return.
- **"Download all as ZIP"** - one button bundles every currently-shown
  capture (plus its hash/GPS files) into a single `.zip`.
- **A result cap (`max_results`, default 200)** instead of an unbounded
  list - see "Design note on pagination" below for why this was chosen
  over true multi-page navigation.

**Deliberately not added:** anything about cracked-password content or
crack status - that's `wpa-sec-list.py`'s job, and duplicating it here
would just create two places to maintain the same feature.

## Design note on the result cap vs. pagination

A full multi-page UI (page 1/2/3 links, "next"/"previous", preserving
your current search filter across pages) is real added complexity - more
state to track, more edge cases, more surface area for something to break
on a screen-free web page most people load, glance at, and download from
once. A simple newest-first cap with a configurable `max_results` and a
visible "showing N of M" note when truncated solves the actual problem
(an unbounded list on a very large handshake folder) with far less code
and far less that can go wrong, at the cost of not being able to reach
very old captures from the web page itself once you're past the cap
(they're still on disk - SSH/SFTP still reaches them either way). If you
outgrow this later, we can revisit.

## Install

1. Copy `handshakes_dl_ng.py` into your custom plugins folder
   (`custom_plugins` in `config.toml`, typically
   `/etc/pwnagotchi/custom-plugins/`).
2. Add the block from `config.toml.example` to
   `/etc/pwnagotchi/config.toml`.
3. Restart pwnagotchi:
   ```
   sudo systemctl restart pwnagotchi
   ```
4. Visit `http://<pi-ip-or-hostname>:8080/plugins/handshakes_dl_ng/` in a
   browser.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Page loads but shows no captures | Check you actually have `.pcapng` files in your handshake folder - `ls /etc/pwnagotchi/handshakes/*.pcapng` |
| "Plugin not ready" | `on_config_changed` hasn't fired yet - give it a few seconds after a fresh restart |
| A download link 404s | The file was deleted/moved after the page was rendered - reload the page |
| ZIP download is slow/times out on a big handshake folder | Lower `max_results` in config.toml - the ZIP only bundles what's currently shown |
