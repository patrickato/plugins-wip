"""Minimal stub for `tomlkit`, which this dev/test environment cannot
install. pwnagotchi/utils.py imports it at module level but only calls
it from config-file read/write helpers this suite never exercises - a
stub that only needs to exist, not function, is all that's needed to
load the REAL pwnagotchi.utils module for testing."""


def dumps(*args, **kwargs):
    raise NotImplementedError("tomlkit stub - not exercised by these tests")


def loads(*args, **kwargs):
    raise NotImplementedError("tomlkit stub - not exercised by these tests")


def dump(*args, **kwargs):
    raise NotImplementedError("tomlkit stub - not exercised by these tests")
