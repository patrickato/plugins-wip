# CrackPipelineNG

**Status: work-in-progress (in `plugins-wip`).** Not yet moved to
`complete-plugins` - no real-hardware hashcat run has been possible in this
sandbox (see NOTES.md's "known limitations").

**FOR YOUR OWN AUTHORIZED NETWORKS ONLY.** This plugin runs hashcat against
captured WiFi handshakes. `authorized_networks` in `config.toml` is empty by
default - every capture still gets classified (a `.route` sidecar file
telling you WEP / open / WPA), but hcxpcapngtool/hashcat are **never**
invoked against anything until you explicitly list a network. Read
`config.toml`'s comments before enabling cracking.

## What this is, and why it's one suite instead of four

This merges four plugins the repo owner (`patrickato`) wrote for his own
fork - `ClaudeCrackAuto.py`, `RuleMutationCrack.py`, `HashFormatDetector.py`,
and `best_quickdic.py` (all under `test-plugins/pwnagotchi-plugins/`) - into
one properly-architected pipeline, instead of four separate plugins each
spinning up their own threads/locks and independently reinventing "convert
with hcxpcapngtool, then run hashcat." Keeping them separate would mean:

- Up to four concurrent hashcat/hcxpcapngtool subprocesses fighting over one
  Pi's CPU if more than one fired at the same time (a real risk - a single
  handshake capture is exactly the kind of event all four listened for).
- Four different, inconsistently-named allowlist options (`whitelist`,
  `targets`, and no allowlist at all in two of them) to keep in sync.
- **best_quickdic's actual missing-authorization bug staying unfixed** - see
  "The most important change" below. Merging was the only way to guarantee
  the same gate applies everywhere cracking actually happens.

This suite is a single `CrackPipelineNG` class with one background worker
thread (a real job queue, not four independent thread-spawners), one
`authorized_networks` allowlist that gates every actual hashcat invocation,
and one consistent set of config options. See NOTES.md for the full
per-original bug list and design writeup.

## The most important change: best_quickdic's scoping was corrected, not preserved

`best_quickdic.py`'s own docstring argued it didn't need an
`authorized_networks`-style allowlist, since it only post-processes a
handshake file already captured, not an active behavior against a network.
That reasoning doesn't hold: automatically running hashcat against the
password of every network a pwnagotchi happens to pick up - not just ones
you own - is an unauthorized password-cracking attempt against other
people's networks, regardless of whether it ever touches a radio directly.

**In this merged plugin, the same `authorized_networks` allowlist (empty by
default, same name/semantics as this repo's `wifi-jammer-suite`) gates every
actual hashcat invocation - plain pass and rule pass alike. No exceptions.**
See NOTES.md for the full writeup of this correction.

## Install

Requires, on the pwnagotchi itself:
- `hcxtools` (provides `hcxpcapngtool`) - `sudo apt install hcxtools`
- `hashcat` - only needed if you ever set `run_local = true` (the default);
  if you always crack elsewhere, you can skip installing it and leave
  `run_local = false`.

Steps:
1. Copy `crack_pipeline_ng.py` into your custom plugins folder
   (`custom_plugins` in `config.toml`, typically
   `/etc/pwnagotchi/custom-plugins/`).
2. Add the block from `config.toml` to `/etc/pwnagotchi/config.toml`.
3. **Fill in `authorized_networks`** with your own network(s) - the plugin
   loads and classifies captures either way, but will never run hashcat
   against anything until you do this.
4. Put at least one `*.txt` wordlist in `wordlist_folder` (default
   `/etc/pwnagotchi/wordlists`) if you want local cracking (`run_local =
   true`, the default).
5. Restart pwnagotchi:
   ```
   sudo systemctl restart pwnagotchi
   ```
6. Visit `http://pwnagotchi.local:8080/plugins/crack_pipeline_ng/` for a
   status page: queue depth, what's currently being processed, and recent
   results.

## Config walkthrough

See `config.toml` for every option with full comments. In short:

| Option | Default | What it does |
|---|---|---|
| `authorized_networks` | `[]` | The real safety gate - BSSID or SSID entries. Empty = classification only, no cracking ever. |
| `wordlist_folder` | `/etc/pwnagotchi/wordlists` | Every `*.txt` in here is tried, sorted by name. |
| `max_wordlists_per_run` | `0` | Cap wordlists tried per capture per pass; `0` = no limit. |
| `per_wordlist_timeout_secs` | `300` | Plain-pass timeout per wordlist. |
| `rules_file` | `/usr/share/hashcat/rules/best64.rule` | Rule file for the second (mutation) pass, tried only if the plain pass finds nothing. |
| `rule_pass_timeout_secs` | `1800` | Rule-pass timeout per wordlist. |
| `convert_timeout_secs` | `120` | hcxpcapngtool conversion timeout. |
| `run_local` | `true` | `false` stops after export - no hashcat is ever run on-device. |
| `export_dir` | `/etc/pwnagotchi/crack_pipeline_ng/exports` | Where `.hc22000`/`.cracked` files land. |
| `log_file` | `/etc/pwnagotchi/crack_pipeline_ng/results.log` | Human-readable results log. |
| `notify_enabled` | `false` | Push results through apprise-notify-suite (or whatever `apprise_plugin_name` points at). |
| `apprise_plugin_name` | `apprise_notify_ng` | Sibling plugin file basename to look up for notifications. |
| `show_on_screen` | `true` | Small on-screen status badge (queue depth + last result). |
| `position_x` / `position_y` | `0` / `150` | On-screen badge position. |

## What you'll see

- **`.route` sidecar files** next to every capture (`<capture>.pcapng.route`)
  - even ones not in `authorized_networks` - telling you what was detected
  and what to do about it (aircrack-ng for WEP, "nothing to crack" for open
  networks, "classified crackable but not authorized" for an unlisted
  WPA/WPA2/PMKID network, or that it's queued for cracking).
- **An on-screen badge** (`CRK`) showing queue depth and the last result
  (`idle`, `queued`, `converting`, `cracking`, `cracked!`, `not found`,
  `exported`, ...).
- **A webhook status page** at `/plugins/crack_pipeline_ng/` with the live
  queue depth, what's currently being worked, and the last 50 results.
- **Notifications** (if `notify_enabled = true` and apprise-notify-suite is
  installed/configured) for exports, crack successes, and "not found"
  results.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Nothing is ever cracked, only `.route` sidecars appear | Check `authorized_networks` isn't empty and exactly matches your network's real BSSID/SSID (startup log says how many of each it loaded) |
| `.route` sidecar says `route: unknown` | The capture's AP argument had no `encryption` field at all (a bare-MAC-string shape this fork's agent.py sometimes produces) - this plugin can't classify it automatically; inspect the capture by hand |
| Queue depth keeps growing, nothing seems to finish | A single wordlist/rule pass can legitimately take a long time (`per_wordlist_timeout_secs`/`rule_pass_timeout_secs`) - the queue is strictly one-job-at-a-time by design, so a slow job blocks everything behind it. Lower the timeouts or trim your wordlists if this is a problem on your hardware. |
| `exported` instead of a crack attempt | Either `run_local = false`, `hashcat` isn't installed, or no `*.txt` wordlists were found in `wordlist_folder` - check the log for which |
| No notifications arrive | Check `notify_enabled = true`, that `apprise_plugin_name` matches your actual notification plugin's file basename, and that plugin's own `urls`/`config_path` are set - startup log (`on_ready`) says whether the sibling was found at all |

## Still open / needs real-hardware testing

See NOTES.md's "known limitations" section - no real-hardware hashcat run
has been possible in this sandbox.
