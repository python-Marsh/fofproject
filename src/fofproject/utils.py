from datetime import datetime
from typing import Union
import signal
import threading

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import plotly.io as pio
import pandas as pd

class GracefulCycle:
    """Context manager that catches SIGINT/SIGTERM for graceful mid-cycle shutdown.

    Usage::

        with GracefulCycle() as gc:
            for item in items:
                if gc.should_stop:
                    break
                process(item)
                save_progress()

    On the first interrupt, ``should_stop`` is set so the current iteration
    can finish and state can be saved.  A second interrupt raises
    ``KeyboardInterrupt`` immediately so the process isn't stuck.
    """

    def __init__(self, logger=None, phase: str = ""):
        self.should_stop = False
        self._logger = logger
        self._phase = phase
        self._original_sigint = None
        self._original_sigterm = None
        self._interrupted_count = 0
        self._lock = threading.Lock()

    def _handler(self, signum, frame):
        with self._lock:
            self._interrupted_count += 1
            if self._interrupted_count >= 2:
                # Second interrupt — raise immediately
                raise KeyboardInterrupt
            self.should_stop = True
            sig_name = signal.Signals(signum).name
            if self._logger:
                self._logger.info(
                    f"Received {sig_name}, finishing current item then stopping. "
                    "Press Ctrl+C again to force quit.",
                    phase=self._phase,
                )
            else:
                print(
                    f"\n[{sig_name}] Finishing current item then stopping. "
                    "Press Ctrl+C again to force quit."
                )

    def __enter__(self):
        self._original_sigint = signal.getsignal(signal.SIGINT)
        self._original_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGINT, self._handler)
        signal.signal(signal.SIGTERM, self._handler)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        signal.signal(signal.SIGINT, self._original_sigint)
        signal.signal(signal.SIGTERM, self._original_sigterm)
        return False


def in_notebook():
    try:
        from IPython import get_ipython
        return 'IPKernelApp' in get_ipython().config
    except Exception:
        return False


def parse_month(mstr: Union[str, datetime]) -> datetime:
    if isinstance(mstr, str):
        return datetime.strptime(mstr, "%Y-%m")
    return mstr


def list_of_dicts_to_df(lst, value_col_name):
    """Convert list of dicts to a DataFrame keyed by 'month'."""
    df = pd.DataFrame(lst)
    return df[["month", "value"]].rename(columns={"value": value_col_name})


def hex_to_rgba(hex_color: str, alpha: float = 0.2) -> str:
    """Convert hex color like '#RRGGBB' to rgba(R,G,B,alpha)."""
    hex_color = hex_color.lstrip("#")
    r, g, b = tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def compute_identifier(performance_data):
    """Compute a fund identifier from the first 5 months of performance data.

    Takes the last 2 decimal digits of each of the first 5 monthly returns
    and concatenates them. For example, if the first 5 returns are
    3.17%, 0.01%, -0.51%, 3.33%, -7.81% (stored as 0.0317, 0.0001, -0.0051,
    0.0333, -0.0781), the identifier is "1701513381".

    The values are sorted chronologically (earliest first), so we take the
    first 5 dates in ascending order.
    """
    if not isinstance(performance_data, list) or len(performance_data) < 5:
        return ""

    # Sort by date ascending and take first 5
    sorted_perf = sorted(
        performance_data, key=lambda x: datetime.strptime(x["date"], "%d/%m/%Y")
    )
    first_five = sorted_perf[:5]

    identifier_parts = []
    for entry in first_five:
        # Multiply by 100 to get percentage, e.g. 0.0317 -> 3.17
        pct_value = entry["value"] * 100
        # Format to 2 decimal places and take last 2 digits of the decimal
        formatted = f"{abs(pct_value):.2f}"
        # Get the 2 decimal digits (after the dot)
        decimal_part = formatted.split(".")[1]
        identifier_parts.append(decimal_part)

    identifier = "".join(identifier_parts)
    # Ensure identifier is always exactly 10 digits
    return identifier[:10].ljust(10, "0")

