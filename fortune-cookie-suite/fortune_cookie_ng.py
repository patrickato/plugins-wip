import logging
import random
import subprocess
import time

import pwnagotchi.plugins as plugins
import pwnagotchi.ui.fonts as fonts
from pwnagotchi.ui.components import LabeledValue
from pwnagotchi.ui.view import BLACK

DEFAULT_FORTUNES = [
    "You will have a successful hacking session!",
    "Good fortune will come your way.",
    "Be prepared for a surprise in your network captures!",
    "Your Pwnagotchi is feeling lucky today.",
    "A new handshake is closer than it appears.",
    "Patience captures more packets than haste.",
    "The strongest signal is the one you didn't expect.",
    "Today's deauth is tomorrow's handshake.",
    "A watched access point never associates.",
    "Fortune favors the well-antenna'd.",
    "Your next epoch brings good luck.",
    "Trust in your wordlist.",
    "The best channel is the one nobody else is hopping to.",
    "Great captures come to those who wait.",
    "A quiet network hides a curious secret.",
    "Your persistence will be rewarded with a PMKID.",
    "Somewhere, a weak password awaits discovery.",
    "The road to root is paved with patience.",
    "An unexpected peer brings unexpected luck.",
    "Today is a good day to sniff the air.",
]


class FortuneCookieNG(plugins.Plugin):
    __author__ = "rebuilt from @vanshksingh's fortune_cookie.py (FortuneCookiePlugin)"
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = "Displays a rotating fortune cookie message on screen."

    DEFAULTS = {
        "enabled": True,
        "orientation": "horizontal",
        "fortunes": DEFAULT_FORTUNES,
        "rotate_interval_seconds": 60,
        # If set, shell out to the real Unix `fortune` command for a fresh
        # fortune instead of picking from the configured list. Falls back
        # to the configured list if the command is missing/fails/times out.
        "fortune_command": "",
    }

    def __init__(self):
        self.current_fortune = None
        self._last_rotate = 0

    def _opt(self, key):
        return self.options.get(key, self.DEFAULTS[key])

    def on_loaded(self):
        logging.info("[FortuneCookieNG] plugin loaded")

    def on_ui_setup(self, ui):
        if ui.is_waveshare_v2():
            position = (0, 95)
        elif ui.is_waveshare_v3():
            position = (0, 95)
        elif ui.is_waveshare_v1():
            position = (0, 95)
        elif ui.is_waveshare144lcd():
            position = (0, 92)
        elif ui.is_inky():
            position = (0, 83)
        elif ui.is_waveshare27inch():
            position = (0, 153)
        else:
            position = (0, 91)

        if self._opt("orientation") == "vertical":
            # Vertical: label above value, on its own line.
            ui.add_element(
                "fortune-cookie",
                LabeledValue(
                    color=BLACK,
                    label="Fortune:",
                    value="",
                    position=position,
                    label_font=fonts.Bold,
                    text_font=fonts.Small,
                ),
            )
        else:
            # Horizontal (default): no separate label, one combined line -
            # the fortune text itself carries the content.
            ui.add_element(
                "fortune-cookie",
                LabeledValue(
                    color=BLACK,
                    label="",
                    value="",
                    position=position,
                    label_font=fonts.Bold,
                    text_font=fonts.Small,
                ),
            )

    def on_unload(self, ui):
        with ui._lock:
            ui.remove_element("fortune-cookie")

    def on_ui_update(self, ui):
        if not self._opt("enabled"):
            ui.set("fortune-cookie", "Fortune Cookie Plugin is disabled")
            return

        now = time.time()
        interval = float(self._opt("rotate_interval_seconds"))
        if self.current_fortune is None or (now - self._last_rotate) >= interval:
            self.current_fortune = self._get_fortune()
            self._last_rotate = now

        ui.set("fortune-cookie", self.current_fortune)

    def _get_fortune(self):
        command = self._opt("fortune_command")
        if command:
            fortune = self._run_fortune_command(command)
            if fortune:
                return fortune
        return self._pick_from_list()

    def _pick_from_list(self):
        fortunes = self._opt("fortunes") or DEFAULT_FORTUNES
        return random.choice(fortunes)

    @staticmethod
    def _run_fortune_command(command):
        """Shell out to the configured fortune command. Never lets a
        missing binary, a timeout, or a non-zero exit propagate - always
        falls back to the configured list on any failure."""
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            logging.warning(f"[FortuneCookieNG] fortune_command failed: {e}")
            return None

        if result.returncode != 0:
            logging.warning(
                f"[FortuneCookieNG] fortune_command exited {result.returncode}: "
                f"{result.stderr.strip()}"
            )
            return None

        output = result.stdout.strip()
        return output if output else None
