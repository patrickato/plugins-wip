import logging
import os
import signal
import threading
import time

import RPi.GPIO as GPIO
import numpy as np

from subprocess import Popen, PIPE, check_output

import pwnagotchi.plugins as plugins
from pwnagotchi.ui.components import *
from pwnagotchi.ui.view import BLACK
import pwnagotchi.ui.fonts as fonts


class Touch_Button(Widget):
    def __init__(
        self,
        position=(0, 0, 30, 30),
        color="White",
        *,
        state=False,
        momentary=False,
        reverse=False,
        text=None,
        value=None,
        text_color="Black",
        font=None,
        image=None,
        shadow="Black",
        highlight="White",
        outline=None,
        alt_text=None,
        alt_text_color=None,
        alt_font=None,
        alt_image=None,
        alt_color=None,
        event_handler=None,
    ):
        super().__init__(position, color)
        self.event_handler = event_handler  # name of the plugin to notify (press/release/move)
        self.xy = position
        self.color = color
        self.state = state  # False=off, True=on
        # False = toggle on/off, True  = on while pressed, off on release (single action)
        self.momentary = momentary
        self.reverse = (
            reverse  # if True, then default state is True, and alt state is False
        )

        self.shadow = shadow  # on "falling" sides of button
        self.highlight = highlight  # on "rising" sides of button
        self.outline = outline  # outer ring of button, drawn last

        self.text = text  # text label
        self.value = value  # default value (if set, displayed under text)
        self.text_color = text_color  # default text_color
        self.font = font  # default font (None)
        if image:
            try:
                self.image = Image.open(image)
            except Exception as e:
                logging.warning("Image %s error: %s" % (image, repr(e)))
                self.image = None
        else:
            self.image = None  # default image (None)

        # alternate display items for pressed state
        self.alt_color = alt_color
        self.alt_text_color = alt_text_color if alt_text_color else text_color
        self.alt_text = alt_text
        self.alt_font = alt_font if alt_font else font
        if alt_image:
            try:
                self.alt_image = Image.open(alt_image)
            except Exception as e:
                logging.warning("Image %s error: %s" % (alt_image, repr(e)))
                self.alt_image = None
        else:
            self.alt_image = None  # default image (None)

    def draw(self, canvas, drawer):
        try:
            pressed = self.state != self.reverse
            xy = np.array(self.xy)

            # draw button highlight and shadow
            if pressed:
                upper = list(np.add(xy, np.array([-1, -1, 0, 0])))
                drawer.rectangle(upper, fill=self.highlight)
            else:
                lower = list(np.add(xy, np.array([2, 2, 1, 1])))
                drawer.rectangle(lower, fill=self.shadow)
                xy = np.add(xy, np.array([-1, -1, -1, -1]))

            # draw button background
            color = self.alt_color if pressed and self.alt_color else self.color
            drawer.rectangle(list(xy), fill=color, outline=self.outline)

            image = self.alt_image if pressed and self.alt_image else self.image
            if image:
                canvas.paste(image, list(xy))

            text = self.alt_text if pressed and self.alt_text else self.text

            value = self.value

            if value:
                if text:
                    text = "%s\n%s" % (text, value)
                else:
                    text = "%s" % value

            text_color = self.alt_text_color if pressed else self.text_color
            text_font = self.alt_font if pressed else self.font

            if text:
                textpos = ((xy[0] + xy[2]) / 2, (xy[1] + xy[3]) / 2 + 1)
                drawer.text(
                    textpos,
                    text,
                    anchor="mm",
                    fill=text_color,
                    font=text_font,
                    align="center",
                )
        except Exception as e:
            # FIXED: the original called logging(repr(e)) here - `logging`
            # is the imported module object, not callable, so any real
            # exception during button drawing raised a brand new,
            # unhandled TypeError from inside this except block instead
            # of just being logged, which could take down the UI render
            # thread entirely.
            logging.error("[Touch_Button] draw: %s", repr(e))


DEFAULTS = {
    "enabled": False,
    # ADDED: how long (in seconds) a press must be held before release
    # also dispatches a "touch_longpress" event, in addition to the
    # normal "touch_release" one.
    "longpress_seconds": 0.6,
}


class TouchUING(plugins.Plugin):
    __author__ = "fixed/extended by this project's plugin audit, from itsdarklikehell/Sniffleupagus's Touch_UI.py"
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = "Use touchscreen input to toggle settings."
    __name__ = "TouchUING"
    __help__ = __description__

    # Touch screen support
    #
    # uses system touchscreens in /dev/input/event*
    #
    # Requires tslib and evtest:
    #
    #  % sudo apt install evtest libts-bin
    #
    # Tested with Inland 3.5" TFT touchscreen, 26-pin connector
    # - install https://github.com/goodtft/LCD-show
    #
    # plugins that want to receive touch events can implement these
    # callback functions:
    #
    # on_touch_ready(self, touchscreen)
    # on_touch_press(self, ts, ui, ui_element, touch_data)
    # on_touch_release(self, ts, ui, ui_element, touch_data)
    # on_touch_move(self, ts, ui, ui_element, touch_data)
    #
    # touch_data = { point: [x,y], pressure: p }
    #
    # A button's event_handler, if set, names the single plugin to
    # notify (via plugins.one) rather than broadcasting to every plugin.

    def __init__(self):
        self.running = False
        self._ts_thread = None
        self.keepGoing = False
        self._view = None
        self._agent = None
        self._beingTouched = False
        self._ui_elements = []
        self.touchscreen = None
        self.touch_elements = {}
        self.needsAptPackages = None
        self.buttonCurrentZone = None
        # ADDED: last-touch timestamp and in-progress press start time,
        # used by the new webhook status page and long-press detection.
        self._last_touch = None
        self._press_start = None

        logging.debug("[TouchUING] plugin init")

    def _opt(self, key):
        return self.options.get(key, DEFAULTS[key])

    def touchScreenHandler(self, ts_device=None):
        try:
            if not ts_device:
                try:
                    evtest = Popen(
                        "/usr/bin/evtest",
                        stdout=PIPE,
                        stderr=PIPE,
                        universal_newlines=True,
                    )
                except FileNotFoundError:
                    # FIXED: the original's check for a missing evtest
                    # binary was `if not evtest`, but a Popen object is
                    # always truthy - that check could never fire, so a
                    # missing binary would instead raise
                    # FileNotFoundError right here, which the original
                    # only caught much further out (the broad try/except
                    # wrapping this whole method), silently exiting
                    # without ever recording that evtest/libts-bin needed
                    # installing. Catching it at the actual failure point
                    # lets needsAptPackages actually get set.
                    logging.info("[TouchUING] evtest not found")
                    self.needsAptPackages = ["evtest", "libts-bin"]
                    return

                while True:
                    output = str(evtest.stderr.readline())
                    if not output:
                        break
                    output = output.rstrip("\n")
                    logging.info("[TouchUING] Looking for screen: %s", repr(output))
                    try:
                        if "touchscreen" in output.lower():
                            (ts_device, rest) = output.split(":", 2)
                            ts_device = str(ts_device)
                            logging.info(
                                "[TouchUING] Found touchscreen device %s", ts_device
                            )
                            break
                    except Exception as e:
                        logging.error(repr(e))

            self.keepGoing = True
            while ts_device and self.keepGoing:
                cmd = "/usr/bin/ts_print"
                os.environ["TSLIB_TSDEVICE"] = "%s" % ts_device
                try:
                    self.touchscreen = (
                        Popen(
                            ["stdbuf", "-o0", cmd],
                            env=os.environ,
                            stdout=PIPE,
                            universal_newlines=True,
                            shell=False,
                        )
                        if not self.needsAptPackages
                        else None
                    )
                except FileNotFoundError:
                    logging.info("[TouchUING] ts_print not found")
                    self.needsAptPackages = ["evtest", "libts-bin"]
                    self.touchscreen = None

                if self.touchscreen:
                    logging.info("[TouchUING] ts_print running")
                    self.running = True
                    for output in self.touchscreen.stdout:
                        if not output or not self.keepGoing:
                            break
                        output = output.strip()
                        logging.debug("[TouchUING] Touch '%s'", output)
                        (tstamp, y, x, depth) = output.split()
                        x = int(x)
                        y = int(y)

                        rotation = (
                            self._agent._config["ui"]["display"]["rotation"]
                            if self._agent
                            else 180
                        )

                        if rotation == 180:
                            x = self._view._width - x
                        else:
                            y = self._view._height - y

                        depth = int(depth)
                        if tstamp:
                            logging.debug(
                                "[TouchUING] Touch %s at %s, %s", depth, x, y
                            )
                            self.process_touch([int(x), int(y)], depth)
                    logging.info("[TouchUING] ts_print exited")
                    self.running = False
                else:
                    logging.info("[TouchUING] No touchscreen?")
                time.sleep(1)
        except Exception as e:
            logging.info("[TouchUING] Handler: %s", repr(e))

    def on_webhook(self, path, request):
        # ADDED: a real status page - the original just logged that the
        # webhook was hit and returned nothing at all, which left no way
        # to check on the touchscreen's state remotely.
        logging.info("[TouchUING] webhook pressed")

        thread_alive = bool(self._ts_thread and self._ts_thread.is_alive())
        ts_active = self.touchscreen is not None
        last_touch = self._last_touch or "never"
        missing_packages = (
            ", ".join(self.needsAptPackages) if self.needsAptPackages else "none"
        )

        return (
            "<html><body><h1>TouchUING</h1>"
            f"<p>Reader thread running: {thread_alive}</p>"
            f"<p>Touchscreen process active: {ts_active}</p>"
            f"<p>Last touch seen: {last_touch}</p>"
            f"<p>Missing apt packages: {missing_packages}</p>"
            "</body></html>"
        )

    def on_loaded(self):
        logging.info("[TouchUING] plugin loaded")
        try:
            self.init_gpio()
        except Exception as e:
            logging.warning(repr(e))

        try:
            self.init_ts_handler()
        except Exception as e:
            logging.warning(repr(e))

        plugins.on("touch_ready", self)

    def on_unload(self, ui):
        try:
            self.keepGoing = False
            if self._ts_thread:
                if self.touchscreen:
                    logging.debug("[TouchUING] TERM to %s", self.touchscreen.pid)
                    os.kill(self.touchscreen.pid, signal.SIGTERM)
                logging.info("[TouchUING] Waiting for thread to exit")
                self._ts_thread.join()
                logging.info("[TouchUING] And its done.")
        except Exception as e:
            logging.error("%s", repr(e))

        try:
            i = 0
            for n in self._ui_elements:
                ui.remove_element(n)
                logging.info("[TouchUING] Removed %s", repr(n))
                i += 1
            if i:
                logging.info("[TouchUING] plugin unloaded %d elements", i)
        except Exception as e:
            logging.error("%s", repr(e))

        try:
            if "gpios" in self.options:
                for i in self.options["gpios"].values():
                    logging.info("[TouchUING] Stop detecting GPIO %s", repr(i))
                    GPIO.remove_event_detect(i)
        except Exception as e:
            logging.error("%s", repr(e))

    def on_internet_available(self, agent):
        if self.needsAptPackages:
            # FIXED: list.extend() mutates the list in place and returns
            # None - the original called
            # check_output(["apt","install","-y"].extend(self.needsAptPackages)),
            # which always evaluated to check_output(None) and crashed
            # every single time connectivity came up with any packages
            # pending, regardless of whether apt/those packages were
            # even available.
            try:
                check_output(["apt", "install", "-y"] + self.needsAptPackages)
                self.needsAptPackages = None
            except Exception as e:
                logging.warning("[TouchUING] apt install failed: %s", repr(e))

    def pointInBox(self, point, box):
        try:
            logging.info("[TouchUING] is %s in %s", repr(point), repr(box))
            return (
                point[0] >= box[0]
                and point[0] <= box[2]
                and point[1] >= box[1]
                and point[1] <= box[3]
            )
        except Exception as e:
            logging.info(repr(e))

    def collect_touch_elements(self):
        pass

    def process_touch(self, tpoint, depth):
        logging.info("[TouchUING] PT: %s: %s", repr(tpoint), repr(depth))

        touch_data = {"point": tpoint, "pressure": depth}
        # ADDED: last-touch timestamp, surfaced on the webhook status page.
        self._last_touch = time.strftime("%H:%M:%S")

        ui_elements = self._view._state._state
        touch_element = None
        touch_elements = list(
            filter(lambda x: hasattr(ui_elements[x], "state"), ui_elements.keys())
        )
        logging.info("[TouchUING] Touchable: %s", repr(touch_elements))
        # ADDED: whether this release follows a press held at least
        # longpress_seconds - drives the extra "touch_longpress" event
        # dispatch below.
        is_longpress = False
        try:
            if int(depth) > 0:
                command = "touch_move" if self._beingTouched else "touch_press"
                if command == "touch_press":
                    # ADDED: mark the start of a new press for long-press
                    # duration timing on release.
                    self._press_start = time.time()
                self._beingTouched = True
            elif int(depth) == 0:
                command = "touch_release"
                self._beingTouched = False
                if self._press_start is not None:
                    held = time.time() - self._press_start
                    if held >= self._opt("longpress_seconds"):
                        is_longpress = True
                self._press_start = None
            else:
                command = None

            for te in touch_elements:
                logging.info(
                    "[TouchUING] Touching %s, %s", te, repr(ui_elements[te].xy)
                )
                if self.pointInBox(tpoint, ui_elements[te].xy):
                    logging.debug(
                        "Touch element %s: %s @ %s", repr(te), depth, repr(tpoint)
                    )
                    touch_element = te
                    break
        except Exception as e:
            logging.warning(repr(e))

        if command:
            if touch_element:
                button = ui_elements[touch_element]
                if button.momentary:
                    if command == "touch_press" or command == "touch_release":
                        self._view._state._changes[touch_element] = True
                    button.state = self._beingTouched
                    # FIXED: the original checked a bare, undefined name
                    # `reverse` here (not `self.reverse`, not
                    # `button.reverse`) - referencing it raised
                    # NameError the moment a momentary button was ever
                    # touched. The button's own `reverse` attribute is
                    # what was clearly intended.
                    if button.reverse:
                        button.state = not button.state
                elif command == "touch_press":
                    button.state = not button.state
                    self._view._state._changes[touch_element] = True

                if getattr(button, "event_handler", None):
                    logging.info(
                        "UI_Element %s Command: %s, handler: %s, data: %s",
                        touch_element, command, button.event_handler, repr(touch_data),
                    )
                    plugins.one(
                        button.event_handler,
                        command,
                        self,
                        self._view,
                        touch_element,
                        touch_data,
                    )
                    if is_longpress:
                        # ADDED: long-press detection, dispatched as its
                        # own event alongside the normal touch_release,
                        # so a plugin that only cares about a deliberate
                        # long-press doesn't have to reimplement
                        # press-duration timing itself.
                        plugins.one(
                            button.event_handler,
                            "touch_longpress",
                            self,
                            self._view,
                            touch_element,
                            touch_data,
                        )
                else:
                    logging.info(
                        "UI_Element %s Command: %s, handler: %s, data: %s",
                        touch_element, command, None, repr(touch_data),
                    )
                    plugins.on(command, self, self._view, touch_element, touch_data)
                    if is_longpress:
                        plugins.on(
                            "touch_longpress", self, self._view, touch_element, touch_data
                        )
            else:
                logging.debug("Touch Command: %s, data: %s", command, repr(touch_data))
                plugins.on(command, self, self._view, touch_element, touch_data)
                if is_longpress:
                    plugins.on(
                        "touch_longpress", self, self._view, touch_element, touch_data
                    )

    # button handlers to cycle through touch areas and click (not implemented yet)
    def okButtonPress(self, button):
        logging.info("[TouchUING] OK Button pressed: %s", repr(button))

    def okButtonRelease(self, button):
        logging.info("[TouchUING] OK Button released: %s", repr(button))

    def backButtonPress(self, button):
        logging.info("[TouchUING] Back Button pressed: %s", repr(button))
        self.buttonCurrentZone = None

    def backButtonRelease(self, button):
        logging.info("[TouchUING] Back Button released: %s", repr(button))
        self.buttonCurrentZone = None

    def nextButtonPress(self, button):
        logging.info("[TouchUING] Next Button pressed: %s", repr(button))

    def nextButtonRelease(self, button):
        logging.info("[TouchUING] Next Button released: %s", repr(button))

    def prevButtonPress(self, button):
        logging.info("[TouchUING] Prev Button pressed: %s", repr(button))

    def prevButtonRelease(self, button):
        logging.info("[TouchUING] Prev Button released: %s", repr(button))

    def on_display_setup(self, display):
        pass

    def on_ready(self, agent):
        self._agent = agent

    def init_gpio(self):
        if "gpios" not in self.options:
            return
        GPIO.setmode(GPIO.BCM)
        button_map = {
            "ok": (self.okButtonPress, self.okButtonRelease),
            "next": (self.nextButtonPress, self.nextButtonRelease),
            "back": (self.backButtonPress, self.backButtonRelease),
            "prev": (self.prevButtonPress, self.prevButtonRelease),
        }
        for name, (press_cb, release_cb) in button_map.items():
            if name not in self.options["gpios"]:
                continue
            try:
                pin = int(self.options["gpios"][name])
                GPIO.setup(pin, GPIO.IN, GPIO.PUD_UP)
                GPIO.add_event_detect(
                    pin, GPIO.FALLING, callback=press_cb, bouncetime=600
                )
                GPIO.add_event_detect(
                    pin, GPIO.RISING, callback=release_cb, bouncetime=600
                )
            except Exception as e:
                logging.warning("%s button: %s", name, repr(e))

    def init_ts_handler(self):
        try:
            logging.info("[TouchUING] starting ts_print thread")
            self._ts_thread = threading.Thread(
                target=self.touchScreenHandler, args=(), daemon=True
            )
            if not self._ts_thread:
                logging.info("[TouchUING] Thread failed?")
            self._ts_thread.start()
            logging.info("[TouchUING] started thread, unit is ready")
        except Exception as e:
            logging.error(repr(e))

    def on_ui_setup(self, ui):
        self._view = ui

    def on_rebooting(self, agent):
        pass

    def on_wait(self, agent, t):
        pass

    def on_sleep(self, agent, t):
        pass
