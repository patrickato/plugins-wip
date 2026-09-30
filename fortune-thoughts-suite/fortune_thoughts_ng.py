"""FortuneThoughtsNG - a merge of fortune_cookie.py-derived and
showerthoughts-derived functionality into a single suite.

Why merged: the post-cluster-review pass found that fortune-cookie-suite
(FortuneCookieNG, rebuilt from the upstream `fortune_cookie.py`) and
showerthoughts-suite (ShowerThoughtsNG, a from-scratch build - no
original source was ever locatable) were both small, closely-related
"rotate a short text message on screen while idle" plugins with almost
identical shapes (a single `LabeledValue` UI element, a rotation timer,
a local content pool, reactive-state-driven immediate rotation) and
were worth combining into one suite rather than maintaining two nearly
parallel implementations side by side. This file preserves every real
behavior from both originals (see NOTES.md for the full line-by-line
accounting) behind one new `content_source` option that picks which
pool of text actually gets shown, and exposes exactly ONE on-screen
element (`ELEMENT_NAME` below) - the two originals' separate screen
elements are being replaced, not run side-by-side.

This fork's plugin loader (`pwnagotchi/plugins/__init__.py`) does
`plugin.options = config['main']['plugins'][name]` - a plain assignment,
never a merge with a plugin's own defaults - so every option here is
read through `_opt()` against the module-level `DEFAULTS` dict, never
`self.options[...]` directly. The config section name MUST match this
file's exact basename: `[main.plugins.fortune_thoughts_ng]`.
"""

import html
import json
import logging
import os
import random
import subprocess
import threading
import time

import pwnagotchi.plugins as plugins
import pwnagotchi.ui.fonts as fonts
from pwnagotchi.ui.components import LabeledValue
from pwnagotchi.ui.view import BLACK

ELEMENT_NAME = "fortune_thoughts_ng"
CACHE_FILE_DEFAULT = "/etc/pwnagotchi/fortune_thoughts_ng_cache.json"
USER_AGENT = "pwnagotchi:fortune_thoughts_ng:v1.0.0 (by /u/pwnagotchi-user)"

# Local content pool, carried forward verbatim from fortune-cookie-suite's
# DEFAULT_FORTUNES (fortune_cookie_ng.py) - used whenever content_source
# resolves to the local list, whatever the reason (explicit "local"/
# "command" mode, or a fallback from "reddit"/"auto").
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


class FortuneThoughtsNG(plugins.Plugin):
    __author__ = (
        "rebuilt/merged from @vanshksingh's fortune_cookie.py "
        "(FortuneCookiePlugin) and this repo's from-scratch "
        "showerthoughts_ng.py (ShowerThoughtsNG, no original source located)"
    )
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = (
        "Displays a rotating fortune / local command output / "
        "r/Showerthoughts message on screen."
    )

    DEFAULTS = {
        "enabled": True,
        "orientation": "horizontal",
        # Which pool of text to actually display - see the module
        # docstring / NOTES.md for the full fallback chain per value.
        "content_source": "local",
        # --- fortune-cookie-derived options ---
        "fortunes": DEFAULT_FORTUNES,
        "fortune_command": "",
        # --- showerthoughts-derived options ---
        "position_x": None,
        "position_y": None,
        "subreddit": "Showerthoughts",
        "fetch_limit": 100,
        "refresh_interval_seconds": 3600,
        "cache_file": None,
        "min_score": 5,
        "max_length": 60,
        "reactive_states": ["bored", "lonely", "sad"],
        # Unified rotation interval - replaces both originals' separate
        # defaults (fortune-cookie: 60s, showerthoughts: 30s). See
        # NOTES.md for why 45s was picked.
        "rotate_interval_seconds": 45,
    }

    def __init__(self):
        self.current_message = None
        self._last_rotate = 0.0
        self._pool = []  # reddit-fetched pool
        self._last_refresh = 0.0  # throttle timestamp for on_internet_available
        self._last_fetch_success = None  # wall-clock time of last successful fetch
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
        logging.info("[FortuneThoughtsNG] plugin loaded")
        self._load_cache()

    def on_ui_setup(self, ui):
        position = self._position(ui)

        if self._opt("orientation") == "vertical":
            # Vertical: label above value, on its own line.
            ui.add_element(
                ELEMENT_NAME,
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
            # the message itself carries the content.
            ui.add_element(
                ELEMENT_NAME,
                LabeledValue(
                    color=BLACK,
                    label="",
                    value="",
                    position=position,
                    label_font=fonts.Bold,
                    text_font=fonts.Small,
                ),
            )

    def _position(self, ui):
        """Explicit position_x/position_y override wins outright when both
        are set (showerthoughts-derived). Otherwise, fall back to the
        per-hardware auto-detected table (fortune-cookie-derived) - kept
        as the universal fallback because it's strictly more precise than
        showerthoughts' old plain bottom-line default, which is dropped."""
        x = self._opt("position_x")
        y = self._opt("position_y")
        if x is not None and y is not None:
            return (x, y)

        if ui.is_waveshare_v2() or ui.is_waveshare_v3() or ui.is_waveshare_v1():
            return (0, 95)
        elif ui.is_waveshare144lcd():
            return (0, 92)
        elif ui.is_inky():
            return (0, 83)
        elif ui.is_waveshare27inch():
            return (0, 153)
        else:
            return (0, 91)

    def on_unload(self, ui):
        try:
            ui.remove_element(ELEMENT_NAME)
        except KeyError:
            pass

    # ------------------------------------------------------------------
    # Reddit fetching (showerthoughts-derived)
    # ------------------------------------------------------------------

    def on_internet_available(self, agent):
        # Only start the background-fetch machinery at all when the
        # reddit pool can actually be used - no point spending a
        # thread/network call to fill a pool that "local"/"command" mode
        # will never read from.
        if self._opt("content_source") not in ("reddit", "auto"):
            return

        now = time.time()
        if now - self._last_refresh < self._opt("refresh_interval_seconds"):
            return
        if self._fetch_thread and self._fetch_thread.is_alive():
            return
        self._last_refresh = now
        self._fetch_thread = threading.Thread(
            target=self._fetch_thoughts, daemon=True, name="FortuneThoughtsNGFetch"
        )
        self._fetch_thread.start()

    def _fetch_thoughts(self):
        try:
            import requests
        except ImportError as e:
            logging.error("[FortuneThoughtsNG] requests not available: %s", e)
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
            logging.error("[FortuneThoughtsNG] fetch failed: %s", e)
            return

        if resp.status_code == 429:
            logging.warning("[FortuneThoughtsNG] rate limited by reddit, will retry next cycle")
            return
        if resp.status_code != 200:
            logging.warning("[FortuneThoughtsNG] unexpected status %s from reddit", resp.status_code)
            return

        try:
            data = resp.json()
            posts = data["data"]["children"]
        except (KeyError, TypeError, ValueError) as e:
            logging.error("[FortuneThoughtsNG] couldn't parse reddit response: %s", e)
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
            logging.warning("[FortuneThoughtsNG] fetch returned no usable thoughts")
            return

        with self._lock:
            self._pool = thoughts
        self._last_fetch_success = time.time()
        self._save_cache(thoughts)
        logging.info("[FortuneThoughtsNG] refreshed pool: %s thoughts", len(thoughts))

    # ------------------------------------------------------------------
    # Display / rotation
    # ------------------------------------------------------------------

    def on_ui_update(self, ui):
        if not self._opt("enabled"):
            ui.set(ELEMENT_NAME, "FortuneThoughtsNG is disabled")
            return

        now = time.time()
        interval = float(self._opt("rotate_interval_seconds"))
        if self.current_message is None or (now - self._last_rotate) >= interval:
            self._rotate()

        ui.set(ELEMENT_NAME, self.current_message)

    def _rotate(self):
        self.current_message = self._get_message()
        self._last_rotate = time.time()

    def _reactive_rotate(self, state_name):
        # Applied uniformly regardless of content_source - there's no
        # reason to restrict "get a new one now" to just the reddit-
        # sourced content the way showerthoughts originally did; a fresh
        # local fortune/command result is just as valid a reaction to a
        # bored/lonely/sad state.
        if state_name in self._opt("reactive_states"):
            self._rotate()

    def on_bored(self, agent):
        self._reactive_rotate("bored")

    def on_lonely(self, agent):
        self._reactive_rotate("lonely")

    def on_sad(self, agent):
        self._reactive_rotate("sad")

    def _get_message(self):
        source = self._opt("content_source")
        if source == "command":
            return self._local_or_command()
        elif source == "reddit":
            pool = self._reddit_pool()
            if pool:
                return random.choice(pool)
            return self._pick_from_list()
        elif source == "auto":
            pool = self._reddit_pool()
            if pool:
                return random.choice(pool)
            return self._local_or_command()
        else:
            # "local" (default) and any unrecognized value fall back
            # here too, so a typo'd content_source never breaks display.
            return self._local_or_command()

    def _reddit_pool(self):
        with self._lock:
            return list(self._pool)

    def _local_or_command(self):
        """fortune_command takes priority when configured, same priority
        order fortune-cookie-suite already used internally; falls back to
        the local fortunes list on any failure or if unset."""
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
            logging.warning(f"[FortuneThoughtsNG] fortune_command failed: {e}")
            return None

        if result.returncode != 0:
            logging.warning(
                f"[FortuneThoughtsNG] fortune_command exited {result.returncode}: "
                f"{result.stderr.strip()}"
            )
            return None

        output = result.stdout.strip()
        return output if output else None

    # ------------------------------------------------------------------
    # Cache (showerthoughts-derived)
    # ------------------------------------------------------------------

    def _load_cache(self):
        path = self._cache_file()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r") as f:
                data = json.load(f)
            if isinstance(data, list):
                with self._lock:
                    self._pool = [str(t) for t in data]
                logging.info("[FortuneThoughtsNG] loaded %s cached thoughts", len(self._pool))
        except (IOError, json.JSONDecodeError, ValueError) as e:
            logging.warning("[FortuneThoughtsNG] couldn't load cache: %s", e)

    def _save_cache(self, thoughts):
        path = self._cache_file()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump(thoughts, f)
        except (IOError, TypeError) as e:
            logging.warning("[FortuneThoughtsNG] couldn't save cache: %s", e)

    # ------------------------------------------------------------------
    # Webhook - a small status page through pwnagotchi's own built-in web
    # UI webhook mechanism, not a separate server (see NOTES.md).
    # ------------------------------------------------------------------

    def on_webhook(self, path, request):
        source = self._opt("content_source")

        if source in ("reddit", "auto"):
            pool_size = len(self._reddit_pool())
            if self._last_fetch_success:
                last_refresh = time.strftime(
                    "%Y-%m-%d %H:%M:%S UTC", time.gmtime(self._last_fetch_success)
                )
            else:
                last_refresh = "never"
        else:
            pool_size = len(self._opt("fortunes") or DEFAULT_FORTUNES)
            last_refresh = "n/a (content_source is not reddit/auto)"

        current = html.escape(self.current_message or "")

        return (
            "<html><body><h1>FortuneThoughtsNG</h1>"
            f"<p>content_source: {html.escape(str(source))}</p>"
            f"<p>pool size: {pool_size}</p>"
            f"<p>currently displayed: {current}</p>"
            f"<p>last reddit refresh: {html.escape(last_refresh)}</p>"
            "</body></html>"
        )
