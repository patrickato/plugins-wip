"""Minimal stub for the native `prctl` module, which this dev/test
environment cannot install. pwnagotchi/plugins/__init__.py only calls
prctl.set_name() (to label the plugin-event-queue worker threads for
`ps`/`top`) - a no-op here is all that's needed to load the REAL
plugin loader for testing."""


def set_name(name):
    pass
