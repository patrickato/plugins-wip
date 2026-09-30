import json
import logging
import os
import time
from dateutil.relativedelta import relativedelta
import datetime

import pwnagotchi.plugins as plugins
import pwnagotchi.ui.fonts as fonts
from pwnagotchi.ui.components import LabeledValue
from pwnagotchi.ui.view import BLACK

# Real brain.json path used by the framework. The plugin only ever reads
# this file - it never writes to it (see the fallback-state design note
# below and in NOTES.md).
BRAIN_PATH = "/root/brain.json"

# The plugin's own fallback state file. If the real brain.json genuinely
# has no "born_at" key, we self-heal by writing a fresh timestamp here
# instead of touching /root/brain.json - that file is owned by the core
# framework/other plugins, and clobbering it (or racing a later writer
# that does add a real born_at) would be worse than just maintaining our
# own small side file.
FALLBACK_PATH = "/root/birthday_ng_fallback.json"


class BirthdayNG(plugins.Plugin):
    __author__ = "rebuilt from itsdarklikehell/nullm0ose's birthday.py (Birthday)"
    __version__ = "1.0.0"
    __license__ = "MIT"
    __description__ = "Shows your Pwnagotchi's age or birthday on-screen."
    __dependencies__ = {
        "apt": ["none"],
        "pip": ["python-dateutil"],
    }

    DEFAULTS = {
        "enabled": False,
        "show_age": True,
        "show_birthday": False,
        # Preferred, convention-named position options. When either is left
        # unset (None) the legacy age_x_coord / age_y_coord below are used, so
        # existing configs keep working unchanged.
        "position_x": None,
        "position_y": None,
        "age_x_coord": 0,
        "age_y_coord": 0,
        # Shown on the actual anniversary of born_at (same month+day as
        # today). {age} is replaced with the current age string.
        "birthday_message": "Happy Birthday to me! I am {age} old today!",
    }

    def __init__(self):
        self.born_at = None
        logging.debug(f"[{self.__class__.__name__}] plugin init")

    def _opt(self, key):
        return self.options.get(key, self.DEFAULTS[key])

    def _resolve_position(self):
        # Preferred convention: position_x / position_y. Fall back to the legacy
        # age_x_coord / age_y_coord names for back-compat, then to (0, 0). The
        # Age and Birthday elements are mutually exclusive, so one pair is enough.
        px = self._opt("position_x")
        py = self._opt("position_y")
        x = int(px) if px is not None else int(self._opt("age_x_coord"))
        y = int(py) if py is not None else int(self._opt("age_y_coord"))
        return (x, y)

    def on_loaded(self):
        self.load_data(BRAIN_PATH)
        logging.info(f"[{self.__class__.__name__}] plugin loaded")

    def on_ui_setup(self, ui):
        if self._opt("show_age"):
            ui.add_element(
                "Age",
                LabeledValue(
                    color=BLACK,
                    label=" ♥ Age ",
                    value="",
                    position=self._resolve_position(),
                    label_font=fonts.Bold,
                    text_font=fonts.Medium,
                ),
            )
        elif self._opt("show_birthday"):
            ui.add_element(
                "Birthday",
                LabeledValue(
                    color=BLACK,
                    label=" ♥ Born: ",
                    value="",
                    position=self._resolve_position(),
                    label_font=fonts.Bold,
                    text_font=fonts.Medium,
                ),
            )

    def on_unload(self, ui):
        with ui._lock:
            try:
                ui.remove_element("Age")
            except Exception as e:
                logging.error(f"[{self.__class__.__name__}] unload: %s" % e)
        with ui._lock:
            try:
                ui.remove_element("Birthday")
            except Exception as e:
                logging.error(f"[{self.__class__.__name__}] unload: %s" % e)
        logging.info(f"[{self.__class__.__name__}] plugin unloaded")

    def on_ui_update(self, ui):
        if self._opt("show_age"):
            if self.born_at is None:
                ui.set("Age", "unknown")
                return
            age = self.calculate_age()
            age_string = self.format_age(age)
            if self.is_birthday_today():
                ui.set("Age", self._opt("birthday_message").format(age=age_string))
            else:
                ui.set("Age", age_string)
        elif self._opt("show_birthday"):
            if self.born_at is None:
                ui.set("Birthday", "unknown")
                return
            born_date = datetime.datetime.fromtimestamp(self.born_at)
            birthday_string = born_date.strftime("%b %d '%y")
            ui.set("Birthday", birthday_string)

    @staticmethod
    def format_age(age):
        years, months, days = age
        age_labels = []
        if years == 1:
            age_labels.append(f"{years}Yr")
        elif years > 1:
            age_labels.append(f"{years}Yrs")
        if months > 0:
            age_labels.append(f"{months}m")
        if days > 0:
            if years < 1:
                age_labels.append(f"{days} days")
            else:
                age_labels.append(f"{days}d")
        return " ".join(age_labels)

    def is_birthday_today(self):
        if self.born_at is None:
            return False
        born_date = datetime.datetime.fromtimestamp(self.born_at)
        today = datetime.datetime.now()
        return born_date.month == today.month and born_date.day == today.day

    def load_data(self, data_path):
        """Load born_at from the real brain.json. If the file exists but has
        no "born_at" key at all (rather than crashing or silently leaving a
        bogus 0/epoch age), fall back to our own self-healing state file -
        creating it with "now" the first time this happens so the age
        display becomes meaningful again instead of staying "unknown"
        forever."""
        born_at = self._read_born_at_from_brain(data_path)
        if born_at is not None:
            self.born_at = born_at
            return

        self.born_at = self._load_or_create_fallback()

    @staticmethod
    def _read_born_at_from_brain(data_path):
        if not os.path.exists(data_path):
            return None
        try:
            with open(data_path) as f:
                data = json.load(f)
            return data.get("born_at")
        except (OSError, ValueError, json.JSONDecodeError) as e:
            logging.warning(f"[BirthdayNG] could not read {data_path}: {e}")
            return None

    def _load_or_create_fallback(self):
        if os.path.exists(FALLBACK_PATH):
            try:
                with open(FALLBACK_PATH) as f:
                    data = json.load(f)
                born_at = data.get("born_at")
                if born_at is not None:
                    return born_at
            except (OSError, ValueError, json.JSONDecodeError) as e:
                logging.warning(f"[BirthdayNG] could not read {FALLBACK_PATH}: {e}")

        born_at = time.time()
        try:
            with open(FALLBACK_PATH, "w") as f:
                json.dump({"born_at": born_at}, f)
            logging.info(
                f"[BirthdayNG] brain.json has no born_at - created fallback state "
                f"in {FALLBACK_PATH}"
            )
        except OSError as e:
            logging.warning(f"[BirthdayNG] could not write {FALLBACK_PATH}: {e}")
        return born_at

    def calculate_age(self):
        born_date = datetime.datetime.fromtimestamp(self.born_at)
        today = datetime.datetime.now()
        age = relativedelta(today, born_date)
        return age.years, age.months, age.days

    def on_webhook(self, path, request):
        logging.info(f"[{self.__class__.__name__}] webhook pressed")
        # Return a simple status page. Returning None makes Flask raise a 500 on
        # the bare index path (GET /plugins/birthday_ng/), so always return a body.
        if self.born_at is None:
            body = "born_at unknown"
        elif self._opt("show_age"):
            body = "Age: " + self.format_age(self.calculate_age())
        elif self._opt("show_birthday"):
            born_date = datetime.datetime.fromtimestamp(self.born_at)
            body = "Born: " + born_date.strftime("%b %d '%y")
        else:
            body = "BirthdayNG"
        return "<html><body><h2>BirthdayNG</h2><p>%s</p></body></html>" % body
