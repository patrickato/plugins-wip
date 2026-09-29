import datetime
import logging

from pwnagotchi.ui.components import LabeledValue
from pwnagotchi.ui.view import BLACK
import pwnagotchi.ui.fonts as fonts
import pwnagotchi.plugins as plugins


# This fork's loader does NOT merge a plugin's __defaults__ into
# self.options - every option must be read with a real fallback.
DEFAULTS = {
    "enabled": False,
    # None on all four means: use the original plugin's own hardcoded
    # positions, so nothing changes for the common case.
    "date_position_x": None,
    "date_position_y": None,
    "time_position_x": None,
    "time_position_y": None,
    # strftime format strings. The original hardcoded "%m/%d/%y" and
    # "%I:%M%p" (12-hour, no leading zero, no seconds) with no way to
    # change either - e.g. no way to get a 24-hour clock without
    # editing the plugin's source. Any valid strftime string works
    # here; an invalid one falls back to the original default.
    "date_format": "%m/%d/%y",
    "time_format": "%I:%M%p",
}

DATE_ELEMENT = "clock_ng_date"
TIME_ELEMENT = "clock_ng_time"


class ClockNG(plugins.Plugin):
    __author__ = "fixed/extended by this project's plugin audit, from LoganMD/NeonLightning's clock.py"
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = "Clock/Calendar for pwnagotchi."
    __name__ = "ClockNG"
    __help__ = __description__

    def _opt(self, key):
        return self.options.get(key, DEFAULTS[key])

    def on_loaded(self):
        logging.info("[ClockNG] plugin loaded")

    def _date_position(self):
        x = self._opt("date_position_x")
        y = self._opt("date_position_y")
        if x is None or y is None:
            return (100, 0)  # the original's own hardcoded position
        return (x, y)

    def _time_position(self):
        x = self._opt("time_position_x")
        y = self._opt("time_position_y")
        if x is None or y is None:
            return (100, 95)  # the original's own hardcoded position
        return (x, y)

    def on_ui_setup(self, ui):
        ui.add_element(
            DATE_ELEMENT,
            LabeledValue(
                color=BLACK,
                label="",
                value="-/-/-",
                position=self._date_position(),
                label_font=fonts.Small,
                text_font=fonts.Small,
            ),
        )
        ui.add_element(
            TIME_ELEMENT,
            LabeledValue(
                color=BLACK,
                label="",
                value="-:--",
                position=self._time_position(),
                label_font=fonts.Small,
                text_font=fonts.Small,
            ),
        )

    def _format(self, now, fmt, default):
        # ADDED: the original called now.strftime(...) directly with a
        # hardcoded literal, so a bad format string was never possible.
        # Now that it's user-configurable, an invalid strftime format
        # (e.g. a stray "%" at the end of the string) must not be able
        # to crash the render loop - fall back to the original default
        # instead, and log once so it's visible why.
        try:
            return now.strftime(fmt)
        except (ValueError, TypeError) as e:
            logging.warning(
                "[ClockNG] invalid format %r, falling back to the default: %s",
                fmt, e,
            )
            return now.strftime(default)

    def on_ui_update(self, ui):
        now = datetime.datetime.now()
        ui.set(DATE_ELEMENT, self._format(now, self._opt("date_format"), DEFAULTS["date_format"]))
        ui.set(TIME_ELEMENT, self._format(now, self._opt("time_format"), DEFAULTS["time_format"]))

    def on_unload(self, ui):
        # ADDED: the original's remove_element calls had no error
        # handling at all - if either element somehow wasn't created
        # (e.g. on_ui_setup never ran), unload would raise instead of
        # cleanly no-op'ing, the same defensive pattern used elsewhere
        # in this audit (CrackHouseNG, DisplayAircrackNG, etc.).
        with ui._lock:
            for name in (DATE_ELEMENT, TIME_ELEMENT):
                try:
                    ui.remove_element(name)
                except KeyError:
                    pass
        logging.info("[ClockNG] plugin unloaded")
