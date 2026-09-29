"""Minimal stand-in for the real `plotly` package, used only so
VizNG's module can be imported and exercised in a sandbox without
network access to install the real (fairly heavy) plotly dependency.
Not shipped - the real plugin genuinely requires real plotly on
actual hardware. Only covers what VizNG's create_graph() touches.
"""
import json


class utils:
    class PlotlyJSONEncoder(json.JSONEncoder):
        def default(self, o):
            if hasattr(o, "__dict__"):
                return o.__dict__
            return super().default(o)
