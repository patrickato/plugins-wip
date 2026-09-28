# hashbot (companion bot - NOT installed on the pwnagotchi)

Runs on your own computer or a small always-on server (not the pi). Talks
to Discord and to your pwnagotchi over SSH. This is the piece that
replaces both the original `discoBoss.py` and the original `hashbot.py` -
one bot, no duplicated logic.

## Requirements

- Python 3.9+ on whatever machine you run this on (your PC, a Raspberry
  Pi other than the pwnagotchi, a small VPS - anything that can stay
  online)
- A Discord account and a server you control
- Passwordless SSH key access from this machine to your pwnagotchi

## Install

```
cd discohash-suite/hashbot
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Then open `.env` and fill in every value - see `../SETUP.md` for the
full step-by-step walkthrough (creating the Discord bot application,
getting your channel ID and user ID, and setting up the SSH key).

## Run

```
python3 hashbot.py
```

Leave it running (a terminal, a `screen`/`tmux` session, or set it up as
a system service - see `../SETUP.md` step 9 for an optional
`systemd` unit file if you want it to survive a reboot of this machine).

## Commands

All commands only work for the Discord user ID in `AUTHORIZED_USER_ID` -
anyone else gets a flat "Not authorized."

| Command | What it does |
|---|---|
| `!dumphash [N]` | Bundles the last N hashes DiscoHashNG posted (default 20) into a downloadable `.22000` file |
| `!status` | Uptime, CPU temperature, and total handshake count |
| `!uptime` | Just the uptime |
| `!lastcrack` | Most recently cracked password from your WPA-SEC potfile, if any |
| `!reboot` | Reboots the pwnagotchi - asks you to reply exactly `Yes!` or `NO!` first |
| `!poweroff` | Powers off the pwnagotchi - same confirmation step |
| `!help` | Built-in command list |

## Why exact `Yes!`/`NO!` confirmation, not just "y/n"?

Picking an uncommon, exact phrase means an ordinary message you happen to
type in the channel right after running `!reboot` ("yes I saw that",
"no wait") can't accidentally confirm or cancel it. You have to
deliberately type the exact confirmation text.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Bot won't start, complains about missing .env values | You haven't filled in every field in `.env` yet |
| "Not authorized." on every command, even from you | `AUTHORIZED_USER_ID` in `.env` doesn't match your real Discord user ID - re-copy it (see `../SETUP.md`) |
| `!status`/`!reboot`/etc all fail with an SSH error | Check `PI_SSH_HOST`/`PI_SSH_USER`/`PI_SSH_KEY_PATH` in `.env`, and confirm `ssh -i <key> <user>@<host>` works from this machine without a password prompt |
| `!reboot`/`!poweroff` say "SSH error: ... sudo: a password is required" | Your pi user needs a scoped passwordless-sudo rule for shutdown - see `../SETUP.md` step 8 |
| `!dumphash` finds nothing | Check `HASH_CHANNEL_ID` in `.env` matches the channel DiscoHashNG actually posts into |
