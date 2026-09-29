import json
import logging
import os
import random
import threading
import time

import pwnagotchi.plugins as plugins
from pwnagotchi.ui.components import LabeledValue
from pwnagotchi.ui.view import BLACK
import pwnagotchi.ui.fonts as fonts

ELEMENT_NAME = "showerthoughts_ng"
CACHE_FILE_DEFAULT = "/etc/pwnagotchi/showerthoughts_ng_cache.json"
USER_AGENT = "pwnagotchi:showerthoughts_ng:v1.0.0 (by /u/pwnagotchi-user)"

# No source for the original "Showerthoughts" plugin could be found
# anywhere - this is a from-scratch build, not a fix. Pulls random
# headlines from r/Showerthoughts' public JSON endpoint and rotates
# through them on-screen while the unit is idle.


class ShowerThoughtsNG(plugins.Plugin):
    __author__ = "built from scratch - no original source was locatable"
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = "Displays random r/Showerthoughts headlines on-screen while idle."

    DEFAULTS = {
        "enabled": False,
        "position_x": None,
        "position_y": None,
        "subreddit": "Showerthoughts",
        "fetch_limit": 100,
        "refresh_interval_seconds": 3600,
        "rotate_interval_seconds": 30,
        "cache_file": None,
        "min_score": 5,
        "max_length": 60,
        "reactive_states": ["bored", "lonely", "sad"],
    }

    def __init__(self):
        self._pool = []
        self._current = ""
        self._last_rotate = 0.0
        self._last_refresh = 0.0
        self._lock = threading.Lock()
        self._fetch_thread = None

    def _opt(self, key):
        return self.options.get(key, self.DEFAULTS[key])

    def _cache_file(self):
        return self._opt("cache_file") or CACHE_FILE_DEFAULT

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_loaded(self):
        logging.info("[ShowerThoughtsNG] plugin loaded.")
        self._load_cache()
        if self._pool:
            self._current = random.choice(self._pool)

    def on_ui_setup(self, ui):
        pos = self._position(ui)
        ui.add_element(
            ELEMENT_NAME,
            LabeledValue(
                label=None,
                value="",
                position=pos,
                color=BLACK,
                text_font=fonts.Small,
                label_font=fonts.Small,
            ),
        )

    def _position(self, ui):
        x = self._opt("position_x")
        y = self._opt("position_y")
        if x is not None and y is not None:
            return (x, y)
        return (0, ui.height() - 10)

    def on_unload(self, ui):
        try:
            ui.remove_element(ELEMENT_NAME)
        except KeyError:
            pass

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------

    def on_internet_available(self, agent):
        now = time.time()
        if now - self._last_refresh < self._opt("refresh_interval_seconds"):
            return
        if self._fetch_thread and self._fetch_thread.is_alive():
            return
        self._last_refresh = now
        self._fetch_thread = threading.Thread(
            target=self._fetch_thoughts, daemon=True, name="ShowerThoughtsNGFetch"
        )
        self._fetch_thread.start()

    def _fetch_thoughts(self):
        try:
            import requests
        except ImportError as e:
            logging.error("[ShowerThoughtsNG] requests not available: %s", e)
            return

        url = "https://www.reddit.com/r/%s/hot.json" % self._opt("subreddit")
        try:
            resp = requests.get(
                url,
                params={"limit": self._opt("fetch_limit")},
                headers={"User-Agent": USER_AGENT},
                timeout=10,
            )
        except Exception as e:
            logging.error("[ShowerThoughtsNG] fetch failed: %s", e)
            return

        if resp.status_code == 429:
            logging.warning("[ShowerThoughtsNG] rate limited by reddit, will retry next cycle")
            return
        if resp.status_code != 200:
            logging.warning("[ShowerThoughtsNG] unexpected status %s from reddit", resp.status_code)
            return

        try:
            data = resp.json()
            posts = data["data"]["children"]
        except (KeyError, TypeError, ValueError) as e:
            logging.error("[ShowerThoughtsNG] couldn't parse reddit response: %s", e)
            return

        thoughts = []
        min_score = self._opt("min_score")
        max_len = self._opt("max_length")
        for post in posts:
            p = post.get("data", {})
            if p.get("stickied"):
                continue
            title = p.get("title", "").strip()
            score = p.get("score", 0)
            if not title or score < min_score:
                continue
            if len(title) > max_len:
                title = title[: max_len - 1].rstrip() + "…"
            thoughts.append(title)

        if not thoughts:
            logging.warning("[ShowerThoughtsNG] fetch returned no usable thoughts")
            return

        with self._lock:
            self._pool = thoughts
        self._save_cache(thoughts)
        logging.info("[ShowerThoughtsNG] refreshed pool: %s thoughts", len(thoughts))

    # ------------------------------------------------------------------
    # Display / rotation
    # ------------------------------------------------------------------

    def on_ui_update(self, ui):
        now = time.time()
        if now - self._last_rotate >= self._opt("rotate_interval_seconds"):
            self._rotate()
        ui.set(ELEMENT_NAME, self._current)

    def _rotate(self):
        with self._lock:
            if self._pool:
                self._current = random.choice(self._pool)
        self._last_rotate = time.time()

    def _reactive_rotate(self, state_name):
        if state_name in self._opt("reactive_states"):
            self._rotate()

    def on_bored(self, agent):
        self._reactive_rotate("bored")

    def on_lonely(self, agent):
        self._reactive_rotate("lonely")

    def on_sad(self, agent):
        self._reactive_rotate("sad")

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def _load_cache(self):
        path = self._cache_file()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._pool = [str(t) for t in data]
                logging.info("[ShowerThoughtsNG] loaded %s cached thoughts", len(self._pool))
        except (IOError, json.JSONDecodeError, ValueError) as e:
            logging.warning("[ShowerThoughtsNG] couldn't load cache: %s", e)

    def _save_cache(self, thoughts):
        path = self._cache_file()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump(thoughts, f)
        except (IOError, TypeError) as e:
            logging.warning("[ShowerThoughtsNG] couldn't save cache: %s", e)

    def on_webhook(self, path, request):
        logging.info("[ShowerThoughtsNG] webhook pressed")
