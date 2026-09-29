# BanthexNG

**Status: work-in-progress (in `plugins-wip`).** Not yet moved to
`complete-plugins` - still needs real-hardware testing before it's
considered done.

A rebuild of `banthex-de.py`. Auto-uploads captured handshakes to
[banthex.de](https://banthex.de) (a wpa-sec-compatible cracking service)
and optionally downloads cracked results back as a potfile.

`banthex-de.py` was picked over its near-identical sibling `banthex.py`
because `banthex.py` has its own extra bug (a corrupted-state-file
recovery path that deletes the wrong file); `banthex.py` was removed
from the master list rather than fixed, since this one was the better
starting point. See
`test-plugins:plugin-upgrade-proposals/cluster-06-cloud-crack-upload/NOTES.md`
for that history.

Target hardware: Raspberry Pi 4 + 3.5" TFT screen, jayofelony 64-bit
pwnagotchi image.

## Requirements & dependencies

- Hardware: any pwnagotchi running the jayofelony 64-bit image (verified
  against a Pi 4 + 3.5" TFT setup).
- Python: `requests` (pip) - the only non-stdlib import.
- **>>> USER INPUT REQUIRED <<<**: an account and API key at
  [banthex.de](https://banthex.de/index.php/register/) - see
  `config.toml`.

## What's fixed vs. the original

1. The recurring bug in this whole project: `filename.endswith('.pcap')`
   never matches this fork's real `.pcapng` captures. Fixed.
2. A handshake that failed to upload once was added to a permanent skip
   list with nothing ever clearing it - a single transient network
   hiccup meant it would never be retried again until the whole plugin
   reloaded. Fixed with bounded retries (`max_upload_attempts`) - the
   same fix already applied to `hashespwnagotchi_ng.py` in this same
   cluster, for consistency across both.

## What's added

- `upload_timeout` / `download_timeout` config options (the original
  hardcoded both at 30 seconds with no way to change them).
- `download_check_interval_hours` config option (the original hardcoded
  "don't re-download more than once per hour" with no way to change it).
- Bounded upload retries (fix #2 above).

## What's deliberately unchanged - worth knowing

`on_webhook()` sets your `api_key` as a plaintext cookie on a redirect to
banthex.de. That's how this service's own web-based auth works (the same
pattern the real wpa-sec site uses) - it's not something this plugin can
change on its own without breaking compatibility with the service. Worth
knowing: if anything is inspecting that redirect (a proxy in the
middle, browser history on a shared machine), your API key is visible in
it. Nothing to do about it here, just flagging it so it's not a surprise.

## Install

1. Copy `banthex_ng.py` into your custom plugins folder (`custom_plugins`
   in `config.toml`, typically `/etc/pwnagotchi/custom-plugins/`).
2. Add the block from `config.toml` to
   `/etc/pwnagotchi/config.toml`, filling in your banthex.de `api_key`
   (`>>> USER INPUT REQUIRED <<<`).
3. Restart pwnagotchi:
   ```
   sudo systemctl restart pwnagotchi
   ```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| "api_key isn't set" / "api_url isn't set" | Fill in both in `config.toml` under `[main.plugins.banthex_ng]` |
| Nothing ever uploads | Check the AP isn't in your `whitelist`; check your banthex.de API key is valid - that side can't be verified from a sandbox, only on real hardware |
| A handshake seems permanently stuck / never uploads | Check the logs for "giving up on ... after N failed attempts" - it hit `max_upload_attempts`; raise that value if this keeps happening |
| `banthex.cracked.potfile` never updates | Check `download_results = true` is set, and that more than `download_check_interval_hours` has passed since the last download |
