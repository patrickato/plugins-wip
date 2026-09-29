import logging

from pwnagotchi.ui.components import LabeledValue
from pwnagotchi.ui.view import BLACK
import pwnagotchi.plugins as plugins
import pwnagotchi.ui.fonts as fonts
import pwnagotchi


# This fork's loader does NOT merge a plugin's __defaults__ into
# self.options - every option must be read with a real fallback.
DEFAULTS = {
    "enabled": False,
    # None on both means: use the original's own hardcoded position.
    "position_x": None,
    "position_y": None,
}

ELEMENT_NAME = "display_version_ng"


class DisplayVersionNG(plugins.Plugin):
    __author__ = "fixed/extended by this project's plugin audit, from itsdarklikehell/Teraskull's display_version.py"
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = "Adds the pwnagotchi software version to the display."
    __name__ = "DisplayVersionNG"
    __help__ = __description__

    def _opt(self, key):
        return self.options.get(key, DEFAULTS[key])

    def on_loaded(self):
        logging.info("[DisplayVersionNG] plugin loaded")

    def on_ui_setup(self, ui):
        pos_x = self._opt("position_x")
        pos_y = self._opt("position_y")
        if pos_x is None or pos_y is None:
            pos_x, pos_y = (185, 110)  # the original's own hardcoded position

        ui.add_element(
            ELEMENT_NAME,
            LabeledValue(
                color=BLACK,
                label="",
                value="v0.0.0",
                position=(pos_x, pos_y),
                label_font=fonts.Small,
                text_font=fonts.Small,
            ),
        )

    def on_ui_update(self, ui):
        ui.set(ELEMENT_NAME, f"v{pwnagotchi.__version__}")

    def on_unload(self, ui):
        with ui._lock:
            try:
                ui.remove_element(ELEMENT_NAME)
                logging.info("[DisplayVersionNG] plugin unloaded")
            except Exception as e:
                logging.error("[DisplayVersionNG] unload: %s", repr(e))

    def on_webhook(self, path, request):
        logging.info("[DisplayVersionNG] webhook pressed")
