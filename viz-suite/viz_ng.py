import json
import logging
import random
import time
from functools import lru_cache
from math import pi, cos, sin
from threading import Lock

import plotly
import plotly.graph_objects as go
from flask import render_template_string, abort, jsonify

import pwnagotchi.plugins as plugins

# This fork's loader does NOT merge a plugin's __defaults__ into
# self.options - every option must be read with a real fallback.
DEFAULTS = {
    "enabled": False,
    # ADDED: how often the webhook page polls for new graph/meta data,
    # in milliseconds. The original hardcoded this to 5000 in the
    # page's own JavaScript.
    "poll_interval_ms": 5000,
    # ADDED: where to read CrackHouseNG's merged cracked-network list
    # from, so already-cracked networks can be marked on the graph.
    # Defaults to CrackHouseNG's own default saving_path. Set to an
    # empty string to disable cross-referencing entirely.
    "crack_house_saving_path": "/root/handshakes/crack_house_ng.potfile",
}

# FIXED: the original imported "from pwnagotchi.wifi import
# freq_to_channel" - that module path does not exist anywhere on this
# fork (confirmed: there is no pwnagotchi/wifi.py at all; the real
# module lives at pwnagotchi/mesh/wifi.py). Every attempt to load this
# plugin failed at import time with ModuleNotFoundError, before a
# single line of the plugin's own code ever ran - this is a bigger bug
# than the uninitialized-self.channel issue originally flagged, and
# supersedes it: the plugin could never load on this fork at all.
from pwnagotchi.mesh.wifi import freq_to_channel

TEMPLATE = """
{% extends "base.html" %}

{% set active_page = "plugins" %}

{% block title %}
    Viz
{% endblock %}

{% block scripts %}
    {{ super() }}
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
{% endblock %}

{% block script %}
    $(document).ready(function(){
        var hasData = false;
        var ajaxDataRenderer = function(url, plot, options) {
        var ret = null;

        $.ajax({
            async: false,
            url: url,
            dataType:"json",
            success: function(data) {
                ret = JSON.parse(data);
            }
        });
        return ret;
        };

        function loadGraphData() {
            var layout = {
                title: 'Viz Map',
                hovermode: 'closest',
                showlegend: false,
                xaxis: {
                    title: {
                        text: 'Signal',
                    },
                },
                yaxis: {
                    title: {
                        text: 'Channel',
                    },
                    tickmode: 'linear',
                    tick0: 1,
                    dtick: 1
                }
            };
            var result = ajaxDataRenderer('/plugins/viz/update');
            if (Array.isArray(result) && Object.keys(result).length > 0) {
                if (hasData == false) {
                    $('#plot').text('');
                    Plotly.newPlot('plot', result, layout);
                    hasData = true;
                } else {
                    Plotly.animate('plot', {
                        data: result,
                        layout: layout
                    }, {
                    transition: {
                        duration: 1000,
                        easing: 'cubic-in-out'
                    },
                    frame: {
                        duration: 1000
                    }
                    })
                }
            }
        }
        loadGraphData();
        setInterval(loadGraphData, __POLL_INTERVAL_MS__);

        function loadMetaData() {
            $.ajax({
                async: false,
                url: '/plugins/viz/meta',
                dataType: 'json',
                success: function(data) {
                    $('#lastUpdate').text(data.last_update || 'never');
                }
            });
        }
        loadMetaData();
        setInterval(loadMetaData, __POLL_INTERVAL_MS__);
    });
{% endblock %}

{% block content %}
    <div class="chart" id="plot">
        Waiting for data...
    </div>
    <p>Last updated: <span id="lastUpdate">never</span></p>
{% endblock %}
"""


class VizNG(plugins.Plugin):
    __author__ = "fixed/extended by this project's plugin audit, from itsdarklikehell/dadav's viz.py"
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = "Visualizes the surrounding APs and their clients as a graph."
    __name__ = "VizNG"
    __help__ = __description__

    COLORS = [
        "aliceblue", "aqua", "aquamarine", "azure", "beige", "bisque",
        "black", "blanchedalmond", "blue", "blueviolet", "brown",
        "burlywood", "cadetblue", "chartreuse", "chocolate", "coral",
        "cornflowerblue", "cornsilk", "crimson", "cyan", "darkblue",
        "darkcyan", "darkgoldenrod", "darkgray", "darkgrey", "darkgreen",
        "darkkhaki", "darkmagenta", "darkolivegreen", "darkorange",
        "darkorchid", "darkred", "darksalmon", "darkseagreen",
        "darkslateblue", "darkslategray", "darkslategrey",
        "darkturquoise", "darkviolet", "deeppink", "deepskyblue",
        "dimgray", "dimgrey", "dodgerblue", "firebrick", "forestgreen",
        "fuchsia", "gainsboro", "gold", "goldenrod", "gray", "grey",
        "green", "greenyellow", "honeydew", "hotpink", "indianred",
        "indigo", "ivory", "khaki", "lavender", "lavenderblush",
        "lawngreen", "lemonchiffon", "lightblue", "lightcoral",
        "lightcyan", "lightgoldenrodyellow", "lightgray", "lightgrey",
        "lightgreen", "lightpink", "lightsalmon", "lightseagreen",
        "lightskyblue", "lightslategray", "lightslategrey",
        "lightsteelblue", "lightyellow", "lime", "limegreen", "linen",
        "magenta", "maroon", "mediumaquamarine", "mediumblue",
        "mediumorchid", "mediumpurple", "mediumseagreen",
        "mediumslateblue", "mediumspringgreen", "mediumturquoise",
        "mediumvioletred", "midnightblue", "mintcream", "mistyrose",
        "moccasin", "navy", "oldlace", "olive", "olivedrab", "orange",
        "orangered", "orchid", "palegoldenrod", "palegreen",
        "paleturquoise", "palevioletred", "papayawhip", "peachpuff",
        "peru", "pink", "plum", "powderblue", "purple", "red",
        "rosybrown", "royalblue", "rebeccapurple", "saddlebrown",
        "salmon", "sandybrown", "seagreen", "seashell", "sienna",
        "silver", "skyblue", "slateblue", "slategray", "slategrey",
        "snow", "springgreen", "steelblue", "tan", "teal", "thistle",
        "tomato", "turquoise", "violet", "wheat", "yellow",
        "yellowgreen",
    ]
    COLOR_MEMORY = dict()

    def __init__(self):
        self.data = None
        # FIXED: the original never initialized self.channel in
        # __init__ - it was only ever set inside on_channel_hop(). The
        # /plugins/viz/update webhook (on_webhook -> create_graph) reads
        # self.channel unconditionally, so hitting that endpoint before
        # the first channel-hop event ever fired (plausible right after
        # boot) raised an unhandled AttributeError, 500ing the request.
        self.channel = None
        self.lock = Lock()
        # ADDED: tracks when on_unfiltered_ap_list last actually stored
        # new data, surfaced on the webhook page so it's obvious at a
        # glance whether the graph is stale.
        self._last_update = None

    def _opt(self, key):
        # getattr rather than a direct self.options.get(...): the real
        # framework always assigns self.options before any hook fires,
        # but a plugin instance can legitimately exist without one yet
        # (e.g. freshly constructed in a test), so this stays safe
        # either way instead of raising AttributeError.
        return getattr(self, "options", {}).get(key, DEFAULTS[key])

    def _poll_interval_ms(self):
        # ADDED: guard against a misconfigured (zero/negative/non-numeric)
        # poll interval hammering the webhook with a busy-loop of AJAX
        # calls, or a literal "0" landing straight in the page's HTML.
        interval = self._opt("poll_interval_ms")
        try:
            interval = int(interval)
            if interval <= 0:
                raise ValueError
        except (TypeError, ValueError):
            logging.warning(
                "[VizNG] invalid poll_interval_ms %r, falling back to the "
                "default (%s ms)", interval, DEFAULTS["poll_interval_ms"],
            )
            return DEFAULTS["poll_interval_ms"]
        return interval

    def _cracked_hostnames(self):
        # ADDED: cross-references CrackHouseNG's own merged cracked-list
        # file (there's no other shared-state mechanism for plugins on
        # this fork) so already-cracked networks can be marked on the
        # graph. Read fresh on every webhook hit rather than cached, so
        # it always reflects CrackHouseNG's latest known list; this is a
        # plain small text file read, not worth caching.
        saving_path = self._opt("crack_house_saving_path")
        if not saving_path:
            return frozenset()
        cracked = set()
        try:
            with open(saving_path) as f:
                for line in f:
                    hostname, _, _ = line.rstrip().partition(":")
                    if hostname:
                        cracked.add(hostname.lower())
        except FileNotFoundError:
            pass
        except Exception as e:
            logging.debug(
                "[VizNG] couldn't read crack_house_saving_path %s: %s",
                saving_path, e,
            )
        return frozenset(cracked)

    def on_loaded(self):
        logging.info("[VizNG] plugin loaded")

    @staticmethod
    def lookup_color(node):
        random.seed(node)
        if node not in VizNG.COLOR_MEMORY:
            VizNG.COLOR_MEMORY[node] = random.choice(VizNG.COLORS)
        return VizNG.COLOR_MEMORY[node]

    @staticmethod
    def random_pos(name, x0, y0, r):
        random.seed(name)
        t = 2 * pi * random.random()
        x = r * cos(t)
        y = r * sin(t)
        return x + x0, y + y0

    @staticmethod
    @lru_cache(maxsize=13)
    def create_graph(data, channel=None, cracked=frozenset()):
        if not data:
            return "{}"

        data = json.loads(data)

        node_text = list()
        edge_x = list()
        edge_y = list()
        node_x = list()
        node_y = list()
        node_symbols = list()
        node_sizes = list()
        node_colors = list()

        for ap_data in data:
            name = ap_data["hostname"] or ap_data["vendor"] or ap_data["mac"]
            color = VizNG.lookup_color(name)
            x, y = abs(ap_data["rssi"]), freq_to_channel(ap_data["frequency"])
            node_x.append(x)
            node_y.append(y)
            # ADDED: mark/color nodes that are already in CrackHouseNG's
            # cracked list, cross-referenced case-insensitively to match
            # CrackHouseNG's own case-insensitive matching addition -
            # so "MyLab" is recognized whether the potfile has it as
            # "MyLab", "mylab", or "MYLAB".
            is_cracked = name.lower() in cracked
            node_text.append(f"{name} [CRACKED]" if is_cracked else name)
            node_symbols.append("star" if is_cracked else "square")
            node_sizes.append(15 + len(ap_data["clients"]) * 3)
            node_colors.append(color)

            for c in ap_data["clients"]:
                cname = c["hostname"] or c["vendor"] or c["mac"]
                xx, yy = VizNG.random_pos(cname, x, y, 3)
                node_x.append(xx)
                node_y.append(yy)
                node_text.append(cname)
                node_symbols.append("circle")
                node_sizes.append(10)
                node_colors.append(color)

                edge_x.append(x)
                edge_x.append(xx)
                edge_x.append(None)
                edge_y.append(y)
                edge_y.append(yy)
                edge_y.append(None)

        edge_trace = go.Scatter(
            x=edge_x,
            y=edge_y,
            line=dict(width=1, color="#888"),
            hoverinfo="none",
            mode="lines",
        )

        node_trace = go.Scatter(
            x=node_x,
            y=node_y,
            mode="markers",
            marker=dict(
                size=node_sizes,
                color=node_colors,
                symbol=node_symbols,
            ),
            hovertext=node_text,
            hoverinfo="text",
        )

        channel_line = (
            go.Scatter(
                mode="lines",
                line=dict(width=15, color="#ff0000"),
                x=[min(node_x) - 5, max(node_x) + 5],
                y=[channel, channel],
                opacity=0.25,
                hoverinfo="none",
            )
            if channel
            else dict()
        )

        return json.dumps(
            (channel_line, edge_trace, node_trace), cls=plotly.utils.PlotlyJSONEncoder
        )

    def on_unfiltered_ap_list(self, agent, data):
        with self.lock:
            data = sorted(data, key=lambda k: k["mac"])
            self.data = json.dumps(data)
            # ADDED: last-updated timestamp, surfaced via the "meta"
            # webhook path and shown on the page itself.
            self._last_update = time.strftime("%Y-%m-%d %H:%M:%S")

    def on_channel_hop(self, agent, channel):
        with self.lock:
            self.channel = channel

    def on_webhook(self, path, request):
        if not path or path == "/":
            # ADDED: configurable poll interval substituted into the
            # page's JavaScript, replacing the original's hardcoded
            # 5000ms literal. Plain string replace (not Jinja/format)
            # since the template is full of its own "{" characters.
            html = TEMPLATE.replace(
                "__POLL_INTERVAL_MS__", str(self._poll_interval_ms())
            )
            return render_template_string(html)

        if path == "update":
            with self.lock:
                cracked = self._cracked_hostnames()
                g = VizNG.create_graph(self.data, self.channel, cracked)
                return jsonify(g)

        if path == "meta":
            # ADDED: last-updated timestamp endpoint, polled by the page.
            with self.lock:
                return jsonify({"last_update": self._last_update or "never"})

        abort(404)
