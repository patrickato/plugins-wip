# DiscoHash Suite - full setup walkthrough

This covers everything from a blank Discord account to a working
pi -> Discord hash pipeline with remote status/reboot/poweroff. Do these
in order. Total time: about 20-30 minutes the first time.

You will end up with two independent pieces:
- **`pi-plugin/discohash_ng.py`** - installed on the pwnagotchi, posts hashes to Discord
- **`hashbot/hashbot.py`** - runs on your own computer/server, reads Discord and controls the pi over SSH

## 1. Create a private Discord server (skip if you already have one)

1. Open Discord, click the **+** at the bottom of the server list.
2. **Create My Own** -> **For me and my friends** (doesn't matter, it's just a label).
3. Name it anything, e.g. "pwnagotchi".

This gives you a server only you are in by default - nobody else can see or join it unless you invite them.

## 2. Create a private channel for hashes

1. In your new server, create a text channel (right-click the server name -> **Create Channel**), name it something like `disco-hashes`.
2. Leave its permissions as default (private to server members - since you're the only member, only you can see it).

## 3. Enable Developer Mode (needed to copy IDs)

1. Discord app -> **User Settings** (gear icon near your name) -> **Advanced**.
2. Turn on **Developer Mode**.

## 4. Get your channel ID and your own user ID

1. Right-click the `disco-hashes` channel -> **Copy Channel ID**. This is your `HASH_CHANNEL_ID`.
2. Right-click your own username anywhere (member list, a message you sent) -> **Copy User ID**. This is your `AUTHORIZED_USER_ID`.

Save both somewhere - you'll paste them into `hashbot/.env` in step 7.

## 5. Create the Discord bot application (for hashbot.py)

1. Go to https://discord.com/developers/applications in a browser.
2. **New Application** -> name it (e.g. "hashbot") -> **Create**.
3. Left sidebar -> **Bot** -> **Add Bot** (or it may already exist) -> confirm.
4. Under **Privileged Gateway Intents**, turn on **Message Content Intent**. This is required for the bot to read your commands.
5. Click **Reset Token** (or **Copy** if this is the first time) and copy the token somewhere safe. This is your `DISCORD_TOKEN`. **Never share this or commit it to git** - anyone with it can control your bot.
6. Left sidebar -> **OAuth2** -> **URL Generator**. Under **Scopes** check `bot`. Under **Bot Permissions** check: `Send Messages`, `Read Message History`, `Attach Files`. Copy the generated URL at the bottom.
7. Paste that URL into a browser, pick your server, **Authorize**. Your bot now appears in your server's member list (offline until you run `hashbot.py`).

## 6. Create the Discord webhook (for discohash_ng.py, on the pi)

1. In Discord, go to the `disco-hashes` channel -> the gear/settings icon -> **Integrations** -> **Webhooks** -> **New Webhook**.
2. Name it anything (e.g. "DiscoHashNG"), leave the channel as `disco-hashes`.
3. Click **Copy Webhook URL**. This is your `webhook_url` for `pi-plugin/config.toml`.

## 7. Set up SSH key access from your computer to the pwnagotchi

`hashbot.py` needs to reach the pwnagotchi over SSH without a password prompt (it can't type one for you).

On the machine that will run `hashbot.py`:
```
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_pwnagotchi -N ""
ssh-copy-id -i ~/.ssh/id_ed25519_pwnagotchi.pub pi@<your-pwnagotchi-ip-or-hostname>
```
Test it works with no password prompt:
```
ssh -i ~/.ssh/id_ed25519_pwnagotchi pi@<your-pwnagotchi-ip-or-hostname> echo ok
```
If that printed `ok` with no password prompt, you're set. `PI_SSH_KEY_PATH` in `.env` is the path to the **private** key (`~/.ssh/id_ed25519_pwnagotchi`, no `.pub`).

## 8. Allow passwordless reboot/poweroff (only if you want those commands to work)

By default `sudo shutdown` still asks for a password, which `hashbot.py` can't answer. Grant a narrow, scoped exception - **not** full passwordless sudo:

On the pwnagotchi:
```
sudo visudo -f /etc/sudoers.d/hashbot-shutdown
```
Add this one line (replace `pi` if your username differs):
```
pi ALL=(ALL) NOPASSWD: /sbin/shutdown
```
Save and exit. This only exempts the `shutdown` command specifically - your pi user still needs a password for everything else via sudo.

If you'd rather not grant this at all, skip it - `!status`, `!uptime`, `!dumphash`, and `!lastcrack` all work fine without it; only `!reboot`/`!poweroff` need it.

## 9. Fill in the config files

- **`pi-plugin/config.toml`**: fill in `webhook_url` from step 6, then merge this block into `/etc/pwnagotchi/config.toml` on the pi. Follow `pi-plugin/README.md` for full install steps.
- **`hashbot/.env.example`**: copy to `hashbot/.env`, fill in everything from steps 4, 5, and 7. Follow `hashbot/README.md` for full run steps.

## 10. (Optional) Keep hashbot.py running automatically

If you're running `hashbot.py` on a Linux machine that's normally on, a simple `systemd` service keeps it running and restarts it if it crashes:

```
sudo tee /etc/systemd/system/hashbot.service <<'EOF'
[Unit]
Description=hashbot Discord companion for pwnagotchi DiscoHashNG
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/path/to/discohash-suite/hashbot
ExecStart=/path/to/discohash-suite/hashbot/venv/bin/python3 hashbot.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now hashbot
```
Replace both `/path/to/...` lines with the real path on your machine. This step is entirely optional - running `python3 hashbot.py` in a terminal works fine for testing.

## You're done when...

- The pwnagotchi's log shows `[DiscoHashNG] plugin loaded` with no `webhook_url is not set` warning.
- `hashbot.py`'s terminal shows `hashbot logged in as ...`.
- Your next captured handshake shows up as an embed in `#disco-hashes` within one epoch.
- `!status` in Discord returns real uptime/temp/handshake-count numbers.
