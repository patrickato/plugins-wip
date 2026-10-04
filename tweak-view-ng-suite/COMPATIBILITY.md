# Compatibility Contract

## Software

**Primary supported target for this alpha:** Jayofelony Pwnagotchi **2.9.5.8 64-bit**.

The code intentionally isolates version-sensitive internals in `JayUIAdapter`. Future Jay releases should therefore require an adapter review/update rather than a rewrite of the editor and persistence layer.

## Raspberry Pi models

Tweak View NG does not branch on Raspberry Pi model. If the current 64-bit Jayofelony image boots and supplies its normal Pwnagotchi `View`, the plugin uses the same code path.

## Displays

Tweak View NG does not branch on display model or fixed resolution. It asks the active Pwnagotchi view for `width()` and `height()` at runtime. Rotation is therefore inherited from Jay's already-resolved view dimensions.

A display is considered compatible when Jayofelony's image exposes it through the normal Pwnagotchi view/render path. Custom/out-of-tree displays may also work if they honor that interface, but are not claimed compatible until tested.

## Compatibility levels

- **Targeted:** Jayofelony 2.9.5.8 64-bit.
- **Dynamically adaptable:** Pi model, resolution, landscape/portrait, supported display driver.
- **Needs physical validation:** exact display refresh behavior and performance.
- **Future Jay versions:** expected to be adapter-review work, not automatically claimed compatible.
