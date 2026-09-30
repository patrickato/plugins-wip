import logging
import os
import time

from pwnagotchi.ui.components import LabeledValue
from pwnagotchi.ui.view import BLACK
import pwnagotchi.ui.fonts as fonts
import pwnagotchi.plugins as plugins


# This fork's loader does NOT merge a plugin's __defaults__ into
# self.options - every option must be read with a real fallback.
DEFAULTS = {
    "enabled": False,
    # None on both means: use the original's own default position,
    # (ui.width() // 2 - 10, 0).
    "position_x": None,
    "position_y": None,
    # ADDED: how often (in seconds) to actually shell out to `ps -A`
    # and re-check whether aircrack-ng is running. The original did
    # this on every single UI render tick - typically several times a
    # second - which is a lot of unnecessary process spawning for a
    # status that only needs to be roughly current.
    "check_interval": 3,
    # ADDED: configurable status text, replacing the original's bare
    # "(1)"/"(0)".
    "running_text": "AC:ON",
    "stopped_text": "AC:OFF",
}

ELEMENT_NAME = "display_aircrack_ng"


class DisplayAircrackNG(plugins.Plugin):
    __author__ = "fixed/extended by this project's plugin audit, from itsdarklikehell/7h30th3r0n3's display-aircrack.py"
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = "Shows whether aircrack-ng is currently running."
    __name__ = "DisplayAircrackNG"
    __help__ = __description__

    def __init__(self):
        self._running = False
        self._next_check = 0

    def _opt(self, key):
        return self.options.get(key, DEFAULTS[key])

    def _check_interval(self):
        # ADDED: guard against a misconfigured (zero/negative/non-numeric)
        # interval turning this back into a check-on-every-tick busy loop.
        interval = self._opt("check_interval")
        try:
            interval = float(interval)
            if interval <= 0:
                raise ValueError
        except (TypeError, ValueError):
            logging.warning(
                "[DisplayAircrackNG] invalid check_interval %r, falling "
                "back to the default (%s seconds)",
                interval, DEFAULTS["check_interval"],
            )
            return DEFAULTS["check_interval"]
        return interval

    def on_loaded(self):
        logging.info("[DisplayAircrackNG] plugin loaded")

    def _default_position(self, ui):
        return (ui.width() // 2 - 10, 0)

    def on_ui_setup(self, ui):
        pos_x = self._opt("position_x")
        pos_y = self._opt("position_y")
        if pos_x is None or pos_y is None:
            pos_x, pos_y = self._default_position(ui)

        ui.add_element(
            ELEMENT_NAME,
            LabeledValue(
                color=BLACK,
                label="",
                value="",
                position=(pos_x, pos_y),
                label_font=fonts.Bold,
                text_font=fonts.Medium,
            ),
        )

    def on_unload(self, ui):
        with ui._lock:
            try:
                ui.remove_element(ELEMENT_NAME)
                logging.info("[DisplayAircrackNG] plugin unloaded")
            except Exception as e:
                logging.error("[DisplayAircrackNG] unload: %s", repr(e))

    def on_ui_update(self, ui):
        # FIXED/ADDED: the original shelled out to `ps -A` on every
        # single call to on_ui_update - i.e. every UI render tick,
        # typically several times a second - just to check one boolean
        # status that doesn't need to be that fresh. Now only actually
        # re-checks every check_interval seconds; the displayed value
        # is still updated every tick from the last known state.
        now = time.time()
        if now >= self._next_check:
            self._next_check = now + self._check_interval()
            try:
                self._running = "aircrack-ng" in os.popen("ps -A").read()
            except Exception as e:
                logging.debug("[DisplayAircrackNG] ps check failed: %s", e)

        ui.set(
            ELEMENT_NAME,
            self._opt("running_text") if self._running else self._opt("stopped_text"),
        )

    def on_webhook(self, path, request):
        logging.info("[DisplayAircrackNG] webhook pressed")
        # Return a body. Returning None makes Flask raise a 500 on the bare
        # index path (GET /plugins/display_aircrack_ng/).
        state = self._opt("running_text") if self._running else self._opt("stopped_text")
        return (
            "<html><body><h2>DisplayAircrackNG</h2>"
            "<p>aircrack-ng: %s</p></body></html>" % state
        )
