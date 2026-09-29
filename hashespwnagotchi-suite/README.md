# HashesPwnagotchiNG

**Status: work-in-progress (in `plugins-wip`).** Not yet moved to
`complete-plugins` - still needs real-hardware testing (and confirming
the hashes.pw account/API side actually works) before it's considered
done.

A rebuild of `hashespwnagotchi.py`. Converts captured handshakes to
`.22000`/`.16800` hash files locally (via `hcxpcapngtool`) and uploads
them to [hashes.pw](https://hashes.pw). For captures that couldn't be
converted at all, it also checks whether GPS data is available for them
(reading the same `.gps.json`/`.geo.json`/`.paw-gps.json` sidecar files
this project's other plugins already produce or read, including
`gps_tagger_ng.py`'s own `.gps.json` sidecar).

Target hardware: Raspberry Pi 4 + 3.5" TFT screen, jayofelony 64-bit
pwnagotchi image.

## Why this one was rebuilt, not just left alone

The original had a real, confirmed security issue: it built
`hcxpcapngtool`/`tcpdump` commands using Python string formatting and
ran them through a real shell. This fork's handshake filenames embed the
AP's ESSID directly (`{ESSID}_{BSSID}.pcapng`), and an ESSID is a value
**any nearby device can broadcast as literally anything** - including
shell metacharacters. A maliciously-named AP could have injected
arbitrary commands, run as root, the moment this plugin tried to convert
a handshake captured from it. That's fixed here (see "What's fixed"
below) - this rebuild is what makes it safe to run at all, not just
better.

## Requirements & dependencies

- Hardware: any pwnagotchi running the jayofelony 64-bit image (verified
  against a Pi 4 + 3.5" TFT setup).
- `hcxtools` (provides `hcxpcapngtool`) must be installed:
  ```
  sudo apt-get install libcurl4-openssl-dev libssl-dev zlib1g-dev
  git clone https://github.com/ZerBea/hcxtools.git
  cd hcxtools && make && sudo make install
  ```
  The plugin checks for `hcxpcapngtool` on `PATH` when it loads and logs
  a clear error immediately if it's missing, rather than failing
  confusingly on the first real capture.
- `tcpdump` (used only as a fallback when trying to identify an AP for a
  PMKID that needs repairing and no AP info was available from the
  capture itself) - already present on virtually every pwnagotchi image,
  nothing extra to install for it specifically.
- Python: `requests` (pip) - the only non-stdlib import.
- **>>> USER INPUT REQUIRED <<<**: an account and API key at
  [hashes.pw](https://hashes.pw) - see `config.toml`.

## What's fixed vs. the original

1. **The security issue above** - every external command now runs via
   `subprocess.run([...], shell=False)` with each argument passed
   separately. Nothing from a filename (or anything else) is ever
   interpreted by a shell, so there is no injection surface regardless
   of what an AP names itself.
2. `on_config_changed()` referenced `self.status`, which was never
   assigned anywhere (only `self.report` was) - setting the `interval`
   option crashed this method every time, silently preventing the
   startup batch-conversion pass from ever running. Fixed.
3. The batch-conversion pass filtered for `.pcap` - this fork only
   writes `.pcapng`. Fixed (this bug was masked by bug #2 in the
   original - you'd never even get this far without hitting that crash
   first, if `interval` was set).
4. Filename/extension parsing used `path.split(".")[0]`, which breaks if
   an ESSID contains a period (a legal SSID character). Fixed with
   `os.path.splitext()` throughout.
5. The whitelist-exclusion call was present in the source but commented
   out - re-enabled.
6. A Python-2-only `.encode("hex")` call in one branch of the PMKID
   repair logic - fixed to match the (already-correct) fallback branch's
   `.encode().hex()`.
7. A handshake that failed to upload once was permanently blacklisted
   for the rest of that run, with no retry - a single transient network
   hiccup meant it would never be retried again until the whole plugin
   reloaded. Fixed with bounded retries (see `max_upload_attempts`
   below).
8. A bare-indexed API response field (`response["token"]`) that could
   raise an uncaught `KeyError` if the API ever returned a different
   shape - fixed to raise the same `ValueError` the surrounding code
   already handles cleanly.

## What's added

- `hcxpcapngtool_timeout` / `upload_timeout` config options, so a
  corrupt capture or a hung connection can't block the plugin
  indefinitely (the original had no timeout on either).
- A startup check for `hcxpcapngtool` on `PATH`, with one clear error
  logged immediately if it's missing.
- Bounded upload retries with a configurable delay (fix #7 above).

## Install

1. Install `hcxtools` (see Requirements above) if you don't already have
   it - other plugins in this project (the hashie family, DiscoHashNG)
   may have already installed it for you.
2. Copy `hashespwnagotchi_ng.py` into your custom plugins folder
   (`custom_plugins` in `config.toml`, typically
   `/etc/pwnagotchi/custom-plugins/`).
3. Add the block from `config.toml` to
   `/etc/pwnagotchi/config.toml`, filling in your hashes.pw `api_key`
   (`>>> USER INPUT REQUIRED <<<`).
4. Restart pwnagotchi:
   ```
   sudo systemctl restart pwnagotchi
   ```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| "hcxpcapngtool not found on PATH" at load | Install `hcxtools` (see Requirements) |
| "api_key isn't set" / "api_url isn't set" | Fill in both in `config.toml` under `[main.plugins.hashespwnagotchi_ng]` |
| Nothing ever uploads | Check the AP isn't in your `whitelist`; check `hashes.pw` account/API key are valid - that side can't be verified from a sandbox, only on real hardware |
| A handshake seems permanently stuck / never uploads | Check the logs for "giving up on ... after N failed attempts" - it hit `max_upload_attempts`; raise that value or check hashes.pw's status if this keeps happening |
