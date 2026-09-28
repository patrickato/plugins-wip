"""Minimal stand-in for discord.ext.commands, just enough surface to import
hashbot.py and exercise its own logic (auth checks, confirm flow, ssh_run)
without a real Discord connection. Mirrors the real API's shape."""


class CheckFailure(Exception):
    pass


class DefaultHelpCommand:
    pass


class Context:
    def __init__(self, author, channel, content=""):
        self.author = author
        self.channel = channel
        self.message = Message(author, channel, content)
        self.sent = []

    async def send(self, *args, **kwargs):
        self.sent.append((args, kwargs))


class Message:
    def __init__(self, author, channel, content=""):
        self.author = author
        self.channel = channel
        self.content = content


def check(predicate):
    def decorator(func):
        func.__check_predicate__ = predicate
        return func
    return decorator


class Command:
    def __init__(self, func, name):
        self.callback = func
        self.name = name
        self.checks = []
        self.error_handler = None

    def error(self, func):
        self.error_handler = func
        return func

    async def invoke(self, ctx, *args, **kwargs):
        for chk in self.checks:
            ok = await chk(ctx)
            if not ok:
                raise CheckFailure()
        return await self.callback(ctx, *args, **kwargs)


class Bot:
    def __init__(self, command_prefix=None, intents=None, help_command=None):
        self.command_prefix = command_prefix
        self.intents = intents
        self.commands_by_name = {}
        self._wait_for_queue = []

    def event(self, func):
        setattr(self, func.__name__, func)
        return func

    def command(self, name=None):
        def decorator(func):
            cmd_name = name or func.__name__
            cmd = Command(func, cmd_name)
            if hasattr(func, "__check_predicate__"):
                cmd.checks.append(func.__check_predicate__)
            self.commands_by_name[cmd_name] = cmd
            return cmd
        return decorator

    def get_channel(self, channel_id):
        return None

    async def wait_for(self, event_name, timeout=None, check=None):
        raise NotImplementedError("stub: override in test")

    def run(self, token):
        raise RuntimeError("stub bot.run() should not be called in tests")
