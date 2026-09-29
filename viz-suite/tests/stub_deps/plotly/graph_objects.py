"""Minimal stand-in for plotly.graph_objects, test-only (see __init__.py)."""


class Scatter:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
