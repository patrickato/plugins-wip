"""
hashbot.py - Discord companion bot for the DiscoHashNG pwnagotchi plugin.

RUN THIS ON YOUR OWN COMPUTER OR A SERVER - NOT ON THE PWNAGOTCHI ITSELF.
It is not a pwnagotchi plugin; it's a standalone script that talks to
Discord and to your pwnagotchi over SSH.

See ../SETUP.md for full step-by-step setup instructions, including how
to create the Discord bot application and how to set up passwordless SSH
key access to your pwnagotchi.

============================================================
>>> USER INPUT REQUIRED <<<
All configuration lives in a `.env` file next to this script - copy
`.env.example` to `.env` and fill in every value there. Nothing in this
file itself needs editing.
============================================================

Commands (all restricted to AUTHORIZED_USER_ID from your .env):
  !dumphash [N]   - bundle the last N hashes DiscoHashNG posted into a file
  !status         - uptime, temperature, and handshake count
  !uptime         - just the uptime
  !lastcrack      - most recently cracked password, if any
  !reboot         - reboot the pwnagotchi (asks for Yes!/NO! confirmation)
  !poweroff       - power off the pwnagotchi (asks for Yes!/NO! confirmation)
  !help           - built-in command list (from discord.py)

Fixes vs. the original discoBoss.py/hashbot.py this replaces:
  - Commands are matched exactly (discord.py's command parser), not with
    a naive "if '!reboot' in message" substring check - a stray mention
    of the word "reboot" in an unrelated message can no longer trigger it.
  - Every command is gated to a single authorized Discord user ID, not
    just "anyone who can post in this channel."
  - Destructive commands (reboot/poweroff) require a follow-up exact
    "Yes!" or "NO!" reply within 30 seconds before doing anything.
  - No hardcoded token/channel ID in the source - everything sensitive
    lives in .env, which is gitignored.
"""

import asyncio
import io
import logging
import os

import discord
import paramiko
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
HASH_CHANNEL_ID = os.getenv("HASH_CHANNEL_ID")
AUTHORIZED_USER_ID = os.getenv("AUTHORIZED_USER_ID")
PI_SSH_HOST = os.getenv("PI_SSH_HOST")
PI_SSH_PORT = int(os.getenv("PI_SSH_PORT", "22"))
PI_SSH_USER = os.getenv("PI_SSH_USER", "pi")
PI_SSH_KEY_PATH = os.getenv("PI_SSH_KEY_PATH")
PI_HANDSHAKE_DIR = os.getenv("PI_HANDSHAKE_DIR", "/etc/pwnagotchi/handshakes")

_required = {
    "DISCORD_TOKEN": DISCORD_TOKEN,
    "HASH_CHANNEL_ID": HASH_CHANNEL_ID,
    "AUTHORIZED_USER_ID": AUTHORIZED_USER_ID,
    "PI_SSH_HOST": PI_SSH_HOST,
    "PI_SSH_KEY_PATH": PI_SSH_KEY_PATH,
}
_missing = [name for name, val in _required.items() if not val]
if _missing:
    raise SystemExit(
        "hashbot.py: missing required .env values: "
        + ", ".join(_missing)
        + "\nCopy .env.example to .env in this folder and fill these in - "
        + "see ../SETUP.md."
    )

AUTHORIZED_USER_ID = int(AUTHORIZED_USER_ID)
HASH_CHANNEL_ID = int(HASH_CHANNEL_ID)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [hashbot] %(levelname)s: %(message)s"
)
log = logging.getLogger("hashbot")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=commands.DefaultHelpCommand())


def is_authorized():
    async def predicate(ctx):
        if ctx.author.id != AUTHORIZED_USER_ID:
            await ctx.send("Not authorized.")
            log.warning(
                f"Unauthorized command attempt from {ctx.author} "
                f"({ctx.author.id}): {ctx.message.content!r}"
            )
            return False
        return True

    return commands.check(predicate)


def ssh_run(command, timeout=20):
    """Run a command on the pwnagotchi over SSH (key auth). Returns (ok, output)."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=PI_SSH_HOST,
            port=PI_SSH_PORT,
            username=PI_SSH_USER,
            key_filename=PI_SSH_KEY_PATH,
            timeout=timeout,
        )
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        return True, (out + err).strip()
    except Exception as e:
        return False, str(e)
    finally:
        client.close()


async def confirm_action(ctx, action_description):
    """Ask for an exact 'Yes!' / 'NO!' reply. Returns True, False, or None (timeout)."""
    await ctx.send(
        f"WARNING: this will {action_description}. "
        f"Reply exactly `Yes!` to confirm or `NO!` to cancel (30s)."
    )

    def check(m):
        return (
            m.author.id == ctx.author.id
            and m.channel.id == ctx.channel.id
            and m.content in ("Yes!", "NO!")
        )

    try:
        reply = await bot.wait_for("message", timeout=30.0, check=check)
    except asyncio.TimeoutError:
        await ctx.send("No confirmation received in time - cancelled.")
        return None

    if reply.content == "Yes!":
        return True
    await ctx.send("Cancelled.")
    return False


@bot.event
async def on_ready():
    log.info(f"hashbot logged in as {bot.user} (id={bot.user.id})")


@bot.command(name="dumphash")
@is_authorized()
async def dumphash(ctx, num_hashes: int = 20):
    """Bundle the last N hashes DiscoHashNG posted into a downloadable file."""
    channel = bot.get_channel(HASH_CHANNEL_ID)
    if channel is None:
        await ctx.send("Configured HASH_CHANNEL_ID could not be found - check your .env.")
        return

    await ctx.send(f"Gathering up to {num_hashes} hash(es) from <#{HASH_CHANNEL_ID}>...")
    collected = []
    async for message in channel.history(limit=max(num_hashes * 3, 100)):
        if not message.embeds:
            continue
        embed = message.embeds[0].to_dict()
        for field in embed.get("fields", []):
            if field.get("name") == "Hash":
                collected.append(field["value"].strip("`\n"))
                break
        if len(collected) >= num_hashes:
            break

    if not collected:
        await ctx.send("No hashes found in that channel.")
        return

    data = ("\n".join(collected)).encode("utf-8")
    await ctx.send(
        f"{len(collected)} hash(es):",
        file=discord.File(fp=io.BytesIO(data), filename="hashes.22000"),
    )


@bot.command(name="status")
@is_authorized()
async def status(ctx):
    """Show pwnagotchi uptime, temperature, and handshake count."""
    ok1, uptime_out = ssh_run("uptime -p")
    ok2, temp_out = ssh_run("vcgencmd measure_temp")
    ok3, count_out = ssh_run(f"ls -1 {PI_HANDSHAKE_DIR}/*.pcapng 2>/dev/null | wc -l")
    if not (ok1 and ok2 and ok3):
        await ctx.send(
            f"Could not reach the pwnagotchi over SSH: "
            f"{uptime_out or temp_out or count_out}"
        )
        return
    await ctx.send(
        f"**Uptime:** {uptime_out}\n"
        f"**Temp:** {temp_out}\n"
        f"**Handshakes captured:** {count_out}"
    )


@bot.command(name="uptime")
@is_authorized()
async def uptime_cmd(ctx):
    """Show just the pwnagotchi's uptime."""
    ok, out = ssh_run("uptime -p")
    await ctx.send(out if ok else f"SSH error: {out}")


@bot.command(name="lastcrack")
@is_authorized()
async def lastcrack(ctx):
    """Show the most recently cracked password from the WPA-SEC potfile, if any."""
    ok, out = ssh_run("tail -n 1 /etc/pwnagotchi/wpa-sec.cracked.potfile 2>/dev/null")
    if not ok:
        await ctx.send(f"SSH error: {out}")
        return
    await ctx.send(f"Last cracked entry:\n```{out or '(none found)'}```")


@bot.command(name="reboot")
@is_authorized()
async def reboot(ctx):
    """Reboot the pwnagotchi (asks for confirmation first)."""
    confirmed = await confirm_action(ctx, "**reboot** the pwnagotchi")
    if confirmed:
        ok, out = ssh_run("sudo shutdown -r now")
        await ctx.send("Reboot command sent." if ok else f"SSH error: {out}")


@bot.command(name="poweroff")
@is_authorized()
async def poweroff(ctx):
    """Power off the pwnagotchi (asks for confirmation first)."""
    confirmed = await confirm_action(ctx, "**power off** the pwnagotchi")
    if confirmed:
        ok, out = ssh_run("sudo shutdown -h now")
        await ctx.send("Poweroff command sent." if ok else f"SSH error: {out}")


@dumphash.error
@status.error
@uptime_cmd.error
@lastcrack.error
@reboot.error
@poweroff.error
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        return  # already handled/logged inside is_authorized()
    log.exception(f"Command error: {error}")
    await ctx.send(f"Error: {error}")


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
