"""Minimal stand-in for paramiko, just enough to import hashbot.py and
test its ssh_run() logic with a controllable fake SSH backend."""


class AutoAddPolicy:
    pass


class _FakeStream:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data


class SSHClient:
    # Test hook: set SSHClient.SCRIPT to a callable(command) -> (stdout_bytes, stderr_bytes)
    # or set SSHClient.RAISE to an exception instance to simulate a connection failure.
    SCRIPT = None
    RAISE = None

    def set_missing_host_key_policy(self, policy):
        pass

    def connect(self, hostname=None, port=None, username=None, key_filename=None, timeout=None):
        if SSHClient.RAISE is not None:
            raise SSHClient.RAISE
        self._connected = True

    def exec_command(self, command, timeout=None):
        if SSHClient.SCRIPT is not None:
            out, err = SSHClient.SCRIPT(command)
        else:
            out, err = b"", b""
        return None, _FakeStream(out), _FakeStream(err)

    def close(self):
        pass
