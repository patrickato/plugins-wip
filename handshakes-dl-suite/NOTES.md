# Research notes: HandshakesDLNG

Source: `handshakes-dl-hashie.py` (itsdarklikehell/pwnagotchi-plugins,
author me@sayakb.com). This supersedes both that file and the plainer
`handshakes-dl.py`, which was removed from the `test-plugins` master
list as a redundant subset - see
`test-plugins:plugin-upgrade-proposals/cluster-29-handshakes-dl/NOTES.md`
for that history.

## Bugs found in the original (source-verified)

1. **`.pcap`-vs-`.pcapng`** - the recurring bug throughout this whole
   project. `glob.glob(os.path.join(handshakes_dir, "*.pcap"))` never
   matches this fork's real `.pcapng` captures, and
   `os.path.basename(path)[:-5]` assumes a 5-character extension
   (`.pcap`), which would mis-slice a 7-character `.pcapng` name even if
   the glob were fixed.

2. **A second, independent bug found during this rebuild, not previously
   documented**: `send_from_directory(directory=dir, filename=path,
   as_attachment=True)`. Verified directly against the Flask actually
   installed in this dev environment (3.1.3) that `send_from_directory`'s
   second parameter was renamed from `filename` to `path` - calling it
   with `filename=` raises `TypeError` immediately:
   ```
   >>> send_from_directory('/tmp', filename='x.txt')
   TypeError: send_from_directory() missing 1 required positional argument: 'path'
   ```
   This fork's own `pyproject.toml` declares an unversioned `flask`
   dependency (no pin), so a fresh install pulls whatever's current -
   meaning this second bug is very likely live on real installs
   independent of the `.pcap` issue. Note: this specific bug only
   affects the plain `handshakes-dl.py`'s download route (which
   hardcoded `filename=path + '.pcap'`) and `handshakes-dl-hashie.py`'s
   download route the same way (`filename=path`) - both use the old
   keyword name.

## What changed in this rewrite

- Fixed both bugs above: correct `.pcapng` matching/slicing (via
  `os.path.splitext`-equivalent length math), and `send_from_directory`
  called with the current `path=` keyword.
- File size and capture date (`os.path.getsize`/`os.path.getmtime`) shown
  per row.
- GPS file detection (`.gps.json`/`.geo.json`) added as its own
  downloadable entry per capture, same treatment as the existing hash
  formats (`.2500`/`.16800`/`.22000`) - purely additive, reads a file
  another plugin already writes, no new dependency.
- Sorted newest-first by `mtime`.
- A `max_results` config option (default 200) caps how many captures are
  shown/zipped at once, with a visible "showing N of M" note when
  truncated - chosen over full pagination for simplicity (see the design
  note in README.md for the reasoning, per the user's "use your best
  recommendation" call on this point).
- A "download all as ZIP" endpoint (`download-all.zip`) that bundles
  every currently-shown capture plus its hash/GPS files using
  `zipfile`/`io.BytesIO`, returned as a Flask `Response` with the correct
  `Content-Disposition` header.
- Deliberately did not add anything about cracked-password content or
  crack status - that's `wpa-sec-list.py`'s job, already tracked
  separately; duplicating it here would create two places to maintain
  the same feature.

## Testing done (sandbox, no real hardware)

20 tests in `tests/test_handshakes_dl_ng.py`, notably including a **full
page render against this fork's actual `base.html` template** (pulled
from the cloned jayofelony source, not a mock) via a real Flask app/
request context - not just a logic-level check. Covered: `.pcap` files
are never collected, `.pcapng` files are, hash/GPS files are attached to
the correct capture only, newest-first sort order, byte/KB size
formatting, the `max_results` cap and its truncation notice, a real
single-file download via the current Flask API, a clean 404 on a missing
file (rather than a crash), and a real ZIP built and verified to contain
exactly the expected files (capture + hash + GPS, never the legacy
`.pcap` file).

Also confirmed `handshakes_dl_ng.py` registers correctly against the
**real** jayofelony plugin loader (`Plugin.__init_subclass__`, loaded
from the actual cloned framework source), with `on_config_changed` and
`on_webhook` present as expected.

One test bug caught and fixed along the way, worth noting for anyone
extending this suite: an early version of the `max_results` cap test
checked for the substring `"older"` in the rendered page and got a false
positive, because the word **"placeholder"** in the search box's HTML
contains "older" as a substring. Fixed by checking for the actual
download-link path instead of a bare filename substring.

## Still open / needs real-hardware testing

- Not yet tested against a live pi + 3.5" TFT + jayofelony 64-bit image.
- The real page render was tested against this fork's actual
  `base.html`, but the actual browser rendering/CSS/JS behavior (the
  client-side search filter, the table layout) hasn't been visually
  checked - worth a quick look once installed.
- ZIP download performance/memory use on a genuinely large handshake
  folder (hundreds of files) hasn't been measured - the whole ZIP is
  built in memory (`io.BytesIO`) before sending, which is fine for
  reasonable capture counts but could matter if `max_results` is raised
  very high.

## Config verified against upstream (2026-09-28)

Compared `config.toml` against the real upstream sample
(`itsdarklikehell/pwnagotchi-plugins/configs/handshakes-dl-hashie.toml`,
which just sets `enabled = true`) and the original's `__defaults__`
(`enabled` only - `max_results` doesn't exist upstream at all, since the
original always showed every capture with no limit). Checked
`self.options.get("max_results")` in `handshakes_dl_ng.py` - it's
present and documented in `config.toml`. Renamed from
`config.toml.example` to `config.toml`.

## Original config preserved

A real original config file for `handshakes-dl-hashie.py` was found (exact match) at
`itsdarklikehell/pwnagotchi-plugins/configs/handshakes-dl-hashie.toml`. Preserved verbatim in this suite's own folder as
`config.original.toml`, per the project's standing config-preservation requirement,
for reference/troubleshooting if the rebuilt `config.toml` above ever
needs comparing against the source.
