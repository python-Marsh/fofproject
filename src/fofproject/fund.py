from __future__ import annotations
import math
from collections import OrderedDict
from collections.abc import MutableMapping
from datetime import datetime
from typing import Dict, List, Union, Optional
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from matplotlib.font_manager import FontProperties
from fofproject.utils import hex_to_rgba, parse_month, compute_identifier
from dateutil.relativedelta import relativedelta

from fofproject.paths import SAVE_DIR as save_dir

# Use a font that is available in this container environment.
MEASURE_FONT_FAMILY = "DejaVu Sans"


def get_font2height():
    """
    Build a lookup table mapping font sizes (1-39) to their rendered heights in inches.

    Creates a matplotlib figure for each font size, renders sample text, and measures
    the bounding box height. Used to dynamically size table cells based on font size.

    Returns
    -------
    dict
        Dictionary where keys are font sizes (int) and values are heights in inches (float).
    """
    font2height = {}
    for font_size in range(1, 40):
        fig, ax = plt.subplots()
        text = ax.text(
            0,
            0,
            "1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZ",
            fontsize=font_size,
            fontname=MEASURE_FONT_FAMILY,
        )
        # Draw the figure to ensure renderer is available
        fig.canvas.draw()
        fig.set_dpi(200)  # Set a specific DPI for consistency
        # Get the bounding box in display (pixel) coordinates
        bbox = text.get_window_extent(renderer=fig.canvas.get_renderer())
        height_in_inches = bbox.height / fig.dpi
        font2height[font_size] = height_in_inches
        plt.close(fig)
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
    """
    Find the largest font size whose rendered height fits within a target height.

    Iterates through font sizes from largest to smallest and returns the first
    one that fits. Used for auto-sizing text in table cells.

    Parameters
    ----------
    target_height : float
        Maximum allowed height in inches.
    font2height : dict
        Lookup table from get_font2height() mapping font sizes to heights.

    Returns
    -------
    int or None
        Largest font size that fits, or None if no font size is small enough.
    """
    for font_size, height in reversed(font2height.items()):
        if height <= target_height:
            return font_size
    return None


def get_available_benchmarks(file_path="BENCHMARK.csv"):
    """Read BENCHMARK.csv header and return list of available index names."""
    df = pd.read_csv(file_path, nrows=0)
    return [col for col in df.columns if col != "date"]


def load_benchmarks(file_path="BENCHMARK.csv"):
    """Load benchmark/index funds from a CSV file with zero fees.

    Parameters
    ----------
    file_path : str
        Path to the benchmark CSV file.

    Returns
    -------
    dict
        Dictionary mapping benchmark names to Fund objects.
    """
    df = pd.read_csv(file_path)
    funds = FundDict()
    for col in df.columns:
        if col == "date":
            continue
        returns = [
            {"date": d, "value": v} for d, v in zip(df["date"], df[col]) if pd.notna(v)
        ]
        fund = Fund(
            name=col,
            monthly_returns=returns,
            identifier=compute_identifier(returns),
            performance_fee=0,
            management_fee=0,
        )
        funds[col] = fund
    return funds


def assign_benchmarks(funds, benchmarks):
    """Wire up suggested_benchmark_name → default_benchmark Fund object.

    Skips funds that already have a default_benchmark (e.g. from benchmark_map).

    Parameters
    ----------
    funds : dict
        Dictionary of Fund objects to assign benchmarks to.
    benchmarks : dict
        Dictionary of benchmark Fund objects (e.g. from load_benchmarks()).
    """
    for fund in funds.values():
        if fund.default_benchmark is not None:
            continue
        name = getattr(fund, "suggested_benchmark_name", None)
        if name and name in benchmarks:
            fund.default_benchmark = benchmarks[name]


class FundDict(MutableMapping):
    """A dict-like container that keys funds by identifier internally.

    Externally it behaves like a regular ``dict[str, Fund]`` keyed by
    fund *name* — iteration, ``in``, ``keys()``, ``items()`` all expose
    names so that downstream code (batch.py, automation.py, …) works
    unchanged.

    When two funds share the same *name* but have different *identifiers*,
    both are stored without overwriting.  A name-based lookup in that case
    raises ``KeyError`` listing the ambiguous identifiers so the caller
    can disambiguate.

    Direct identifier-based access (``fd[identifier_string]``) also works.
    """

    def __init__(self, *args, **kwargs):
        # _store: identifier → Fund  (the authoritative store)
        self._store: OrderedDict[str, "Fund"] = OrderedDict()
        # _name_index: name → [identifier, …]  (insertion-ordered)
        self._name_index: dict[str, list[str]] = {}
        # Bootstrap from a plain dict / iterable of pairs
        if args or kwargs:
            tmp = dict(*args, **kwargs)
            for v in tmp.values():
                self._insert(v)

    # ---- internal helpers ------------------------------------------------

    def _insert(self, fund: "Fund"):
        ident = fund.identifier or fund.name
        self._store[ident] = fund
        self._name_index.setdefault(fund.name, [])
        if ident not in self._name_index[fund.name]:
            self._name_index[fund.name].append(ident)

    def _resolve_key(self, key: str) -> str:
        """Return the identifier for *key* (which may be a name or identifier)."""
        # Direct identifier hit
        if key in self._store:
            return key
        # Name-based lookup
        ids = self._name_index.get(key)
        if ids is None:
            raise KeyError(key)
        if len(ids) == 1:
            return ids[0]
        raise KeyError(
            f"Ambiguous fund name '{key}' maps to {len(ids)} funds "
            f"(identifiers: {ids}). Use the identifier to access the "
            f"specific fund."
        )

    # ---- MutableMapping interface ----------------------------------------

    def __getitem__(self, key: str) -> "Fund":
        return self._store[self._resolve_key(key)]

    def __setitem__(self, key: str, fund: "Fund"):
        # ``key`` is ignored in favour of the fund's own identifier.
        self._insert(fund)

    def __delitem__(self, key: str):
        ident = self._resolve_key(key)
        fund = self._store.pop(ident)
        ids = self._name_index.get(fund.name, [])
        if ident in ids:
            ids.remove(ident)
        if not ids:
            self._name_index.pop(fund.name, None)

    def __contains__(self, key) -> bool:
        try:
            self._resolve_key(key)
            return True
        except KeyError:
            return False

    def __len__(self) -> int:
        return len(self._store)

    def __iter__(self):
        """Yield fund *names* in insertion order (backward compat)."""
        for fund in self._store.values():
            yield fund.name

    def __repr__(self):
        names = [f.name for f in self._store.values()]
        return f"FundDict({names})"

    # ---- dict-style conveniences -----------------------------------------

    def keys(self):
        return list(self)

    def values(self):
        return list(self._store.values())

    def items(self):
        return [(f.name, f) for f in self._store.values()]

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def update(self, other=None, **kwargs):
        if other is not None:
            if isinstance(other, FundDict):
                for fund in other._store.values():
                    self._insert(fund)
            elif isinstance(other, dict):
                for v in other.values():
                    self._insert(v)
            else:
                for k, v in other:
                    self._insert(v)
        for v in kwargs.values():
            self._insert(v)

    def pop(self, key, *args):
        try:
            ident = self._resolve_key(key)
        except KeyError:
            if args:
                return args[0]
            raise
        fund = self._store.pop(ident)
        ids = self._name_index.get(fund.name, [])
        if ident in ids:
            ids.remove(ident)
        if not ids:
            self._name_index.pop(fund.name, None)
        return fund

    def by_identifier(self, identifier: str) -> "Fund":
        """Direct lookup by identifier, bypassing name resolution."""
        return self._store[identifier]

    def identifiers(self):
        """Return list of all identifiers in insertion order."""
        return list(self._store.keys())

    def has_duplicate_names(self) -> dict[str, list[str]]:
        """Return {name: [ids]} for names that map to multiple funds."""
        return {n: ids for n, ids in self._name_index.items() if len(ids) > 1}


def input_monthly_returns(
    file_path,
    performance_fee=0.2,
    management_fee=0.01,
    benchmark_csv=None,
    benchmark_map=None,
):
    """
    Load monthly return data from a CSV file and create Fund objects for each column.

    Expects a CSV with a 'date' column (DD/MM/YYYY format) and one column per fund
    containing decimal returns (e.g., 0.05 for 5%). Each non-date column becomes
    a separate Fund instance.

    Parameters
    ----------
    file_path : str
        Path to the CSV file containing monthly returns.
    performance_fee : float, default 0.2
        Performance fee as decimal (0.2 = 20%). Stored on Fund for reference.
    management_fee : float, default 0.01
        Management fee as decimal (0.01 = 1%). Stored on Fund for reference.
    benchmark_csv : str, optional
        Path to a BENCHMARK.csv file. If provided, benchmark indices are loaded
        (with zero fees) and merged into the returned dict.
    benchmark_map : dict, optional
        Mapping of fund names to benchmark names (e.g., {"HAO": "MSCI CHINA"}).
        Sets default_benchmark on each fund automatically.

    Returns
    -------
    dict
        Dictionary mapping fund names (column headers) to Fund objects.

    Example
    -------
    >>> funds = input_monthly_returns("RETURN DATA.csv",
    ...     benchmark_csv="BENCHMARK.csv",
    ...     benchmark_map={"HAO": "MSCI CHINA", "LIM": "TOPIX"})
    >>> funds["HAO"].beta_to()  # uses MSCI CHINA automatically
    """
    # Read CSV file
    df = pd.read_csv(file_path)
    # Drop unnamed columns caused by trailing commas in the CSV
    df = df.loc[:, ~df.columns.str.startswith("Unnamed")]
    funds = FundDict()
    for col in df.columns:
        if col == "date":
            continue  # skip the date column

        returns = [
            {"date": d, "value": v} for d, v in zip(df["date"], df[col]) if pd.notna(v)
        ]

        # Create a Fund instance
        fund = Fund(
            name=col,
            monthly_returns=returns,
            identifier=compute_identifier(returns),
            performance_fee=performance_fee,
            management_fee=management_fee,
        )
        funds[col] = fund

    # Auto-load benchmarks from separate CSV
    if benchmark_csv:
        benchmarks = load_benchmarks(benchmark_csv)
        funds.update(benchmarks)

    # Wire up default benchmarks from explicit mapping
    if benchmark_map:
        for fund_name, benchmark_name in benchmark_map.items():
            if fund_name in funds and benchmark_name in funds:
                funds[fund_name].default_benchmark = funds[benchmark_name]

    return funds


def subset_of_funds(funds, keys=["RDGFF", "MSCI CHINA", "MSCI GLOBAL"]):
    """
    Extract a subset of funds from a larger fund dictionary.

    Useful for selecting specific funds to plot or analyze together.
    Returns None for any key not found in the original dictionary.

    Parameters
    ----------
    funds : dict
        Dictionary of Fund objects, typically from input_monthly_returns().
    keys : list of str
        Fund names to extract from the dictionary.

    Returns
    -------
    dict
        Dictionary containing only the requested funds (or None for missing keys).

    Example
    -------
    >>> all_funds = input_monthly_returns("data.csv")
    >>> selected = subset_of_funds(all_funds, ["RDGFF", "MSCI CHINA"])
    >>> plot_cumulative_returns(selected)
    """
    result = FundDict()
    for k in keys:
        fund = funds.get(k, None)
        if fund is not None:
            result[k] = fund
    return result


class Fund:
    def __init__(
        self,
        name: str,
        monthly_returns: List[Dict],
        one_liner: Optional[str] = None,
        geo_focus: Optional[str] = None,
        strategy: Optional[List[str]] = None,
        asset_class: Optional[List[str]] = None,
        identifier: Optional[str] = None,
        ir_name: Optional[str] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        base: Optional[str] = None,
        fund_inception: Optional[str] = None,
        aum_size: Optional[float] = None,
        return_pa: Optional[float] = None,
        volatility_pa: Optional[float] = None,
        min_ticket: Optional[float] = None,
        net_exposure: Optional[List[float]] = None,
        net_return: Optional[bool] = None,
        management_fee: Optional[float] = None,
        performance_fee: Optional[float] = None,
        default_benchmark: Optional["Fund"] = None,
        suggested_benchmark_name: Optional[str] = None,
    ):
        """
        Initialize a Fund object with monthly return data and optional metadata.

        Parses and validates monthly returns, ensuring dates are continuous (no gaps).
        Automatically computes summary statistics like annualized return, volatility,
        Sharpe ratio, max drawdown, etc. upon initialization.

        Parameters
        ----------
        name : str
            Fund identifier (e.g., "RDGFF", "MSCI CHINA"). Used in labels and filenames.
        monthly_returns : List[Dict]
            List of dicts with 'date' (DD/MM/YYYY string) and 'value' (decimal return).
            Example: [{"date": "01/01/2024", "value": 0.05}, ...]
        one_liner : str, optional
            Single-sentence summary of the fund.
        geo_focus : str, optional
            Geographic focus (e.g., "APAC", "China", "Global").
        strategy : List[str], optional
            Strategy types (e.g., ["Equity LS", "Systematic"]).
        asset_class : List[str], optional
            Asset class focus (e.g., ["Equities", "Credit"]).
        identifier : str, optional
            Unique identifier derived from performance data.
        ir_name : str, optional
            Investor relations contact name.
        email : str, optional
            Contact email address.
        phone : str, optional
            Contact phone number.
        base : str, optional
            Fund/manager base location (e.g., "Hong Kong", "Zurich, Switzerland").
        fund_inception : str, optional
            Fund inception date as ISO string "YYYY-MM-DD".
        aum_size : float, optional
            Assets under management in millions USD.
        return_pa : float, optional
            Annualized return as decimal (e.g., 0.125 = 12.5%).
        volatility_pa : float, optional
            Annualized volatility as decimal (e.g., 0.145 = 14.5%).
        min_ticket : float, optional
            Minimum investment in thousands USD.
        net_exposure : List[float], optional
            Range of net exposure [min, max] as decimals (e.g., [0.3, 0.7] for 30-70%).
        net_return : bool, optional
            Whether returns are net of fees.
        management_fee : float, optional
            Annual management fee as decimal (0.01 = 1%).
        performance_fee : float, optional
            Performance fee as decimal (0.2 = 20%).

        Raises
        ------
        ValueError
            If monthly_returns is not a list of dicts or dates are not continuous.

        Attributes
        ----------
        total_cum_rtn : float
            Cumulative return from inception to latest date.
        total_ann_rtn : float
            Annualized return from inception to latest date.
        total_vol : float
            Annualized volatility from inception to latest date.
        total_sharpe : float
            Sharpe ratio from inception to latest date.
        total_max_dd : float
            Maximum drawdown from inception to latest date.
        total_sortino : float
            Sortino ratio from inception to latest date.
        total_pos_months : float
            Percentage of months with positive returns.
        """
        if not all(isinstance(x, dict) for x in monthly_returns):
            raise ValueError(f"{name}: No valid monthly returns")

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
        processed_returns.sort(key=lambda x: x["datetime"])
        for i in range(1, len(processed_returns)):
            prev = processed_returns[i - 1]["month"]
            curr = processed_returns[i]["month"]
            expected = prev + relativedelta(months=1)
            if curr != expected:
                raise ValueError(
                    f"{name}: Monthly returns not continuous — expected {expected.date()}, found {curr.date()}"
                )
        self.name = name
        self.one_liner = one_liner
        self.geo_focus = geo_focus
        self.strategy = strategy
        self.asset_class = asset_class
        self.identifier = identifier
        self.ir_name = ir_name
        self.email = email
        self.phone = phone
        self.base = base
        self.fund_inception = fund_inception
        self.contact_info = (
            f"{ir_name} - Based in {base}, try reachout via email '{email}' or phone '{phone}'"
            if ir_name
            else "No contact info"
        )
        self.aum_size = aum_size
        self.return_pa = return_pa
        self.volatility_pa = volatility_pa
        self.min_ticket = min_ticket
        self.net_exposure = net_exposure
        self.net_exposure_info = (
            f"Net Exposure = {min(self.net_exposure) * 100}% to {max(self.net_exposure) * 100}%"
            if self.net_exposure
            else "No net exposure info"
        )
        self.net_return = net_return
        self.management_fee = management_fee
        self.performance_fee = performance_fee
        self.default_benchmark = default_benchmark
        self.suggested_benchmark_name = suggested_benchmark_name
        self.monthly_returns = processed_returns
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
        self.total_max_dd = (
            self.max_drawdown(self.inception_date, self.latest_date)
            if self.monthly_returns
            else None
        )
        self.total_sortino = (
            self.sortino_ratio(self.inception_date, self.latest_date)
            if self.monthly_returns
            else None
        )
        self.total_pos_months = (
            self.positive_months(self.inception_date, self.latest_date)
            if self.monthly_returns
            else None
        )

    def get_monthly_return(self, year: int, month: int):
        """
        Retrieve the return for a specific month.

        Parameters
        ----------
        year : int
            Calendar year (e.g., 2024).
        month : int
            Month number (1-12).

        Returns
        -------
        float or None
            The monthly return as a decimal, or None if not found.
        """
        target = datetime(year, month, 1)
        for entry in self.monthly_returns:
            if entry["month"] == target:
                return entry["value"]
        return None

    def compute_inception_date(self):
        """
        Find the earliest month in the return series.

        Returns
        -------
        datetime or None
            First day of the earliest month with data, or None if no data.
        """
        return (
            min(entry["month"] for entry in self.monthly_returns)
            if self.monthly_returns
            else None
        )

    def compute_latest_date(self):
        """
        Find the most recent month in the return series.

        Returns
        -------
        datetime or None
            First day of the latest month with data, or None if no data.
        """
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
        Calculate the total compounded return over a date range.

        Compounds all monthly returns between start_month and end_month (inclusive)
        using the formula: (1 + r1) * (1 + r2) * ... * (1 + rn) - 1

        Parameters
        ----------
        start_month : str or datetime
            Start of the period. Accepts 'YYYY-MM' string or datetime object.
        end_month : str or datetime
            End of the period. Accepts 'YYYY-MM' string or datetime object.

        Returns
        -------
        float
            Cumulative return as a decimal (e.g., 0.25 means 25% total return).

        Example
        -------
        >>> fund.cumulative_return("2023-01", "2023-12")  # Full year 2023
        0.1523  # 15.23% cumulative return
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
        Calculate the annualized (CAGR) return over a date range.

        Converts cumulative return to an equivalent annual rate using:
        (1 + cumulative_return) ^ (12 / num_months) - 1

        This allows comparing returns across different time periods on an
        equal footing.

        Parameters
        ----------
        start_month : str or datetime
            Start of the period. Accepts 'YYYY-MM' string or datetime object.
        end_month : str or datetime
            End of the period. Accepts 'YYYY-MM' string or datetime object.

        Returns
        -------
        float
            Annualized return as a decimal (e.g., 0.12 means 12% per year).

        Example
        -------
        >>> fund.annualized_return("2020-01", "2023-12")  # 4-year period
        0.0823  # 8.23% annualized return
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
        """
        Calculate annualized volatility (standard deviation of returns).

        Computes the standard deviation of monthly returns and scales to annual
        by multiplying by sqrt(12). Higher volatility indicates more variable returns.

        Parameters
        ----------
        start_month : str or datetime, optional
            Start of the period (inclusive). If None, uses inception date.
        end_month : str or datetime, optional
            End of the period (inclusive). If None, uses latest date.
        ddof : int, default 1
            Degrees of freedom for standard deviation calculation.
            Use 1 for sample std (default), 0 for population std.

        Returns
        -------
        float
            Annualized volatility as a decimal (e.g., 0.15 means 15% annual volatility).
            Returns 0.0 if no data in range.

        Example
        -------
        >>> fund.volatility("2023-01", "2023-12")
        0.1234  # 12.34% annualized volatility
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

    def rolling_volatility(self, window=12):
        """
        Calculate rolling annualized volatility over a sliding window.

        For each month, computes volatility using the preceding 'window' months.
        Useful for visualizing how fund risk changes over time.

        Parameters
        ----------
        window : int, default 12
            Number of months in the rolling window. 12 gives trailing 1-year volatility.

        Returns
        -------
        pd.Series
            Rolling volatility indexed by date. First (window-1) values are None
            due to insufficient history.

        Example
        -------
        >>> rolling_vol = fund.rolling_volatility(window=12)
        >>> rolling_vol.plot()  # Visualize volatility over time
        """
        dates = [entry["datetime"] for entry in self.monthly_returns]
        vols = []

        for i in range(len(dates)):
            if i + 1 < window:
                vols.append(None)
            else:
                start_month = dates[i - window + 1].strftime("%Y-%m")
                end_month = dates[i].strftime("%Y-%m")
                v = self.volatility(start_month=start_month, end_month=end_month)
                vols.append(v)

        return pd.Series(vols, index=pd.to_datetime(dates))

    def vol_of_vol(self, window=12):
        """
        Calculate volatility-of-volatility (standard deviation of rolling volatility).

        Measures how stable the fund's risk profile is over time. Higher values
        indicate more variable/unpredictable volatility.

        Parameters
        ----------
        window : int, default 12
            Rolling window size in months for the underlying volatility calculation.

        Returns
        -------
        float
            Standard deviation of the rolling volatility series.

        Example
        -------
        >>> fund.vol_of_vol(window=12)
        0.0234  # Volatility varies by about 2.34% over time
        """
        rolling_vol = self.rolling_volatility(window=window)
        vol_of_vol = rolling_vol.std()
        return vol_of_vol

    def sharpe_ratio(self, start_month=None, end_month=None, risk_free_rate=0.0):
        """
        Calculate the Sharpe ratio (risk-adjusted return).

        Measures excess return per unit of total risk:
        Sharpe = (Annualized Return - Risk-Free Rate) / Annualized Volatility

        Higher values indicate better risk-adjusted performance. Generally:
        - < 1.0: Subpar
        - 1.0-2.0: Good
        - > 2.0: Excellent

        Parameters
        ----------
        start_month : str or datetime, optional
            Start of the period (inclusive). If None, uses inception date.
        end_month : str or datetime, optional
            End of the period (inclusive). If None, uses latest date.
        risk_free_rate : float, default 0.0
            Annual risk-free rate as decimal (e.g., 0.03 for 3%).

        Returns
        -------
        float
            Sharpe ratio. Returns 0.0 if volatility is zero.

        Example
        -------
        >>> fund.sharpe_ratio("2020-01", "2023-12", risk_free_rate=0.02)
        1.45  # Good risk-adjusted performance
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
        Calculate the Sortino ratio (downside risk-adjusted return).

        Similar to Sharpe but only penalizes downside volatility, not upside.
        More appropriate for funds with asymmetric return distributions.
        Sortino = (Annualized Return - Risk-Free Rate) / Downside Deviation

        Parameters
        ----------
        start_month : str or datetime, optional
            Start of the period (inclusive). If None, uses inception date.
        end_month : str or datetime, optional
            End of the period (inclusive). If None, uses latest date.
        risk_free_rate : float, default 0.0
            Annual risk-free rate as decimal (e.g., 0.02 for 2%).

        Returns
        -------
        float
            Sortino ratio. Returns NaN if no downside deviation, 0.0 if no data.

        Example
        -------
        >>> fund.sortino_ratio("2020-01", "2023-12")
        1.82  # Higher than Sharpe means positive skew in returns
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
        Calculate the maximum peak-to-trough decline.

        Measures the largest percentage drop from any peak to subsequent trough.
        Key risk metric showing worst-case loss an investor could have experienced.

        Parameters
        ----------
        start_month : str or datetime, optional
            Start of the period (inclusive). If None, uses inception date.
        end_month : str or datetime, optional
            End of the period (inclusive). If None, uses latest date.

        Returns
        -------
        float
            Maximum drawdown as a decimal (e.g., 0.25 means 25% peak-to-trough loss).

        Example
        -------
        >>> fund.max_drawdown("2020-01", "2023-12")
        0.1834  # Worst drawdown was 18.34% from peak
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
        Calculate the win rate (percentage of months with positive returns).

        Measures consistency of positive performance. Higher values indicate
        more reliable positive months, though doesn't account for magnitude.

        Parameters
        ----------
        start_month : str or datetime, optional
            Start of the period (inclusive). If None, uses inception date.
        end_month : str or datetime, optional
            End of the period (inclusive). If None, uses latest date.

        Returns
        -------
        float
            Fraction of months with positive returns (e.g., 0.65 means 65% win rate).

        Example
        -------
        >>> fund.positive_months("2020-01", "2023-12")
        0.5833  # 58.33% of months had positive returns
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
        Calculate average return during winning months.

        Shows the typical gain magnitude when the fund is up. Compare with
        return_in_negative_months to assess gain/loss asymmetry.

        Parameters
        ----------
        start_month : str or datetime, optional
            Start of the period (inclusive). If None, uses inception date.
        end_month : str or datetime, optional
            End of the period (inclusive). If None, uses latest date.

        Returns
        -------
        float
            Average monthly return during positive months as decimal.

        Example
        -------
        >>> fund.return_in_positive_months("2020-01", "2023-12")
        0.0312  # Average gain of 3.12% in winning months
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
        Calculate average return during losing months.

        Shows the typical loss magnitude when the fund is down. Compare with
        return_in_positive_months to assess gain/loss asymmetry.

        Parameters
        ----------
        start_month : str or datetime, optional
            Start of the period (inclusive). If None, uses inception date.
        end_month : str or datetime, optional
            End of the period (inclusive). If None, uses latest date.

        Returns
        -------
        float
            Average monthly return during negative months as decimal (negative value).

        Example
        -------
        >>> fund.return_in_negative_months("2020-01", "2023-12")
        -0.0198  # Average loss of 1.98% in losing months
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

    def join_two_funds(self, benchmark_fund=None, start_month=None, end_month=None):
        """
        Align this fund's returns with a benchmark over their common date range.

        Finds the overlapping period between both funds and the requested date range,
        then extracts matching return values. Used internally by correlation, beta,
        and capture ratio calculations.

        Parameters
        ----------
        benchmark_fund : Fund
            The benchmark Fund object to align with.
        start_month : str or datetime, optional
            Requested start date. Will be adjusted to common range.
        end_month : str or datetime, optional
            Requested end date. Will be adjusted to common range.

        Returns
        -------
        tuple
            (fund_values, benchmark_values) - two aligned lists of decimal returns.
            Returns empty lists if no overlapping period.
        """
        benchmark_fund = benchmark_fund or self.default_benchmark
        if benchmark_fund is None:
            raise ValueError(
                f"{self.name}: benchmark_fund required but none provided and no default_benchmark set"
            )
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

    def correlation_to(self, benchmark_fund=None, start_month=None, end_month=None):
        """
        Calculate Pearson correlation coefficient with a benchmark.

        Measures how closely the fund's returns move with the benchmark.
        Range is -1 to +1:
        - +1: Perfect positive correlation (moves together)
        - 0: No linear relationship
        - -1: Perfect negative correlation (moves opposite)

        Parameters
        ----------
        benchmark_fund : Fund
            The benchmark Fund object to correlate with.
        start_month : str or datetime, optional
            Start of the period (inclusive). If None, uses common inception.
        end_month : str or datetime, optional
            End of the period (inclusive). If None, uses common end date.

        Returns
        -------
        float
            Correlation coefficient between -1 and +1.

        Example
        -------
        >>> fund.correlation_to(msci_china, "2020-01", "2023-12")
        0.72  # Moderate positive correlation
        """
        benchmark_fund = benchmark_fund or self.default_benchmark
        if benchmark_fund is None:
            raise ValueError(
                f"{self.name}: benchmark_fund required but none provided and no default_benchmark set"
            )
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

    def beta_to(self, benchmark_fund=None, start_month=None, end_month=None):
        """
        Calculate beta (market sensitivity) relative to a benchmark.

        Beta measures how much the fund moves for each 1% move in the benchmark:
        - Beta = 1.0: Moves in line with benchmark
        - Beta > 1.0: More volatile than benchmark (amplified moves)
        - Beta < 1.0: Less volatile than benchmark (dampened moves)
        - Beta < 0: Moves opposite to benchmark

        Calculated as: Cov(fund, benchmark) / Var(benchmark)

        Parameters
        ----------
        benchmark_fund : Fund
            The benchmark Fund object (typically a market index).
        start_month : str or datetime, optional
            Start of the period (inclusive). If None, uses common inception.
        end_month : str or datetime, optional
            End of the period (inclusive). If None, uses common end date.

        Returns
        -------
        float
            Beta coefficient relative to the benchmark.

        Example
        -------
        >>> fund.beta_to(msci_china, "2020-01", "2023-12")
        0.65  # Fund captures 65% of benchmark's moves
        """
        benchmark_fund = benchmark_fund or self.default_benchmark
        if benchmark_fund is None:
            raise ValueError(
                f"{self.name}: benchmark_fund required but none provided and no default_benchmark set"
            )
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

    def upside_capture_ratio(
        self, benchmark_fund=None, start_month=None, end_month=None
    ):
        """
        Calculate upside capture ratio relative to a benchmark.

        Measures fund participation in benchmark gains during up markets.
        Compares compounded fund returns to compounded benchmark returns
        only during months when the benchmark was positive.

        Interpretation:
        - 100%: Fund matches benchmark gains
        - >100%: Fund outperforms in up markets (desirable)
        - <100%: Fund underperforms in up markets

        Parameters
        ----------
        benchmark_fund : Fund
            The benchmark Fund object to compare against.
        start_month : str or datetime, optional
            Start of the period (inclusive).
        end_month : str or datetime, optional
            End of the period (inclusive).

        Returns
        -------
        float
            Upside capture as percentage (e.g., 110 means 110%).
            Returns None if no positive benchmark months.

        Example
        -------
        >>> fund.upside_capture_ratio(msci_china, "2020-01", "2023-12")
        85.5  # Fund captures 85.5% of benchmark's upside
        """
        benchmark_fund = benchmark_fund or self.default_benchmark
        if benchmark_fund is None:
            raise ValueError(
                f"{self.name}: benchmark_fund required but none provided and no default_benchmark set"
            )
        fund_values, benchmark_values = self.join_two_funds(
            benchmark_fund=benchmark_fund, start_month=start_month, end_month=end_month
        )

        if not fund_values or not benchmark_values:
            return None

        # Find months where benchmark is positive
        fund_up = []
        bench_up = []
        for f_val, b_val in zip(fund_values, benchmark_values):
            if b_val > 0:
                fund_up.append(f_val)
                bench_up.append(b_val)

        if not bench_up:
            return None

        # Compound returns for up months
        fund_compound = np.prod([1 + r for r in fund_up]) - 1
        bench_compound = np.prod([1 + r for r in bench_up]) - 1

        if bench_compound == 0:
            return None

        return (fund_compound / bench_compound) * 100

    def downside_capture_ratio(
        self, benchmark_fund=None, start_month=None, end_month=None
    ):
        """
        Calculate downside capture ratio relative to a benchmark.

        Measures fund participation in benchmark losses during down markets.
        Compares compounded fund returns to compounded benchmark returns
        only during months when the benchmark was negative.

        Interpretation:
        - 100%: Fund matches benchmark losses
        - <100%: Fund loses less in down markets (desirable)
        - >100%: Fund loses more in down markets (concerning)

        Parameters
        ----------
        benchmark_fund : Fund
            The benchmark Fund object to compare against.
        start_month : str or datetime, optional
            Start of the period (inclusive).
        end_month : str or datetime, optional
            End of the period (inclusive).

        Returns
        -------
        float
            Downside capture as percentage (e.g., 70 means 70%).
            Returns None if no negative benchmark months.

        Example
        -------
        >>> fund.downside_capture_ratio(msci_china, "2020-01", "2023-12")
        65.0  # Fund only captures 65% of benchmark's downside (good)
        """
        benchmark_fund = benchmark_fund or self.default_benchmark
        if benchmark_fund is None:
            raise ValueError(
                f"{self.name}: benchmark_fund required but none provided and no default_benchmark set"
            )
        fund_values, benchmark_values = self.join_two_funds(
            benchmark_fund=benchmark_fund, start_month=start_month, end_month=end_month
        )

        if not fund_values or not benchmark_values:
            return None

        # Find months where benchmark is negative
        fund_down = []
        bench_down = []
        for f_val, b_val in zip(fund_values, benchmark_values):
            if b_val < 0:
                fund_down.append(f_val)
                bench_down.append(b_val)

        if not bench_down:
            return None

        # Compound returns for down months
        fund_compound = np.prod([1 + r for r in fund_down]) - 1
        bench_compound = np.prod([1 + r for r in bench_down]) - 1

        if bench_compound == 0:
            return None

        return (fund_compound / bench_compound) * 100

    def capture_ratio(self, benchmark_fund=None, start_month=None, end_month=None):
        """
        Calculate the capture ratio (upside capture / downside capture).

        Combines upside and downside capture into a single metric that shows
        how asymmetrically the fund participates in market moves.

        Interpretation:
        - > 1.0: Captures more upside than downside (desirable)
        - = 1.0: Symmetric participation
        - < 1.0: Captures more downside than upside (concerning)

        Parameters
        ----------
        benchmark_fund : Fund
            The benchmark Fund object to compare against.
        start_month : str or datetime, optional
            Start of the period (inclusive).
        end_month : str or datetime, optional
            End of the period (inclusive).

        Returns
        -------
        float
            Ratio of upside to downside capture.
            Returns None if either capture ratio cannot be computed.

        Example
        -------
        >>> fund.capture_ratio(msci_china, "2020-01", "2023-12")
        1.31  # Upside 85.5% / Downside 65% = 1.31 (favorable asymmetry)
        """
        benchmark_fund = benchmark_fund or self.default_benchmark
        if benchmark_fund is None:
            raise ValueError(
                f"{self.name}: benchmark_fund required but none provided and no default_benchmark set"
            )
        upside = self.upside_capture_ratio(benchmark_fund, start_month, end_month)
        downside = self.downside_capture_ratio(benchmark_fund, start_month, end_month)

        if upside is None or downside is None or downside == 0:
            return None

        return upside / downside

    def plot_monthly_return_distribution(
        self,
        *,
        start_month: str | None = None,  # "YYYY-MM"
        end_month: str | None = None,  # "YYYY-MM"
        bins: int = 24,
        show_stats_lines: bool = True,
        save=False,
    ):
        """
        Create a histogram of monthly return distribution with KDE overlay.

        Visualizes the shape of return distribution including frequency of
        positive/negative months. Includes statistical annotations (mean,
        median, percentiles) and a smoothed KDE curve.

        Parameters
        ----------
        start_month : str, optional
            Start of the period in 'YYYY-MM' format. Defaults to inception.
        end_month : str, optional
            End of the period in 'YYYY-MM' format. Defaults to latest date.
        bins : int, default 24
            Number of histogram bins.
        show_stats_lines : bool, default True
            Whether to show vertical lines for mean and zero.
        save : bool, default False
            If True, saves PNG to output directory.

        Returns
        -------
        plotly.graph_objects.Figure
            Interactive Plotly figure object.
        """
        from math import pi, sqrt

        # ----- style + palette (inlined here) -----
        layout_config = {
            "font": dict(family="Open Sans, Roboto", size=14, color="#53565A"),
            "margin": dict(l=54, r=42, t=84, b=72),
            "grid_color": "#e9e9ea",
        }
        color = "#C1AE94"  # keep your requested color
        fund_name = self.name or "Fund"

        # ----- clamp date window to available history -----
        sm = parse_month(start_month) if start_month else self.inception_date
        em = parse_month(end_month) if end_month else self.latest_date

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
            else:
                vals.append(float(e["value"]))

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
                font=dict(size=22),
                x=0.5,
                xanchor="center",
                y=0.925,
                yanchor="middle",
            ),
            template="plotly_white",
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
        if save:
            file_name = f"{self.name} monthly return distribution plot {sm.strftime('%Y-%m-%d')} to {em.strftime('%Y-%m-%d')}.png"
            save_path = f"{save_dir}/{file_name}"
            fig.write_image(save_path, scale=2)
        return fig

    def compare_worst_performance(
        self,
        benchmark_fund=None,
        n_worst=10,
        title="Fund Performance on Benchmark's Worst Days",
        save=False,
    ):
        """
        Compare fund behavior during benchmark's worst months.

        Creates a bar chart showing how the fund performed during the N worst
        benchmark months. Useful for assessing downside protection and crisis
        behavior. Includes average lines for both fund and benchmark.

        Parameters
        ----------
        benchmark_fund : Fund
            The benchmark Fund object to compare against.
        n_worst : int, default 10
            Number of worst benchmark months to analyze.
        title : str, default "Fund Performance on Benchmark's Worst Days"
            Chart title.
        save : bool, default False
            If True, saves PNG to output directory.

        Returns
        -------
        plotly.graph_objects.Figure
            Interactive grouped bar chart comparing returns.
        """
        benchmark_fund = benchmark_fund or self.default_benchmark
        if benchmark_fund is None:
            raise ValueError(
                f"{self.name}: benchmark_fund required but none provided and no default_benchmark set"
            )

        # Align both series by date
        fund_returns = self.monthly_returns
        bench_returns = benchmark_fund.monthly_returns
        fund_returns = pd.Series(
            {entry["datetime"]: entry["value"] for entry in fund_returns}
        )
        bench_returns = pd.Series(
            {entry["datetime"]: entry["value"] for entry in bench_returns}
        )
        # Combine into a DataFrame
        combined = pd.concat([fund_returns, bench_returns], axis=1, join="inner")
        combined.columns = [self.name, benchmark_fund.name]

        # Find N worst benchmark returns (lowest values)
        worst_bench = combined[benchmark_fund.name].nsmallest(n_worst)

        # Keep those same dates and sort by severity (worst to least bad)
        worst_df = combined.loc[worst_bench.index].sort_values(by=benchmark_fund.name)

        # Assign rank labels for x-axis (1 = worst)
        worst_df["Rank"] = range(1, len(worst_df) + 1)
        x_labels = [f"#{r}" for r in worst_df["Rank"]]

        # Build chart
        fig = go.Figure()

        # Benchmark bars (grey)
        fig.add_trace(
            go.Bar(
                x=x_labels,
                y=worst_df[benchmark_fund.name],
                name=f"{benchmark_fund.name} Returns",
                marker_color="rgba(152,154,156,0.8)",
                hovertemplate="Rank: %{x}<br>Return: %{y:.2%}<extra>Benchmark</extra>",
            )
        )

        # Fund bars (gold)
        fig.add_trace(
            go.Bar(
                x=x_labels,
                y=worst_df[self.name],
                name=f"{self.name} Returns",
                marker_color="rgba(193,174,148,0.85)",
                hovertemplate="Rank: %{x}<br>Return: %{y:.2%}<extra>Fund</extra>",
            )
        )

        # Average horizontal lines
        fig.add_hline(
            y=worst_df[self.name].mean(),
            line_dash="dot",
            line_color="rgba(193,174,148,0.6)",
            annotation_text=f"{self.name} Avg: {worst_df[self.name].mean():.2%}",
            annotation_position="top left",
        )
        fig.add_hline(
            y=worst_df[benchmark_fund.name].mean(),
            line_dash="dot",
            line_color="rgba(152,154,156,0.6)",
            annotation_text=f"{benchmark_fund.name} Avg: {worst_df[benchmark_fund.name].mean():.2%}",
            annotation_position="bottom left",
        )

        # Layout and aesthetics
        fig.update_layout(
            title=dict(text=f"<b>{title}</b>", x=0.5, font=dict(size=20)),
            xaxis=dict(
                title=f"Top {n_worst} Worst Benchmark Returns (Ranked)",
                tickfont=dict(size=10),
                showgrid=False,
            ),
            yaxis=dict(
                title="Daily Return",
                tickformat=".2%",
                zeroline=True,
                zerolinecolor="black",
            ),
            barmode="group",
            hovermode="x unified",
            template="plotly_white",
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5
            ),
            margin=dict(l=60, r=40, t=60, b=80),
        )

        if save:
            file_name = f"{self.name}_vs_{benchmark_fund.name}_{title}.png"
            save_path = f"{save_dir}/{file_name}"
            fig.write_image(save_path, scale=2)
            print(f"Chart saved to {file_name}")

        return fig

    def plot_rolling_vol_vs_benchmark(
        self,
        benchmark_fund=None,
        window=12,
        title="Rolling Volatility (annually)",
        save=False,
    ):
        """
        Plot rolling volatility time series with optional benchmark comparison.

        Visualizes how fund risk evolves over time using trailing window volatility.
        Shows historical volatility levels as horizontal reference lines and
        annotates volatility-of-volatility for stability assessment.

        Parameters
        ----------
        benchmark_fund : Fund, optional
            Benchmark to overlay for comparison. If None, shows fund only.
        window : int, default 12
            Rolling window size in months (12 = trailing 1-year).
        title : str, default "Rolling Volatility (annually)"
            Chart title.
        save : bool, default False
            If True, saves PNG to output directory.

        Returns
        -------
        plotly.graph_objects.Figure
            Interactive line chart with volatility time series.
        """

        benchmark_fund = benchmark_fund or self.default_benchmark

        def to_py_dt(idx):
            if isinstance(idx, pd.PeriodIndex):
                idx = idx.to_timestamp()
            idx = pd.to_datetime(idx, errors="coerce")
            idx = pd.DatetimeIndex(idx).tz_localize(None)
            return [ts.to_pydatetime() for ts in idx]

        # Fund rolling vol & vol-of-vol
        fund_rv = self.rolling_volatility(window=window)
        fund_rv.index = pd.to_datetime(fund_rv.index)
        fund_rv = fund_rv.dropna()

        x_fund = to_py_dt(fund_rv.index)

        fund_vov = self.vol_of_vol(window=window)

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=x_fund,
                y=fund_rv.values,
                mode="lines",
                name=f"{self.name} (rolling vol)",
                line=dict(color="#C1AE94", width=2, dash="solid"),
                hovertemplate="Date: %{x|%Y-%m}<br>Vol: %{y:.4f}<extra>Fund</extra>",
            )
        )
        fig.add_hline(
            y=self.total_vol,
            line_dash="dash",  # solid, dash, dot, etc.
            line_color="rgba(193, 174, 148, 0.5)",
            annotation_text=f"{self.name}'s Historical Vol",
            annotation=dict(  # <-- styling only
                font=dict(family="Roboto Bold", size=11, color="#C1AE94"),
                align="left",
                bgcolor="rgba(0,0,0,0)",  # optional
            ),
            annotation_position="top right",
        )

        annotation_text = (
            f"<b>Vol-of-Vol (std of rolling vol)</b><br>{self.name}: {fund_vov:.4f}"
        )

        # Benchmark if provided
        if benchmark_fund is not None:
            bench_rv = benchmark_fund.rolling_volatility(window=window)
            bench_rv.index = pd.to_datetime(bench_rv.index)
            bench_vov = benchmark_fund.vol_of_vol(window=window)
            bench_rv = bench_rv.dropna()

            # Align series for plotting

            x_bench = to_py_dt(bench_rv.index)
            start_date = max(min(x_bench), min(x_fund))
            end_date = min(max(x_bench), max(x_fund))
            fig.update_xaxes(range=[start_date, end_date])

            fig.add_trace(
                go.Scatter(
                    x=x_bench,
                    y=bench_rv.values,
                    mode="lines",
                    name=f"{benchmark_fund.name} (rolling vol)",
                    line=dict(color="#989A9C", width=2, dash="solid"),
                    hovertemplate="Date: %{x|%Y-%m}<br>Vol: %{y:.4f}<extra>Benchmark</extra>",
                )
            )
            fig.add_hline(
                y=benchmark_fund.total_vol,
                line_dash="dash",  # solid, dash, dot, etc.
                line_color="rgba(152,154,156,0.5)",
                annotation_text=f"{benchmark_fund.name}'s Historical Vol",
                annotation=dict(  # <-- styling only
                    font=dict(family="Roboto Bold", size=11, color="#989A9C"),
                    align="left",
                    bgcolor="rgba(0,0,0,0)",  # optional
                ),
                annotation_position="top right",
            )

            annotation_text += f"<br>{benchmark_fund.name}: {bench_vov:.4f}"
        # Layout
        fig.update_layout(
            title=dict(
                text=f"<b>{self.name} -- {title}<b>",  # Title text
                x=0.5,  # Centered (0=left, 0.5=center, 1=right)
                y=0.925,  # Vertical position
                xanchor="center",  # Anchor position
                yanchor="middle",
                font=dict(
                    size=22,  # Font size
                    color="black",  # Font color
                ),
            ),
            legend=dict(
                orientation="v",
                font=dict(size=9),
                yanchor="bottom",
                y=-0.25,
                xanchor="right",
                x=1,
            ),
            margin=dict(l=60, r=40, t=60, b=100),
            xaxis=dict(title="Date", tickfont=dict(size=12, color="#53565A")),
            yaxis=dict(
                title="Rolling Volatility", tickfont=dict(size=12, color="#53565A")
            ),
            hovermode="x unified",
            template="plotly_white",
        )
        fig.add_annotation(
            xref="paper",
            yref="paper",
            x=0.98,
            y=0.98,
            xanchor="right",
            yanchor="top",
            align="right",
            showarrow=False,
            text=annotation_text,
            bgcolor="#F6F6F7",
            bordercolor=hex_to_rgba("#C1AE94", 0.9),
            borderwidth=1,
        )
        if save:
            file_name = f"{self.name} rolling vol plot.png"
            save_path = f"{save_dir}/{file_name}"
            fig.write_image(save_path, scale=2)
        return fig

    def export_monthly_table(
        self,
        language: str = "en",
        benchmark_fund: Fund = None,
        benchmark_name: str = None,
        inception_column: bool = False,
        end_month=None,
        save=False,
    ):
        """
        Create a calendar-style table of monthly returns by year.

        Generates a grid showing Jan-Dec returns for each year, plus YTD and
        optional since-inception columns. Can include benchmark for comparison
        (alternating rows). Styled with matplotlib for clean PNG export.

        Parameters
        ----------
        language : str, default "en"
            Language for labels ("en" for English, "cn" for Chinese).
        benchmark_fund : Fund, optional
            If provided, adds alternating benchmark rows for comparison.
        benchmark_name : str, optional
            Custom display name for benchmark. Uses benchmark_fund.name if None.
        inception_column : bool, default False
            If True, adds a cumulative since-inception column.
        end_month : str, optional
            Last month to include ('YYYY-MM'). Defaults to latest available.
        save : bool, default False
            If True, saves PNG to output directory.

        Returns
        -------
        matplotlib.pyplot
            Matplotlib figure object (call .show() to display).
        """
        benchmark_fund = benchmark_fund or self.default_benchmark
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
            "en": {"ytd_label": "YTD", "year": "Year", "inc": "Since\nInception"},
            "cn": {"ytd_label": "年初至今", "year": "年分", "inc": "成立至今"},
        }

        if language == "en":
            table_labels = month_labels["en"]
            ytd_label = other_labels["en"]["ytd_label"]
            year_label = other_labels["en"]["year"]
            inc_label = other_labels["en"]["inc"]
        elif language == "cn":
            table_labels = month_labels["cn"]
            ytd_label = other_labels["cn"]["ytd_label"]
            year_label = other_labels["cn"]["year"]
            inc_label = other_labels["cn"]["inc"]
        # ---------------- prepare data ----------------
        if end_month is None:
            end_month_dt = self.latest_date
        else:
            end_month_dt = pd.to_datetime(end_month + "-01")

        df = [entry for entry in self.monthly_returns if entry["month"] <= end_month_dt]

        monthly_returns_list = [df]
        if benchmark_fund:
            benchmark_monthly_returns = [
                entry
                for entry in benchmark_fund.monthly_returns
                if (entry["month"] in [e["month"] for e in self.monthly_returns])
            ]
            monthly_returns_list.append(benchmark_monthly_returns)
        df_list = []
        for i, monthly_returns in enumerate(monthly_returns_list):
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
            if inception_column == True:
                # make sure only the first fund is calculated
                if i == 0:
                    df_sorted = df.sort_index(ascending=True)
                    # Compound the YTD multipliers and subtract 1
                    df_sorted["INC"] = (1.0 + df_sorted["YTD"]).cumprod() - 1.0
                    # Put the new column back on your original order (if you had it sorted desc)
                    df["INC"] = df_sorted["INC"].reindex(df.index)
                else:
                    df["INC"] = np.nan

            def pct_str(x):
                """Format a float as a percentage string."""
                return f"{x * 100:,.2f}%" if pd.notna(x) else ""

            df = df.map(pct_str)
            # append to list
            df_list.append(df)
        #
        if inception_column == True:
            end_label = [ytd_label, inc_label]
        else:
            end_label = [ytd_label]
        if len(df_list) == 2:
            rows = []
            for year in df_list[0].index:
                row_value = df_list[0].loc[year].tolist()
                row_benchmark = (
                    df_list[1].loc[year].fillna("-").tolist()
                    if year in df_list[1].index
                    else ["-"] * df_list[1].shape[1]
                )

                row_benchmark = [
                    b if v else "" for v, b in zip(row_value, row_benchmark)
                ]
                rows.append([year] + row_value)
                rows.append(
                    [benchmark_name if benchmark_name else benchmark_fund.name]
                    + row_benchmark
                )
            df = pd.DataFrame(rows, columns=["year"] + df_list[0].columns.to_list())

            df.columns = [year_label] + table_labels + end_label
        else:
            df = df_list[0]
            df.columns = table_labels + end_label
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
        double_line = False
        if benchmark_name:
            double_line = ("\n" in benchmark_name) or inception_column
        scaler = 0.75 if double_line else 1
        font_size = find_largest_font_size(cell_height * 0.55 * scaler, FONT2HEIGHT)
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
                    if benchmark_fund and row % 2 == 1
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
        if save:
            file_name = (
                f"{self.name} monthly return table {end_month_dt.strftime('%Y-%m-%d')}"
                + (f" {benchmark_fund.name}" if benchmark_fund else "")
                + ".png"
            )
            # file_name = self.name + ' key metrics table ' + end_month_dt + benchmark.name if benchmark else '' + '.png'
            save_path = f"{save_dir}/{file_name}"
            plt.savefig(save_path, bbox_inches="tight", pad_inches=0, dpi=200)
        return plt

    def export_key_metrics_table(
        self,
        end_month: str,
        benchmark_fund=None,
        language="en",
        metrics=None,
        horizontal: bool = False,
        fix_aspect: bool = False,
        save=False,
    ):
        """
        Create a summary table of key performance metrics.

        Generates a styled table with configurable metrics including return,
        risk, and benchmark-relative measures. Supports vertical (2-column)
        or horizontal (1-row) layouts.

        Parameters
        ----------
        end_month : str
            End date for calculations in 'YYYY-MM' format.
        benchmark_fund : Fund, optional
            Required for beta, correlation, and capture ratio metrics.
        language : str, default "en"
            Language for labels ("en" for English, "cn" for Chinese).
        metrics : list of str, optional
            Specific metrics to include. Options: 'cagr', 'vol', 'sharpe',
            'sortino', 'cum', 'mdd', 'beta', 'corr', 'capture', 'win',
            'best', 'worst', 'aum', 'skew', 'kurt', 'turnover'.
            If None, uses default order.
        horizontal : bool, default False
            If True, creates horizontal single-row layout.
        fix_aspect : bool, default False
            If True with horizontal=True, fixes aspect ratio.
        save : bool, default False
            If True, saves PNG to output directory.

        Returns
        -------
        matplotlib.figure.Figure
            Matplotlib figure object.
        """
        benchmark_fund = benchmark_fund or self.default_benchmark
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
                "win": "Percentage of Positive Return Months",
                "best": "Best Month",
                "worst": "Worst Month",
                "aum": "AUM",
                "skew": "Skewness",
                "kurt": "Kurtosis",
                "turnover": "Avg. Monthly Turnover",
                "capture": f"Capture Ratio (vs. {benchmark_fund.name})",
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
                "capture": f"捕获比率（{benchmark_fund.name}）",
            },
        }
        L = labels.get(language, labels["en"])
        end_month = parse_month(end_month)
        placeholder_values = {
            "cagr": f"{100 * self.annualized_return(self.inception_date, end_month):.1f}%",
            "vol": f"{100 * self.volatility(self.inception_date, end_month):.1f}%",
            "mdd": f"{100 * self.max_drawdown(self.inception_date, end_month):.1f}%",
            "cum": f"{100 * self.cumulative_return(self.inception_date, end_month):.1f}%",
            "win": f"{self.positive_months(self.inception_date, end_month) * 100:.2f}%",
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
            "capture": f"{self.capture_ratio(benchmark_fund, self.inception_date, end_month):.2f}",
        }

        default_order = [
            "cagr",
            "vol",
            "sharpe",
            "sortino",
            "mdd",
            "beta",
            "corr",
            "capture",
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
        if save:
            save_path = f"{save_dir}/{self.name} key metrics table {end_month.strftime('%Y-%m-%d')}.png"
            plt.savefig(save_path, bbox_inches="tight", pad_inches=0, dpi=200)
        return fig

    def export_correlation_table(
        self,
        end_month: str,
        benchmark_funds,
        language: str = "en",
        save: bool = False,
    ):
        """
        Create a horizontal table showing correlations to multiple benchmarks.

        Displays fund correlation coefficients against a set of benchmarks
        in a single-row format. Useful for presenting diversification metrics.

        Parameters
        ----------
        end_month : str
            End date in 'YYYY-MM' format (used for reference, correlations
            calculated from inception to latest common date).
        benchmark_funds : dict
            Dictionary of {name: Fund} benchmark objects to correlate with.
        language : str, default "en"
            Language for labels ("en" for English, "cn" for Chinese).
        save : bool, default False
            If True, saves PNG to output directory.

        Returns
        -------
        matplotlib.figure.Figure
            Matplotlib figure object.

        Raises
        ------
        ValueError
            If benchmark_funds is empty.
        """

        if not benchmark_funds:
            raise ValueError("benchmark_funds must be a non-empty list.")

        header_fill = "#cbb69d"
        cell_fill = "#f0f0f0"
        text_color = "black"

        labels = {
            "en": {
                "benchmark": "Benchmark",
                "corr": "Correlation",
            },
            "cn": {
                "benchmark": "基准",
                "corr": "相关性",
            },
        }
        L = labels.get(language, labels["en"])

        end_month = parse_month(end_month)

        # Compute correlations vs each benchmark
        benchmark_names = list(benchmark_funds.keys())
        corr_values = [
            f"{self.correlation_to(b, self.inception_date, self.latest_date):.2f}"
            for b in benchmark_funds.values()
        ]

        font_size = 30
        font_width = FONT2HEIGHT[font_size]
        font_height = FONT2HEIGHT[font_size]

        # Use L to localize a stub column (first column) and the header label
        cell_text = [
            [L["corr"], *corr_values]
        ]  # one data row with a localized stub cell
        col_labels = [
            L["benchmark"],
            *benchmark_names,
        ]  # header row with a localized first cell

        col_widths = [
            font_width * max(len(lab), len(val)) * 1.2
            for lab, val in zip(col_labels, cell_text[0])
        ]
        table_width = sum(col_widths)

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

        if save:
            save_path = (
                f"{save_dir}/{self.name} correlation table "
                f"{end_month.strftime('%Y-%m-%d')}.png"
            )
            plt.savefig(save_path, bbox_inches="tight", pad_inches=0, dpi=200)

        return fig

    def summary_of_a_fund(
        self, benchmark_fund=None, language="en", save=False, show=True
    ):
        """
        Generate a comprehensive fund analysis with multiple visualizations.

        Produces a complete fund report including:
        1. Fund description and contact info (printed)
        2. Monthly returns calendar table
        3. Key performance metrics table
        4. Return distribution histogram
        5. Rolling volatility time series
        6. Worst month comparison vs benchmark

        Parameters
        ----------
        benchmark_fund : Fund, optional
            Benchmark for comparative analysis. Required for relative metrics.
        language : str, default "en"
            Language for labels ("en" for English, "cn" for Chinese).
        save : bool, default False
            If True, saves all charts as PNGs to output directory.
        show : bool, default True
            If False, suppresses interactive display of charts.

        Returns
        -------
        None
            Displays all charts interactively via .show() calls (when show=True).
        """
        benchmark_fund = benchmark_fund or self.default_benchmark

        if show:
            print(self.one_liner)
            print(
                f"Net Exposure = {min(self.net_exposure) * 100}% to {max(self.net_exposure) * 100}%"
            ) if self.net_exposure else None
            print(self.contact_info)
        endmonth_str = datetime.strftime(self.latest_date, format="%Y-%m")
        plot1 = self.export_monthly_table(
            language, benchmark_fund=benchmark_fund, save=save
        )
        plot2 = self.export_key_metrics_table(
            benchmark_fund=benchmark_fund,
            end_month=endmonth_str,
            language=language,
            metrics=["cagr", "vol", "sharpe", "sortino", "mdd", "beta", "corr", "win"],
            horizontal=False,
            save=save,
        )
        plot3 = self.plot_monthly_return_distribution(save=save)
        plot4 = self.plot_rolling_vol_vs_benchmark(
            benchmark_fund=benchmark_fund, save=save
        )
        plot5 = self.compare_worst_performance(
            benchmark_fund,
            n_worst=10,
            title="Fund Performance on Benchmark's Worst Days",
            save=save,
        )

        if show:
            plot1.show()
            plot2.show()
            plot3.show()
            plot4.show()
            plot5.show()


def compare_funds(fund_dict, benchmark_fund=None):
    """
    Create a comparison DataFrame of multiple funds with key metrics.

    Aggregates fund metadata and performance statistics into a single table
    for side-by-side comparison. Includes descriptive info, fees, and all
    major performance metrics.

    Parameters
    ----------
    fund_dict : dict
        Dictionary mapping fund names to Fund objects.
    benchmark_fund : Fund, optional
        If provided, adds a "Capture Ratio" column showing each fund's
        upside/downside capture relative to the benchmark.

    Returns
    -------
    pd.DataFrame
        DataFrame with one row per fund containing:
        - Metadata: Name, Description, Location, Strategy, Sector, Managers,
          Contact, AUM, Net Exposure, Net Return, Fees
        - Dates: Inception Date, Latest Date, # Months
        - Performance: Cumulative Return, Annualized Return, Volatility,
          Sharpe Ratio, Sortino Ratio, Max Drawdown, Positive Months
        - Benchmark-relative (if benchmark_fund provided): Capture Ratio

    Example
    -------
    >>> df = compare_funds({"Fund A": fund_a, "Fund B": fund_b}, benchmark_fund=msci)
    >>> df[["Name", "Annualized Return", "Sharpe Ratio", "Capture Ratio"]]
    """
    data = []
    for name, fund in fund_dict.items():
        row = {
            "Name": fund.name,
            "One Liner": fund.one_liner,
            "Geo Focus": fund.geo_focus,
            "Strategy": fund.strategy,
            "Asset Class": fund.asset_class,
            "Identifier": fund.identifier,
            "IR Contact": fund.contact_info,
            "AUM (in Mn USD)": fund.aum_size,
            "Net Exposure": fund.net_exposure_info,
            "Net Return": fund.net_return,
            "Mgmt Fee": fund.management_fee,
            "Perf Fee": fund.performance_fee,
            "Inception Date": fund.inception_date,
            "Latest Date": fund.latest_date,
            "Month Running": (fund.latest_date - fund.inception_date).days / 30.0
            if fund.inception_date and fund.latest_date
            else None,
            "# Months": fund.num_months,
            "Cumulative Return": fund.total_cum_rtn,
            "Annualized Return": fund.total_ann_rtn,
            "Volatility": fund.total_vol,
            "Sharpe Ratio": fund.total_sharpe,
            "Sortino Ratio": fund.total_sortino,
            "Max Drawdown": fund.total_max_dd,
            "Positive Months": fund.total_pos_months,
        }
        bm = benchmark_fund or fund.default_benchmark
        if bm is not None:
            row["Capture Ratio"] = fund.capture_ratio(
                bm, fund.inception_date, fund.latest_date
            )
        data.append(row)

    df = pd.DataFrame(data)
    return df
