import logging
import os
import time

import pwnagotchi.plugins as plugins
from pwnagotchi.ui.components import Text
from pwnagotchi.ui.view import BLACK
import pwnagotchi.ui.fonts as fonts
import pwnagotchi.utils as utils

# This fork's loader does NOT merge a plugin's __defaults__ into
# self.options - every option must be read with a real fallback.
DEFAULTS = {
    "enabled": False,
    # If true, hijacks the stock "uptime" UI element's label instead of
    # creating a separate "more_uptime" element.
    "override": False,
    "position_x": None,
    "position_y": None,
}

ELEMENT_NAME = "more_uptime_ng"

try:
    HZ = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
except (ValueError, KeyError, AttributeError):
    HZ = 100  # sane fallback for non-Linux/dev environments


class MoreUptimeNG(plugins.Plugin):
    __author__ = "fixed/extended by this project's plugin audit, from itsdarklikehell/evilsocket's more_uptime.py"
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = (
        "Cycles a small UI element between system uptime, pwnagotchi "
        "process uptime, and time-since-plugin-loaded."
    )
    __name__ = "MoreUptimeNG"
    __help__ = __description__

    def __init__(self):
        self._start = time.time()
        self._state = 0
        self._next = 0

    def _opt(self, key):
        return self.options.get(key, DEFAULTS[key])

    def on_loaded(self):
        self._start = time.time()
        self._state = 0
        self._next = 0
        logging.info("[MoreUptimeNG] plugin loaded")

    def on_unload(self, ui):
        try:
            if not self._opt("override") and ui.has_element(ELEMENT_NAME):
                ui.remove_element(ELEMENT_NAME)
        except Exception as e:
            logging.warning("[MoreUptimeNG] unload: %s", repr(e))
        logging.info("[MoreUptimeNG] plugin unloaded")

    def on_ready(self, agent):
        self._agent = agent

    def on_ui_setup(self, ui):
        if self._opt("override"):
            # Hijacking the stock "uptime" element - nothing to add.
            return
        try:
            pos_x = self._opt("position_x")
            pos_y = self._opt("position_y")
            if pos_x is None or pos_y is None:
                pos_x, pos_y = (ui.width() - 58, 12)

            # FIXED: the original nested this add_element() call one
            # level too deep, inside the "else" branch of "if 'position'
            # in self.options" - meaning the element was only ever
            # created when the user had NOT set a custom position, and
            # every later on_ui_update() call then failed trying to
            # update an element that was never created whenever a
            # position WAS configured.
            ui.add_element(
                ELEMENT_NAME,
                Text(
                    color=BLACK,
                    value="up --:--",
                    position=(pos_x, pos_y),
                    font=fonts.Small,
                ),
            )
        except Exception as e:
            logging.warning("[MoreUptimeNG] ui setup: %s", repr(e))

    def on_ui_update(self, ui):
        try:
            if time.time() > self._next:
                self._next = int(time.time()) + 5
                self._state = (self._state + 1) % 3

            uptimes = open("/proc/uptime").read().split()
            if self._state == 2:
                res = utils.secs_to_hhmmss(float(uptimes[0]))
                label = "UP"
            elif self._state == 1:
                process_stats = open("/proc/self/stat").read().split()
                res = utils.secs_to_hhmmss(
                    float(uptimes[0]) - (int(process_stats[21]) / HZ)
                )
                label = "PR"
            else:
                res = utils.secs_to_hhmmss(time.time() - self._start)
                label = "IN"

            logging.debug("[MoreUptimeNG] %s: %s", label, res)

            if self._opt("override"):
                try:
                    ui_state = ui._state._state
                    ui_state["uptime"].label = label
                except Exception as e:
                    logging.warning("[MoreUptimeNG] label hijack: %s", repr(e))
                ui.set("uptime", res)
            else:
                ui.set(ELEMENT_NAME, "%s %s" % (label, res))
        except Exception as e:
            # FIXED: the original's except handler referenced `uiItems`,
            # a variable that only ever existed inside the "override"
            # branch of the try block above - any exception raised
            # BEFORE that point (e.g. /proc/uptime unreadable) crashed
            # this handler a second time with a NameError, completely
            # masking the real error.
            logging.warning("[MoreUptimeNG] ui update: %s", repr(e))

    def on_webhook(self, path, request):
        logging.info("[MoreUptimeNG] webhook pressed")
