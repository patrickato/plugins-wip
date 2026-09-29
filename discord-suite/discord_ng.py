import json
import logging
import os
import time
import threading
import queue
import atexit
from collections import deque
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime

import requests
from requests import RequestException
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import pwnagotchi
import pwnagotchi.plugins as plugins

# --- Config-driven path resolution -----------------------------------------
CANONICAL_HANDSHAKES = "/etc/pwnagotchi/handshakes"

DISCORD_TIMEOUT = 30
DISCORD_QUICK_TIMEOUT = 10
WIGLE_TIMEOUT = 10

MAX_QUEUE_SIZE = 1000
WORKER_SLEEP_INTERVAL = 2.0

CACHE_EXPIRY_DAYS = 30

logger = logging.getLogger("pwnagotchi.plugins.discord_ng")


@dataclass
class CachedLocation:
    lat: str
    lon: str
    timestamp: float

    def is_expired(self, expiry_days: int = CACHE_EXPIRY_DAYS) -> bool:
        age_days = (time.time() - self.timestamp) / 86400
        return age_days > expiry_days

    def to_dict(self) -> Dict[str, Any]:
        return {"lat": self.lat, "lon": self.lon, "timestamp": self.timestamp}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CachedLocation":
        return cls(
            lat=data["lat"], lon=data["lon"], timestamp=data.get("timestamp", time.time())
        )


class DiscordNG(plugins.Plugin):
    __author__ = "rebuilt from WPA2's Discord v3.0.2 (discord.py)"
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = (
        "Sends handshakes with location data and session reports to a Discord "
        "webhook. Threaded worker queue, WiGLE location cache, HTTP retries."
    )

    DEFAULTS = {
        "enabled": False,
        "webhook_url": None,
        "wigle_api_key": None,
        "disable_wigle_lookup": False,
        "attachment_mode": "file",  # "file" or "json_only"
        "include_session_stats": True,
        "cache_file": None,  # defaults to <handshakes dir>/discord_ng_wigle_cache.json
    }

    def __init__(self):
        self.webhook_url: Optional[str] = None
        self.api_key: Optional[str] = None

        self.http_session = self._create_http_session()

        self.wigle_cache: Dict[str, CachedLocation] = {}
        self.cache_lock = threading.Lock()

        self.recent_handshakes: deque = deque(maxlen=200)
        self.handshake_lock = threading.Lock()

        self._event_queue = queue.Queue(maxsize=MAX_QUEUE_SIZE)
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._cleanup_done = False

        self.session_lock = threading.Lock()
        self.session_handshakes = 0
        self.start_time = time.time()
        self.session_id = os.urandom(4).hex()

        self._cache_save_timer: Optional[threading.Timer] = None
        self._cache_dirty = False
        self._cache_file: Optional[str] = None

        atexit.register(self._on_exit_cleanup)

    def _opt(self, key):
        return self.options.get(key, self.DEFAULTS[key])

    @staticmethod
    def _create_http_session() -> requests.Session:
        session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    # ------------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------------

    def _handshake_dir(self) -> str:
        try:
            cfg = getattr(pwnagotchi, "config", None)
            if cfg and cfg.get("bettercap", {}).get("handshakes"):
                return cfg["bettercap"]["handshakes"]
        except Exception:
            pass
        return CANONICAL_HANDSHAKES

    def on_loaded(self):
        logger.info("DiscordNG plugin loaded (v%s).", self.__version__)

        self.webhook_url = self._opt("webhook_url")
        self.api_key = self._opt("wigle_api_key")

        self._cache_file = self._opt("cache_file") or os.path.join(
            self._handshake_dir(), "discord_ng_wigle_cache.json"
        )
        try:
            os.makedirs(os.path.dirname(self._cache_file), exist_ok=True)
        except Exception as e:
            logger.warning("Couldn't create cache dir: %s", e)

        self._load_wigle_cache()

        if not self.webhook_url:
            logger.error("DiscordNG: Missing webhook_url in configuration.")
            return

        if not self.api_key and not self._opt("disable_wigle_lookup"):
            logger.warning("DiscordNG: Missing wigle_api_key - location lookups disabled.")

        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True, name="DiscordNGWorker"
        )
        self._worker_thread.start()

        self._schedule_cache_save()

        logger.info("DiscordNG: Worker thread started. Session ID: %s", self.session_id)

    def on_unload(self, ui):
        logger.info("DiscordNG: Unloading...")
        self._on_exit_cleanup()

    def _on_exit_cleanup(self):
        if self._cleanup_done:
            return
        self._cleanup_done = True
        logger.info("DiscordNG: Cleaning up...")

        if self._cache_save_timer:
            self._cache_save_timer.cancel()

        self._save_wigle_cache()

        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=5.0)
            if self._worker_thread.is_alive():
                logger.warning("Worker thread did not finish in time")

        try:
            self.http_session.close()
        except Exception as e:
            logger.error("Error closing HTTP session: %s", e)

        logger.info("DiscordNG: Cleanup complete.")

    # ------------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------------

    def on_ready(self, agent):
        self.start_time = time.time()
        logger.info("DiscordNG: Pwnagotchi is ready.")

        unit_name = self._get_unit_name(agent)

        self._queue_notification(
            content="\U0001F7E2 **Pwnagotchi is Online!**",
            embed={
                "title": f"{unit_name} is Ready",
                "description": f"Unit is ready and sniffing.\n**Plugin Session ID:** `{self.session_id}`",
                "color": 5763719,
                "timestamp": self._get_iso_timestamp(),
            },
        )

        if self._opt("include_session_stats"):
            self._report_previous_session(agent, unit_name)

    def on_handshake(self, agent, filename, access_point, client_station):
        bssid = access_point.get("mac", "00:00:00:00:00:00")
        client_mac = client_station.get("mac", "00:00:00:00:00:00")
        handshake_key = (filename, bssid.lower(), client_mac.lower())

        with self.handshake_lock:
            if handshake_key in self.recent_handshakes:
                logger.debug("Duplicate handshake ignored: %s", filename)
                return
            self.recent_handshakes.append(handshake_key)

        with self.session_lock:
            self.session_handshakes += 1
            current_count = self.session_handshakes

        logger.info("New handshake captured: %s (Total: %s)", filename, current_count)

        try:
            self._event_queue.put_nowait(
                {
                    "type": "handshake",
                    "filename": filename,
                    "access_point": access_point,
                    "client_station": client_station,
                    "session_count": current_count,
                }
            )
        except queue.Full:
            logger.error("Event queue is full! Dropping handshake notification.")

    # ------------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------------

    def _worker_loop(self):
        logger.debug("Worker thread started")

        while not self._stop_event.is_set():
            try:
                event = self._event_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            try:
                self._dispatch_event(event)
                if not self._stop_event.is_set():
                    time.sleep(WORKER_SLEEP_INTERVAL)
            except Exception as e:
                logger.error("Error processing event: %s", e, exc_info=True)
            finally:
                self._event_queue.task_done()

        logger.debug("Processing remaining queue items...")
        while not self._event_queue.empty():
            try:
                event = self._event_queue.get_nowait()
                self._dispatch_event(event)
                self._event_queue.task_done()
            except queue.Empty:
                break
            except Exception as e:
                logger.error("Error in shutdown processing: %s", e)

        logger.debug("Worker thread finished")

    def _dispatch_event(self, event):
        event_type = event.get("type")
        if event_type == "handshake":
            self._process_handshake(event)
        elif event_type == "notification":
            self._send_discord_payload(event["content"], event.get("embeds", []))
        else:
            logger.warning("Unknown event type: %s", event_type)

    def _queue_notification(self, content: str, embed: Optional[Dict] = None):
        try:
            payload = {"type": "notification", "content": content, "embeds": [embed] if embed else []}
            self._event_queue.put_nowait(payload)
        except queue.Full:
            logger.error("Event queue is full! Dropping notification.")

    def _process_handshake(self, event: Dict[str, Any]):
        filename = event["filename"]
        ap = event["access_point"]
        client = event["client_station"]
        session_count = event.get("session_count", 0)

        bssid = ap.get("mac", "Unknown")
        ap_name = ap.get("hostname", "Unknown")
        client_mac = client.get("mac", "Unknown")

        logger.info("Processing handshake for Discord: %s [%s] -> %s", ap_name, bssid, client_mac)

        location = self._get_location_from_wigle(bssid)
        if location:
            loc_str = (
                f"**Lat:** {location.lat}, **Lon:** {location.lon}\n"
                f"[\U0001F5FA View on Google Maps]"
                f"(https://www.google.com/maps/search/?api=1&query={location.lat},{location.lon})"
            )
        elif self._opt("disable_wigle_lookup"):
            loc_str = "Location lookups disabled"
        else:
            loc_str = "Location not available (not in WiGLE database)"

        embed = {
            "title": "\U0001F510 New Handshake Captured!",
            "description": f"**Access Point:** {ap_name}\n**BSSID:** `{bssid}`",
            "fields": [
                {"name": "\U0001F4F1 Client Station", "value": f"`{client_mac}`", "inline": True},
                {"name": "\U0001F4CA Channel", "value": str(ap.get("channel", "Unknown")), "inline": True},
                {"name": "\U0001F5C2 Handshake File", "value": f"`{os.path.basename(filename)}`", "inline": False},
                {"name": "\U0001F4CD Location", "value": loc_str, "inline": False},
            ],
            "footer": {"text": f"Session: {session_count} handshakes | ID: {self.session_id}"},
            "timestamp": self._get_iso_timestamp(),
            "color": 16776960,
        }

        attach_path = filename if self._opt("attachment_mode") == "file" else None
        self._send_discord_payload(
            content=f"\U0001F91D New handshake from **{ap_name}**", embeds=[embed], file_path=attach_path
        )

    # ------------------------------------------------------------------------
    # Discord API
    # ------------------------------------------------------------------------

    def _send_discord_payload(self, content: str, embeds: List[Dict], file_path: Optional[str] = None):
        if not self.webhook_url:
            logger.warning("No webhook URL configured, skipping Discord message")
            return

        payload_dict = {"content": content, "embeds": embeds}

        try:
            if file_path and os.path.exists(file_path):
                self._send_with_file(payload_dict, file_path)
            else:
                self._send_json_only(payload_dict)
        except RequestException as e:
            logger.error("Discord API request failed: %s", e)
        except Exception as e:
            logger.error("Unexpected error sending to Discord: %s", e, exc_info=True)

    def _send_with_file(self, payload_dict: Dict, file_path: str):
        try:
            filename = os.path.basename(file_path)
            logger.info("Sending Discord notification with file attachment: %s", filename)

            with open(file_path, "rb") as f:
                files = {"file": (filename, f, "application/octet-stream")}
                data = {"payload_json": json.dumps(payload_dict)}
                response = self.http_session.post(
                    self.webhook_url, files=files, data=data, timeout=DISCORD_TIMEOUT
                )
                self._handle_discord_response(response, with_file=True)
        except FileNotFoundError:
            logger.error("File not found: %s", file_path)
            self._send_json_only(payload_dict)
        except IOError as e:
            logger.error("Error reading file %s: %s", file_path, e)
            self._send_json_only(payload_dict)

    def _send_json_only(self, payload_dict: Dict):
        logger.info("Sending Discord notification (JSON only)")
        response = self.http_session.post(
            self.webhook_url,
            json=payload_dict,
            headers={"Content-Type": "application/json"},
            timeout=DISCORD_QUICK_TIMEOUT,
        )
        self._handle_discord_response(response, with_file=False)

    @staticmethod
    def _handle_discord_response(response: requests.Response, with_file: bool = False):
        attachment_info = " (with file)" if with_file else ""
        if response.status_code in (200, 204):
            logger.info("✓ Discord notification sent successfully%s", attachment_info)
        elif response.status_code == 429:
            try:
                data = response.json()
                retry_after = data.get("retry_after", "unknown")
                logger.warning("Discord rate limit hit. Retry after: %ss", retry_after)
            except Exception:
                logger.warning("Discord rate limit hit (couldn't parse retry info)")
        else:
            logger.error("Discord API error: %s - %s", response.status_code, response.text)

    # ------------------------------------------------------------------------
    # WiGLE
    # ------------------------------------------------------------------------

    def _get_location_from_wigle(self, bssid: str) -> Optional[CachedLocation]:
        if not bssid or self._opt("disable_wigle_lookup"):
            return None

        normalized_bssid = bssid.lower()

        with self.cache_lock:
            if normalized_bssid in self.wigle_cache:
                cached = self.wigle_cache[normalized_bssid]
                if not cached.is_expired():
                    logger.debug("WiGLE cache hit for %s", normalized_bssid)
                    return cached
                else:
                    logger.debug("WiGLE cache expired for %s", normalized_bssid)
                    del self.wigle_cache[normalized_bssid]

        if not self.api_key:
            return None

        logger.debug("Querying WiGLE API for %s", normalized_bssid)
        location = self._query_wigle_api(normalized_bssid)

        if location:
            with self.cache_lock:
                self.wigle_cache[normalized_bssid] = location
                self._cache_dirty = True

        return location

    def _query_wigle_api(self, bssid: str) -> Optional[CachedLocation]:
        headers = {"Authorization": f"Basic {self.api_key}"}
        params = {"netid": bssid}

        try:
            response = self.http_session.get(
                "https://api.wigle.net/api/v2/network/detail",
                headers=headers,
                params=params,
                timeout=WIGLE_TIMEOUT,
            )

            if response.status_code == 200:
                data = response.json()
                if data.get("success") and data.get("results"):
                    result = data["results"][0]
                    lat = result.get("trilat", "N/A")
                    lon = result.get("trilong", "N/A")
                    if lat != "N/A" and lon != "N/A":
                        logger.debug("WiGLE lookup successful for %s", bssid)
                        return CachedLocation(lat=str(lat), lon=str(lon), timestamp=time.time())
                    else:
                        logger.debug("WiGLE returned invalid coordinates for %s", bssid)
                else:
                    logger.debug("WiGLE API returned no results for %s", bssid)
            elif response.status_code == 404:
                logger.debug("BSSID %s not found in WiGLE database", bssid)
            else:
                logger.warning("WiGLE API error: %s", response.status_code)
        except RequestException as e:
            logger.error("WiGLE API request failed: %s", e)
        except (KeyError, ValueError, TypeError) as e:
            logger.error("Error parsing WiGLE response: %s", e)

        return None

    # ------------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------------

    def _load_wigle_cache(self):
        if not self._cache_file or not os.path.exists(self._cache_file):
            logger.debug("No existing cache file found")
            return

        try:
            with open(self._cache_file, "r") as f:
                raw_cache = json.load(f)

            loaded_count = 0
            expired_count = 0
            with self.cache_lock:
                for bssid, data in raw_cache.items():
                    try:
                        cached_loc = CachedLocation.from_dict(data)
                        if cached_loc.is_expired():
                            expired_count += 1
                            continue
                        self.wigle_cache[bssid] = cached_loc
                        loaded_count += 1
                    except (KeyError, TypeError, ValueError) as e:
                        logger.warning("Skipping invalid cache entry for %s: %s", bssid, e)

            logger.info("Loaded %s WiGLE cache entries (%s expired)", loaded_count, expired_count)
        except (IOError, json.JSONDecodeError) as e:
            logger.error("Error loading cache file: %s", e)
            self.wigle_cache = {}

    def _save_wigle_cache(self):
        if not self._cache_dirty or not self._cache_file:
            logger.debug("Cache not dirty, skipping save")
            return

        try:
            with self.cache_lock:
                raw_cache = {
                    bssid: loc.to_dict()
                    for bssid, loc in self.wigle_cache.items()
                    if not loc.is_expired()
                }
                self._cache_dirty = False

            with open(self._cache_file, "w") as f:
                json.dump(raw_cache, f, indent=2)

            logger.info("Saved %s WiGLE cache entries", len(raw_cache))
        except (IOError, TypeError) as e:
            logger.error("Error saving cache file: %s", e)

    def _schedule_cache_save(self):
        if self._stop_event.is_set():
            return
        self._save_wigle_cache()
        self._cache_save_timer = threading.Timer(300.0, self._schedule_cache_save)
        self._cache_save_timer.daemon = True
        self._cache_save_timer.start()

    # ------------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------------

    @staticmethod
    def _get_unit_name(agent) -> str:
        try:
            config = agent.config()
            return config["main"]["name"]
        except (KeyError, AttributeError, TypeError):
            return "Pwnagotchi"

    def _report_previous_session(self, agent, unit_name: str):
        if not hasattr(agent, "last_session") or not agent.last_session:
            return

        last = agent.last_session

        duration_str = str(getattr(last, "duration", "0:00:00"))
        if duration_str == "0:00:00":
            logger.debug("Previous session had no duration, skipping report")
            return

        handshakes = getattr(last, "handshakes", 0)
        epochs = getattr(last, "epochs", 0)
        # Fixed from the original: the real LastSession attribute is
        # `deauthed`, not `deauths` - the original's getattr(..., 'deauths', 0)
        # silently always returned 0 because that attribute never existed.
        deauthed = getattr(last, "deauthed", 0)

        logger.info("Reporting previous session: %s handshakes in %s", handshakes, duration_str)

        fields = [
            {"name": "\U0001F91D Handshakes", "value": str(handshakes), "inline": True},
            {"name": "⏱️ Duration", "value": duration_str, "inline": True},
            {"name": "\U0001F504 Epochs", "value": str(epochs), "inline": True},
        ]

        if deauthed > 0:
            fields.append({"name": "\U0001F4A5 Deauths", "value": str(deauthed), "inline": True})

        self._queue_notification(
            content="\U0001F4CB **Previous Session Report**",
            embed={
                "title": f"{unit_name} - Session Summary",
                "description": "Statistics from the last session before restart/mode switch",
                "fields": fields,
                "color": 12370112,
                "timestamp": self._get_iso_timestamp(),
            },
        )

    def on_webhook(self, path, request):
        logger.info("webhook pressed")

    @staticmethod
    def _get_iso_timestamp() -> str:
        return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
