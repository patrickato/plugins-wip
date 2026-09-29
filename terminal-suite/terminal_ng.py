import ipaddress
import logging
import os
import re
import subprocess
import time

import pwnagotchi.plugins as plugins
from flask import render_template_string

WEBSSH2_SERV = """
[Unit]
Description=Access Webssh2
After=default.target

[Service]
ExecStart=/bin/bash WebSSH2
Restart=always
User=pi
Group=pi

[Install]
WantedBy=default.target
"""

SERV_PATH = "/etc/systemd/system/webssh2.service"

TEMPLATE = """
{% extends "base.html" %}
{% block styles %}
{{ super() }}
<style>
#term-container {
    margin: 0;
    padding: 0;
    border: none;
}
</style>
{% endblock %}

{% block content %}
    <div id="term-container">
        {% if allowed %}
            <iframe style="width:calc(100vw - 10px); height:calc(100vh - 47px);" id="term-iframe" src="{{ ws_url }}" scrolling="no"></iframe>
        {% else %}
            <p>Access to the terminal is restricted to <code>{{ allowed_networks }}</code>. Your address is <code>{{ remote_addr }}</code>.</p>
        {% endif %}
    </div>
{% endblock %}
"""


class TerminalNG(plugins.Plugin):
    __author__ = "rebuilt from NeonLightning's terminal2.py (WebSSH2Plugin)"
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = "WebSSH2 in-browser terminal access, reachable through the pwnagotchi web UI."

    DEFAULTS = {
        "enabled": False,
        # CIDR ranges allowed to load the terminal iframe. Defaults to the
        # common private ranges instead of the original's two hardcoded,
        # author-specific IPs (10.0.0.0/24 and 192.168.44.44/32).
        "allowed_networks": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
        "ws_host": "127.0.0.1",
        "ws_port": 2222,
        "startup_poll_attempts": 12,
        "startup_poll_interval": 5,
    }

    def __init__(self):
        self.ready = False

    def _opt(self, key):
        return self.options.get(key, self.DEFAULTS[key])

    def on_loaded(self):
        logging.info("[TerminalNG] plugin loading")
        if not os.path.exists(SERV_PATH):
            logging.info("[TerminalNG] creating systemd unit file")
            self._write_unit_file()
            os.system("sudo systemctl enable webssh2.service")
            os.system("sudo systemctl start webssh2.service")
            self._wait_for_service()
        else:
            logging.info("[TerminalNG] systemd unit file already exists, skipping creation")
            output = self._service_status_output("is-active")
            if output.strip() != "active":
                os.system("sudo systemctl disable webssh2.service")
                self._write_unit_file()
                os.system("sudo systemctl enable webssh2.service")
                os.system("sudo systemctl start webssh2.service")
            else:
                self.ready = True

    def _write_unit_file(self):
        with open(SERV_PATH, "w") as f:
            f.write(WEBSSH2_SERV)

    @staticmethod
    def _service_status_output(subcommand):
        try:
            return subprocess.check_output(
                ["systemctl", subcommand, "webssh2.service"]
            ).decode("utf-8")
        except subprocess.CalledProcessError as e:
            # `systemctl is-active`/`status` exit non-zero for an inactive
            # service - that's a normal outcome here, not a real error.
            return e.output.decode("utf-8") if e.output else ""

    def _wait_for_service(self):
        for _ in range(int(self._opt("startup_poll_attempts"))):
            output = self._service_status_output("status")
            if "Active: active" in output and "listening on" in output:
                ip_address = self.extract_ip_address(output)
                if ip_address is not None:
                    self.ready = True
                    logging.info("[TerminalNG] service started successfully on %s", ip_address)
                    return
            time.sleep(int(self._opt("startup_poll_interval")))

        logging.error("[TerminalNG] failed to confirm service startup within the polling window")

    @staticmethod
    def extract_ip_address(output):
        match = re.search(r"listening on (\S+)", output)
        if match:
            return match.group(1)
        return None

    def on_unload(self, ui):
        logging.info("[TerminalNG] plugin unloading")
        os.system("sudo systemctl stop webssh2.service")
        os.system("sudo systemctl disable webssh2.service")
        os.system("sudo rm -f {}".format(SERV_PATH))
        logging.info("[TerminalNG] plugin stopped")
        self.ready = False

    def _is_allowed(self, remote_addr):
        if not remote_addr:
            return False
        try:
            addr = ipaddress.ip_address(remote_addr)
        except ValueError:
            return False

        for net in self._opt("allowed_networks"):
            try:
                if addr in ipaddress.ip_network(net, strict=False):
                    return True
            except ValueError:
                logging.warning("[TerminalNG] invalid allowed_networks entry: %s", net)
        return False

    def on_webhook(self, path, request):
        remote_addr = request.remote_addr
        allowed = self._is_allowed(remote_addr)
        ws_url = "http://{host}:{port}/ssh/host/127.0.0.1?port=22".format(
            host=self._opt("ws_host"), port=self._opt("ws_port")
        )
        return render_template_string(
            TEMPLATE,
            allowed=allowed,
            ws_url=ws_url,
            allowed_networks=", ".join(self._opt("allowed_networks")),
            remote_addr=remote_addr,
        )
