import logging

import pwnagotchi.plugins as plugins

# This fork's loader does NOT merge a plugin's __defaults__ into
# self.options - every option must be read with a real fallback.
DEFAULTS = {
    "enabled": False,
    "refresh_interval": 50,
    "show_status": True,
}


class ScreenRefreshNG(plugins.Plugin):
    __author__ = "fixed/extended by this project's plugin audit, from itsdarklikehell/rossmarks's screen_refresh.py"
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = (
        "Periodically re-initializes the e-ink display driver to clear "
        "ghosting, after a configurable number of UI updates."
    )
    __name__ = "ScreenRefreshNG"
    __help__ = __description__

    def __init__(self):
        self.update_count = 0

    def _opt(self, key):
        return self.options.get(key, DEFAULTS[key])

    def on_loaded(self):
        logging.info("[ScreenRefreshNG] plugin loaded")

    def on_ui_update(self, ui):
        interval = self._opt("refresh_interval")
        if not interval or interval <= 0:
            return

        self.update_count += 1
        if self.update_count < interval:
            return

        self.update_count = 0

        # FIXED: the original called ui.init_display(), a method that
        # only exists on this fork's Display class (pwnagotchi/ui/display.py)
        # - the object actually passed to on_ui_update is always the
        # plain View (confirmed: View.update() calls
        # plugins.on('ui_update', self) with itself), which has no such
        # method. Every single firing of this plugin raised
        # AttributeError and did nothing.
        #
        # There is no public View API for forcing a hardware refresh -
        # Display.init_display() itself just calls
        # self._implementation.initialize() directly, and
        # self._implementation is set on View too (Display inherits
        # from View and reuses the same attribute), so that's the one
        # real path available from a plain View. This reaches into a
        # private attribute because there's no supported alternative on
        # this fork - see NOTES.md/README's "still open" section.
        try:
            impl = getattr(ui, "_implementation", None)
            if impl is not None:
                impl.initialize()
                logging.info("[ScreenRefreshNG] display refreshed")
                if self._opt("show_status"):
                    ui.set("status", "Screen cleaned")
            else:
                logging.debug(
                    "[ScreenRefreshNG] no display implementation available to refresh"
                )
        except Exception as e:
            logging.warning("[ScreenRefreshNG] refresh failed: %s", repr(e))

    def on_webhook(self, path, request):
        logging.info("[ScreenRefreshNG] webhook pressed")
