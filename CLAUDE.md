# CLAUDE.md — plugins-wip

Staging repo for **rebuilt pwnagotchi plugins** (the `*NG` suites) before they graduate to
`patrickato/complete-plugins`. Guidance for any AI or human working here. Read
`CONVENTIONS.md` too — it is the detailed cross-suite contract; this file is the orientation
layer.

## Target platform (build for this)

Pi 4 + 3.5" MPI3501 TFT (480×320), **jayofelony 64-bit pwnagotchi image**. Monochrome-safe
on-screen elements; assume the small TFT.

## Non-negotiable fork facts

- **The loader does NOT merge `__defaults__`.** It sets `plugin.options =
  config['main']['plugins'][name]` verbatim. So every suite reads options through a
  module-level `DEFAULTS` dict + the `_opt()/_opt_int()/_opt_bool()/_opt_float()` helpers —
  never `self.options[key]` assuming presence.
- **The config section name is the plugin file's exact basename** (case included), under
  `[main.plugins.<basename>]` — never the class name, never the upstream plugin's old name.
  Two filename conventions coexist and both are correct as long as the section matches the
  file: snake_case (`sigstr_ng.py` → `[main.plugins.sigstr_ng]`) and the one CamelCase
  outlier (`MadHatterNG.py` → `[main.plugins.MadHatterNG]`).
- **`pwnagotchi.plugins.loaded`** is `{basename: instance}`. Sibling lookups go through a
  configurable name option (never a hardcoded string) — see CONVENTIONS.md §"Looking up a
  sibling plugin" for the required pattern.
- Real hooks only. Several original plugins declared hooks this fork never calls
  (`on_ai_*`, etc.); a rebuild must use hooks that actually fire.

## The data bus (how suites interoperate — and how Beast Core reads them)

Suites converge on **fixed default paths**; a consumer's default path equals the producer's
default, so integration "just works." The canonical producers:

| Artifact | Default path | Written by |
|---|---|---|
| Capture timing CSV | `/etc/pwnagotchi/timer_ng.csv` | timer-suite |
| Cracked networks | `/etc/pwnagotchi/handshakes/crack_house_ng.potfile` | crack-house-suite |
| Bluetooth device table (already WiFi/GPS/cracked-correlated) | `/etc/pwnagotchi/handshakes/bluetooth_recon_ng.json` | bluetooth-recon-suite |
| GPS tags + per-capture sidecars | `/etc/pwnagotchi/gps_tagger_ng/` and `<capture>.gps.json` | gps-tagger-suite |

Known consumers: dossier (reads all four), bluetooth-recon (timer+crack-house+gps),
viz (crack-house), sigstr & mad-hatter (timer CSV), fix-region (gps sibling). When adding a
cross-read, make the path a config option defaulting to the producer's default. **Beast Core
can join this bus as just another read-only consumer — the paths are the contract.**

## Offensive / firing-capable plugins — scope, not a cage

Offensive plugins ship at full capability. The only thing centralized is *aiming*: any plugin that
can deauth/jam/target consults the **central Scope** — the one list of targets you own or are
authorized to assess (your own networks and devices, your lab, gear you bought to test, engagements
you're contracted for, ranges, CTFs, consenting peers). That space is broad by design, not a bench
restriction. It starts empty only so nothing fires at a target you didn't choose; arming is one
gesture (add / bulk-import a list / arm-lab, with per-job groups and optional expiry so lapsed
permission stops authorizing itself). Scope decides WHERE a plugin is aimed, never WHAT it can do —
inside scope it's unrestricted, and keep legitimate use low-friction. The reference deauth
implementation (`WifiJtest`, formerly `WifiJammerNG`) now lives as `wifiJtest` in
`patrickato/test-plugins`; `crack-pipeline-suite` in this repo consults the same central Scope.

The only firing that stays out entirely is the kind that can't be aimed and hits bystanders:
indiscriminate BLE/beacon spam and RF jamming (also illegal to transmit) — detect those, never emit
them. That is the whole exclusion list.

## Any suite running its own web server

Use the `bind_scope` option (`auto`/`tailscale`/`localhost`/`lan`) and always log + show the
exact URL. Default to the least-exposed scope that works. (web2ssh, handshaker, terminal run
their own listeners — treat them as separate attack surfaces.)

## Per-suite layout & tests

Each `*-suite/` holds the `*_ng.py` plugin, a real `config.toml` (built to the fork's
`defaults.toml` conventions, with `>>> USER INPUT REQUIRED <<<` markers), a `README.md`
(install + troubleshooting + requirements), a `NOTES.md` (full bug/research writeup), and a
`tests/` suite tested against the **real cloned jayofelony framework** where feasible (not
just mocks). Run a suite's tests from that suite's folder. Clearly separate what's
sandbox-verified vs. what still needs a real-hardware pass.

## Workflow

1. Nothing graduates to `complete-plugins` until it passes a **real-hardware** pass on the
   actual Pi (install per the suite README, verify on-device). Sandbox green ≠ done.
2. Keep originals preserved (verbatim, plus any real upstream config as
   `config.original.toml`) so a rebuild/merge can be reverted.
3. Offensive/attack plugins: exercise only against targets in the central Scope — anything you own
   or are authorized to assess (your lab, your gear, contracted engagements, ranges, CTFs,
   consenting peers), not merely a bench.

See `patrickato/test-plugins` for the audit that feeds this repo, and
`patrickato/beastagotchi` for the platform these plugins run alongside.
