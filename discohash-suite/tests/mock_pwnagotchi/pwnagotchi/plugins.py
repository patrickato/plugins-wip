class Plugin:
    """Minimal stand-in for the real pwnagotchi.plugins.Plugin base class.
    The real one auto-registers via __init_subclass__ against the live
    framework; not needed for this logic-level test, so this is
    intentionally a no-op base."""
    options = {}
