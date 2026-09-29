# SpamPeersNG

Sends a canned pwnmail greeting to newly-encountered pwnagotchi peers.
Rebuilt from `spam_peers.py` (`Spam_Peers`).

## What was actually broken

- `__init__` called raw, unguarded `os.listdir("/root/peers")`. Because
  `Plugin.__init_subclass__` calls `cls()` (i.e. `__init__`) immediately,
  with no `try`/`except` anywhere in the loader, a `FileNotFoundError`
  here - e.g. on a fresh install, or any device that hasn't detected a
  peer yet, so `/root/peers` simply doesn't exist - crashed the plugin's
  *loading* entirely, not just one hook.
- `on_peer_detected` read `peer.adv['identity']` via raw dict indexing.
  The real `Peer` class (`pwnagotchi.mesh.peer`) explicitly documents
  that `self.adv` can be `{}` when a peer sends a malformed/null
  advertisement, and provides `_adv_str()` specifically to survive this.
  Direct `['identity']` indexing `KeyError`s on exactly the malformed-peer
  case the framework itself guards against.

## What this rebuild adds

- All "known peers" loading moved out of `__init__` into `on_loaded`
  (which the framework does run with exception handling), and guarded
  with an existence check regardless.
- `peer.adv.get('identity')` instead of `peer.adv['identity']`; peers
  with no identity in their advertisement are skipped and logged, not
  crashed on.
- **Persisted state**: the "already greeted" list is now saved to a JSON
  state file (`state_file`, default
  `/etc/pwnagotchi/spam_peers_ng_state.json`) instead of living only in
  memory, so a frequently-seen peer isn't re-greeted on every reboot.
  Disk state is merged with any `known_peers` supplied via config on
  load.
- **Configurable cooldown**: `regreet_after_hours` (default 168 = 1
  week; 0 = never re-greet) replaces "greet once per session forever" -
  a last-greeted timestamp is tracked per peer identity.
- **Jitter**: the greeting fires after a random delay
  (`min_delay_seconds`/`max_delay_seconds`, default 1-8s) via a
  background timer, not synchronously inside `on_peer_detected`, so the
  reply doesn't look like an instant bot response.

## Configuration

See `config.toml`. Nothing requires user-specific values to run - the
defaults are reasonable out of the box; adjust `messages`,
`regreet_after_hours` and the delay range to taste.

## Still open

- No real-device test against an actual peer mesh / live `grid.send_message`
  call - the test suite mocks `pwnagotchi.grid.send_message` and exercises
  the plugin's own state, cooldown and scheduling logic against the real
  cloned framework.
- `_send_greeting` runs on a `threading.Timer` thread; if the plugin is
  unloaded mid-delay the timer is not explicitly cancelled (daemon thread,
  so it won't block process exit, but the greeting could still fire after
  unload in a live long-running process).
