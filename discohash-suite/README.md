# DiscoHash Suite

**Status: work-in-progress (in `plugins-wip`).** Not yet moved to
`complete-plugins` - still needs real-hardware testing before it's
considered done.

A rebuilt, consolidated replacement for three separate plugins found in
the `patrickato/test-plugins` audit: the original `DiscoHash`,
`discoBoss.py`, and `hashbot.py`. All three did overlapping jobs (posting
handshake hashes to Discord, and letting you retrieve/control things from
Discord) with real bugs and one redundant always-on bot running directly
on the pwnagotchi. This suite consolidates them into two clean pieces
with no duplicated logic.

Target hardware: Raspberry Pi 4 + 3.5" TFT screen, jayofelony 64-bit
pwnagotchi image.

## Pieces

| Piece | Runs on | Job |
|---|---|---|
| [`pi-plugin/discohash_ng.py`](pi-plugin/) | the pwnagotchi | Converts new handshakes to hashes, posts them + GPS to Discord |
| [`hashbot/hashbot.py`](hashbot/) | your own computer/server | Discord commands: retrieve hashes, check status, reboot/poweroff (with confirmation) |

## Why two pieces instead of three?

The original `discoBoss.py` ran an entire second Discord bot **on the
pwnagotchi itself** just to expose `!dumphash`/`!reboot`/`!poweroff` -
duplicating what the original `hashbot.py` (which already correctly ran
off-device) did for hash retrieval. Consolidating removes that redundant
always-on network service from the device that's actually out capturing
handshakes, with no loss of functionality - `hashbot.py` now owns every
Discord command, including reboot/poweroff, reaching the pi over SSH
instead.

See [`NOTES.md`](NOTES.md) for the full research writeup: what was
verified from the original source, every bug found, and why each design
decision here was made.

## Start here

Follow [`SETUP.md`](SETUP.md) from the top - it walks through creating
the Discord server/bot/webhook, setting up SSH access, and filling in
every config file, in order.

## What's still needed before this can move to `complete-plugins`

- [ ] Real-hardware install/test on the actual pi + 3.5" TFT setup
- [ ] Confirm a live handshake capture actually posts to Discord end-to-end
- [ ] Confirm `!reboot`/`!poweroff` confirmation flow and SSH execution work as designed
- [ ] Confirm duplicate-post protection survives an actual reboot
- [ ] Any adjustments that come out of the above
