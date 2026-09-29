"""Minimal stand-in for RPi.GPIO, test-only. The real module only
exists on actual Raspberry Pi hardware; this sandbox has none. Not
shipped - TouchUING genuinely requires the real RPi.GPIO on-device.
"""
BCM = "BCM"
IN = "IN"
OUT = "OUT"
PUD_UP = "PUD_UP"
FALLING = "FALLING"
RISING = "RISING"

_events = {}


def setmode(mode):
    pass


def setup(pin, direction, pull=None):
    pass


def add_event_detect(pin, edge, callback=None, bouncetime=None):
    _events[(pin, edge)] = callback


def remove_event_detect(pin):
    _events.pop((pin, FALLING), None)
    _events.pop((pin, RISING), None)
