# core-patches — durable fixes to pwnagotchi core files

Pwnagotchi core files (not plugins) sometimes need a fix that a `pip`/image
update will overwrite. This folder keeps those fixes as re-appliable patches so
an update can't silently undo them.

> Per the repo rule "No Pwnagotchi core-file modifications," these are **not**
> shipped inside any suite and are **not** applied automatically. They live here
> as documented, opt-in recovery patches for a specific device, applied by hand.

## 0001 — agent.py channel-ready retry (the "stuck on one channel" fix)

**Hardware:** Pi 4 + onboard BCM4345/6 with nexmon, Jayofelony 2.9.5.x 64-bit.

**Symptom:** pwnagotchi never channel-hops — the radio stays pinned to one
channel, epoch logs show `hops=0`, `Active:`/`Next:` never grow, and almost
nothing is captured. The screen/faces work; it just never scans the band.

**Cause:** a boot race. `agent.py` calls `utils.iface_channels(mon_iface)`
exactly once, the instant `wlan0mon` comes up. On this radio the interface
isn't ready that instant, so the call returns an empty list; pwnagotchi then
believes the radio supports no channels and disables hopping for the whole
session.

**Fix:** retry the channel query up to 15× (1s apart) until it returns a
non-empty list. Verified on-device: after the patch the radio hops across the
full 2.4 **and** 5 GHz channel set and captures resume.

**Apply (after any pwnagotchi update that rewrites agent.py):**

```sh
sh core-patches/apply-core-patches.sh
sudo systemctl restart pwnagotchi
```

The script backs up the current `agent.py` first, refuses to half-apply if the
upstream file changed, verifies the result, and is a no-op if the fix is already
present. If the context ever stops matching, it prints the 8 lines to add by
hand (see `0001-agent-channel-ready-retry.patch`).

**Check it's working after a reboot:**

```sh
# should list several channels, not empty:
sudo grep "supported channels" /etc/pwnagotchi/log/pwnagotchi.log | tail -1
# epochs should show hops>0 once there are APs around:
sudo grep -oE "hops=[0-9]+" /etc/pwnagotchi/log/pwnagotchi.log | tail -5
```

### Upstreaming

This is a genuine upstream bug (single-shot channel probe racing a slow monitor
interface). Worth a PR to jayofelony/pwnagotchi: wrap the
`self._supported_channels = utils.iface_channels(mon_iface)` call in the same
retry loop. Until then, this patch is the local durable fix.
