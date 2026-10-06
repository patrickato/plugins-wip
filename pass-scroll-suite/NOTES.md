# PassScroll - build notes

## Origin

New plugin, built on user request (2026-10-06): "scroll the last couple found
passwords instead of just displaying the last one found... or combines from all
possible sources and then scrolls through. Maybe 5 to 10." Not a rebuild of any
upstream plugin.

## Design decisions

- **Multiple sources, one list.** Reads every path in `sources`, parses, dedupes
  on `(ssid, password)`, keeps the most recent `max_entries`. Default sources are
  the plugins-wip **data-bus canonical producers** (crack-house-suite's
  `crack_house_ng.potfile`, the standard wpa-sec / OnlineHashCrack / my.potfile),
  so it interoperates with the rest of the repo out of the box per the data-bus
  convention in CLAUDE.md. Extra paths are user-addable.
- **Format auto-detection.** Three parsers in `_parse_line`: hashcat 22000 potfile
  (ESSID hex-decoded from the hash's 6th `*`-field), wpa-sec
  `bssid:station:essid:password`, and plain `essid:password`. Unrecognized lines
  are skipped silently.
- **Recency ordering.** Source files are read in mtime order (newest last); within
  a file, later lines win; dedupe keeps the latest occurrence. `entries[-N:]` is
  what rotates.
- **Fork conventions.** Config section = file basename (`passscroll`). Every option
  read through `DEFAULTS` + `_opt/_opt_int/_opt_bool` because the loader ignores
  `__defaults__`. Real hooks only: `on_loaded`, `on_ui_setup`, `on_ui_update`,
  `on_unload`. UI element removed on unload.
- **Cheap on the hot path.** `on_ui_update` re-reads files only every
  `refresh_interval` seconds, not every render tick.

## Verified vs. deferred

- **Verified (reasoning/sandbox):** parser logic for the three formats, dedupe +
  recency windowing, option fallbacks, UI element add/update/remove shape against
  the jayofelony UI component API.
- **Deferred (NOT done yet):**
  - No automated `tests/` suite yet (this went in as a quick side build, not a full
    *NG rebuild). Add one before any graduation to `complete-plugins`.
  - **Real-hardware pass needed:** confirm the on-screen element renders/positions
    correctly on the 3.5" MPI3501 TFT, and confirm the default source paths and
    their exact on-disk line formats match what crack-house-suite / wpa-sec
    actually write on the live rig.
  - `position` default `[5, 95]` is a guess; tune on the real display.

## Safety / scope

Read-only, local-only: it opens result files and draws text. Nothing is
transmitted or uploaded. Passwords shown are the user's own cracked results on
the user's own device; `mask` is provided for shoulder-surfing situations.
