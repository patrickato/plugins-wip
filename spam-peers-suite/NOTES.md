# Notes: SpamPeersNG

## Bugs found

- `__init__` did unguarded `os.listdir("/root/peers")`. Since
  `Plugin.__init_subclass__` calls `cls()` immediately with no
  `try`/`except` anywhere in the loader, a `FileNotFoundError` here (a
  fresh install, or any device that's never detected a peer, so
  `/root/peers` doesn't exist) crashed the whole plugin's *loading*,
  not just one hook - unlike `on_loaded`, `on_peer_detected`, etc.,
  which the framework's event dispatch does wrap.
- `on_peer_detected` did `peer.adv['identity']`, raw dict indexing. The
  real `pwnagotchi.mesh.peer.Peer` class explicitly documents that
  `self.adv` can be `{}` if a peer sends a malformed/null advertisement
  (guarded against everywhere else in the framework via `_adv_str()`/
  `_adv_int()`), and this direct indexing would `KeyError` on exactly
  that case. (The original wrapped the whole hook body in a bare
  `try/except Exception`, so this particular bug wouldn't have crashed
  the daemon - but it would silently drop the greeting for any
  malformed-advertisement peer without ever attempting a fix like
  `.get()` would allow, e.g. treating it as "no identity, skip".)

## What this build does

- Moves all `/root/peers` preloading out of `__init__` into `on_loaded`,
  with an `os.path.isdir` guard.
- Uses `peer.adv.get('identity')`, skips/logs peers with no identity.
- Persists the greeted-peers list to a JSON state file
  (identity -> last-greeted timestamp), loaded on `on_loaded` and
  written after every greeting. Merges config-supplied `known_peers` in
  as pre-greeted entries (so you don't spam your own other units).
- `regreet_after_hours` config option (default 168h/1wk, 0 = never)
  drives `_should_greet()`, checked against the persisted timestamp.
- Greeting is dispatched via `threading.Timer(delay, ...)` with
  `delay = random.uniform(min_delay_seconds, max_delay_seconds)`, so
  `on_peer_detected` itself returns immediately and the actual
  `grid.send_message` call happens on a background thread after the
  jitter.

## Testing

`tests/test_spam_peers_ng.py`, run against the real cloned
`jayofelony/pwnagotchi` framework, with `pwnagotchi.grid.send_message`
mocked (no real mesh) and `threading.Timer` either invoked at
`delay=0` and joined, or its scheduling verified directly rather than
waiting out real jitter. Covers: real plugin registration; `__init__`
never touching the filesystem (no crash when `/root/peers` doesn't
exist, verified by patching `os.path.isdir` to raise if called at
construction time); `on_loaded` safely skipping a missing `/root/peers`
directory; `on_loaded` preloading peers from a fake `/root/peers`
directory including a malformed peer file (unreadable JSON) not
crashing the whole load; `on_peer_detected` with a normal identity
greeting once and persisting to the state file; `on_peer_detected`
with `peer.adv == {}` (malformed advertisement) not crashing and not
sending anything; a peer within the cooldown window (`regreet_after_hours`)
not being re-greeted; a peer past the cooldown window being re-greeted;
`regreet_after_hours = 0` meaning "never regreet" even long after first
greeting; state round-tripping through a fresh plugin instance reading
the same state file; `known_peers` from config being merged in as
pre-greeted; and the jitter delay actually falling within the
configured `min_delay_seconds`/`max_delay_seconds` range.

## Still open

- See README's "Still open" section (no live mesh/device test; timer
  not explicitly cancelled on unload).
