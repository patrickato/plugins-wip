import csv
import datetime
import logging
import os

import pwnagotchi.plugins as plugins
from pwnagotchi.ui.components import LabeledValue
from pwnagotchi.ui.view import BLACK
import pwnagotchi.ui.fonts as fonts


# This fork's loader does NOT merge a plugin's __defaults__ into
# self.options - every option must be read with a real fallback.
DEFAULTS = {
    "enabled": False,
    "output_path": "/etc/pwnagotchi/timer_ng.csv",
    "max_rows": 5000,
    "show_on_screen": False,
    "ui_metric": "last",  # "last" or "average"
    "ui_average_window": 10,
    "position_x": None,
    "position_y": None,
}

ELEMENT_NAME = "timer_ng"
FIELDNAMES = [
    "timestamp",
    "network",
    "time_to_deauth",
    "time_to_handshake",
    "time_between_deauth_and_handshake",
]


def _as_ap_dict(ap):
    # on_handshake can hand plugins a bare MAC string instead of a full
    # AP dict (pwnagotchi/agent.py, the "ap_and_station is None" branch)
    # - the original timer.py never accounted for this and would have
    # crashed trying to read a network name off a plain string.
    if isinstance(ap, dict):
        return ap
    return {"mac": str(ap), "hostname": ""}


def _network_name(ap):
    ap = _as_ap_dict(ap)
    hostname = ap.get("hostname") or ""
    if hostname and hostname != "<hidden>":
        return hostname
    return ap.get("mac", "unknown")


class TimerNG(plugins.Plugin):
    __author__ = "fixed/extended by this project's plugin audit, from itsdarklikehell/idoloninmachina's timer.py"
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = (
        "Measures time-to-deauth and time-to-handshake per network, "
        "logs to a rotating CSV, and tracks best/worst times per network."
    )
    __name__ = "TimerNG"
    __help__ = __description__

    def __init__(self):
        self.reset_times()
        self._network_stats = {}
        self._recent_handshake_times = []
        self._last_handshake_seconds = None

    def _opt(self, key):
        return self.options.get(key, DEFAULTS[key])

    def on_loaded(self):
        logging.info("[TimerNG] plugin loaded, writing to %s", self._opt("output_path"))

    def on_ui_setup(self, ui):
        if not self._opt("show_on_screen"):
            return
        pos_x = self._opt("position_x")
        pos_y = self._opt("position_y")
        if pos_x is None:
            pos_x = 0
        if pos_y is None:
            pos_y = ui.height() - 10 if hasattr(ui, "height") else 0
        with ui._lock:
            ui.add_element(
                ELEMENT_NAME,
                LabeledValue(
                    color=BLACK,
                    label="T2H",
                    value="--",
                    position=(pos_x, pos_y),
                    label_font=fonts.Bold,
                    text_font=fonts.Medium,
                ),
            )

    def on_unload(self, ui):
        if self._opt("show_on_screen"):
            with ui._lock:
                try:
                    ui.remove_element(ELEMENT_NAME)
                except KeyError:
                    pass
        logging.info("[TimerNG] plugin unloaded")

    def on_epoch(self, agent, epoch, epoch_data):
        self.reset_times()

    def on_wifi_update(self, agent, access_points):
        self.wifi_update_time = datetime.datetime.now()

    def on_deauthentication(self, agent, access_point, client_station):
        self.wifi_deauth_time = datetime.datetime.now()
        self._deauth_network = _network_name(access_point)

    def on_handshake(self, agent, filename, access_point, client_station):
        self.wifi_handshake_time = datetime.datetime.now()
        self.process_data(agent, access_point)
        self.reset_times()

    def process_data(self, agent, access_point):
        # A handshake with no prior deauth in this epoch was a passive
        # capture - nothing to time.
        if self.wifi_deauth_time is None or self.wifi_update_time is None:
            return

        network = self._deauth_network or _network_name(access_point)
        time_to_deauth = self._seconds(self.wifi_update_time, self.wifi_deauth_time)
        time_to_handshake = self._seconds(self.wifi_update_time, self.wifi_handshake_time)
        time_between = self._seconds(self.wifi_deauth_time, self.wifi_handshake_time)

        self._record_row(network, time_to_deauth, time_to_handshake, time_between)
        self._update_stats(network, time_to_handshake)

        if self._opt("show_on_screen"):
            self._update_ui(agent, time_to_handshake)

        logging.info(
            "[TimerNG] %s: deauth=%.2fs handshake=%.2fs deauth->handshake=%.2fs",
            network, time_to_deauth, time_to_handshake, time_between,
        )

    def _seconds(self, past, future):
        return (future - past).total_seconds()

    def _record_row(self, network, time_to_deauth, time_to_handshake, time_between):
        path = self._opt("output_path")
        row = {
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
            "network": network,
            "time_to_deauth": f"{time_to_deauth:.3f}",
            "time_to_handshake": f"{time_to_handshake:.3f}",
            "time_between_deauth_and_handshake": f"{time_between:.3f}",
        }
        try:
            self._append_and_rotate(path, row)
        except Exception as e:
            logging.warning("[TimerNG] couldn't write %s: %s", path, e)

    def _append_and_rotate(self, path, row):
        max_rows = self._opt("max_rows")

        if os.path.exists(path):
            with open(path, "r", newline="") as f:
                existing = list(csv.DictReader(f))
        else:
            existing = []

        existing.append(row)
        if max_rows and len(existing) > max_rows:
            # ADDED: rotation - the original rewrote the entire, ever-growing
            # CSV from an in-memory list on every single handshake with no
            # cap at all, via a full pandas DataFrame rewrite. This keeps
            # only the most recent max_rows entries (0/None = unlimited,
            # matching the original's unbounded behavior).
            existing = existing[-max_rows:]

        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(existing)

    def _update_stats(self, network, time_to_handshake):
        stats = self._network_stats.setdefault(
            network, {"count": 0, "best": None, "worst": None}
        )
        stats["count"] += 1
        if stats["best"] is None or time_to_handshake < stats["best"]:
            stats["best"] = time_to_handshake
        if stats["worst"] is None or time_to_handshake > stats["worst"]:
            stats["worst"] = time_to_handshake

    def _update_ui(self, agent, time_to_handshake):
        self._last_handshake_seconds = time_to_handshake
        self._recent_handshake_times.append(time_to_handshake)
        window = self._opt("ui_average_window")
        if window and len(self._recent_handshake_times) > window:
            self._recent_handshake_times = self._recent_handshake_times[-window:]

        if self._opt("ui_metric") == "average" and self._recent_handshake_times:
            value = sum(self._recent_handshake_times) / len(self._recent_handshake_times)
        else:
            value = time_to_handshake

        try:
            agent.view().set(ELEMENT_NAME, f"{value:.1f}s")
        except Exception as e:
            logging.debug("[TimerNG] couldn't update UI element: %s", e)

    def reset_times(self):
        self.wifi_update_time = None
        self.wifi_deauth_time = None
        self.wifi_handshake_time = None
        self._deauth_network = None

    def on_webhook(self, path, request):
        # A small read-only summary - the original just logged that the
        # webhook was pressed and returned nothing.
        lines = ["<html><head><title>TimerNG</title></head><body>"]
        lines.append("<h1>TimerNG - per-network capture times</h1>")
        if not self._network_stats:
            lines.append("<p>No timed captures yet.</p>")
        else:
            lines.append("<table border=1 cellpadding=4><tr><th>Network</th><th>Captures</th><th>Best (s)</th><th>Worst (s)</th></tr>")
            for network, stats in sorted(self._network_stats.items()):
                lines.append(
                    "<tr><td>%s</td><td>%d</td><td>%.2f</td><td>%.2f</td></tr>"
                    % (network, stats["count"], stats["best"], stats["worst"])
                )
            lines.append("</table>")
        lines.append("<p>Full log: %s</p>" % self._opt("output_path"))
        lines.append("</body></html>")
        return "".join(lines)
