"""
handshakes_dl_ng.py - pwnagotchi plugin (runs ON the pi)

Rewrite of handshakes-dl-hashie.py (me@sayakb.com / itsdarklikehell),
fixed for the jayofelony 64-bit fork, plus several usability additions.
Supersedes both the original handshakes-dl-hashie.py and the plain
handshakes-dl.py (which handshakes-dl-hashie.py already made redundant -
see plugin-upgrade-proposals/cluster-29-handshakes-dl/NOTES.md in the
test-plugins repo for that history).

Adds a page to the pwnagotchi's web UI listing every captured handshake,
with per-capture download links for the raw capture, any already-
converted hash file (.2500/.16800/.22000), and any GPS file
(.gps.json/.geo.json) - plus a "download all as ZIP" button.

Bugs fixed vs. the original:
  1. Filtered for ".pcap" throughout (glob, filename-length math) - this
     fork only ever writes ".pcapng", so the page always showed nothing.
     Fixed to use ".pcapng" and os.path.splitext() instead of a
     hardcoded-length slice.
  2. No file size, capture date, or sort order - files came back in
     whatever order glob() happened to return them, with zero context
     per row.
  3. No cap - an unbounded list could get slow to render on a folder with
     hundreds of captures over time.
  4. No bulk-download option - every file had to be clicked individually.
  5. No GPS visibility - a plugin like the ones already tracked in this
     project's GPS/Location category may write a .gps.json/.geo.json
     alongside a capture, but the original page never surfaced it.

Deliberately NOT added: anything about cracked-password content or
hash-cracking status - that's wpa-sec-list.py's job (a plugin already
being tracked separately), and duplicating it here would create two
places to maintain the same feature.

See config.toml.example in this folder for what to add to config.toml,
and README.md for full install/usage notes.
"""

import glob
import io
import logging
import os
import zipfile

import pwnagotchi
import pwnagotchi.plugins as plugins

from flask import Response, abort, render_template_string, send_from_directory

TEMPLATE = """
{% extends "base.html" %}
{% set active_page = "handshakes" %}

{% block title %}
    {{ title }}
{% endblock %}

{% block styles %}
    {{ super() }}
    <style>
        #filter {
            width: 100%;
            font-size: 16px;
            padding: 12px 20px 12px 40px;
            border: 1px solid #ddd;
            margin-bottom: 12px;
            box-sizing: border-box;
        }
        table.handshakes { width: 100%; border-collapse: collapse; }
        table.handshakes th, table.handshakes td {
            text-align: left; padding: 6px 8px; border-bottom: 1px solid #eee;
            font-size: 14px;
        }
        table.handshakes .exts a { margin-right: 10px; white-space: nowrap; }
        .dl-all { display: inline-block; margin-bottom: 12px; padding: 8px 14px; }
        .truncated-note { color: #a55; margin-bottom: 12px; }
    </style>
{% endblock %}

{% block script %}
    var filter = document.getElementById('filter');
    var rows = document.querySelectorAll('#handshakes-table tbody tr');
    filter.onkeyup = function() {
        var filterVal = filter.value.toUpperCase();
        rows.forEach(function(row) {
            var txt = row.textContent || row.innerText;
            row.style.display = txt.toUpperCase().indexOf(filterVal) > -1 ? "" : "none";
        });
    }
{% endblock %}

{% block content %}
    <a class="dl-all" href="/plugins/handshakes_dl_ng/download-all.zip">Download all shown ({{ handshakes|length }}) as ZIP</a>
    {% if truncated %}
        <div class="truncated-note">
            Showing the newest {{ handshakes|length }} of {{ total_count }} captures.
            Raise <code>max_results</code> in config.toml to see more.
        </div>
    {% endif %}
    <input type="text" id="filter" placeholder="Search for ..." title="Type in a filter">
    <table class="handshakes" id="handshakes-table">
        <thead>
            <tr><th>Name</th><th>Size</th><th>Captured</th><th>Files</th></tr>
        </thead>
        <tbody>
        {% for hs in handshakes %}
            <tr>
                <td>{{ hs.name }}</td>
                <td>{{ hs.size_human }}</td>
                <td>{{ hs.captured_at }}</td>
                <td class="exts">
                    {% for ext, label in hs.download_links %}
                        <a href="/plugins/handshakes_dl_ng/{{ hs.name }}{{ ext }}">{{ label }}</a>
                    {% endfor %}
                </td>
            </tr>
        {% endfor %}
        </tbody>
    </table>
{% endblock %}
"""

HASH_EXTS = [".2500", ".16800", ".22000"]
GPS_EXTS = [".gps.json", ".geo.json"]

EXT_LABELS = {
    ".pcapng": "capture",
    ".2500": "hash (2500)",
    ".16800": "hash (16800/PMKID)",
    ".22000": "hash (22000)",
    ".gps.json": "GPS",
    ".geo.json": "GPS",
}


class _Handshake:
    def __init__(self, name, size_bytes, mtime, exts):
        self.name = name
        self.size_bytes = size_bytes
        self.mtime = mtime
        self.exts = exts

    @property
    def size_human(self):
        size = float(self.size_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
            size /= 1024

    @property
    def captured_at(self):
        import datetime

        return datetime.datetime.fromtimestamp(self.mtime).strftime("%Y-%m-%d %H:%M")

    @property
    def download_links(self):
        return [(ext, EXT_LABELS.get(ext, ext)) for ext in self.exts]


class HandshakesDLNG(plugins.Plugin):
    __author__ = "me@sayakb.com (original); rewritten for jayofelony fork"
    __version__ = "2.0.0"
    __license__ = "GPL3"
    __description__ = (
        "Web-UI page to browse and download handshake captures, their "
        "converted hash files, and any GPS data - with file size/date, "
        "a newest-first cap, and a download-all-as-ZIP button."
    )
    __name__ = "HandshakesDLNG"
    __help__ = __description__
    __dependencies__ = {
        "apt": ["none"],
        "pip": [],
    }
    # NOTE: this fork's plugin loader does NOT read __defaults__ - set
    # every option explicitly in config.toml. See config.toml.example.
    __defaults__ = {
        "enabled": False,
        "max_results": 200,
    }

    def __init__(self):
        self.ready = False
        self.config = None

    def on_loaded(self):
        logging.info(f"[{self.__class__.__name__}] plugin loaded")

    def on_config_changed(self, config):
        self.config = config
        self.ready = True

    # ------------------------------------------------------------------

    def _handshake_dir(self):
        return self.config["bettercap"]["handshakes"]

    def _collect(self):
        handshake_dir = self._handshake_dir()
        pcapng_files = glob.glob(os.path.join(handshake_dir, "*.pcapng"))

        results = []
        for full_path in pcapng_files:
            base = os.path.basename(full_path)
            name = base[: -len(".pcapng")]
            full_no_ext = full_path[: -len(".pcapng")]

            exts = [".pcapng"]
            for ext in HASH_EXTS:
                if os.path.isfile(full_no_ext + ext):
                    exts.append(ext)
            for ext in GPS_EXTS:
                if os.path.isfile(full_no_ext + ext):
                    exts.append(ext)
                    break  # only ever one GPS source per capture

            try:
                size_bytes = os.path.getsize(full_path)
                mtime = os.path.getmtime(full_path)
            except OSError:
                continue

            results.append(_Handshake(name, size_bytes, mtime, exts))

        results.sort(key=lambda h: h.mtime, reverse=True)
        return results

    def on_webhook(self, path, request):
        if not self.ready:
            return "Plugin not ready"

        if path == "/" or not path:
            all_handshakes = self._collect()
            max_results = int(self.options.get("max_results", 200))
            total_count = len(all_handshakes)
            handshakes = all_handshakes[:max_results]
            return render_template_string(
                TEMPLATE,
                title="Handshakes | " + pwnagotchi.name(),
                handshakes=handshakes,
                total_count=total_count,
                truncated=total_count > len(handshakes),
            )

        if path == "download-all.zip":
            return self._download_all_zip()

        handshake_dir = self._handshake_dir()
        try:
            logging.info(f"[{self.__class__.__name__}] serving {handshake_dir}/{path}")
            return send_from_directory(directory=handshake_dir, path=path, as_attachment=True)
        except FileNotFoundError:
            abort(404)

    def _download_all_zip(self):
        max_results = int(self.options.get("max_results", 200))
        handshakes = self._collect()[:max_results]
        handshake_dir = self._handshake_dir()

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for hs in handshakes:
                for ext in hs.exts:
                    full_path = os.path.join(handshake_dir, hs.name + ext)
                    if os.path.isfile(full_path):
                        zf.write(full_path, arcname=hs.name + ext)
        buf.seek(0)

        return Response(
            buf.read(),
            mimetype="application/zip",
            headers={"Content-Disposition": "attachment; filename=handshakes.zip"},
        )
