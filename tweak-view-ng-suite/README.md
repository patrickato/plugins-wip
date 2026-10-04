# Tweak View NG

**A safe, resolution-independent UI layout editor for Pwnagotchi.** Move, restyle
and add elements on your Pwnagotchi's screen from a browser — drag things where you
want them, nudge them pixel-by-pixel, line them up, and save named layouts — without
editing a single config file by hand or touching Pwnagotchi's core.

- **Target:** Jayofelony Pwnagotchi **2.9.5.8 / 2.9.5.9**, 64-bit image.
- **Displays:** any resolution. The editor reads the real screen size at runtime —
  nothing is hard-coded to a specific TFT or e-ink panel. Developed and
  hardware-tested on a Pi 4 with a 3.5" 480×320 TFT.
- **Version:** 0.2.0-beta1.

> **This is a separate, independent plugin** — not the original *Tweak View* or
> *Tweak View 2*, and not a patch on top of them. It installs as its own plugin
> (`tweak_view_ng`), with its own config section, URL and layout file, so it can
> sit alongside anything else without conflict. It can *import* an old Tweak View
> layout so you don't start from scratch, but it never touches the old file.
> Concept credit to the original authors is in [Credits](#credits--license).

---

## Contents

- [What it does](#what-it-does)
- [Requirements](#requirements)
- [Install — pick any method](#install--pick-any-method)
- [Opening the editor](#opening-the-editor)
- [Using the editor](#using-the-editor)
- [The recovery editor](#the-recovery-editor)
- [Configuration](#configuration)
- [Where your layout is stored](#where-your-layout-is-stored)
- [Coming from Tweak View / Tweak View 2](#coming-from-tweak-view--tweak-view-2)
- [Uninstall](#uninstall)
- [Troubleshooting](#troubleshooting)
- [For developers: running the tests](#for-developers-running-the-tests)
- [Credits & license](#credits--license)

---

## What it does

Tweak View NG gives you a live, browser-based editor for your Pwnagotchi's display:

- **See your actual screen** in the browser (a live render of the real `/ui` frame)
  with a draggable box over every element.
- **Move elements** by dragging, by arrow keys, or with a 1-pixel nudge pad.
- **Line things up** — snap an element to an edge or center of its strip, match
  another element's X or Y, or stack several elements into an even column.
- **Restyle** elements — font, label, wrap, length, colors — from a safe,
  validated property list (it won't let you set something that would crash the UI).
- **Add your own** lines, rectangles and ellipses (dividers, boxes, status dots).
- **Keep multiple layouts** as named profiles (e.g. a `day` and a `night`
  arrangement) and switch between them.
- **Undo/redo** everything, revert a single element, or reset a whole profile.
- **Nothing is permanent or risky:** edits save atomically to their own JSON file,
  the previous file is backed up first, and disabling the plugin returns your
  screen to normal. Pwnagotchi's core files are never modified.

---

## Requirements

- A Pwnagotchi running the **Jayofelony 2.9.5.8 or 2.9.5.9** 64-bit image.
- Network access to the Pwnagotchi's web UI (USB-ethernet at `10.0.0.2`, the
  configured Wi-Fi/`bettercap` web port, Bluetooth tether, etc. — however you
  normally reach `http://<pwnagotchi>:8080`).
- That's it. No extra Python packages, no internet access on the Pi at runtime,
  no web fonts or CDNs — the editor is fully self-contained and works offline.

---

## Install — pick any method

All of these end up in the same place. SSH into your Pwnagotchi first. If you're
new, use **Method 1**.

### Method 1 — one-line installer (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/patrickato/plugins-wip/main/tweak-view-ng-suite/install.sh | sudo sh
```

The installer is **plug-and-play**. It finds your plugin directory automatically
(reads `custom_plugins` from your `config.toml`, falling back to the default),
backs up any existing copy, downloads and installs `tweak_view_ng.py`, adds a
`[main.plugins.tweak_view_ng]` section **only if one isn't there already** (it
never overwrites settings you've set), restarts Pwnagotchi, and prints the editor
URL. When it finishes, open the URL it shows you. Done.

> **Prefer to read before you run?** It's short, plain `sh` — open `install.sh`
> first, or download and run it manually.

### Method 2 — one-line installer with `wget`

Same thing, for systems that have `wget` but not `curl`:

```bash
wget -qO- https://raw.githubusercontent.com/patrickato/plugins-wip/main/tweak-view-ng-suite/install.sh | sudo sh
```

### Method 3 — clone the repo and run it

Good if you want to read everything first, or grab other plugins too:

```bash
git clone https://github.com/patrickato/plugins-wip
cd plugins-wip/tweak-view-ng-suite
sudo sh install.sh
```

(The installer uses the local `tweak_view_ng.py` next to it — no re-download.)

### Method 4 — just drop in the plugin file

If you'd rather handle config yourself, download only the plugin into your
plugins directory (default path shown — check yours with
`grep custom_plugins /etc/pwnagotchi/config.toml`):

```bash
sudo curl -fsSL -o /etc/pwnagotchi/custom-plugins/tweak_view_ng.py \
  https://raw.githubusercontent.com/patrickato/plugins-wip/main/tweak-view-ng-suite/tweak_view_ng.py
```

Then do the config + restart from **Method 5**, steps 2–3.

### Method 5 — fully manual

1. **Copy the plugin** into your custom-plugins directory:

   ```bash
   sudo cp tweak_view_ng.py /etc/pwnagotchi/custom-plugins/
   ```

2. **Enable it** in `/etc/pwnagotchi/config.toml` (see `config.toml.example`):

   ```toml
   [main.plugins.tweak_view_ng]
   enabled = true
   # everything below is optional — these are the defaults
   filename = "/etc/pwnagotchi/tweak_view_ng.json"
   legacy_filename = "/etc/pwnagotchi/tweak_view.json"
   auto_import_legacy = true
   backup = true
   history_limit = 50
   strict_version = false
   ```

3. **Restart** Pwnagotchi:

   ```bash
   sudo systemctl restart pwnagotchi
   ```

### Method 6 — jayofelony plugin-repo list (advanced)

Jayofelony can pull plugins from repo archives listed in `config.toml`. Add this
repo's archive, then update:

```toml
main.custom_plugin_repos = [
  "https://github.com/patrickato/plugins-wip/archive/main.zip",
]
```

```bash
sudo pwnagotchi plugins update
```

Then enable `tweak_view_ng` as in Method 5, step 2, and restart. (Methods 1–3 are
more direct; this one is handy if you already manage plugins through
`custom_plugin_repos`.)

---

## Opening the editor

Once Pwnagotchi has restarted (give it ~30–60 seconds), open:

```text
http://<your-pwnagotchi>:8080/plugins/tweak_view_ng/
```

If you've never opened the web UI before, it may ask for the Pwnagotchi web
username/password you set in `config.toml` (`ui.web.username` / `ui.web.password`).

You should see three panels: the **element list** on the left, the **live preview**
in the middle, and the **properties + tools** on the right.

> **Tip:** click the **?** button in the top-right to turn on **help mode** — then
> hover any control and a bubble explains what it does. It's the fastest way to
> learn the editor.

---

## Using the editor

### Selecting and moving

- **Click** an element in the left list or **click its box** on the preview to
  select it. The selected box is highlighted; others are dimmed.
- **Drag** a box to move it. It follows your cursor live and commits when you let
  go. With **Snap** on (top bar), it snaps softly to the divider lines and screen
  edges.
- **Arrow keys** nudge the selected element 1 pixel; **Shift + arrow** moves 10.
- The **Nudge 1px pad** (↑ ← → ↓, in the *Position* card) does the same as the
  arrow keys, one pixel per click — handy on a touchscreen or without a keyboard.

A box turns **red** while you drag it off the screen or across a divider line —
that's a *warning*, not a block. You can still drop it there.

### Aligning and arranging

In the right-hand panel:

- **Align to an edge** (*Position* card) — snap the selected element to the
  Left / Center / Right of the screen, or the Top / Middle / Bottom of the strip
  it lives in (the bands set by the `line1` / `line2` dividers).
- **Match X of… / Match Y of…** (*Arrange* card) — line the selected element up
  with another element's exact horizontal or vertical position.
- **Stack…** (*Arrange* card) — pick several elements (comma-separated) and space
  them evenly down a single clean column.

### Styling

Select an element and edit its properties in the right panel, then click **Apply**:

- **font / text_font / label_font / alt_font** — choose from Pwnagotchi's built-in
  fonts and a range of DejaVu Mono sizes.
- **label**, **label_spacing**, **max_length**, **width**, **wrap** — text layout.
- **color**, **bgcolor**, **fill** — for displays/elements that support them.
- **xy** — the raw coordinates, if you'd rather type them.

Only properties that are safe to change are shown, and every value is validated
before it's applied, so you can't set something that would break the UI.

### Adding your own shapes

The **+ Line**, **+ Rect** and **+ Ellipse** buttons (top-left) add custom widgets
— dividers, boxes/badges around a value, status dots/rings — which you can then
drag, size and style like anything else. Remove one by selecting it and using
Revert/Reset, or delete it from the shape's controls.

### Profiles (multiple layouts)

The *Profile* card keeps separate named layouts:

- **New** — create a fresh layout.
- Switch between them with the dropdown.
- **Rename** / **Del** — manage them (the `default` profile is protected).
- **Export** / **Import** — download the whole layout as JSON to back up or share,
  or load one back.

### Undo, revert, reset

- **Undo / Redo** (top bar) step through every change.
- **Revert element** undoes all changes to just the selected element.
- **Reset profile** clears every change in the current profile, back to defaults.

### Display overlays

- **Snap** (top bar) — toggle soft snapping while dragging.
- **Zones** (top bar) — shade the top and bottom status strips so you can see the
  regions you're aligning into.

---

## The recovery editor

If the full editor ever won't load (an old browser, a broken custom shape, a
styling issue), there's a dependency-light, **JavaScript-free** fallback that can
still read and change every property:

```text
http://<your-pwnagotchi>:8080/plugins/tweak_view_ng/recovery
```

It's a plain HTML form — ugly but bulletproof. Use it to fix whatever broke, then
go back to the full editor.

---

## Configuration

All options live under `[main.plugins.tweak_view_ng]` in `config.toml`. **Only
`enabled` is required** — everything else has a sensible default.

| Option | Default | What it does |
|---|---|---|
| `enabled` | — | `true` to load the plugin. |
| `filename` | `/etc/pwnagotchi/tweak_view_ng.json` | Where your layout is saved. |
| `legacy_filename` | `/etc/pwnagotchi/tweak_view.json` | Old Tweak View file to import from (see below). |
| `auto_import_legacy` | `true` | Import an old Tweak View layout once, if no NG layout exists yet. |
| `backup` | `true` | Write a `.bak` copy before overwriting the layout file. |
| `history_limit` | `50` | How many undo steps to keep. |
| `strict_version` | `false` | If `true`, refuse to start on anything but Pwnagotchi 2.9.5.8 (useful for strict testing; leave `false` for normal use, including 2.9.5.9). |

---

## Where your layout is stored

Your edits are written to **`/etc/pwnagotchi/tweak_view_ng.json`** (configurable).
Saves are **atomic** (`fsync` + `os.replace`), and the previous version is copied to
`…json.bak` before each save, so a power loss mid-save can't corrupt your layout.

Tweak View NG **never** edits Pwnagotchi's core files, and it never overwrites the
old `tweak_view.json`. When the plugin unloads, it restores every property it
touched and removes the custom shapes it added, so turning it off returns your
screen to its base layout.

---

## Coming from Tweak View / Tweak View 2

If you already have a `/etc/pwnagotchi/tweak_view.json` from the original Tweak View
or Tweak View 2, Tweak View NG will **import it automatically** the first time it
runs (when no NG layout exists yet). Your old file is left untouched; the converted
layout lands in the NG file. Custom shapes (`CustomLine` / `CustomRect` /
`CustomEllipse`) and `VSS.*` property edits are converted; anything malformed is
skipped with a reason rather than failing the whole import. You can also import a
file by hand any time from the editor's **Import** button.

---

## Uninstall

```bash
sudo sh uninstall.sh
```

or manually:

```bash
sudo rm /etc/pwnagotchi/custom-plugins/tweak_view_ng.py
# then set enabled = false (or remove the section) in config.toml and restart
sudo systemctl restart pwnagotchi
```

Your layout JSON is **preserved** either way, so you can reinstall later and pick up
where you left off. To wipe it too, delete `/etc/pwnagotchi/tweak_view_ng.json`.

---

## Troubleshooting

**The editor page is blank or won't load.**
Give Pwnagotchi 30–60s to finish restarting, then hard-refresh (Ctrl-Shift-R). If
it still won't load, use the [recovery editor](#the-recovery-editor).

**"Session expired — reload the page."**
Your CSRF token went stale (often after a reboot with the page left open). Just
reload the page.

**Changes don't show on the physical screen.**
If your `ui.fps = 0`, the screen only redraws on events — Tweak View NG forces a
redraw after each edit, but give it a moment. Confirm the plugin is actually loaded:

```bash
sudo grep -i "tweak view ng" /etc/pwnagotchi/log/pwnagotchi.log | tail -3
```

You want a line like `Tweak View NG ready: 480x320, N properties/shapes applied`.

**It didn't install where Pwnagotchi loads plugins from.**
Plugin directories vary. Check what yours is and confirm the file is there:

```bash
grep custom_plugins /etc/pwnagotchi/config.toml
```

The one-line installer handles this automatically.

**I want it to refuse anything but 2.9.5.8.**
Set `strict_version = true`. For normal use (including 2.9.5.9) leave it `false`.

---

## For developers: running the tests

From this folder:

```bash
python3 -m pytest                    # offline unit suite (simulated UI)
python3 -m pytest tests/integration  # against the REAL Jayofelony framework
```

The integration suite auto-clones the pinned Jayofelony `v2.9.5.8` tag on a
dev machine; on the Pi itself it binds to the already-installed framework with no
clone. The embedded editor JavaScript is mirrored in `tests/webui.js` and
syntax-checked with `node --check`.

Repo layout:

- `tweak_view_ng.py` — the entire plugin, a single deployable file.
- `tests/` — offline unit tests, `tests/integration/` real-framework tests,
  `tests/fixtures/` legacy + NG sample layouts.
- `config.toml.example` — ready-to-paste config.
- `install.sh` / `uninstall.sh` — the installers.
- `CHANGELOG.md` — version history. `TEST_REPORT.md` — what's verified, and how.
- `EDITOR_UPGRADES.md` — design record for the editor UX work.

---

## Credits & license

Tweak View NG is an independent, ground-up reimplementation for the Jayofelony
fork, built on the ideas of the plugins that pioneered live on-device UI editing:

- **Original Tweak View** — NurseJackass & Sniffleupagus.
- **Tweak View 2** (v2.0.0) — Sniffleupagus & BraedenP232
  ([Sniffleupagus/pwnagotchi_plugins](https://github.com/Sniffleupagus/pwnagotchi_plugins)).
- **Tweak View NG** — patrickato.

The automatic legacy importer exists specifically so Tweak View / Tweak View 2
users keep their layouts. Full attribution is in [CREDITS.md](CREDITS.md).
Licensed **GPL-3.0**.
