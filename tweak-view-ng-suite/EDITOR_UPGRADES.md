# Tweak View NG — Editor UX Upgrades (design note)

Status: **implemented in `0.1.0-alpha4`** (built on the alpha3 CSRF branch).
Features 1–3 + all companions landed; see "Implemented" below. The original
proposal is kept beneath it as the design record.

## Implemented (alpha5-alpha9, on-hardware pass)

Found and fixed through live testing on the Pi, then polished:

- alpha9: **stack reflow fix** (the no-gap default now anchors at the top-most
  element and uses an even, readable pitch, so it visibly tidies a column
  instead of barely moving already-spaced elements); a **1px nudge pad** for
  fine, keyboard-free adjustments; **hover help mode** (the `?` button turns on
  per-control tooltips that finally document +Line/+Rect/+Ellipse); **labels
  on selected + hover only**; and a **tasteful visual refresh** — titled
  section cards, dimmed unselected boxes, cleaner spacing, mobile header wrap.
- alpha5: fixed dead drag (startDrag rebuilt the overlay and detached the box).
- alpha6: **per-element alignment** replaced the confusing per-strip buttons —
  an align pad (Left/Center/Right, Top/Middle/Bottom) acting on the selected
  element within its own region; plus red-box-clears-on-drop and
  top-strip-no-longer-always-red fixes.
- alpha7: the overlay is never rebuilt mid-drag, so a box can't freeze/detach
  when the pwnagotchi UI updates while you hold it.
- alpha8 (polish): **Match X / Match Y** of another element; **Stack** elements
  evenly down a column (the chosen alternative to text rotation); **Rename /
  Delete** profiles with the default protected; a header **saved** indicator and
  a **help overlay**.

Text rotation was considered and deliberately skipped — see the alpha8
changelog note.

## Implemented (alpha4)

- **Real-time drag** — the overlay box follows the cursor during the drag;
  commit still happens once on release.
- **Arrow-key nudge** — select an element, arrow keys move it 1px (Shift = 10px).
- **Border/overlap warning** — the box turns red while dragging across `line1`/
  `line2` or the screen edge (and, when overlap-warn is on, over another
  element). A warning, not a block.
- **Auto-align strips** — `Align top` / `Distribute top` / `Align bottom` /
  `Distribute bottom`. Strip membership is auto-detected by the divider lines;
  the math runs server-side in a new `api/align` endpoint as one undoable
  transaction (align = median baseline, distribute = even gaps between the
  outermost elements).
- **Snap-to-guides** — `Snap` toggle (on by default): dragging softly snaps to
  `line1`/`line2` and screen edges within 3px.
- **Safe-zone overlay** — `Zones` toggle (on by default): top and bottom strips
  are faintly shaded so you can see what you're aligning into.
- **Element labels + "moved" indicator** — each overlay box shows its name;
  elements changed from default get a dot in the list/title and a highlighted
  box.

### Considered, deferred (scope discipline — a 480×320 editor, not Photoshop)

- Multi-select / group-move — auto-align covers the real "tidy a strip" need.
- Rotation, z-order/layering, rich color pickers — little benefit on a mono TFT.
- Editor themes/skins — gold-plating.

---

(original proposal follows)

Status: **proposed** (targeting `0.1.0-alpha4`, after the alpha3 CSRF fix merges)
Scope: the in-browser editor only (`WEB_UI` in `tweak_view_ng.py`). No change to
the capture/scan path. Author of ideas: repo owner; drafted collaboratively.

## Goal

Make positioning elements on the 480×320 TFT feel like a real design tool:
immediate visual feedback while dragging, a gentle warning when you cross
something important, and one-click tidying of the top/bottom status strips.
Guiding principle from the owner: **give users options, make it easy** — every
feature is one click or zero-config, and everything is undoable.

## Why these are low-risk

The editor already has the scaffolding:
- `drawBoxes()` renders a draggable overlay box per element from its `xy`.
- `startDrag / moveDrag / endDrag` handle pointer dragging; `moveDrag` already
  updates the `xy` input live but does **not** move the box or commit until
  release.
- Undo/redo is a server-side stack; any batch of edits is reversible.
- Screen-edge clamping already exists server-side (`_resolve_xy`).

So most of this is client-side JS layered onto existing hooks. Server changes
are limited to one new batch-edit endpoint for auto-align (below).

---

## Feature 1 — Real-time drag

**Now:** during a drag the element's overlay box stays put; only the `xy` text
field updates; the box and the previewed element jump to the new spot on
release.

**Proposed:** the overlay box follows the cursor live during `moveDrag`
(client-only, no per-move API calls). On release, commit once via the existing
`apply()` (one API call, as today). The previewed *image* still only redraws
after commit — that's a server render — but the moving box gives the
"I'm dragging this" feel immediately.

**Build:** in `moveDrag`, update the selected box's `left/top` (%); in
`endDrag`, commit as today. ~15 lines. No server change.

**Risk:** none meaningful. Worst case the box and the post-commit image differ
by the clamp amount at the edges (already true today).

---

## Feature 2 — Border / overlap warning (box turns red)

**Intent:** while dragging, warn when the element crosses an important boundary
so layouts don't end up on top of the divider lines or stacked on each other.

**"Important boundary" = (proposed):**
- the two divider lines `line1` / `line2` (themselves movable elements),
- the screen edges,
- optionally, overlap with another element's box.

**Behavior:** a **warning, not a block.** Box border goes blue → red (and/or a
one-line status hint) when the dragged box crosses a divider or the edge margin,
or overlaps another box. The user can still drop it there — red just means "are
you sure?". This matters because on a mono TFT, text bounding boxes are
approximate (the code already notes this), so a hard block would sometimes be
wrong. A warning never is.

**Build:** client-side; `drawBoxes`/`moveDrag` already know every element's xy,
so collision math is a loop over the other boxes + the two line elements.
Add/remove a `.warn` CSS class. ~25–40 lines. No server change.

**Open question:** should overlap-with-another-element be on by default, or only
divider/edge crossing? (Overlap is noisier.) Suggest: divider+edge on by
default, element-overlap as a toggle.

---

## Feature 3 — Auto-align the top / bottom strips  ⭐

The richest feature and the most pwnagotchi-specific. The top strip (elements
above `line1`: channel, aps, uptime, …) and bottom strip (below `line2`:
shakes, mode, …) are where layouts get visually messy.

**Design (the "options, made easy" answer):** the standard design-tool pair,
one click each, per strip. Four buttons:

| Button | What it does |
|---|---|
| **Align top** | snap every above-`line1` element to a shared baseline (same y) |
| **Distribute top** | space those elements with equal horizontal gaps |
| **Align bottom** | same baseline snap, below-`line2` elements |
| **Distribute bottom** | equal gaps, bottom strip |

- **Zero configuration** — the user clicks; it looks right. "Easy."
- **Options** — they choose strip + align vs distribute (vs both). "Options."
- **Auto-detected membership** — the editor decides which elements are in the
  top strip (y < line1.y) and bottom strip (y > line2.y) automatically; the user
  doesn't select anything.
- **Non-destructive** — each button is just a batch of xy edits; undo/redo
  reverses it, so trying it is free.

**Align baseline choice:** snap to the *median* y of the strip's elements (least
surprising — things move the least), or to a fixed offset from the line. Suggest
median, with the line as a fallback when a strip has one element.

**Distribute math:** sort strip elements by x; hold the leftmost and rightmost
in place; space the rest with equal gaps between box centers. (Equal edge-gaps
is an alternative; center-distribution reads more even with mixed widths.)

**Build:** a new server endpoint `api/align` taking `{strip: "top"|"bottom",
op: "align"|"distribute"}` that computes the new positions server-side (it knows
real element widths better than the browser's approximate boxes) and applies
them as one undoable transaction, reusing the existing apply/persist path. Four
buttons in the UI call it. Medium size; the transaction/undo plumbing already
exists.

**Why server-side for this one:** element width/placement is more accurate from
the real widget objects than the browser's approximate overlay boxes, and doing
it as one `_mutating_route` transaction gets atomic persist + single-undo for
free.

---

## Suggested companions (from Claude, same spirit)

- **Arrow-key nudge** — selected element moves 1px per arrow press, 10px with
  Shift. Pixel precision matters on 480×320; trivial given the `xy` input
  already exists. Client-only.
- **Snap-to-guides** — while dragging, softly snap the box to `line1`/`line2`
  and screen edges within a few px (toggle to disable for free drag). Makes
  "nice and even" happen during manual drags, complements Feature 3.
- **Safe-zone overlay toggle** — faintly shade the top strip (above `line1`) and
  bottom strip (below `line2`) so the user can see the regions they're aligning
  into. Directly supports Feature 3; client-only.

---

## Proposed build order

1. **Feature 1 (real-time drag)** — quick win, get it on the Pi, feel it.
2. **Arrow-key nudge** — tiny, high-value, pairs with #1.
3. **Feature 2 (warning)** — builds on the live-drag box.
4. **Feature 3 (auto-align)** — the big one; needs the `api/align` endpoint +
   tests.
5. **Snap-to-guides + safe-zone overlay** — polish once the above feel right.

Each lands as its own commit with tests, validated on the real Pi (the editor is
now a proven on-device test bed). Version bumps as we go; graduate toward
`complete-plugins` once the set is hardware-confirmed.

## Open questions for the owner

1. Feature 2: element-overlap warning on by default, or divider/edge only?
2. Feature 3: "both" as a third button per strip (align **and** distribute in
   one click), or keep the two separate?
3. Distribute: equal center-gaps or equal edge-gaps? (Suggest center.)
4. Build all as one alpha4 batch, or ship Feature 1 to the Pi first and iterate?
