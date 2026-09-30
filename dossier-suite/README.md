# DossierNG

**Status: work-in-progress (in `plugins-wip`).**

A brand-new plugin, built from scratch for this project's post-
cluster-review "red-team-ideas" brainstorm (idea #1: "unified target
dossier"). It is a **pure read-and-assemble/report plugin** - it does
not scan WiFi or Bluetooth itself, does not run any bettercap command,
and never writes to or modifies any other suite's files.

## The problem it solves

If you want to know everything this pwnagotchi has learned about one
specific target network - was it cracked? what's the password? where
was it GPS-tagged? how long did it take to capture the handshake? what
Bluetooth devices (especially trackers) were seen nearby around the
same time? - you currently have to check **four separate plugins'
status pages** and manually cross-reference them by hostname:

- crack-house-suite's page (cracked password)
- timer-suite's page (capture timing)
- gps-tagger-suite's page/files (GPS tag)
- bluetooth-recon-suite's page (nearby Bluetooth devices, and its own
  network correlation)

DossierNG does that cross-referencing for you and presents **one
assembled page per target**, sorted by how fully documented each
target already is.

## How it works

On a timer (`refresh_interval_seconds`, default 30s - not on every
single page load), DossierNG reads:

1. **crack-house-suite's potfile** directly, for the cracked password.
2. **timer-suite's CSV** directly, for capture timing (using the most
   recent row per network if there's more than one).
3. **gps-tagger-suite's tag directory** directly, for the GPS
   coordinates (using the most-recently-modified tag file per
   hostname).
4. **bluetooth-recon-suite's device table**, but *not* to re-derive
   correlation - bluetooth-recon-suite has already computed, for every
   Bluetooth device it has seen, which WiFi hostnames were active
   nearby around the same time (`correlated_networks`). DossierNG just
   inverts that field: for a given hostname, "which Bluetooth device
   records list this hostname in their own `correlated_networks`?"
   See NOTES.md for the full reasoning behind reading this instead of
   re-implementing the time-window matching.

Every one of those four reads is independently guarded - if one
source's file/directory is missing, empty, or unparseable, the other
three still populate normally. Nothing here scans anything or produces
new data; it only assembles data four other suites already produced.

## Install

1. Copy `dossier_ng.py` into your custom plugins folder.
2. Add the block from `config.toml` to `/etc/pwnagotchi/config.toml`.
3. Check the four `*_path` options match where your other suites
   actually write their data (the shipped defaults match each source
   suite's own default, so if you're running all four with their
   defaults, there's nothing to change).
4. Restart pwnagotchi:
   ```
   sudo systemctl restart pwnagotchi
   ```
5. Open the plugin's page through pwnagotchi's normal web UI (its
   `on_webhook` route) - no separate port, no separate server.

You don't need all four source suites installed to get value out of
this plugin - a dossier just has fewer populated sections for whatever
sources aren't present.

## What the report looks like

**Index page** (`/`) - every known hostname, most-fully-documented
first, each with a one-line summary and a link to its own detail page:

```
DossierNG - unified target dossiers

Hostname       Completeness  Cracked  GPS  Nearby BT devices
MyHomeLab      4/4           yes      yes  2 (tracker!)
CoffeeShopWifi 2/4           yes      no   0
GuestNet_5G    1/4           no       no   0
```

**Detail page** (`/target/<hostname>`) - the full record for one
hostname: cracked password, GPS coordinates with a ready-to-use Google
Maps link, handshake timing history, and every Bluetooth device
correlated to that hostname (trackers called out and sorted first):

```
Dossier: MyHomeLab

Cracked password: hunter2

GPS
Lat/Lon: 40.7128, -74.0060
View on Google Maps
Update type: WU <G>
Tagged at: 1735689600.0

Timing
Timestamp: 2026-09-20T14:32:10
Time to deauth: 3.210s
Time to handshake: 5.870s
Deauth->handshake: 2.660s

Possible nearby Bluetooth devices
(Heuristic - see the in-page notice about what this does and doesn't mean)

MAC                Name        Vendor        Tracker         RSSI  Last seen
AA:BB:CC:00:00:01  -           Apple, Inc.   YES (airtag)    -42   2026-09-20T14:31:00+00:00
```

**Export** (`/export`) - the full assembled dossier dict as JSON, for
scripting.

## A note on the Bluetooth section

The "possible nearby Bluetooth devices" list is a **heuristic**,
inherited as-is from bluetooth-recon-suite's own best-effort,
time-window correlation. It means "this device was seen active nearby
around roughly the same time as activity on this network" - it is
**not** proof of physical co-location, and it does **not** mean the
device belongs to this network or its owner. The report page says this
plainly, every time.

## Testing

Run with (from this directory):
```
python3 tests/test_dossier_ng.py
```
