"""Serialization helpers and fig.show() suppression for server context."""

import io
import base64
from contextlib import contextmanager

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import plotly.graph_objects as go


@contextmanager
def suppress_show():
    """Temporarily disable fig.show() for both Plotly and Matplotlib."""
    orig_plotly = go.Figure.show
    orig_plt = plt.show
    go.Figure.show = lambda self, *a, **kw: None
    plt.show = lambda *a, **kw: None
    try:
        yield
    finally:
        go.Figure.show = orig_plotly
        plt.show = orig_plt


def plotly_fig_to_json(fig) -> dict:
    """Convert a Plotly Figure to a JSON-serializable dict."""
    import json as _json
    return _json.loads(fig.to_json())


def matplotlib_fig_to_base64(fig_or_plt) -> str:
    """Render a Matplotlib Figure (or plt module) to a PNG base64 data URI string.

    Some fund.py methods return the plt module instead of a Figure object.
    """
    import matplotlib.figure
    buf = io.BytesIO()
    if isinstance(fig_or_plt, matplotlib.figure.Figure):
        fig_or_plt.savefig(buf, format="png", dpi=150, bbox_inches="tight")
        plt.close(fig_or_plt)
    else:
        # plt module was returned — use gcf() to get current figure
        plt.savefig(buf, format="png", dpi=150, bbox_inches="tight")
        plt.close("all")
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("utf-8")
    return f"data:image/png;base64,{b64}"
