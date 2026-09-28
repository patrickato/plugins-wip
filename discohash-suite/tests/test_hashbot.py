import sys
import os
import asyncio
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, "..", "hashbot"))

# Set required env vars directly (real dotenv is installed; load_dotenv() with
# no matching .env file just no-ops and leaves our pre-set env vars alone).
os.environ.update({
    "DISCORD_TOKEN": "fake-token-for-testing",
    "HASH_CHANNEL_ID": "123456789012345678",
    "AUTHORIZED_USER_ID": "999888777666555444",
    "PI_SSH_HOST": "192.0.2.1",
    "PI_SSH_PORT": "22",
    "PI_SSH_USER": "pi",
    "PI_SSH_KEY_PATH": "/tmp/fake_key",
    "PI_HANDSHAKE_DIR": "/etc/pwnagotchi/handshakes",
})

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


# --- Test 1: module imports cleanly and registers all expected commands ---
import hashbot  # noqa: E402

check("AUTHORIZED_USER_ID parsed as int", hashbot.AUTHORIZED_USER_ID == 999888777666555444)
check("HASH_CHANNEL_ID parsed as int", hashbot.HASH_CHANNEL_ID == 123456789012345678)

expected_commands = {"dumphash", "status", "uptime", "lastcrack", "reboot", "poweroff"}
registered = set(hashbot.bot.commands_by_name.keys())
check(f"all expected commands registered ({expected_commands})", expected_commands.issubset(registered))

# --- Test 2: missing required env var causes a clean, actionable SystemExit ---
import subprocess

result = subprocess.run(
    [sys.executable, "-c", (
        "import sys; "
        f"sys.path.insert(0, {os.path.join(HERE, 'stub_deps')!r}); "
        f"sys.path.insert(0, {os.path.join(HERE, '..', 'hashbot')!r}); "
        "import os; "
        "os.environ.pop('DISCORD_TOKEN', None); "
        "import hashbot"
    )],
    capture_output=True, text=True,
)
check("missing DISCORD_TOKEN exits non-zero", result.returncode != 0)
check("missing-var error message names the missing var", "DISCORD_TOKEN" in result.stderr)


# --- Test 3: authorization check blocks the wrong user, allows the right one ---
import discord.ext.commands as commands_stub  # noqa: E402


class FakeUser:
    def __init__(self, uid):
        self.id = uid

    def __str__(self):
        return f"user#{self.id}"


async def run_auth_test():
    dumphash_cmd = hashbot.bot.commands_by_name["dumphash"]
    # Unauthorized caller
    ctx_bad = commands_stub.Context(FakeUser(111), channel=object(), content="!dumphash")
    try:
        await dumphash_cmd.invoke(ctx_bad, 5)
        return False, "expected CheckFailure for unauthorized user, none raised"
    except commands_stub.CheckFailure:
        pass
    except Exception as e:
        return False, f"unexpected exception for unauthorized user: {e!r}"

    # Authorized caller - patch bot.get_channel to avoid needing a real channel
    ctx_good = commands_stub.Context(FakeUser(hashbot.AUTHORIZED_USER_ID), channel=object(), content="!dumphash")
    with mock.patch.object(hashbot.bot, "get_channel", return_value=None):
        await dumphash_cmd.invoke(ctx_good, 5)
    # get_channel returned None -> command sends the "could not be found" message and returns
    sent_texts = [a[0] if a else "" for a, kw in ctx_good.sent]
    if not any("could not be found" in str(t) for t in sent_texts):
        return False, f"authorized call did not reach command body as expected: {sent_texts}"
    return True, ""


ok, msg = asyncio.run(run_auth_test())
check("unauthorized user blocked, authorized user reaches command body" + (f" ({msg})" if msg else ""), ok)


# --- Test 4: ssh_run() correctly relays stdout/stderr and success/failure ---
import paramiko  # noqa: E402


def fake_script_ok(command):
    return (f"ran: {command}".encode(), b"")


paramiko.SSHClient.SCRIPT = fake_script_ok
paramiko.SSHClient.RAISE = None
ok, out = hashbot.ssh_run("uptime -p")
check("ssh_run returns ok=True and relays stdout on success", ok is True and out == "ran: uptime -p")

paramiko.SSHClient.RAISE = ConnectionRefusedError("connection refused")
ok, out = hashbot.ssh_run("uptime -p")
check("ssh_run returns ok=False and the error text on connection failure", ok is False and "refused" in out)
paramiko.SSHClient.RAISE = None


# --- Test 5: confirm_action requires the EXACT strings "Yes!"/"NO!" ---
async def run_confirm_test():
    results = []

    class FakeCtx:
        def __init__(self, author_id, channel_id):
            self.author = FakeUser(author_id)
            self.channel = type("C", (), {"id": channel_id})()
            self.sent = []

        async def send(self, *a, **kw):
            self.sent.append(a[0] if a else "")

    class FakeMsg:
        def __init__(self, author_id, channel_id, content):
            self.author = FakeUser(author_id)
            self.channel = type("C", (), {"id": channel_id})()
            self.content = content

    ctx = FakeCtx(42, 7)

    # Case A: exact "Yes!" confirms
    async def fake_wait_for(event_name, timeout=None, check=None):
        msg = FakeMsg(42, 7, "Yes!")
        assert check(msg) is True
        return msg

    with mock.patch.object(hashbot.bot, "wait_for", side_effect=fake_wait_for):
        result = await hashbot.confirm_action(ctx, "do the thing")
    results.append(("exact Yes! confirms", result is True))

    # Case B: exact "NO!" cancels
    async def fake_wait_for_no(event_name, timeout=None, check=None):
        return FakeMsg(42, 7, "NO!")

    with mock.patch.object(hashbot.bot, "wait_for", side_effect=fake_wait_for_no):
        result = await hashbot.confirm_action(ctx, "do the thing")
    results.append(("exact NO! cancels", result is False))

    # Case C: the check() predicate rejects near-miss text like "yes" (lowercase, no !)
    async def fake_wait_for_check_only(event_name, timeout=None, check=None):
        near_miss = FakeMsg(42, 7, "yes")
        exact = FakeMsg(42, 7, "Yes!")
        return exact if check(exact) and not check(near_miss) else None

    with mock.patch.object(hashbot.bot, "wait_for", side_effect=fake_wait_for_check_only):
        result = await hashbot.confirm_action(ctx, "do the thing")
    results.append(("lowercase 'yes' does not match the confirm predicate", result is True))

    # Case D: a message from a different user/channel is rejected by the predicate
    async def fake_wait_for_wrong_author(event_name, timeout=None, check=None):
        wrong_author = FakeMsg(999, 7, "Yes!")
        right_author = FakeMsg(42, 7, "Yes!")
        assert check(wrong_author) is False
        assert check(right_author) is True
        return right_author

    with mock.patch.object(hashbot.bot, "wait_for", side_effect=fake_wait_for_wrong_author):
        result = await hashbot.confirm_action(ctx, "do the thing")
    results.append(("predicate rejects messages from a different author", result is True))

    return results


for name, cond in asyncio.run(run_confirm_test()):
    check(name, cond)


print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("ALL TESTS PASSED")
