import logging
import socket
import time

import pwnagotchi.plugins as plugins
from pwnagotchi.ui.components import LabeledValue
from pwnagotchi.ui.view import BLACK
import pwnagotchi.ui.fonts as fonts


# This fork's loader does NOT merge a plugin's __defaults__ into
# self.options - see pwnagotchi/plugins/__init__.py's load(). Every
# option must be read with a real fallback, never raw indexing.
DEFAULTS = {
    "enabled": False,
    "position_x": None,
    "position_y": None,
    "label": "WWW",
    "connected_value": "C",
    "disconnected_value": "D",
    "active_recheck": True,
    "recheck_test_host": "8.8.8.8",
    "recheck_test_port": 53,
    "recheck_timeout": 1.0,
}

ELEMENT_NAME = "internet_connection_ng"


class InternetConnectionNG(plugins.Plugin):
    __author__ = "consolidated by this project's plugin audit"
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = (
        "Shows internet connectivity status on the display. Consolidates "
        "internet-connection.py, wanmon.py, and internet-conection.py "
        "into one plugin: event-driven (no polling in the render path), "
        "with an optional lightweight periodic re-check so the icon can "
        "actually go back to 'disconnected' if the connection drops."
    )
    __name__ = "InternetConnectionNG"
    __help__ = __description__

    def __init__(self):
        self._connected = False

    def _opt(self, key):
        return self.options.get(key, DEFAULTS[key])

    def on_loaded(self):
        logging.info("[InternetConnectionNG] plugin loaded")

    def on_ui_setup(self, ui):
        pos_x = self._opt("position_x")
        pos_y = self._opt("position_y")
        if pos_x is None:
            pos_x = ui.width() / 2 - 35
        if pos_y is None:
            pos_y = 0

        with ui._lock:
            ui.add_element(
                ELEMENT_NAME,
                LabeledValue(
                    color=BLACK,
                    label=self._opt("label"),
                    value=self._opt("disconnected_value"),
                    position=(pos_x, pos_y),
                    label_font=fonts.Bold,
                    text_font=fonts.Medium,
                ),
            )

    def on_unload(self, ui):
        with ui._lock:
            try:
                ui.remove_element(ELEMENT_NAME)
            except KeyError:
                pass
        logging.info("[InternetConnectionNG] plugin unloaded")

    def on_internet_available(self, agent):
        # Real framework event (pwnagotchi/cli.py) - fires once per
        # epoch/manual-mode tick whenever grid.is_connected() is true.
        # There is no matching "internet lost" event on this fork, so
        # this alone can only ever turn the icon ON, never back off -
        # that's what the active re-check below is for.
        self._set_connected(agent, True)

    def on_epoch(self, agent, epoch, epoch_data):
        self._maybe_recheck(agent)

    def on_ready(self, agent):
        # Get an initial, real read on startup instead of silently
        # assuming "disconnected" until the first epoch completes -
        # but only when active_recheck is on, so a false setting
        # reproduces the original's pure event-driven behavior exactly
        # (never probes on its own, only reacts to internet_available).
        self._maybe_recheck(agent)

    def _maybe_recheck(self, agent):
        if not self._opt("active_recheck"):
            return
        ok = self._quick_check()
        self._set_connected(agent, ok)

    def _quick_check(self):
        # A cheap TCP connect probe - stdlib only, no extra dependency
        # (both wanmon.py and internet-connection.py declared an unused
        # `scapy` pip dependency that neither ever actually imported).
        # Runs from on_epoch/on_ready, never from a UI-render hook, so
        # it can never stutter the display the way internet-conection.py's
        # urllib call inside on_ui_update did.
        host = self._opt("recheck_test_host")
        port = self._opt("recheck_test_port")
        timeout = self._opt("recheck_timeout")
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def _set_connected(self, agent, connected):
        if connected == self._connected:
            return
        self._connected = connected
        try:
            display = agent.view()
            display.set(
                ELEMENT_NAME,
                self._opt("connected_value") if connected else self._opt("disconnected_value"),
            )
        except Exception as e:
            logging.debug("[InternetConnectionNG] couldn't update UI element: %s", e)
        logging.debug("[InternetConnectionNG] connectivity state -> %s", connected)
