import sys
import os
import threading
import time
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")

import pwnagotchi.plugins  # noqa: E402
import apprise_notify_ng as mod  # noqa: E402
import apprise  # noqa: E402 (the stub)

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


def make_plugin(**opts):
    p = mod.AppriseNotifyNG()
    p.options = dict(opts)
    p.on_loaded()
    return p


class FakeImage:
    def save(self, path, fmt):
        self.saved_to = path


class FakeView:
    def image(self):
        return FakeImage()


class FakeAgent:
    def view(self):
        return FakeView()


def drain_queue(plugin, timeout=5.0):
    """Wait until the worker has actually finished processing every queued
    item (not just dequeued it) - queue.join() blocks until task_done() has
    been called once per put(), which is exactly what we want here."""
    joined = threading.Event()

    def _join():
        plugin._queue.join()
        joined.set()

    t = threading.Thread(target=_join, daemon=True)
    t.start()
    joined.wait(timeout=timeout)


# --- Registration ------------------------------------------------------

check(
    "AppriseNotifyNG registers as a real pwnagotchi.plugins.Plugin subclass",
    issubclass(mod.AppriseNotifyNG, pwnagotchi.plugins.Plugin),
)

# --- on_loaded with no apprise available --------------------------------

p = mod.AppriseNotifyNG()
p.options = {}
with mock.patch.dict(sys.modules, {"apprise": None}):
    # Force ImportError path
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "apprise":
            raise ImportError("no apprise")
        return real_import(name, *a, **kw)

    with mock.patch("builtins.__import__", side_effect=fake_import):
        p.on_loaded()

check("on_loaded handles apprise ImportError without crashing", p._apobj is None)

# --- on_loaded with real (stub) apprise, no urls configured -------------

p2 = make_plugin(urls=[], events=["ready"])
check("on_loaded with no urls still creates an Apprise object", p2._apobj is not None)
check("worker thread started", p2._worker_thread is not None and p2._worker_thread.is_alive())
p2.on_unload(mock.Mock())

# --- on_loaded adds configured urls --------------------------------------

p3 = make_plugin(urls=["ntfy://topic", "discord://webhook_id/webhook_token"], events=["ready"])
check("configured urls were added to the Apprise object", len(p3._apobj.added) == 2)
p3.on_unload(mock.Mock())

# --- on_loaded adds config_path ------------------------------------------

p4 = make_plugin(urls=[], config_path="/etc/pwnagotchi/apprise.yml", events=["ready"])
check(
    "configured config_path was added via AppriseConfig",
    len(p4._apobj.added) == 1 and isinstance(p4._apobj.added[0], apprise.AppriseConfig),
)
p4.on_unload(mock.Mock())

# --- Real event actually notifies -----------------------------------------

p5 = make_plugin(urls=["ntfy://topic"], events=["ready"], min_interval_seconds=0)
agent = FakeAgent()
p5.on_ready(agent)
drain_queue(p5)
check(
    "on_ready with 'ready' in events sends exactly one notification",
    len(p5._apobj.notifications) == 1,
)
check(
    "notification body mentions the unit is ready",
    "ready" in p5._apobj.notifications[0]["body"].lower(),
)
p5.on_unload(mock.Mock())

# --- Event not in configured events list is skipped -----------------------

p6 = make_plugin(urls=["ntfy://topic"], events=["handshake"], min_interval_seconds=0)
p6.on_ready(agent)
drain_queue(p6)
check(
    "on_ready is a no-op when 'ready' isn't in the configured events list",
    len(p6._apobj.notifications) == 0,
)
p6.on_unload(mock.Mock())

# --- Only real hooks get wired - no crash from unknown event names --------

p7 = make_plugin(urls=["ntfy://topic"], events=["on_ai_ready", "ready"], min_interval_seconds=0)
check(
    "an unknown/fake event name in the config doesn't crash on_loaded",
    p7._apobj is not None,
)
p7.on_unload(mock.Mock())

# --- Handshake builds a real message from real AP/client dict shape -------

p8 = make_plugin(urls=["ntfy://topic"], events=["handshake"], min_interval_seconds=0)
ap = {"hostname": "TestNet", "mac": "aa:bb:cc:dd:ee:ff"}
client = {"mac": "11:22:33:44:55:66"}
p8.on_handshake(agent, "/tmp/x.pcap", ap, client)
drain_queue(p8)
check("handshake notification was sent", len(p8._apobj.notifications) == 1)
check(
    "handshake body includes the AP hostname",
    "TestNet" in p8._apobj.notifications[0]["body"],
)
p8.on_unload(mock.Mock())

# --- Screenshot attach toggle ------------------------------------------

p9 = make_plugin(
    urls=["ntfy://topic"], events=["ready"], min_interval_seconds=0, attach_screenshot=True
)
p9.on_ready(agent)
drain_queue(p9)
check(
    "attach_screenshot=True includes a saved picture path",
    p9._apobj.notifications[0]["attach"] == "/tmp/apprise_notify_ng.png",
)
p9.on_unload(mock.Mock())

p10 = make_plugin(
    urls=["ntfy://topic"], events=["ready"], min_interval_seconds=0, attach_screenshot=False
)
p10.on_ready(agent)
drain_queue(p10)
check(
    "attach_screenshot=False sends no attachment",
    p10._apobj.notifications[0]["attach"] is None,
)
p10.on_unload(mock.Mock())

# --- Notification failure doesn't crash the worker ------------------------

p11 = make_plugin(urls=["ntfy://topic"], events=["ready"], min_interval_seconds=0)
p11._apobj.notify = mock.Mock(side_effect=RuntimeError("network down"))
p11.on_ready(agent)
drain_queue(p11)
check(
    "worker thread survives a notify() exception",
    p11._worker_thread.is_alive(),
)
p11.on_unload(mock.Mock())

# --- min_interval_seconds throttles back-to-back events --------------------

p12 = make_plugin(urls=["ntfy://topic"], events=["ready"], min_interval_seconds=0.3)
start = time.time()
p12.on_ready(agent)
p12.on_ready(agent)
drain_queue(p12, timeout=3.0)
elapsed = time.time() - start
check("two events within the cooldown window both get sent eventually", len(p12._apobj.notifications) == 2)
check("cooldown actually delayed the second send", elapsed >= 0.25)
p12.on_unload(mock.Mock())

# --- Queue full doesn't crash ------------------------------------------

p13 = make_plugin(urls=["ntfy://topic"], events=["ready"], min_interval_seconds=100, max_queue_size=1)
p13._stop_event.set()  # stop the worker so the queue actually fills up
p13._worker_thread.join(timeout=2.0)
p13._queue.put_nowait(("t", "b", agent))
try:
    p13.on_ready(agent)  # queue is full (maxsize=1, one item already in it)
    ok = True
except Exception:
    ok = False
check("a full notification queue doesn't raise", ok)

# --- on_unload is idempotent / safe with no prior setup --------------------

p14 = mod.AppriseNotifyNG()
p14.options = {}
try:
    p14.on_unload(mock.Mock())
    ok = True
except Exception:
    ok = False
check("on_unload doesn't crash if on_loaded never ran", ok)

# --- webhook handler doesn't crash --------------------------------------

p15 = make_plugin(urls=[], events=[])
try:
    p15.on_webhook("/", mock.Mock())
    ok = True
except Exception:
    ok = False
check("on_webhook doesn't crash", ok)
p15.on_unload(mock.Mock())


print(f"\n{len(failures)} failure(s) out of test run")
if failures:
    sys.exit(1)
