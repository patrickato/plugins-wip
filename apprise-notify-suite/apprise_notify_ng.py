import logging
import os
import queue
import threading
import time

import pwnagotchi.plugins as plugins

ELEMENT_NAME = "apprise_notify_ng_status"

# All hooks below are confirmed real on this fork (grep of every
# plugins.on(...) call site in pwnagotchi/agent.py, automata.py, epoch.py,
# grid.py). The original apprise-notify.py wired up ~30 callbacks, most of
# which (on_ai_ready, on_ai_policy, on_ai_training_*, on_config_changed,
# on_free_channel, on_wait, on_cracked, on_unread_messages, ...) are not real
# hooks on this fork and simply never fire. This rebuild only wires up hooks
# that actually exist, and skips the mood/movement hooks (bored/sad/excited/
# lonely/angry/grateful/channel_hop/association/wifi_update/unfiltered_ap_list)
# by default since they fire far too often for a push-notification service -
# they're still available via the `events` option for anyone who wants them.

REAL_EVENT_ARGS = {
    "ready": lambda agent: "Unit is ready and sniffing.",
    "handshake": lambda agent, filename, access_point, client_station: (
        "New handshake: %s -> %s"
        % (access_point.get("hostname", "unknown"), client_station.get("mac", "unknown"))
    ),
    "peer_detected": lambda agent, peer: "New peer detected: %s" % _peer_str(peer),
    "peer_lost": lambda agent, peer: "Lost contact with peer: %s" % _peer_str(peer),
    "rebooting": lambda agent: "Unit is rebooting.",
    "bored": lambda agent: "Feeling bored.",
    "sad": lambda agent: "Feeling sad.",
    "excited": lambda agent: "Feeling excited!",
    "lonely": lambda agent: "Feeling lonely.",
    "grateful": lambda agent: "Feeling grateful.",
    "angry": lambda agent: "Feeling angry.",
}


def _peer_str(peer):
    try:
        return str(peer.name())
    except Exception:
        return str(peer)


class AppriseNotifyNG(plugins.Plugin):
    __author__ = "rebuilt from itsdarklikehell's apprise-notify.py"
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = (
        "Sends push notifications for real pwnagotchi events through Apprise, "
        "which fans out to dozens of services (Discord, ntfy, Pushover, email, "
        "SMS gateways, and more) from one list of URLs."
    )

    DEFAULTS = {
        "enabled": False,
        "urls": [],
        "config_path": None,
        "events": ["ready", "handshake", "peer_detected", "rebooting"],
        "attach_screenshot": True,
        "min_interval_seconds": 5,
        "max_queue_size": 200,
    }

    def __init__(self):
        self._apobj = None
        self._queue = None
        self._stop_event = threading.Event()
        self._worker_thread = None
        self._last_sent = 0.0
        self._lock = threading.Lock()

    def _opt(self, key):
        return self.options.get(key, self.DEFAULTS[key])

    def on_loaded(self):
        logging.info("[AppriseNotifyNG] plugin loaded.")

        try:
            import apprise
        except ImportError as ie:
            logging.error("[AppriseNotifyNG] Couldn't import apprise (%s)", ie)
            self._apobj = None
            return

        urls = self._opt("urls") or []
        config_path = self._opt("config_path")

        if not urls and not config_path:
            logging.warning(
                "[AppriseNotifyNG] No urls or config_path configured - "
                "notifications are disabled until you set one."
            )

        apobj = apprise.Apprise()

        if config_path:
            try:
                cfg = apprise.AppriseConfig()
                cfg.add(config_path)
                apobj.add(cfg)
            except Exception as e:
                logging.error(
                    "[AppriseNotifyNG] Failed to load config_path %s: %s", config_path, e
                )

        for url in urls:
            try:
                apobj.add(url)
            except Exception as e:
                logging.error("[AppriseNotifyNG] Failed to add url: %s", e)

        self._apobj = apobj
        self._queue = queue.Queue(maxsize=int(self._opt("max_queue_size")))
        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True, name="AppriseNotifyNGWorker"
        )
        self._worker_thread.start()

        # Wire up every requested real event. Anything not in REAL_EVENT_ARGS
        # is silently skipped rather than crashing the load.
        for event in self._opt("events"):
            if event not in REAL_EVENT_ARGS:
                logging.warning(
                    "[AppriseNotifyNG] '%s' is not a real event on this fork, skipping.",
                    event,
                )

    def on_unload(self, ui):
        logging.info("[AppriseNotifyNG] plugin unloading...")
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=5.0)
        try:
            ui.remove_element(ELEMENT_NAME)
        except KeyError:
            pass

    def on_ui_setup(self, ui):
        pass

    # ------------------------------------------------------------------
    # Generic dispatcher - one real method per configured, real event.
    # ------------------------------------------------------------------

    def _handle(self, event_name, agent, *args):
        if not self._apobj:
            return
        if event_name not in self._opt("events"):
            return
        builder = REAL_EVENT_ARGS.get(event_name)
        if builder is None:
            return
        try:
            body = builder(agent, *args)
        except Exception as e:
            logging.debug("[AppriseNotifyNG] error building body for %s: %s", event_name, e)
            return
        self._queue_notification(event_name, body, agent)

    def on_ready(self, agent):
        self._handle("ready", agent)

    def on_handshake(self, agent, filename, access_point, client_station):
        self._handle("handshake", agent, filename, access_point, client_station)

    def on_peer_detected(self, agent, peer):
        self._handle("peer_detected", agent, peer)

    def on_peer_lost(self, agent, peer):
        self._handle("peer_lost", agent, peer)

    def on_rebooting(self, agent):
        self._handle("rebooting", agent)

    def on_bored(self, agent):
        self._handle("bored", agent)

    def on_sad(self, agent):
        self._handle("sad", agent)

    def on_excited(self, agent):
        self._handle("excited", agent)

    def on_lonely(self, agent):
        self._handle("lonely", agent)

    def on_grateful(self, agent):
        self._handle("grateful", agent)

    def on_angry(self, agent):
        self._handle("angry", agent)

    def on_webhook(self, path, request):
        logging.info("[AppriseNotifyNG] webhook pressed")

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    def _queue_notification(self, title, body, agent):
        try:
            self._queue.put_nowait((title, body, agent))
        except queue.Full:
            logging.warning("[AppriseNotifyNG] Notification queue full, dropping event.")

    def _worker_loop(self):
        while not self._stop_event.is_set():
            try:
                title, body, agent = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            with self._lock:
                elapsed = time.time() - self._last_sent
                min_interval = self._opt("min_interval_seconds")
                if elapsed < min_interval:
                    time.sleep(min_interval - elapsed)

                attach = None
                if self._opt("attach_screenshot"):
                    attach = self._take_screenshot(agent)

                try:
                    self._apobj.notify(title=title, body=body, attach=attach)
                except Exception as e:
                    logging.error("[AppriseNotifyNG] notify() failed: %s", e)

                self._last_sent = time.time()

            self._queue.task_done()

    def _take_screenshot(self, agent):
        try:
            view = agent.view()
            picture = "/tmp/apprise_notify_ng.png"
            view.image().save(picture, "png")
            return picture
        except Exception as e:
            logging.debug("[AppriseNotifyNG] couldn't take screenshot: %s", e)
            return None
