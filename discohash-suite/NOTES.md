# Research notes: DiscoHash Suite

Source plugins reviewed (all pulled from previously-cloned repos):
- `DiscoHash` (`discohash.py`) - `pwnagotchi-unofficial/plugins_archive/flamebarke/DiscoHash/discohash.py`, author v0yager
- `discoBoss.py` - `pwnagotchi-unofficial/plugins_archive/Levi-Michael/pwnagotchi-plugins/discoBoss.py`, author A1buS
- `hashbot.py` - `pwnagotchi-unofficial/plugins_archive/flamebarke/DiscoHash/bots/hashbot.py`, author v0yager

`DiscoHash` and `hashbot.py` were both already tracked on the
`test-plugins` master list (Attack/Capture and Web UI/API sections,
respectively). `discoBoss.py` was not on the master list at all - it
surfaced during this review because of its direct functional overlap with
`hashbot.py`, and was pulled into scope with the user's approval.

## Original bugs/issues found (source-verified)

**`discohash.py`:**
1. Hardcoded `handshake_dir = "/root/handshakes/"` - confirmed against
   this fork's own `pwnagotchi/defaults.toml` that the real default is
   `/etc/pwnagotchi/handshakes`. `os.listdir()` on the wrong/nonexistent
   path raises `FileNotFoundError` every epoch (non-fatal - the
   framework's `PluginEventQueue.process_events` catches and logs it -
   but the plugin never does its job).
2. `filename.endswith('.pcap')` - this fork only ever writes `.pcapng`,
   so even with the right path, nothing would ever match. Same bug
   pattern found repeatedly across this whole project.
3. Uses bare module-level `global` variables (`tether`, `fingerprint`,
   `lat`, `lon`, `loc_url`) instead of instance state - works by accident
   in a single-plugin-instance world but is fragile, unconventional, and
   makes the data flow hard to follow.
4. No duplicate-post protection at all - a fixed version of this plugin
   would re-post every handshake in the folder on every single epoch,
   forever.
5. Reads `self.options['webhook_url']` directly with no fallback -
   `KeyError` if not set (recurring project-wide pattern; this fork
   doesn't read `__defaults__` either way, so the real fix is always
   "set it in config.toml").

**`discoBoss.py`:**
1. `self.TOKEN = '<TOKEN>'` and both channel IDs are hardcoded literal
   placeholder strings - never reads `self.options` anywhere in the file,
   no `__defaults__`. Unusable without hand-editing the plugin's source
   directly (not something `config.toml` could ever supply).
2. `!reboot`/`!poweroff` gated only by "which channel did this message
   come from," not by user - anyone with access to that channel could
   shut down or reboot the device.
3. An `async def on_unload():` is defined inside `run_discord_bot()` but
   never registered with `@client.event` - Discord.py never calls it, so
   there's no real cleanup path for the bot/asyncio loop on unload.
4. Its `!dumphash` command duplicates `hashbot.py`'s entire job
   (scrape N recent hash-embed messages from a channel, bundle into a
   `.22000` file) - genuine redundant logic, not just a naming overlap.
5. Runs an entire Discord bot **on the pwnagotchi itself** just for
   remote control - a second always-on network-facing process on the
   same device that holds your handshake captures.

**`hashbot.py`:**
1. Not a pwnagotchi plugin at all - no `import pwnagotchi`, no
   `plugins.Plugin` subclass. Confirmed via this fork's own
   `Plugin.__init_subclass__` registration mechanism (same check used
   project-wide on `quick_rides_to_jail.py` and `prime_gsm_hat.py`) that
   a file with no real subclass never registers - but this one isn't
   even trying to; it's a genuinely separate script meant to run on its
   own machine (its own header comments describe a `.env` file setup).
2. Unconditional module-level `bot.run(TOKEN)` - if this were ever
   mistakenly dropped into the plugins directory, it would execute
   immediately at import time, the same "risky top-level code" hazard
   documented elsewhere in this project (`Pwnagotchi-JSON-to-Wigle-CSV.py`,
   `pwnassistant.py`).
3. Functionally fine otherwise as a standalone script - the actual
   Discord history-scraping logic works.

## Design decisions made this round (with user)

1. **Consolidate to 2 pieces, not 3.** `discoBoss.py`'s control-bot
   function is folded into `hashbot.py` (which already correctly runs off
   the pi), removing the redundant always-on bot from the device itself.
   `DiscoHash` becomes `discohash_ng.py`, kept pi-side since it needs
   direct filesystem access to the handshake folder.
2. **Exact-match commands.** Rebuilt on `discord.ext.commands.Bot`, whose
   command parser matches exact `!command` tokens - structurally fixes
   the `'!reboot' in message` substring-match hazard from the original
   `discoBoss.py`.
3. **Single-user authorization.** Every command checks
   `ctx.author.id == AUTHORIZED_USER_ID` from `.env` - not just "which
   channel," closing the "anyone in the channel can reboot it" gap. The
   user plans to also make the Discord channel private (they'd be the
   only human member either way), making this defense-in-depth rather
   than the only protection.
4. **Confirm-before-destructive.** `!reboot`/`!poweroff` require an exact
   `Yes!`/`NO!` reply within 30 seconds (user's specified wording) before
   executing anything, to guard against an accidental trigger.
5. **Duplicate-post protection.** A small JSON state file on the pi
   tracks every handshake filename already posted, checked before any
   conversion/posting work happens - survives restarts and repeated
   backlog scans.
6. **Retry only on failure.** The Discord webhook POST retries up to
   `retry_attempts` times with `retry_delay` seconds between tries -
   but only enters that loop when a post actually fails; a successful
   first attempt returns immediately with no delay or retry logic
   touched at all.
7. **A couple of added useful commands**, beyond the original scope:
   `!status` (uptime + temp + handshake count in one message) and
   `!lastcrack` (most recent WPA-SEC potfile entry) and `!uptime`
   (uptime alone) - all read-only, all reachable over the same SSH
   connection already required for reboot/poweroff, so no new
   dependencies or attack surface.
8. **No hardcoded secrets anywhere in source.** Token, channel IDs, user
   ID, SSH host/user/key path all live in `.env` (hashbot) or
   `config.toml` (discohash_ng) - both clearly marked
   `>>> USER INPUT REQUIRED <<<` at the point where they're read, per the
   user's request to make it obvious where personal setup is needed.

## Sandbox testing done (before any real-hardware pass)

Everything below was verified in the dev sandbox, without a real pi,
Discord connection, or SSH target:

- Both files syntax-check clean (`py_compile`); `config.toml`
  parses as valid TOML matching this fork's real `[main.plugins.x]`
  section style.
- `discohash_ng.py` was loaded through the **actual** jayofelony plugin
  loader (`Plugin.__init_subclass__`, from the real cloned framework
  source, not a mock) and confirmed to register correctly as
  `discohash_ng`, with all four hooks it uses
  (`on_loaded`/`on_handshake`/`on_epoch`/`on_internet_available`)
  cross-checked against real `plugins.on(...)` call sites in
  `agent.py`/`automata.py`/`cli.py` for correct names and argument
  signatures.
- 18 logic-level tests against `discohash_ng.py` (mocked filesystem/
  subprocess/requests): `.pcap` files are correctly ignored end-to-end,
  `.pcapng` files are correctly processed, duplicate-post state persists
  across instances/restarts, GPS parsing works for both `.gps.json` and
  `.geo.json`, a missing handshake directory doesn't crash the scan, and
  critically - **a successful post never sleeps or retries**, a flaky
  post that succeeds on the 3rd try sleeps exactly twice, and a
  permanently-failing post stops cleanly at `retry_attempts` without ever
  marking the file as posted.
- 11 logic-level tests against `hashbot.py` (stubbed `discord`/`paramiko`,
  real `python-dotenv`): all 6 commands register, a missing `.env` value
  aborts with a clear message naming it, the authorization check blocks a
  non-matching Discord user ID and passes the authorized one, `ssh_run()`
  correctly relays stdout and reports connection failures, and the
  confirm flow requires the *exact* `Yes!`/`NO!` text - a lowercase `yes`
  or a message from a different user/channel does not match.
- No bugs found in either file during this pass.

## Still open / needs real-hardware testing

This sandbox has no path to Discord's API and can't install the real
`discord.py`/`paramiko` packages, so the following still need the actual
pi + 3.5" TFT + jayofelony 64-bit image, a real Discord bot/webhook, and
real SSH access, per `SETUP.md`:

- An actual captured handshake posting to Discord end-to-end (real
  `hcxpcapngtool`/`hcxhashtool` output, real webhook delivery).
- `hashbot.py` actually connecting to Discord and responding to commands
  for real.
- `!reboot`/`!poweroff` actually reaching the pi over real SSH and
  executing (including the sudoers scoping from `SETUP.md` step 8).
- `hcxpcapngtool`/`hcxhashtool` command-line flags were carried over
  unchanged from the original `discohash.py` (already known-correct
  usage, matches the pattern in this fork's own bundled `hashie`-family
  plugins) - not independently re-verified against a live hcxtools
  install.
- SSH-based `!status`/`!reboot`/etc. depend on the sudoers/SSH-key setup
  in `SETUP.md` being followed correctly - worth a dry run before relying
  on `!reboot` in a real situation.

## Config verified against upstream (2026-09-28)

Compared `pi-plugin/config.toml` against the real upstream sample
(`itsdarklikehell/pwnagotchi-plugins/configs/discohash.toml`, which just
sets `enabled = true`) and `discohash.py`'s own `__defaults__`
(`enabled` only - `webhook_url`, `handshake_dir`, `state_file`,
`retry_attempts`, `retry_delay` don't exist upstream as configurable
options at all; the original hardcoded its behavior and read the webhook
URL a different, less clean way). Checked every `self.options.get(...)`/
`self.options[...]` call in `discohash_ng.py` - all five are present and
documented. Renamed `pi-plugin/config.toml.example` to
`pi-plugin/config.toml`.

`hashbot/.env.example` is intentionally kept as `.env.example`, not
renamed - it's a dotenv template for the standalone off-pi Discord bot
(not a pwnagotchi `config.toml`), and this repo's `.gitignore` excludes
real `.env` files from being committed. A real `.env` (with real
secrets) is still required locally; `.env.example` is the trackable
template for it. `hashbot.py` upstream (`itsdarklikehell/pwnagotchi-plugins/hashbot.py`)
reads its Discord token/guild/channel via `os.getenv()` with no
committed sample at all - `.env.example` documents the real variables
(`DISCORD_TOKEN`, `HASH_CHANNEL_ID`, `AUTHORIZED_USER_ID`,
`PI_SSH_HOST`, `PI_SSH_PORT`, `PI_SSH_USER`, `PI_SSH_KEY_PATH`,
`PI_HANDSHAKE_DIR`) against what `hashbot.py` actually consumes.

## Original config preserved

A real original config file for `discohash.py` was found (exact match)
at `itsdarklikehell/pwnagotchi-plugins/configs/discohash.toml` (only
`discohash.py` had a documented real config find - no real config was
ever tracked for `discoBoss.py` or the flamebarke-fork `hashbot.py`
also merged into this suite). Preserved verbatim as
`pi-plugin/discohash.config.original.toml`, alongside the rebuilt
`pi-plugin/config.toml`, per the project's standing config-preservation
requirement.
