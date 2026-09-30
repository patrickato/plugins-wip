import datetime
import json
import logging
import os

import pwnagotchi.plugins as plugins
import pwnagotchi.ui.fonts as fonts
from pwnagotchi.ui.components import LabeledValue
from pwnagotchi.ui.view import BLACK

# Title ladder, keyed by the handshake count needed to reach it. Simplified
# from the original's ~20-tier list (which was built for a scoring system
# that also counted several dead-code "adventure" types) down to a handful
# that still keep the flavor while being reachable off handshakes alone.
TITLES = {
    0: "WiFi Whisperer",
    5: "Signal Maestro",
    15: "Byte Buccaneer",
    30: "Network Nomad",
    50: "Cyber Corsair",
    100: "Protocol Pioneer",
    200: "Digital Druid",
    400: "Epic Explorer",
    800: "System Sorcerer",
    1500: "Legendary Adventurer",
}


class WifiAdventuresNG(plugins.Plugin):
    __author__ = "rebuilt from itsdarklikehell/MaliosDark's wifi_adventures.py (FunAchievements)"
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = (
        "On-screen handshake/streak achievement tracker with a title ladder, "
        "built only on real pwnagotchi hooks."
    )

    DEFAULTS = {
        "enabled": False,
        # On-screen position of the Adventures element. Defaults preserve the
        # original hardcoded spot (0, 95). Negative x means "pixels in from the
        # right edge," matching the repo-wide convention.
        "position_x": 0,
        "position_y": 95,
        # Handshakes needed to reach the next tier of the daily streak
        # target - kept purely cosmetic here (see NOTES.md); the original
        # used it to gate a "completed adventure" bonus, which this rebuild
        # doesn't need since there's no fake adventure-type system left.
        "daily_quest_target": 5,
        # Optional override for where state is persisted. Defaults to a
        # JSON file next to this plugin, same pattern as the original.
        "data_path": None,
    }

    def __init__(self):
        # No file or network I/O here: __init_subclass__ instantiates the
        # plugin immediately with no try/except, so any exception raised in
        # __init__ would crash plugin loading entirely for everyone. State
        # is loaded lazily in on_ready instead.
        self.ready = False
        self.handshake_count = 0
        self.new_networks_count = 0
        self.streak_days = 0
        self.last_handshake_date = None
        self.title = TITLES[0]
        self.seen_bssids = []
        self._data_path = None

    def _opt(self, key):
        return self.options.get(key, self.DEFAULTS[key])

    def _resolve_position(self, ui):
        # Convention: negative x = pixels in from the right edge.
        try:
            x = int(self._opt("position_x"))
        except (TypeError, ValueError):
            x = self.DEFAULTS["position_x"]
        try:
            y = int(self._opt("position_y"))
        except (TypeError, ValueError):
            y = self.DEFAULTS["position_y"]
        if x < 0:
            try:
                x = int(ui.width()) + x
            except Exception:
                x = 0
        return (x, y)

    def _resolve_data_path(self):
        configured = self._opt("data_path")
        if configured:
            return configured
        return os.path.join(
            os.path.dirname(os.path.realpath(__file__)), "wifi_adventures_ng.json"
        )

    def on_loaded(self):
        logging.info("[WifiAdventuresNG] plugin loaded")

    def on_ready(self, agent):
        _ = agent
        self._data_path = self._resolve_data_path()
        self._load()
        self._update_title()
        self.ready = True

    def _load(self):
        if self._data_path and os.path.exists(self._data_path):
            try:
                with open(self._data_path, "r") as f:
                    data = json.load(f)
            except (OSError, ValueError) as e:
                logging.warning("[WifiAdventuresNG] failed to load state: %s", e)
                return
            self.handshake_count = data.get("handshake_count", 0)
            self.new_networks_count = data.get("new_networks_count", 0)
            self.streak_days = data.get("streak_days", 0)
            last = data.get("last_handshake_date")
            self.last_handshake_date = (
                datetime.datetime.strptime(last, "%Y-%m-%d").date() if last else None
            )
            self.seen_bssids = data.get("seen_bssids", [])

    def _save(self):
        if not self._data_path:
            return
        data = {
            "handshake_count": self.handshake_count,
            "new_networks_count": self.new_networks_count,
            "streak_days": self.streak_days,
            "last_handshake_date": (
                self.last_handshake_date.strftime("%Y-%m-%d")
                if self.last_handshake_date
                else None
            ),
            "seen_bssids": self.seen_bssids,
        }
        try:
            with open(self._data_path, "w") as f:
                json.dump(data, f)
        except OSError as e:
            logging.warning("[WifiAdventuresNG] failed to save state: %s", e)

    def _update_title(self):
        current = TITLES[0]
        for threshold in sorted(TITLES.keys(), reverse=True):
            if self.handshake_count >= threshold:
                current = TITLES[threshold]
                break
        if current != self.title:
            self.title = current
            logging.info("[WifiAdventuresNG] title updated: %s", self.title)

    def _bump_streak(self):
        today = datetime.date.today()
        if self.last_handshake_date == today:
            return
        if self.last_handshake_date == today - datetime.timedelta(days=1):
            self.streak_days += 1
        else:
            self.streak_days = 1
        self.last_handshake_date = today

    def on_ui_setup(self, ui):
        ui.add_element(
            "wifiAdventures",
            LabeledValue(
                color=BLACK,
                label="Adventures:  ",
                value=f"{self.handshake_count} ({self.title})",
                position=self._resolve_position(ui),
                label_font=fonts.Medium,
                text_font=fonts.Medium,
            ),
        )

    def on_ui_update(self, ui):
        if self.ready:
            ui.set("wifiAdventures", f"{self.handshake_count} ({self.title})")

    def on_handshake(self, agent, filename, access_point, client_station):
        _ = agent, filename, access_point, client_station
        self.handshake_count += 1
        self._bump_streak()
        self._update_title()
        self._save()
        logging.info(
            "[WifiAdventuresNG] handshake #%d, streak %d day(s), title: %s",
            self.handshake_count,
            self.streak_days,
            self.title,
        )

    def on_unfiltered_ap_list(self, agent, access_points):
        _ = agent
        new_seen = set()
        for ap in access_points or []:
            bssid = ap.get("mac") if isinstance(ap, dict) else None
            if not bssid:
                continue
            if bssid not in self.seen_bssids:
                new_seen.add(bssid)

        if not new_seen:
            return

        self.new_networks_count += len(new_seen)
        self.seen_bssids.extend(sorted(new_seen))
        self._save()
        logging.info(
            "[WifiAdventuresNG] %d new network(s) seen, %d total",
            len(new_seen),
            self.new_networks_count,
        )

    def on_unload(self, ui):
        with ui._lock:
            try:
                ui.remove_element("wifiAdventures")
                logging.info("[WifiAdventuresNG] plugin unloaded")
            except Exception as e:
                logging.error("[WifiAdventuresNG] unload: %s", e)

    def on_webhook(self, path, request):
        _ = path, request
        streak_line = (
            f"{self.streak_days} day(s)" if self.streak_days else "no active streak"
        )
        return f"""
<html>
<head><title>WiFi Adventures NG</title></head>
<body style="font-family: sans-serif; padding: 1em;">
<h1>WiFi Adventures NG</h1>
<ul>
<li><b>Title:</b> {self.title}</li>
<li><b>Handshakes:</b> {self.handshake_count}</li>
<li><b>New networks seen:</b> {self.new_networks_count}</li>
<li><b>Streak:</b> {streak_line}</li>
</ul>
</body>
</html>
"""
