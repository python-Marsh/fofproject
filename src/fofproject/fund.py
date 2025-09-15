from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Union

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from matplotlib.font_manager import FontProperties
from pyfonts import load_google_font

from fofproject.utils import hex_to_rgba, list_of_dicts_to_df, parse_month

current_dir = Path(__file__).parent
save_dir = current_dir.parent.parent / "output"
if not save_dir.exists():
    save_dir.mkdir(parents=True, exist_ok=True)


def get_font2height():
    font2height = {}
    for font_size in range(1, 40):
        fig, ax = plt.subplots()
        text = ax.text(
            0,
            0,
            "1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZ",
            fontsize=font_size,
            fontname="Arial",
        )
        # Draw the figure to ensure renderer is available
        fig.canvas.draw()
        fig.set_dpi(200)  # Set a specific DPI for consistency
        # Get the bounding box in display (pixel) coordinates
        bbox = text.get_window_extent(renderer=fig.canvas.get_renderer())
        height_in_inches = bbox.height / fig.dpi
        font2height[font_size] = height_in_inches
    return font2height


FONT2HEIGHT = get_font2height()

FONT_FNAME = {
    "en": {
        "bold": "src/fofproject/font/Roboto/static/Roboto-Bold.ttf",
        "regular": "src/fofproject/font/Roboto/static/Roboto-Regular.ttf",
    },
    "cn": {
        "bold": "src/fofproject/font/Roboto/static/Roboto-Bold.ttf",
        "regular": "src/fofproject/font/Roboto/static/Roboto-Regular.ttf",
    },
}


def find_largest_font_size(target_height, font2height):
    """Find the largest font size that fits within the target height."""
    for font_size, height in reversed(font2height.items()):
        if height <= target_height:
            return font_size
    return None


def input_monthly_returns(file_path, performance_fee=0.2, management_fee=0.01):
    """Read monthly returns from a CSV file and create Fund instances."""
    # Read CSV file
    df = pd.read_csv(file_path)
    funds = {}
    for col in df.columns:
        if col == "date":
            continue  # skip the date column

        returns = [
            {"date": d, "value": v} for d, v in zip(df["date"], df[col]) if pd.notna(v)
        ]

        # Create a Fund instance
        funds[col] = Fund(
            name=col,
            monthly_returns=returns,
            performance_fee=performance_fee,
            management_fee=management_fee,
        )
    return funds


def subset_of_funds(funds, keys=None):
    """funds: dict of Fund instances;
    keys: list of fund names to extract"""
    default = ["RDGFF", "MSCI CHINA", "MSCI GLOBAL"]
    if keys is None:
        keys = default
    funds_to_be_plot = {k: funds.get(k, None) for k in keys}  # or a custom default
    return funds_to_be_plot


class Fund:
    def __init__(
        self,
        name: str,
        monthly_returns: List[Dict],
        performance_fee: float,
        management_fee: float,
    ):
        """Initialize a Fund object.

        Args:
            name (str): Name of the fund
            monthly_returns (List[Dict]): List of monthly returns with 'date' and 'value' keys
            performance_fee (float): Performance fee as a decimal (e.g., 0.2 for 20%)
            management_fee (float): Management fee as a decimal (e.g., 0.01 for 1%)
        """
        processed_returns = []
        for entry in monthly_returns:
            raw_date = entry["date"]
            # Try parsing date in 'DD/MM/YYYY' format
            dt = datetime.strptime(str(raw_date), "%d/%m/%Y")
            processed_returns.append(
                {
                    "datetime": dt,
                    "month": datetime(dt.year, dt.month, 1),
                    "value": entry["value"],
                }
            )
        self.name = name
        self.monthly_returns = processed_returns
        self.performance_fee = performance_fee
        self.management_fee = management_fee
        self.inception_date = self.compute_inception_date()
        self.latest_date = self.compute_latest_date()
        self.num_months = len(self.monthly_returns)
        self.total_cum_rtn = (
            self.cumulative_return(self.inception_date, self.latest_date)
            if self.monthly_returns
            else None
        )
        self.total_ann_rtn = (
            self.annualized_return(self.inception_date, self.latest_date)
            if self.monthly_returns
            else None
        )
        self.total_vol = (
            self.volatility(self.inception_date, self.latest_date)
            if self.monthly_returns
            else None
        )
        self.total_sharpe = (
            self.sharpe_ratio(self.inception_date, self.latest_date)
            if self.monthly_returns
            else None
        )
        self.total_sortino = (
            self.sortino_ratio(self.inception_date, self.latest_date)
            if self.monthly_returns
            else None
        )
        self.total_max_dd = (
            self.max_drawdown(self.inception_date, self.latest_date)
            if self.monthly_returns
            else None
        )
        self.total_pos_months = (
            self.positive_months(self.inception_date, self.latest_date)
            if self.monthly_returns
            else None
        )

    def get_monthly_return(self, year: int, month: int):
        target = datetime(year, month, 1)
        for entry in self.monthly_returns:
            if entry["month"] == target:
                return entry["value"]
        return None

    def compute_inception_date(self):
        return (
            min(entry["month"] for entry in self.monthly_returns)
            if self.monthly_returns
            else None
        )

    def compute_latest_date(self):
        return (
            max(entry["month"] for entry in self.monthly_returns)
            if self.monthly_returns
            else None
        )

    def __repr__(self):
        return (
            f"Fund(performance_fee={self.performance_fee}, "
            f"management_fee={self.management_fee}, "
            f"monthly_returns={len(self.monthly_returns)} entries)"
        )

    def cumulative_return(
        self, start_month: Union[str, datetime], end_month: Union[str, datetime]
    ) -> float:
        """
        Calculates cumulative value from start_month to end_month (inclusive).

        Parameters
        ----------
        start_month : str
            Month string in 'YYYY-MM' or 'YYYY-M' format (e.g. '2024-7').
        end_month : str
            Month string in 'YYYY-MM' or 'YYYY-M' format (e.g. '2024-12').

        Returns
        -------
        float
            The cumulative return from start_month (exclusive) to end_month (inclusive).
        """
        # convert str to datetime
        start_month = (
            parse_month(start_month) if isinstance(start_month, str) else start_month
        )
        end_month = parse_month(end_month) if isinstance(end_month, str) else end_month

        value = 1.0
        for entry in self.monthly_returns:
            if start_month <= entry["month"] <= end_month:
                value *= 1 + float(entry["value"])
        return value - 1.0

    def annualized_return(self, start_month, end_month):
        """
        Calculates annualized return from start_month to end_month (inclusive).
        start_month and end_month should be in 'YYYY-MM' format.
        """
        # Step 1: Compute cumulative return over the period
        cumulative = self.cumulative_return(start_month, end_month)

        # Step 2: Parse dates
        start_date = parse_month(start_month)
        end_date = parse_month(end_month)

        # Step 3: Calculate number of months in the period
        months = (
            (end_date.year - start_date.year) * 12
            + (end_date.month - start_date.month)
            + 1
        )

        # Step 4: Annualize (compound return adjusted to yearly scale)
        annualized = (1 + cumulative) ** (12 / months) - 1

        return annualized

    def volatility(self, start_month=None, end_month=None, ddof=1):
        """Calculate the volatility of returns.

        Parameters
        ----------
        start_month: str, optional
            The month from which to start calculating volatility. The
            comparison is exclusive, meaning returns with a month strictly
            greater than ``start_month`` are included. If ``None`` (default),
            the calculation uses the beginning of the series.
        end_month: str, optional
            The final month (inclusive) to consider in the calculation. If
            ``None`` (default), the calculation uses the end of the series.

        Returns
        -------
        float
            The standard deviation of the selected series of monthly returns.
            If the range is empty, ``0.0`` is returned.
        """
        start_month = parse_month(start_month)
        start_ms = start_month.month + start_month.year * 12 if start_month else None
        end_month = parse_month(end_month)
        end_ms = end_month.month + end_month.year * 12 if end_month else None
        vals = []
        for entry in self.monthly_returns:
            m = entry["datetime"].month + entry["datetime"].year * 12
            if (start_ms is None or start_ms <= m) and (end_ms is None or m <= end_ms):
                try:
                    vals.append(float(entry["value"]))
                except (TypeError, ValueError):
                    # skip non-numeric values
                    continue

        if not vals:
            return 0.0

        s = pd.Series(vals, dtype="float64")
        if s.empty:
            return 0.0

        monthly_vol = float(s.std(ddof=ddof))
        return monthly_vol * math.sqrt(12.0)

    def sharpe_ratio(self, start_month=None, end_month=None, risk_free_rate=0.0):
        """Calculate the Sharpe ratio of returns.

        Parameters
        ----------
        start_month: str, optional
            The month from which to start calculating the Sharpe ratio. The
            comparison is exclusive, meaning returns with a month strictly
            greater than ``start_month`` are included. If ``None`` (default),
            the calculation uses the beginning of the series.
        end_month: str, optional
            The final month (inclusive) to consider in the calculation. If
            ``None`` (default), the calculation uses the end of the series.
        risk_free_rate: float, optional
            The annualized risk-free rate to use in the calculation. This is
            expressed as a decimal (e.g., ``0.03`` for 3%). Default is ``0.0``.

        Returns
        -------
        float
            The Sharpe ratio of the selected series of monthly returns.
            If the range is empty or volatility is zero, ``0.0`` is returned.
        """

        # Step 1: Calculate annualized return over the specified period
        ann_return = self.annualized_return(
            start_month=start_month,
            end_month=end_month,
        )

        # Step 2: Calculate volatility over the specified period
        vol = self.volatility(
            start_month=start_month,
            end_month=end_month,
        )

        # Step 3: Handle edge cases
        if vol == 0.0:
            return 0.0

        # Step 4: Compute Sharpe ratio
        sharpe = (ann_return - risk_free_rate) / vol

        return sharpe

    def sortino_ratio(self, start_month=None, end_month=None, risk_free_rate=0.0):
        """
        Calculate the annualized Sortino ratio from monthly returns.

        Parameters
        ----------
        start_month : str, optional
            Include months strictly greater than this (exclusive lower bound).
        end_month : str, optional
            Include months up to and including this (inclusive upper bound).
        risk_free_rate : float, default 0.0
            Annual risk-free rate (e.g., 0.02 for 2%).

        Returns
        -------
        float
            Annualized Sortino ratio. Returns 0.0 if no usable data.
        """

        start_month = parse_month(start_month)
        end_month = parse_month(end_month)

        # collect returns
        vals = [
            float(entry["value"])
            for entry in self.monthly_returns
            if (
                (start_month is None or start_month <= entry["datetime"])
                and (end_month is None or entry["datetime"] <= end_month)
            )
        ]
        if not vals:
            return 0.0

        s = np.array(vals)

        # convert annual risk-free rate to monthly
        monthly_rf = (1 + risk_free_rate) ** (1 / 12) - 1

        # excess returns
        excess_returns = s - monthly_rf

        # Downside returns (where returns < target)
        downside = np.minimum(0, s - monthly_rf)

        # Downside deviation (like std dev but only for negative returns)
        downside_deviation = np.sqrt((np.sum(downside**2) / (len(s) + 1))) * np.sqrt(12)

        if downside_deviation == 0:
            return np.nan  # Avoid division by zero
        ann_return = self.annualized_return(start_month, end_month)
        sortino = (ann_return - risk_free_rate) / downside_deviation
        # Annualized Sortino ratio
        return sortino

    def max_drawdown(self, start_month=None, end_month=None):
        """
        Calculate the maximum drawdown from monthly returns.

        Parameters
        ----------
        start_month : str, optional
            Include months strictly greater than this (exclusive lower bound).
        end_month : str, optional
            Include months up to and including this (inclusive upper bound).

        Returns
        -------
        float
            Maximum drawdown as a decimal (e.g., 0.2 for 20%).
            Returns 0.0 if no usable data.
        """

        start_dt = parse_month(start_month)
        end_dt = parse_month(end_month)

        values = []
        cum_value = 1.0

        for entry in self.monthly_returns:
            entry_dt = parse_month(entry["month"])
            if start_dt <= entry_dt <= end_dt:
                cum_value *= 1 + float(entry["value"])
                values.append(cum_value - 1.0)  # cumulative return up to this month

        cumulative = np.array(values)

        # Compute running maximum of cumulative returns
        running_max = np.maximum.accumulate(cumulative)

        # Compute drawdowns
        drawdowns = (running_max - cumulative) / (1 + running_max)

        # Maximum drawdown
        max_drawdown = np.max(drawdowns)

        return max_drawdown

    def positive_months(self, start_month=None, end_month=None):
        """
        Count the number of months with positive returns.

        Parameters
        ----------
        start_month : str, optional
            Include months strictly greater than this (exclusive lower bound).
        end_month : str, optional
            Include months up to and including this (inclusive upper bound).

        Returns
        -------
        int
            Number of months with positive returns.
        """

        start_dt = parse_month(start_month)
        end_dt = parse_month(end_month)

        count = 0
        total = 0
        for entry in self.monthly_returns:
            entry_dt = parse_month(entry["month"])
            if start_dt <= entry_dt <= end_dt and float(entry["value"]) > 0:
                count += 1
            total += 1

        return count / total if total > 0 else 0.0

    def return_in_positive_months(self, start_month=None, end_month=None):
        """
        Calculate cumulative return in months with positive returns.

        Parameters
        ----------
        start_month : str, optional
            Include months strictly greater than this (exclusive lower bound).
        end_month : str, optional
            Include months up to and including this (inclusive upper bound).

        Returns
        -------
        float
            Cumulative return in months with positive returns.
        """

        start_dt = parse_month(start_month)
        end_dt = parse_month(end_month)
        total_rtn = 0
        count = 0
        for entry in self.monthly_returns:
            entry_dt = parse_month(entry["month"])
            if start_dt <= entry_dt <= end_dt and float(entry["value"]) > 0:
                total_rtn += float(entry["value"])
                count += 1

        return total_rtn / count if count > 0 else 0.0

    def return_in_negative_months(self, start_month=None, end_month=None):
        """
        Calculate cumulative return in months with negative returns.

        Parameters
        ----------
        start_month : str, optional
            Include months strictly greater than this (exclusive lower bound).
        end_month : str, optional
            Include months up to and including this (inclusive upper bound).

        Returns
        -------
        float
            Cumulative return in months with negative returns.
        """

        start_dt = parse_month(start_month)
        end_dt = parse_month(end_month)
        total_rtn = 0
        count = 0
        for entry in self.monthly_returns:
            entry_dt = parse_month(entry["month"])
            if start_dt <= entry_dt <= end_dt and float(entry["value"]) < 0:
                total_rtn += float(entry["value"])
                count += 1

        return total_rtn / count if count > 0 else 0.0

    def join_two_funds(self, benchmark_fund, start_month=None, end_month=None):

        fund1 = self.monthly_returns  # market returns
        fund2 = benchmark_fund.monthly_returns  # stock returns

        req_start = parse_month(start_month)
        req_end = parse_month(end_month)

        # gather available months
        f1_months = [e["month"] for e in fund1]
        f2_months = [e["month"] for e in fund2]

        earliest_common_start = max(min(f1_months), min(f2_months))
        latest_common_end = min(max(f1_months), max(f2_months))
        # final clamped range (also respect the user’s requested window)
        adj_start = max(req_start, earliest_common_start)
        adj_end = min(req_end, latest_common_end)

        if adj_start > adj_end:
            return [], [], (adj_start, adj_end)  # or raise, depending on your needs

        fund1_values = [
            entry["value"] for entry in fund1 if adj_start <= entry["month"] <= adj_end
        ]

        fund2_values = [
            entry["value"] for entry in fund2 if adj_start <= entry["month"] <= adj_end
        ]
        return fund1_values, fund2_values

    def correlation_to(self, benchmark_fund, start_month=None, end_month=None):
        """_summary_

        Args:
            benchmark_fund (Fund Class): A index that you want to compare/calculate the correlation with
            start_month (_type_): _description_
            end_month (_type_): _description_
        """
        # Example: list1 and list2 are your two lists
        fund1_values, fund2_values = self.join_two_funds(
            benchmark_fund=benchmark_fund, start_month=start_month, end_month=end_month
        )
        arr1 = np.array(fund1_values)
        arr2 = np.array(fund2_values)

        # correlation matrix
        corr_matrix = np.corrcoef(arr1, arr2)

        # extract the correlation coefficient
        corr = corr_matrix[0, 1]
        return corr

    def beta_to(self, benchmark_fund, start_month=None, end_month=None):
        """
        Calculate the beta of this fund relative to a benchmark fund.

        Parameters
        ----------
        benchmark_fund : Fund
            The benchmark Fund object to compare against.
        start_month : str, optional
            Include months strictly greater than this (exclusive lower bound).
        end_month : str, optional
            Include months up to and including this (inclusive upper bound).

        Returns
        -------
        float
            Beta of this fund relative to the benchmark fund.
            Returns None if insufficient data.
        """
        # Example: assume you already have two lists of dicts
        fund1_values, fund2_values = self.join_two_funds(
            benchmark_fund=benchmark_fund, start_month=start_month, end_month=end_month
        )

        # Combine into 2D numpy array (rows = months, columns = funds)
        np_array = np.column_stack([fund1_values, fund2_values])

        # Now you can access
        m = np_array[:, 1]  # fund 1 (market returns)
        s = np_array[:, 0]  # fund 2 (stock returns)
        covariance = np.cov(s, m)  # Calculate covariance between stock and market
        beta = covariance[0, 1] / covariance[1, 1]

        return beta

    def plot_monthly_return_distribution(
        self,
        *,
        start_month: str | None = None,  # "YYYY-MM"
        end_month: str | None = None,  # "YYYY-MM"
        bins: int = 24,
        value_key: str = "value",  # key in each monthly_returns entry
        show_stats_lines: bool = True,
    ):
        """
        Plot a histogram (bar chart) of this fund's historical monthly returns.
        Style is defined directly inside the function (no external STYLE_DICT).
        Adds a smoothed KDE curve for a softer distribution edge.
        """
        from math import exp, pi, sqrt

        import numpy as np
        import plotly.graph_objects as go

        # --- small helper (in case it's not already defined) ---
        def hex_to_rgba(hx: str, alpha: float = 1.0) -> str:
            hx = hx.lstrip("#")
            r, g, b = int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)
            return f"rgba({r},{g},{b},{alpha})"

        # ----- style + palette (inlined here) -----
        layout_config = {
            "font": dict(family="Montserrat, Roboto", size=14, color="#53565A"),
            "margin": dict(l=54, r=42, t=84, b=72),
            "grid_color": "#e9e9ea",
        }
        color = "#C1AE94"  # keep your requested color
        fund_name = self.name or "Fund"

        # ----- clamp date window to available history -----
        sm = parse_month(start_month) if start_month else self.inception_date
        em = parse_month(end_month) if end_month else self.latest_date

        if not self.monthly_returns:
            raise ValueError("No monthly_returns available on this fund.")

        months_all = [e.get("month") for e in self.monthly_returns if "month" in e]
        first_m, last_m = months_all[0], months_all[-1]
        sm = sm or first_m
        em = em or last_m
        if sm > em:
            raise ValueError("start_month must be <= end_month")

        # ----- extract values -----
        vals = []
        for e in self.monthly_returns:
            m = e.get("month")
            if m is None or not (sm <= m <= em):
                continue
            if value_key in e:
                vals.append(float(e[value_key]))
            else:
                for k_guess in ("return", "ret", "monthly_return", "value"):
                    if k_guess in e:
                        vals.append(float(e[k_guess]))
                        break

        if not vals:
            raise ValueError(
                f"No monthly return values found for {fund_name} in the requested window."
            )

        vals = np.array(vals, dtype=float)
        n = len(vals)
        mean_r = float(np.mean(vals))
        p5, p50, p95 = (float(x) for x in np.percentile(vals, [5, 50, 95]))

        # ----- histogram bin width (for KDE scaling into % per bin) -----
        vmin, vmax = float(np.min(vals)), float(np.max(vals))
        # guard against zero-width
        rng = vmax - vmin if vmax > vmin else max(abs(vmax), 1e-6)
        bin_width = rng / max(bins, 1)

        # ----- simple Gaussian KDE (no scipy) -----
        # Scott's rule-of-thumb bandwidth
        std = float(np.std(vals)) if n > 1 else 1e-6
        h = 1.06 * std * (n ** (-1 / 5)) if n > 1 and std > 0 else (rng / 20.0 or 1e-3)

        x_grid = np.linspace(vmin - bin_width, vmax + bin_width, 400)

        def gaussian_kernel(u):
            return (1.0 / sqrt(2 * pi)) * np.exp(-0.5 * u * u)

        if n > 0:
            # density f(x), integrates to 1
            diffs = (x_grid[:, None] - vals[None, :]) / h
            dens = np.mean(gaussian_kernel(diffs), axis=1) / h
            # Scale to approximate histogram '% of months per bin' at each x:
            kde_percent = dens * bin_width * 100.0
        else:
            kde_percent = np.zeros_like(x_grid)

        # ----- figure -----
        fig = go.Figure()

        # Histogram with softer edges: semi-transparent fill + lighter outline
        fig.add_trace(
            go.Histogram(
                x=vals,
                nbinsx=bins,
                histnorm="percent",
                marker=dict(
                    color=hex_to_rgba(color, 0.55),
                    line=dict(color=hex_to_rgba(color, 0.85), width=0.8),
                ),
                opacity=0.95,
                hovertemplate=(
                    "<b>%{x.start:.2%} – %{x.end:.2%}</b>"
                    "<br>%{y:.1f}% of months"
                    "<extra></extra>"
                ),
                name=fund_name,
                showlegend=False,
            )
        )

        # Smooth KDE curve on top for a softer distribution "edge"
        fig.add_trace(
            go.Scatter(
                x=x_grid,
                y=kde_percent,
                mode="lines",
                line=dict(color=color, width=3),
                name="KDE",
                hovertemplate="<b>%{x:.2%}</b><br>%{y:.2f}% (smooth)<extra></extra>",
                showlegend=False,
            )
        )

        # ----- reference lines -----
        if show_stats_lines:
            # 0% vertical line
            fig.add_shape(
                type="line",
                x0=0,
                x1=0,
                y0=0,
                y1=1,
                xref="x",
                yref="paper",
                line=dict(color="#2F2F2F", width=1, dash="dash"),
            )
            fig.add_annotation(
                x=0,
                y=1.02,
                xref="x",
                yref="paper",
                text="0%",
                showarrow=False,
                font=dict(size=12, color="#666"),
            )
            # mean line
            fig.add_shape(
                type="line",
                x0=mean_r,
                x1=mean_r,
                y0=0,
                y1=1,
                xref="x",
                yref="paper",
                line=dict(color=color, width=2),
            )
            fig.add_annotation(
                x=mean_r,
                y=1.02,
                xref="x",
                yref="paper",
                text=f"Mean {mean_r:.2%}",
                showarrow=False,
                font=dict(size=12, color=color),
            )

        # ----- layout tweaks for a cleaner, smoother feel -----
        fig.update_layout(
            title=dict(
                text=f"<b>{fund_name} — Monthly Return Distribution</b>",
                font=dict(size=26),
                x=0.5,
                xanchor="center",
                y=0.97,
                yanchor="middle",
            ),
            template="plotly_white",
            font=layout_config["font"],
            margin=layout_config["margin"],
            paper_bgcolor="white",
            plot_bgcolor="white",
            hovermode="x unified",
            hoverlabel=dict(
                font=dict(
                    family=layout_config["font"]["family"], size=13, color="#333"
                ),
                bgcolor="white",
                bordercolor=hex_to_rgba(color, 0.6),
            ),
            xaxis=dict(
                title="Monthly Return",
                tickformat="+.0%",
                showgrid=True,
                gridcolor=layout_config["grid_color"],
                zeroline=False,
                ticks="outside",
                ticklen=6,
                tickcolor="#d7d7d9",
            ),
            yaxis=dict(
                title="Months (%)",
                ticksuffix="%",
                showgrid=True,
                gridcolor=layout_config["grid_color"],
                zeroline=False,
                ticks="outside",
                ticklen=6,
                tickcolor="#d7d7d9",
            ),
            bargap=0.35,
        )

        # small stats box (subtle, right-top)
        fig.add_annotation(
            xref="paper",
            yref="paper",
            x=0.98,
            y=0.98,
            xanchor="right",
            yanchor="top",
            align="right",
            showarrow=False,
            text=(
                f"<span style='color:{color};'><b>{fund_name}</b></span><br>"
                f"n = {n}<br>"
                f"Mean = {mean_r:.2%}<br>"
                f"Median = {p50:.2%}<br>"
                f"P5 / P95 = {p5:.2%} / {p95:.2%}"
            ),
            bgcolor="#F6F6F7",
            bordercolor=hex_to_rgba(color, 0.9),
            borderwidth=1,
        )

        file_name = f'{self.name} monthly return distribution plot {sm.strftime("%Y-%m-%d")} to {em.strftime("%Y-%m-%d")}.png'
        save_path = f"{save_dir}/{file_name}"
        fig.write_image(save_path, scale=2)
        return fig

    def export_monthly_table(
        self, language: str = "en", benchmark: Fund = None, benchmark_name: str = None
    ):
        """
        Export a Plotly table of monthly + YTD returns to an interactive HTML file.
        """
        # ---------------- configurations ----------------
        month_labels = {
            "en": [
                "Jan",
                "Feb",
                "Mar",
                "Apr",
                "May",
                "Jun",
                "Jul",
                "Aug",
                "Sep",
                "Oct",
                "Nov",
                "Dec",
            ],
            "cn": [
                "1月",
                "2月",
                "3月",
                "4月",
                "5月",
                "6月",
                "7月",
                "8月",
                "9月",
                "10月",
                "11月",
                "12月",
            ],
        }
        other_labels = {
            "en": {
                "ytd_label": "YTD",
                "year": "Year",
            },
            "cn": {
                "ytd_label": "年初至今",
                "year": "年分",
            },
        }

        if language == "en":
            table_labels = month_labels["en"]
            ytd_label = other_labels["en"]["ytd_label"]
            year_label = other_labels["en"]["year"]
        elif language == "cn":
            table_labels = month_labels["cn"]
            ytd_label = other_labels["cn"]["ytd_label"]
            year_label = other_labels["cn"]["year"]
        # ---------------- prepare data ----------------
        df = self.monthly_returns
        # processed_returns = []
        # for entry in monthly_returns:
        #     raw_date = entry['date']
        #     # Try parsing date in 'DD/MM/YYYY' format
        #     dt = datetime.strptime(str(raw_date), '%d/%m/%Y')
        #     processed_returns.append({d
        #         'datetime': dt,
        #         'month': datetime(dt.year, dt.month, 1),
        #         'value': entry['value']
        #     })
        monthly_returns_list = [self.monthly_returns]
        if benchmark:
            benchmark_monthly_returns = [
                entry
                for entry in benchmark.monthly_returns
                if (entry["month"] in [e["month"] for e in self.monthly_returns])
            ]
            monthly_returns_list.append(benchmark_monthly_returns)
        df_list = []
        for monthly_returns in monthly_returns_list:
            # rechieve monthly returns
            df = pd.DataFrame(monthly_returns)
            # add year and month_num columns
            df["year"] = df["month"].dt.year
            df["month_num"] = df["month"].dt.month
            # pivot table have years as rows and months as columns
            df = (
                df.pivot_table(
                    index="year", columns="month_num", values="value", aggfunc="last"
                )
                .reindex(columns=range(1, 13))
                .sort_index(ascending=False)
            )
            # calculate YTD
            ytd = df.add(1).prod(axis=1, skipna=True) - 1
            df["YTD"] = ytd

            def pct_str(x):
                """Format a float as a percentage string."""
                return f"{x*100:,.2f}%" if pd.notna(x) else ""

            df = df.applymap(pct_str)
            # append to list
            df_list.append(df)
        if len(df_list) == 2:
            rows = []
            for year in df_list[0].index:
                row_value = df_list[0].loc[year].tolist()
                row_benchmark = df_list[1].loc[year].tolist()
                row_benchmark = [
                    b if v else "" for v, b in zip(row_value, row_benchmark)
                ]
                rows.append([year] + row_value)
                rows.append(
                    [benchmark_name if benchmark_name else benchmark.name]
                    + row_benchmark
                )
            df = pd.DataFrame(rows, columns=["year"] + df_list[0].columns.to_list())
            df.columns = [year_label] + table_labels + [ytd_label]
        else:
            df = df_list[0]
            df.columns = table_labels + [ytd_label]
            df.insert(0, year_label, df.index.astype(str))

        # ---------------- figure size calculations ----------------
        width = 26
        min_height, max_height = 3, 10
        num_rows, num_cols = df.shape  # 14 (Year + 12 months + YTD)
        # width
        cell_width = width / num_cols
        # default cell height
        cell_height = 1
        # calculate table height using the default cell height
        height = cell_height * (num_rows + 1)
        # adjust cell height if the table is too tall or too short
        if height < min_height:
            cell_height = min_height / (num_rows + 1)
        elif height > max_height:
            cell_height = max_height / (num_rows + 1)
        # identify text font size that fits in the cell height
        font_size = find_largest_font_size(cell_height * 0.8, FONT2HEIGHT)
        # recalculate final height
        height = cell_height * (num_rows + 1)
        # ----------------draw table ----------------
        # initialize figure
        fig, ax = plt.subplots(figsize=(width, height))
        ax.axis("off")  # Hide axes
        # create table
        table = ax.table(
            cellText=df.values,
            colLabels=df.columns,
            cellLoc="center",  # Center align text in cells
            loc="center",
        )
        # style table
        for (row, col), cell in table.get_celld().items():
            if row == 0:
                # use bold font for header row
                fname = FONT_FNAME[language]["bold"]
                cell.set_facecolor("#cbb69d")
            else:
                # use bold font for value rows if applicable
                fname = (
                    FONT_FNAME[language]["bold"]
                    if benchmark and row % 2 == 1
                    else FONT_FNAME[language]["regular"]
                )
                cell.set_facecolor("#f0f0f0")
            cell.set_width(cell_width / width - 1e-9)
            cell.set_height(cell_height / height - 1e-9)
            cell.set_edgecolor("white")
            font_prop = FontProperties(fname=fname, size=font_size)
            cell.get_text().set_fontproperties(font_prop)

        plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
        plt.margins(0)
        plt.axis("off")
        plt.tight_layout()
        file_name = (
            f'{self.name} monthly return table {self.latest_date.strftime("%Y-%m-%d")}'
            + (f" {benchmark.name}" if benchmark else "")
            + ".png"
        )
        # file_name = self.name + ' key metrics table ' + self.latest_date + benchmark.name if benchmark else '' + '.png'
        save_path = f"{save_dir}/{file_name}"
        plt.savefig(save_path, bbox_inches="tight", pad_inches=0, dpi=200)

    def export_key_metrics_table(
        self,
        end_month,
        benchmark_fund=None,
        language="en",
        metrics=None,
        horizontal: bool = False,
        fix_aspect: bool = False,
        filename="key_metrics_table.png",
    ):
        header_fill = "#cbb69d"
        cell_fill = "#f0f0f0"
        text_color = "black"

        labels = {
            "en": {
                "metric": "Metric",
                "value": "Value",
                "cagr": "Annualized Return",
                "vol": "Volatility",
                "sharpe": "Sharpe Ratio",
                "sortino": "Sortino Ratio",
                "cum": "Cumulative Return",
                "mdd": "Max Drawdown",
                "beta": f"Beta to {benchmark_fund.name}",
                "corr": f"Correlation (vs. {benchmark_fund.name})",
                "win": "Win Rate (Monthly)",
                "best": "Best Month",
                "worst": "Worst Month",
                "aum": "AUM",
                "skew": "Skewness",
                "kurt": "Kurtosis",
                "turnover": "Avg. Monthly Turnover",
            },
            "cn": {
                "metric": "指标",
                "value": "数值",
                "cagr": "年复合增长率 (CAGR)",
                "vol": "波动率",
                "sharpe": "夏普比率",
                "sortino": "索提诺比率",
                "cum": "累计增长率",
                "mdd": "最大回撤",
                "beta": f"贝塔({benchmark_fund.name})",
                "corr": f"相关性（{benchmark_fund.name}）",
                "win": "月度胜率",
                "best": "最佳月份",
                "worst": "最差月份",
                "aum": "管理资产规模（AUM）",
                "skew": "偏度",
                "kurt": "峰度",
                "turnover": "平均月换手率",
            },
        }
        L = labels.get(language, labels["en"])

        placeholder_values = {
            "cagr": f"{100 * self.annualized_return(self.inception_date, end_month):.1f}%",
            "vol": f"{100 * self.volatility(self.inception_date, end_month):.1f}%",
            "mdd": f"{100 * self.max_drawdown(self.inception_date, end_month):.1f}%",
            "cum": f"{100 * self.cumulative_return(self.inception_date, end_month):.1f}%",
            "win": f"{self.positive_months(self.inception_date, end_month)*100:.2f}%",
            "sharpe": f"{self.sharpe_ratio(self.inception_date, end_month):.2f}",
            "sortino": f"{self.sortino_ratio(self.inception_date, end_month):.2f}",
            "beta": f"{self.beta_to(benchmark_fund, self.inception_date, end_month):.2f}",
            "corr": f"{self.correlation_to(benchmark_fund, self.inception_date, end_month):.2f}",
            "best": "[placeholder]",
            "worst": "[placeholder]",
            "aum": "[placeholder]",
            "skew": "[placeholder]",
            "kurt": "[placeholder]",
            "turnover": "[placeholder]",
        }

        default_order = [
            "cagr",
            "vol",
            "sharpe",
            "sortino",
            "mdd",
            "beta",
            "corr",
            "win",
            "best",
            "worst",
            "aum",
            "skew",
            "kurt",
            "turnover",
        ]

        if metrics is None:
            selected = default_order
        else:
            known = set(placeholder_values.keys())
            selected = [m for m in metrics if m in known]
            if not selected:
                raise ValueError("No valid metrics provided.")

        metric_labels = [L[k] for k in selected]
        metric_values = [placeholder_values[k] for k in selected]
        font_size = 30
        font_width = FONT2HEIGHT[font_size]
        font_height = FONT2HEIGHT[font_size]

        if not horizontal:
            # Vertical table: two columns (Metric | Value)
            cell_text = list(zip(metric_labels, metric_values))
            col_labels = [L["metric"], L["value"]]
            col_widths = [
                font_width * max([len(row[i]) for row in cell_text]) * 1.2
                for i in range(2)
            ]
            header_height = font_height * 2.5 * 1.2
            cell_height = font_height * 2.5
            table_width = sum(col_widths)
            table_height = header_height + cell_height * len(cell_text)

        else:
            # Horizontal table: one row of metrics, one row of values
            cell_text = [metric_values]
            col_labels = metric_labels
            col_widths = [
                font_width * max(len(lab), len(val)) * 1.2
                for lab, val in zip(metric_labels, metric_values)
            ]
            table_width = sum(col_widths)
            if fix_aspect:
                table_height = table_width / 14
                header_height = table_height / 2.2 * 1.2
                cell_height = table_height / 2.2
            else:
                header_height = font_height * 2.5 * 1.2
                cell_height = font_height * 2.5
                table_height = header_height + cell_height

        # Create figure sized to fit table
        fig, ax = plt.subplots(figsize=(table_width, table_height))
        ax.axis("off")

        table = ax.table(
            cellText=cell_text,
            colLabels=col_labels,
            loc="center",
            cellLoc="center",
        )

        # Style cells
        for (row, col), cell in table.get_celld().items():
            if row == 0:  # header
                cell.set_facecolor(header_fill)
                cell.set_text_props(color=text_color, weight="bold")
                cell.set_height(header_height / table_height - 1e-9)
                cell.set_width(col_widths[col] / table_width - 1e-9)
            else:  # data rows
                cell.set_facecolor(cell_fill)
                cell.set_text_props(color=text_color)
                cell.set_height(cell_height / table_height - 1e-9)
                cell.set_width(col_widths[col] / table_width - 1e-9)
            font_prop = FontProperties(
                fname=(
                    FONT_FNAME[language]["bold"]
                    if row == 0
                    else FONT_FNAME[language]["regular"]
                ),
                size=font_size,
            )
            cell.get_text().set_fontproperties(font_prop)
            cell.set_edgecolor("white")

        plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
        plt.margins(0)
        plt.axis("off")
        plt.tight_layout()
        save_path = f'{save_dir}/{self.name} key metrics table {end_month.strftime("%Y-%m-%d")}.png'
        plt.savefig(save_path, bbox_inches="tight", pad_inches=0, dpi=200)

    def summary_of_a_fund(self, benchmark_fund=None, language="en"):
        plot1 = self.export_monthly_table(language)
        plot2 = self.export_key_metrics_table(
            benchmark_fund=benchmark_fund,
            end_month=self.latest_date,
            language=language,
            metrics=["cagr", "vol", "sharpe", "sortino", "mdd", "beta", "corr", "win"],
            horizontal=False,
        )
        plot3 = self.plot_monthly_return_distribution()
        return plot1, plot2, plot3
