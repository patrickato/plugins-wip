import json
import logging
import os
import random
import threading
import time
from pathlib import Path

import pwnagotchi.plugins as plugins
import pwnagotchi.grid as grid

# Rebuilt from Spam_Peers (spam_peers.py). Sends a canned pwnmail greeting
# to any newly-seen pwnagotchi peer, once per (configurable) cooldown
# window instead of once-per-session-forever, with the "already greeted"
# list now persisted to disk instead of living only in memory.

STATE_FILE_DEFAULT = "/etc/pwnagotchi/spam_peers_ng_state.json"


class SpamPeersNG(plugins.Plugin):
    __author__ = "rebuilt from @Sniffleupagus's spam_peers.py (Spam_Peers)"
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = "Sends new pwnagotchi peers a canned pwnmail greeting, with persistence, cooldown, and jitter."

    DEFAULTS = {
        "enabled": False,
        "messages": ["THIS TOWN AINT BIG ENOUGH FOR THE 2 OF US"],
        # Peers listed here are never greeted (e.g. your own other units).
        "known_peers": [],
        "state_file": None,
        # Hours before a previously-greeted peer becomes eligible again.
        # 0 = never re-greet (matches the original's "once ever" behavior).
        "regreet_after_hours": 168,
        "min_delay_seconds": 1,
        "max_delay_seconds": 8,
    }

    def __init__(self):
        # NOTE: deliberately does no file I/O here. Plugin.__init_subclass__
        # calls this immediately, with no try/except anywhere in the loader,
        # so any exception raised here (e.g. a FileNotFoundError from a
        # missing /root/peers on a fresh install) would crash plugin
        # *loading* entirely, not just one hook. All of that is deferred to
        # on_loaded, which the framework does run with exception handling.
        self.messages = list(self.DEFAULTS["messages"])
        # identity -> last-greeted unix timestamp (or True for "greeted,
        # no timestamp known" from old state/config data)
        self.greeted = {}
        self._lock = threading.Lock()

    def _opt(self, key):
        return self.options.get(key, self.DEFAULTS[key])

    def _state_file(self):
        return self._opt("state_file") or STATE_FILE_DEFAULT

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_loaded(self):
        if "messages" in self.options:
            self.messages = self.options["messages"]

        self._load_known_peers_dir()
        self._load_state()

        # config-supplied known_peers are merged in as already-greeted,
        # so you don't spam your own other units.
        for identity in self._opt("known_peers"):
            self.greeted.setdefault(identity, True)

        logging.info(
            "[SpamPeersNG] loaded, %d peer(s) already known/greeted, %d message(s)"
            % (len(self.greeted), len(self.messages))
        )

    def _load_known_peers_dir(self):
        # Mirrors the original's intent (seed known_peers from
        # /root/peers), but guarded: a fresh install or a device that's
        # never detected a peer yet simply has no such directory, and
        # that's not an error.
        peers_dir = "/root/peers"
        if not os.path.isdir(peers_dir):
            logging.debug("[SpamPeersNG] %s doesn't exist, nothing to preload" % peers_dir)
            return
        try:
            entries = os.listdir(peers_dir)
        except OSError as e:
            logging.warning("[SpamPeersNG] couldn't list %s: %s" % (peers_dir, e))
            return

        for f in entries:
            path = os.path.join(peers_dir, f)
            try:
                with open(path, "r") as fp:
                    p = json.load(fp)
                name = p["advertisement"]["name"]
                identity = Path(f).stem
                logging.debug("[SpamPeersNG] known peer %s %s" % (name, identity))
                self.greeted.setdefault(identity, True)
            except (OSError, ValueError, KeyError, TypeError) as e:
                logging.warning("[SpamPeersNG] unreadable peer file %s: %s" % (f, e))

    def _load_state(self):
        path = self._state_file()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                with self._lock:
                    self.greeted.update(data)
                logging.info("[SpamPeersNG] loaded %d greeted peer(s) from disk" % len(data))
        except (OSError, ValueError, TypeError) as e:
            logging.warning("[SpamPeersNG] couldn't load state file %s: %s" % (path, e))

    def _save_state(self):
        path = self._state_file()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with self._lock:
                snapshot = dict(self.greeted)
            with open(path, "w") as f:
                json.dump(snapshot, f)
        except (OSError, TypeError) as e:
            logging.warning("[SpamPeersNG] couldn't save state file %s: %s" % (path, e))

    # ------------------------------------------------------------------
    # Peer detection
    # ------------------------------------------------------------------

    def on_peer_detected(self, agent, peer):
        try:
            # peer.adv can legitimately be {} - the real Peer class
            # documents this happens on a malformed/null advertisement -
            # so use .get() instead of raw ['identity'] indexing, which
            # would KeyError on exactly that case.
            peer_print = peer.adv.get("identity")
            if not peer_print:
                logging.debug("[SpamPeersNG] peer with no identity in advertisement, skipping")
                return

            if not self._should_greet(peer_print):
                logging.debug("[SpamPeersNG] not greeting %s, still in cooldown" % repr(peer_print))
                return

            self._mark_greeted(peer_print)
            self._schedule_greeting(peer_print)
        except Exception as e:
            logging.error("[SpamPeersNG] error: %s" % repr(e))

    def _should_greet(self, identity):
        last = self.greeted.get(identity)
        if last is None:
            return True
        regreet_hours = self._opt("regreet_after_hours")
        if not regreet_hours:
            # 0 (or missing/never-timestamped `True` entry) = never re-greet
            return False
        if last is True:
            # greeted before, but with no timestamp on record (e.g. seeded
            # from /root/peers or old config) - treat as "long ago enough"
            # only if a cooldown is configured; otherwise stay silent.
            return True
        return (time.time() - last) >= regreet_hours * 3600

    def _mark_greeted(self, identity):
        with self._lock:
            self.greeted[identity] = time.time()
        self._save_state()

    def _schedule_greeting(self, identity):
        min_delay = max(0, self._opt("min_delay_seconds"))
        max_delay = max(min_delay, self._opt("max_delay_seconds"))
        delay = random.uniform(min_delay, max_delay)
        t = threading.Timer(delay, self._send_greeting, args=(identity,))
        t.daemon = True
        t.start()
        return t

    def _send_greeting(self, identity):
        try:
            message = random.choice(self.messages)
            logging.info("[SpamPeersNG] sending pwnmail to %s" % repr(identity))
            grid.send_message(identity, message)
        except Exception as e:
            logging.error("[SpamPeersNG] failed to send greeting to %s: %s" % (repr(identity), e))
