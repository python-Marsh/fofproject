"""
Fund Workstation - Streamlit UI
A standalone web interface for fund-of-funds investment analysis.
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime
from dateutil.relativedelta import relativedelta
import math
import json
import os
from pathlib import Path

# Page configuration
st.set_page_config(
    page_title="Fund Workstation",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==================== BRAND PALETTE ====================
COLORS = {
    "primary": "#2F2F2F",
    "accent": "#C1AE94",
    "accent_light": "#DACEBF",
    "text": "#53565A",
    "text_light": "#989A9C",
    "bg": "#FAFAFA",
    "card": "#FFFFFF",
    "success": "#81B29A",
    "danger": "#E07A5F",
    "chart": ["#2F2F2F", "#C1AE94", "#53565A", "#81B29A", "#8CA3A0",
              "#A59BA0", "#E07A5F", "#3D405B", "#DACEBF", "#989A9C"],
}


def check_password():
    """Returns True if the user has entered a correct password."""
    def password_entered():
        if st.session_state["password"] == st.secrets["password"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        _render_login()
        st.text_input("Password", type="password", on_change=password_entered, key="password")
        return False

    if st.session_state["password_correct"]:
        return True

    _render_login()
    st.text_input("Password", type="password", on_change=password_entered, key="password")
    st.error("Incorrect password. Please try again.")
    return False


def _render_login():
    st.markdown("""
    <div style="text-align:center; padding:4rem 0 2rem 0;">
        <div style="font-size:3rem; font-weight:800; color:#2F2F2F; letter-spacing:-1px;">
            Fund Workstation
        </div>
        <div style="font-size:1rem; color:#989A9C; margin-top:0.5rem;">
            Enter the password to continue
        </div>
    </div>
    """, unsafe_allow_html=True)


# ==================== GLOBAL CSS ====================
st.markdown("""
<style>
    /* ---- Root overrides ---- */
    .stApp { background: #FAFAFA; }

    /* ---- Sidebar ---- */
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #2F2F2F 0%, #3D405B 100%);
    }
    section[data-testid="stSidebar"] * {
        color: #E8E0D8 !important;
    }
    section[data-testid="stSidebar"] .stRadio label {
        padding: 0.55rem 0.75rem;
        border-radius: 8px;
        transition: background 0.15s;
        font-size: 0.92rem;
    }
    section[data-testid="stSidebar"] .stRadio label:hover {
        background: rgba(193,174,148,0.18);
    }
    section[data-testid="stSidebar"] .stRadio label[data-checked="true"],
    section[data-testid="stSidebar"] input[type="radio"]:checked + div {
        background: rgba(193,174,148,0.25) !important;
    }
    section[data-testid="stSidebar"] hr {
        border-color: rgba(255,255,255,0.12);
    }

    /* ---- Page header ---- */
    .page-header {
        font-size: 1.75rem;
        font-weight: 700;
        color: #2F2F2F;
        margin-bottom: 0.25rem;
        letter-spacing: -0.5px;
    }
    .page-subtitle {
        font-size: 0.95rem;
        color: #989A9C;
        margin-bottom: 1.5rem;
    }

    /* ---- Metric cards ---- */
    .metric-card {
        background: #FFFFFF;
        border: 1px solid #EAEAEA;
        border-radius: 12px;
        padding: 1.25rem 1.5rem;
        text-align: center;
        transition: box-shadow 0.2s;
    }
    .metric-card:hover {
        box-shadow: 0 4px 20px rgba(0,0,0,0.06);
    }
    .metric-label {
        font-size: 0.78rem;
        color: #989A9C;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin-bottom: 0.35rem;
    }
    .metric-value {
        font-size: 1.6rem;
        font-weight: 700;
        color: #2F2F2F;
    }
    .metric-value.positive { color: #81B29A; }
    .metric-value.negative { color: #E07A5F; }

    /* ---- Section dividers ---- */
    .section-title {
        font-size: 1.1rem;
        font-weight: 600;
        color: #2F2F2F;
        margin: 2rem 0 0.75rem 0;
        padding-bottom: 0.4rem;
        border-bottom: 2px solid #C1AE94;
        display: inline-block;
    }

    /* ---- Tables ---- */
    .stDataFrame { border-radius: 10px; overflow: hidden; }

    /* ---- Hide Streamlit branding ---- */
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }
    header[data-testid="stHeader"] { background: transparent; }

    /* ---- File uploader ---- */
    section[data-testid="stSidebar"] .stFileUploader label {
        font-size: 0.85rem;
    }

    /* ---- Plotly chart container ---- */
    .stPlotlyChart { border-radius: 12px; overflow: hidden; }

    /* ---- Tabs ---- */
    .stTabs [data-baseweb="tab-list"] { gap: 0.5rem; }
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px 8px 0 0;
        padding: 0.5rem 1.25rem;
        font-weight: 500;
    }
</style>
""", unsafe_allow_html=True)


# ==================== FUND CLASS ====================

class Fund:
    """Standalone Fund class for Streamlit deployment."""

    def __init__(self, name: str, monthly_returns: list,
                 performance_fee: float = 0.2, management_fee: float = 0.01):
        self.name = name
        self.performance_fee = performance_fee
        self.management_fee = management_fee

        processed_returns = []
        for entry in monthly_returns:
            raw_date = entry["date"]
            dt = datetime.strptime(str(raw_date), "%d/%m/%Y")
            processed_returns.append({
                "datetime": dt,
                "month": datetime(dt.year, dt.month, 1),
                "value": entry["value"],
            })
        processed_returns.sort(key=lambda x: x["datetime"])
        self.monthly_returns = processed_returns

        self.inception_date = min(e["month"] for e in self.monthly_returns) if self.monthly_returns else None
        self.latest_date = max(e["month"] for e in self.monthly_returns) if self.monthly_returns else None
        self.num_months = len(self.monthly_returns)

        if self.monthly_returns:
            self.total_cum_rtn = self.cumulative_return(self.inception_date, self.latest_date)
            self.total_ann_rtn = self.annualized_return(self.inception_date, self.latest_date)
            self.total_vol = self.volatility(self.inception_date, self.latest_date)
            self.total_sharpe = self.sharpe_ratio(self.inception_date, self.latest_date)
            self.total_sortino = self.sortino_ratio(self.inception_date, self.latest_date)
            self.total_max_dd = self.max_drawdown(self.inception_date, self.latest_date)
            self.total_pos_months = self.positive_months(self.inception_date, self.latest_date)
        else:
            self.total_cum_rtn = self.total_ann_rtn = self.total_vol = None
            self.total_sharpe = self.total_sortino = self.total_max_dd = self.total_pos_months = None

    def _parse_month(self, m):
        if m is None:
            return None
        if isinstance(m, str):
            parts = m.split("-")
            return datetime(int(parts[0]), int(parts[1]), 1)
        return m

    def _get_values(self, start_month, end_month):
        start_month = self._parse_month(start_month)
        end_month = self._parse_month(end_month)
        return [
            float(e["value"])
            for e in self.monthly_returns
            if (start_month is None or start_month <= e["month"])
            and (end_month is None or e["month"] <= end_month)
        ]

    def cumulative_return(self, start_month, end_month) -> float:
        vals = self._get_values(start_month, end_month)
        value = 1.0
        for v in vals:
            value *= 1 + v
        return value - 1.0

    def annualized_return(self, start_month, end_month) -> float:
        cumulative = self.cumulative_return(start_month, end_month)
        start_date = self._parse_month(start_month)
        end_date = self._parse_month(end_month)
        months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month) + 1
        return (1 + cumulative) ** (12 / months) - 1

    def volatility(self, start_month=None, end_month=None) -> float:
        vals = self._get_values(start_month, end_month)
        if not vals:
            return 0.0
        return float(pd.Series(vals, dtype="float64").std(ddof=1)) * math.sqrt(12.0)

    def rolling_volatility(self, window=12):
        """Compute rolling annualized volatility."""
        vals = [(e["month"], float(e["value"])) for e in self.monthly_returns]
        if len(vals) < window:
            return [], []
        dates, returns = zip(*vals)
        s = pd.Series(returns, index=dates)
        rolling = s.rolling(window).std(ddof=1) * math.sqrt(12.0)
        mask = rolling.notna()
        return list(rolling.index[mask]), list(rolling[mask])

    def sharpe_ratio(self, start_month=None, end_month=None, risk_free_rate=0.0) -> float:
        ann_return = self.annualized_return(start_month, end_month)
        vol = self.volatility(start_month, end_month)
        if vol == 0.0:
            return 0.0
        return (ann_return - risk_free_rate) / vol

    def sortino_ratio(self, start_month=None, end_month=None, risk_free_rate=0.0) -> float:
        vals = self._get_values(start_month, end_month)
        if not vals:
            return 0.0
        s = np.array(vals)
        monthly_rf = (1 + risk_free_rate) ** (1 / 12) - 1
        downside = np.minimum(0, s - monthly_rf)
        downside_deviation = np.sqrt(np.sum(downside**2) / (len(s) + 1)) * np.sqrt(12)
        if downside_deviation == 0:
            return 0.0
        ann_return = self.annualized_return(start_month, end_month)
        return (ann_return - risk_free_rate) / downside_deviation

    def max_drawdown(self, start_month=None, end_month=None) -> float:
        start_dt = self._parse_month(start_month)
        end_dt = self._parse_month(end_month)
        cum_value = 1.0
        values = []
        for entry in self.monthly_returns:
            if start_dt <= entry["month"] <= end_dt:
                cum_value *= 1 + float(entry["value"])
                values.append(cum_value - 1.0)
        if not values:
            return 0.0
        cumulative = np.array(values)
        running_max = np.maximum.accumulate(cumulative)
        drawdowns = (running_max - cumulative) / (1 + running_max)
        return float(np.max(drawdowns))

    def drawdown_series(self, start_month=None, end_month=None):
        """Return dates and drawdown values for plotting."""
        start_dt = self._parse_month(start_month)
        end_dt = self._parse_month(end_month)
        dates = []
        cum_value = 1.0
        cum_values = []
        for entry in self.monthly_returns:
            if start_dt <= entry["month"] <= end_dt:
                cum_value *= 1 + float(entry["value"])
                cum_values.append(cum_value)
                dates.append(entry["month"])
        if not cum_values:
            return [], []
        arr = np.array(cum_values)
        running_max = np.maximum.accumulate(arr)
        dd = (arr - running_max) / running_max
        return dates, dd.tolist()

    def positive_months(self, start_month=None, end_month=None) -> float:
        vals = self._get_values(start_month, end_month)
        if not vals:
            return 0.0
        return sum(1 for v in vals if v > 0) / len(vals)

    def beta_to(self, benchmark, start_month=None, end_month=None) -> float:
        start_month = self._parse_month(start_month)
        end_month = self._parse_month(end_month)
        bench_dict = {e["month"]: float(e["value"]) for e in benchmark.monthly_returns}
        fund_vals, bench_vals = [], []
        for e in self.monthly_returns:
            m = e["month"]
            if (start_month is None or start_month <= m) and (end_month is None or m <= end_month):
                if m in bench_dict:
                    fund_vals.append(float(e["value"]))
                    bench_vals.append(bench_dict[m])
        if len(fund_vals) < 3:
            return 0.0
        cov = np.cov(fund_vals, bench_vals)
        if cov[1, 1] == 0:
            return 0.0
        return float(cov[0, 1] / cov[1, 1])

    def correlation_to(self, benchmark, start_month=None, end_month=None) -> float:
        start_month = self._parse_month(start_month)
        end_month = self._parse_month(end_month)
        bench_dict = {e["month"]: float(e["value"]) for e in benchmark.monthly_returns}
        fund_vals, bench_vals = [], []
        for e in self.monthly_returns:
            m = e["month"]
            if (start_month is None or start_month <= m) and (end_month is None or m <= end_month):
                if m in bench_dict:
                    fund_vals.append(float(e["value"]))
                    bench_vals.append(bench_dict[m])
        if len(fund_vals) < 3:
            return 0.0
        return float(np.corrcoef(fund_vals, bench_vals)[0, 1])

    def upside_capture(self, benchmark, start_month=None, end_month=None) -> float:
        start_month = self._parse_month(start_month)
        end_month = self._parse_month(end_month)
        bench_dict = {e["month"]: float(e["value"]) for e in benchmark.monthly_returns}
        fund_up, bench_up = [], []
        for e in self.monthly_returns:
            m = e["month"]
            if (start_month is None or start_month <= m) and (end_month is None or m <= end_month):
                if m in bench_dict and bench_dict[m] > 0:
                    fund_up.append(float(e["value"]))
                    bench_up.append(bench_dict[m])
        if not bench_up:
            return 0.0
        fund_geo = np.prod([1 + r for r in fund_up]) ** (1 / len(fund_up)) - 1
        bench_geo = np.prod([1 + r for r in bench_up]) ** (1 / len(bench_up)) - 1
        if bench_geo == 0:
            return 0.0
        return fund_geo / bench_geo

    def downside_capture(self, benchmark, start_month=None, end_month=None) -> float:
        start_month = self._parse_month(start_month)
        end_month = self._parse_month(end_month)
        bench_dict = {e["month"]: float(e["value"]) for e in benchmark.monthly_returns}
        fund_dn, bench_dn = [], []
        for e in self.monthly_returns:
            m = e["month"]
            if (start_month is None or start_month <= m) and (end_month is None or m <= end_month):
                if m in bench_dict and bench_dict[m] < 0:
                    fund_dn.append(float(e["value"]))
                    bench_dn.append(bench_dict[m])
        if not bench_dn:
            return 0.0
        fund_geo = np.prod([1 + r for r in fund_dn]) ** (1 / len(fund_dn)) - 1
        bench_geo = np.prod([1 + r for r in bench_dn]) ** (1 / len(bench_dn)) - 1
        if bench_geo == 0:
            return 0.0
        return fund_geo / bench_geo


# ==================== HELPER FUNCTIONS ====================

def load_funds_from_uploaded(uploaded_file):
    """Load funds from an uploaded CSV file."""
    df = pd.read_csv(uploaded_file)
    funds = {}
    for col in df.columns:
        if col == "date":
            continue
        returns = [
            {"date": d, "value": v} for d, v in zip(df["date"], df[col]) if pd.notna(v)
        ]
        if returns:
            try:
                funds[col] = Fund(name=col, monthly_returns=returns,
                                  performance_fee=0.2, management_fee=0.01)
            except Exception:
                pass
    return funds


def load_funds_from_json(uploaded_file):
    """Load funds from an uploaded JSON file."""
    data = json.load(uploaded_file)
    funds = {}
    if isinstance(data, dict):
        for name, returns in data.items():
            if isinstance(returns, list) and returns:
                try:
                    funds[name] = Fund(name=name, monthly_returns=returns,
                                       performance_fee=0.2, management_fee=0.01)
                except Exception:
                    pass
    return funds


def list_shared_folder_files():
    """List CSV/JSON files from the configured shared network folder."""
    shared_path = st.secrets.get("shared_data_path", "")
    if not shared_path:
        return []
    folder = Path(shared_path)
    if not folder.is_dir():
        return []
    files = sorted(
        [f for f in folder.iterdir() if f.suffix.lower() in (".csv", ".json") and f.is_file()],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return files


def load_funds_from_path(file_path: Path):
    """Load funds from a file path on disk (shared folder)."""
    ext = file_path.suffix.lower()
    if ext == ".csv":
        with open(file_path, "r") as f:
            return load_funds_from_uploaded(f)
    elif ext == ".json":
        with open(file_path, "r") as f:
            return load_funds_from_json(f)
    return {}


def compare_funds(fund_dict):
    data = []
    for name, fund in fund_dict.items():
        data.append({
            "Name": fund.name,
            "Months": fund.num_months,
            "Cumul. Return": fund.total_cum_rtn,
            "Ann. Return": fund.total_ann_rtn,
            "Volatility": fund.total_vol,
            "Sharpe": fund.total_sharpe,
            "Sortino": fund.total_sortino,
            "Max DD": fund.total_max_dd,
            "Pos. Months": fund.total_pos_months,
        })
    return pd.DataFrame(data)


def get_fund_date_range(funds: dict):
    if not funds:
        return None, None
    start_dates = [f.inception_date for f in funds.values() if f.inception_date]
    end_dates = [f.latest_date for f in funds.values() if f.latest_date]
    return max(start_dates) if start_dates else None, min(end_dates) if end_dates else None


# ==================== CHART BUILDERS ====================

def _plotly_theme(fig, height=500):
    """Apply consistent theme to all charts."""
    fig.update_layout(
        template="plotly_white",
        font=dict(family="Inter, system-ui, sans-serif", size=13, color=COLORS["text"]),
        margin=dict(l=50, r=30, t=60, b=50),
        height=height,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5,
                    font=dict(size=11)),
        hovermode="x unified",
        plot_bgcolor="white",
        paper_bgcolor="white",
    )
    fig.update_xaxes(gridcolor="#F0F0F0", zeroline=False)
    fig.update_yaxes(gridcolor="#F0F0F0", zeroline=False)
    return fig


def create_cumulative_chart(funds: dict, start_month=None, end_month=None):
    if not funds:
        return None

    valid_funds = {k: v for k, v in funds.items() if v is not None}
    if not valid_funds:
        return None

    start_dt = datetime.strptime(start_month, "%Y-%m") if start_month else None
    end_dt = datetime.strptime(end_month, "%Y-%m") if end_month else None

    all_start = [f.monthly_returns[0]["month"] for f in valid_funds.values()]
    all_end = [f.monthly_returns[-1]["month"] for f in valid_funds.values()]

    if start_dt is None or start_dt < max(all_start):
        start_dt = max(all_start)
    if end_dt is None or end_dt > min(all_end):
        end_dt = min(all_end)

    prev_month = start_dt - relativedelta(months=1)
    fig = go.Figure()

    for idx, (name, fund) in enumerate(valid_funds.items()):
        months, cum_returns = [], []
        cum_value = 1.0
        for entry in fund.monthly_returns:
            m = entry["month"]
            if prev_month < m <= end_dt:
                cum_value *= (1 + float(entry["value"]))
                months.append(m)
                cum_returns.append(cum_value - 1.0)

        color = COLORS["chart"][idx % len(COLORS["chart"])]
        fig.add_trace(go.Scatter(
            x=months, y=cum_returns, mode="lines", name=name,
            line=dict(color=color, width=2.5 if idx == 0 else 1.8),
        ))

    fig.update_layout(
        title=dict(text=f"Cumulative Returns  ({start_dt.strftime('%Y-%m')} to {end_dt.strftime('%Y-%m')})",
                   font=dict(size=16, color=COLORS["primary"])),
        yaxis_tickformat=".0%",
    )
    fig.add_hline(y=0, line_dash="dot", line_color="#D0D0D0", opacity=0.6)
    return _plotly_theme(fig, height=480)


def create_correlation_heatmap(funds: dict, min_overlap: int = 12):
    series = {}
    for name, f in funds.items():
        if f is None:
            continue
        s = pd.Series({
            e["month"]: float(e["value"])
            for e in f.monthly_returns if e.get("value") is not None
        }).sort_index()
        series[name] = s

    if not series:
        return None, None

    wide = pd.DataFrame(series)
    corr = wide.corr(method="pearson", min_periods=min_overlap)
    funds_order = list(corr.columns)
    z = corr.loc[funds_order, funds_order].values

    text = np.empty_like(z, dtype=object)
    for i in range(len(funds_order)):
        for j in range(len(funds_order)):
            r = z[i, j]
            text[i, j] = f"{r:.2f}" if not pd.isna(r) else "-"

    fig = go.Figure(data=go.Heatmap(
        z=z, x=funds_order, y=funds_order,
        colorscale=[[0, "#E07A5F"], [0.5, "#FFFFFF"], [1, "#81B29A"]],
        zmin=-1, zmax=1, zmid=0,
        colorbar=dict(title="r", thickness=15, len=0.7),
        text=text, texttemplate="%{text}", textfont=dict(size=12),
    ))
    fig.update_layout(
        title=dict(text="Correlation Matrix", font=dict(size=16, color=COLORS["primary"])),
    )
    return _plotly_theme(fig, height=max(420, len(funds_order) * 42)), corr


def create_drawdown_chart(funds: dict, start_month=None, end_month=None):
    if not funds:
        return None

    start_dt = datetime.strptime(start_month, "%Y-%m") if start_month else None
    end_dt = datetime.strptime(end_month, "%Y-%m") if end_month else None

    fig = go.Figure()
    for idx, (name, fund) in enumerate(funds.items()):
        s = start_dt or fund.inception_date
        e = end_dt or fund.latest_date
        dates, dd = fund.drawdown_series(s, e)
        if not dates:
            continue
        color = COLORS["chart"][idx % len(COLORS["chart"])]
        fig.add_trace(go.Scatter(
            x=dates, y=dd, mode="lines", name=name,
            fill="tozeroy",
            line=dict(color=color, width=1.5),
            fillcolor=f"rgba({int(color[1:3],16)},{int(color[3:5],16)},{int(color[5:7],16)},0.12)",
        ))

    fig.update_layout(
        title=dict(text="Underwater (Drawdown) Chart", font=dict(size=16, color=COLORS["primary"])),
        yaxis_tickformat=".0%",
        yaxis_title="Drawdown",
    )
    return _plotly_theme(fig, height=400)


def create_rolling_vol_chart(funds: dict, window=12):
    if not funds:
        return None
    fig = go.Figure()
    for idx, (name, fund) in enumerate(funds.items()):
        dates, vol = fund.rolling_volatility(window)
        if not dates:
            continue
        color = COLORS["chart"][idx % len(COLORS["chart"])]
        fig.add_trace(go.Scatter(
            x=dates, y=vol, mode="lines", name=name,
            line=dict(color=color, width=1.8),
        ))
    fig.update_layout(
        title=dict(text=f"Rolling {window}-Month Volatility (Annualized)",
                   font=dict(size=16, color=COLORS["primary"])),
        yaxis_tickformat=".0%",
        yaxis_title="Volatility",
    )
    return _plotly_theme(fig, height=400)


def create_return_distribution(fund):
    vals = [float(e["value"]) for e in fund.monthly_returns]
    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=vals, nbinsx=30, name=fund.name,
        marker=dict(color=COLORS["accent"], line=dict(color=COLORS["primary"], width=0.5)),
    ))
    avg = np.mean(vals)
    fig.add_vline(x=avg, line_dash="dash", line_color=COLORS["primary"], opacity=0.7,
                  annotation_text=f"Mean: {avg:.2%}", annotation_position="top right")
    fig.add_vline(x=0, line_dash="dot", line_color="#D0D0D0", opacity=0.5)
    fig.update_layout(
        title=dict(text="Monthly Return Distribution", font=dict(size=16, color=COLORS["primary"])),
        xaxis_tickformat=".1%", xaxis_title="Monthly Return",
        yaxis_title="Frequency", bargap=0.05, showlegend=False,
    )
    return _plotly_theme(fig, height=380)


def create_risk_return_scatter(funds: dict):
    if not funds:
        return None

    names, vols, returns = [], [], []
    for name, f in funds.items():
        if f.total_vol is not None and f.total_ann_rtn is not None:
            names.append(name)
            vols.append(f.total_vol)
            returns.append(f.total_ann_rtn)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=vols, y=returns, mode="markers+text", text=names,
        textposition="top center", textfont=dict(size=10, color=COLORS["text"]),
        marker=dict(size=12, color=COLORS["accent"], line=dict(color=COLORS["primary"], width=1.5)),
    ))
    fig.update_layout(
        title=dict(text="Risk-Return Scatter", font=dict(size=16, color=COLORS["primary"])),
        xaxis_title="Annualized Volatility", yaxis_title="Annualized Return",
        xaxis_tickformat=".0%", yaxis_tickformat=".0%",
    )
    fig.add_hline(y=0, line_dash="dot", line_color="#D0D0D0", opacity=0.5)
    return _plotly_theme(fig, height=480)


def create_mvo_chart(funds: dict, mode="Minimum Variance", target_return=0.14):
    """Mean-Variance Optimization using pypfopt."""
    try:
        from pypfopt import EfficientFrontier
    except ImportError:
        return None, None, None

    series = {}
    start_dates, end_dates = [], []
    for name, f in funds.items():
        s = pd.Series({
            e["month"]: float(e["value"])
            for e in f.monthly_returns if e.get("value") is not None
        }).sort_index()
        series[name] = s
        start_dates.append(s.index.min())
        end_dates.append(s.index.max())

    returns_df = pd.DataFrame(series)
    common_start = max(start_dates)
    common_end = min(end_dates)
    filtered = returns_df.loc[common_start:common_end]
    n_months = len(filtered)

    ann_rtn = {}
    for col in filtered.columns:
        ann_rtn[col] = funds[col].annualized_return(common_start, common_end)
    mu = pd.Series(ann_rtn)
    S = filtered.cov() * 12

    ef = EfficientFrontier(mu, S)
    if mode == "Minimum Variance":
        weights = ef.min_volatility()
    elif mode == "Maximum Sharpe":
        weights = ef.max_sharpe()
    else:
        weights = ef.efficient_return(target_return=target_return)

    annual_rtn, annual_vol, annual_sharpe = ef.portfolio_performance(verbose=False)

    sorted_items = sorted(weights.items(), key=lambda x: x[1], reverse=True)
    names_sorted = [k for k, v in sorted_items]
    values_sorted = [v for k, v in sorted_items]

    fig = go.Figure(data=go.Bar(
        x=names_sorted, y=values_sorted,
        marker=dict(color=COLORS["accent"], line=dict(color=COLORS["primary"], width=1)),
        hovertemplate="<b>%{x}</b><br>Weight: %{y:.2%}<extra></extra>",
        width=0.5,
    ))
    fig.update_layout(
        title=dict(text=f"Portfolio Weights - {mode}", font=dict(size=16, color=COLORS["primary"])),
        xaxis=dict(showgrid=False, tickangle=45),
        yaxis=dict(title="Weight", tickformat=".0%"),
    )

    stats = {
        "Overlapping Months": n_months,
        "Expected Return": annual_rtn,
        "Volatility": annual_vol,
        "Sharpe Ratio": annual_sharpe,
    }
    return _plotly_theme(fig, height=450), weights, stats


# ==================== UI COMPONENTS ====================

def metric_card(label, value, fmt="pct", positive_is_good=True):
    """Render a styled metric card."""
    if value is None:
        display = "-"
        css_class = ""
    elif fmt == "pct":
        display = f"{value:.2%}"
        css_class = " positive" if (value > 0 and positive_is_good) or (value < 0 and not positive_is_good) else " negative" if (value < 0 and positive_is_good) or (value > 0 and not positive_is_good) else ""
    elif fmt == "ratio":
        display = f"{value:.2f}"
        css_class = " positive" if value > 0 else " negative" if value < 0 else ""
    elif fmt == "int":
        display = str(int(value))
        css_class = ""
    else:
        display = str(value)
        css_class = ""

    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">{label}</div>
        <div class="metric-value{css_class}">{display}</div>
    </div>
    """, unsafe_allow_html=True)


def page_header(title, subtitle=""):
    st.markdown(f'<div class="page-header">{title}</div>', unsafe_allow_html=True)
    if subtitle:
        st.markdown(f'<div class="page-subtitle">{subtitle}</div>', unsafe_allow_html=True)


def section_title(title):
    st.markdown(f'<div class="section-title">{title}</div>', unsafe_allow_html=True)


# ==================== PAGE RENDERERS ====================

def render_overview(funds, fund_names):
    page_header("Dashboard", "Overview of all uploaded funds")

    # Top-level stats
    n_funds = len(funds)
    avg_sharpe = np.mean([f.total_sharpe for f in funds.values() if f.total_sharpe is not None])
    avg_return = np.mean([f.total_ann_rtn for f in funds.values() if f.total_ann_rtn is not None])
    avg_vol = np.mean([f.total_vol for f in funds.values() if f.total_vol is not None])

    cols = st.columns(4)
    with cols[0]:
        metric_card("Total Funds", n_funds, fmt="int")
    with cols[1]:
        metric_card("Avg Ann. Return", avg_return)
    with cols[2]:
        metric_card("Avg Volatility", avg_vol, positive_is_good=False)
    with cols[3]:
        metric_card("Avg Sharpe", avg_sharpe, fmt="ratio")

    st.markdown("")

    # Risk-Return Scatter
    fig = create_risk_return_scatter(funds)
    if fig:
        st.plotly_chart(fig, use_container_width=True)

    # Comparison table
    section_title("Fund Comparison")
    df = compare_funds(funds)
    sort_col = st.selectbox("Sort by", options=["Sharpe", "Ann. Return", "Volatility", "Sortino", "Max DD"],
                            index=0)
    ascending = sort_col in ["Volatility", "Max DD"]
    sorted_df = df.sort_values(by=sort_col, ascending=ascending)

    display_df = sorted_df.copy()
    for col in display_df.columns:
        if col in ["Ann. Return", "Volatility", "Max DD", "Cumul. Return", "Pos. Months"]:
            display_df[col] = display_df[col].apply(lambda x: f"{x:.2%}" if pd.notna(x) else "-")
        elif col in ["Sharpe", "Sortino"]:
            display_df[col] = display_df[col].apply(lambda x: f"{x:.2f}" if pd.notna(x) else "-")
    st.dataframe(display_df.reset_index(drop=True), use_container_width=True, height=min(500, 40 + len(df) * 35))


def render_performance(funds, fund_names, common_start, common_end):
    page_header("Performance", "Cumulative returns and drawdown analysis")

    selected = st.multiselect("Select funds", options=fund_names, default=fund_names[:5])

    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("Start", value=common_start or datetime(2020, 1, 1))
    with col2:
        end_date = st.date_input("End", value=common_end or datetime.now())

    if not selected:
        st.info("Select at least one fund to display charts.")
        return

    funds_to_plot = {k: funds[k] for k in selected if k in funds}

    tab1, tab2 = st.tabs(["Cumulative Returns", "Drawdown"])

    with tab1:
        fig = create_cumulative_chart(funds_to_plot, start_date.strftime("%Y-%m"), end_date.strftime("%Y-%m"))
        if fig:
            st.plotly_chart(fig, use_container_width=True)

    with tab2:
        fig = create_drawdown_chart(funds_to_plot, start_date.strftime("%Y-%m"), end_date.strftime("%Y-%m"))
        if fig:
            st.plotly_chart(fig, use_container_width=True)


def render_risk(funds, fund_names):
    page_header("Risk Analysis", "Volatility, correlation, and rolling risk metrics")

    tab1, tab2 = st.tabs(["Rolling Volatility", "Correlation"])

    with tab1:
        selected = st.multiselect("Select funds for vol chart", options=fund_names,
                                  default=fund_names[:4], key="risk_vol_select")
        window = st.slider("Rolling window (months)", min_value=3, max_value=36, value=12)
        if selected:
            funds_subset = {k: funds[k] for k in selected if k in funds}
            fig = create_rolling_vol_chart(funds_subset, window=window)
            if fig:
                st.plotly_chart(fig, use_container_width=True)

    with tab2:
        selected_corr = st.multiselect("Select funds for correlation", options=fund_names,
                                       default=fund_names[:6], key="risk_corr_select")
        min_overlap = st.number_input("Min. overlap (months)", value=12, min_value=3, max_value=60)
        if len(selected_corr) >= 2:
            funds_corr = {k: funds[k] for k in selected_corr if k in funds}
            fig, corr_df = create_correlation_heatmap(funds_corr, min_overlap=min_overlap)
            if fig:
                st.plotly_chart(fig, use_container_width=True)


def render_fund_details(funds, fund_names):
    page_header("Fund Details", "Deep dive into individual fund metrics")

    selected_fund = st.selectbox("Select fund", options=fund_names)
    if not selected_fund or selected_fund not in funds:
        return

    fund = funds[selected_fund]

    # Metric cards row 1
    cols = st.columns(4)
    with cols[0]:
        metric_card("Ann. Return", fund.total_ann_rtn)
    with cols[1]:
        metric_card("Volatility", fund.total_vol, positive_is_good=False)
    with cols[2]:
        metric_card("Sharpe Ratio", fund.total_sharpe, fmt="ratio")
    with cols[3]:
        metric_card("Max Drawdown", fund.total_max_dd, positive_is_good=False)

    cols2 = st.columns(4)
    with cols2[0]:
        metric_card("Sortino Ratio", fund.total_sortino, fmt="ratio")
    with cols2[1]:
        metric_card("Cumulative Return", fund.total_cum_rtn)
    with cols2[2]:
        metric_card("Positive Months", fund.total_pos_months)
    with cols2[3]:
        metric_card("Track Record", fund.num_months, fmt="int")

    st.markdown("")

    # Benchmark comparison
    other_funds = [n for n in fund_names if n != selected_fund]
    if other_funds:
        benchmark_name = st.selectbox("Compare against benchmark", options=["None"] + other_funds)
        if benchmark_name != "None":
            benchmark = funds[benchmark_name]
            section_title(f"vs {benchmark_name}")
            bc = st.columns(4)
            with bc[0]:
                metric_card("Correlation", fund.correlation_to(benchmark), fmt="ratio")
            with bc[1]:
                metric_card("Beta", fund.beta_to(benchmark), fmt="ratio")
            with bc[2]:
                metric_card("Upside Capture", fund.upside_capture(benchmark))
            with bc[3]:
                metric_card("Downside Capture", fund.downside_capture(benchmark), positive_is_good=False)
            st.markdown("")

    # Tabs for charts / tables
    tab1, tab2, tab3 = st.tabs(["Return Distribution", "Drawdown", "Monthly Returns"])

    with tab1:
        fig = create_return_distribution(fund)
        if fig:
            st.plotly_chart(fig, use_container_width=True)

    with tab2:
        dates, dd = fund.drawdown_series(fund.inception_date, fund.latest_date)
        if dates:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=dates, y=dd, mode="lines", name="Drawdown",
                fill="tozeroy",
                line=dict(color=COLORS["danger"], width=1.5),
                fillcolor="rgba(224,122,95,0.15)",
            ))
            fig.update_layout(
                title=dict(text=f"Drawdown - {fund.name}",
                           font=dict(size=16, color=COLORS["primary"])),
                yaxis_tickformat=".0%", yaxis_title="Drawdown",
            )
            st.plotly_chart(_plotly_theme(fig, height=380), use_container_width=True)

    with tab3:
        returns_data = [{"Year": e["month"].year, "Month": e["month"].strftime("%b"), "Return": e["value"]}
                        for e in fund.monthly_returns]
        if returns_data:
            returns_df = pd.DataFrame(returns_data)
            pivot = returns_df.pivot(index="Year", columns="Month", values="Return")
            month_order = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            pivot = pivot.reindex(columns=[m for m in month_order if m in pivot.columns])
            pivot["YTD"] = pivot.apply(lambda row: (1 + row.dropna()).prod() - 1, axis=1)
            st.dataframe(
                pivot.style.format("{:.2%}", na_rep="-")
                    .background_gradient(cmap="RdYlGn", vmin=-0.1, vmax=0.1),
                use_container_width=True,
            )


def render_optimization(funds, fund_names):
    page_header("Portfolio Optimization", "Mean-variance optimization using PyPortfolioOpt")

    selected = st.multiselect("Select funds for optimization", options=fund_names,
                              default=fund_names[:5], key="mvo_select")

    col1, col2 = st.columns(2)
    with col1:
        mode = st.selectbox("Optimization mode",
                            options=["Maximum Sharpe", "Minimum Variance", "Target Return"])
    with col2:
        target_return = 0.14
        if mode == "Target Return":
            target_return = st.number_input("Target annual return", value=0.14,
                                            min_value=0.0, max_value=1.0, step=0.01, format="%.2f")

    if len(selected) < 2:
        st.info("Select at least 2 funds to run optimization.")
        return

    funds_subset = {k: funds[k] for k in selected if k in funds}

    try:
        fig, weights, stats = create_mvo_chart(funds_subset, mode=mode, target_return=target_return)
    except Exception as e:
        st.error(f"Optimization failed: {e}")
        return

    if fig is None:
        st.warning("pypfopt is required for portfolio optimization. Install with: `pip install pyportfolioopt`")
        return

    # Stats cards
    cols = st.columns(4)
    with cols[0]:
        metric_card("Overlap", stats["Overlapping Months"], fmt="int")
    with cols[1]:
        metric_card("Expected Return", stats["Expected Return"])
    with cols[2]:
        metric_card("Volatility", stats["Volatility"], positive_is_good=False)
    with cols[3]:
        metric_card("Sharpe Ratio", stats["Sharpe Ratio"], fmt="ratio")

    st.markdown("")
    st.plotly_chart(fig, use_container_width=True)

    # Weights table
    section_title("Portfolio Weights")
    weights_df = pd.DataFrame([
        {"Fund": k, "Weight": v} for k, v in sorted(weights.items(), key=lambda x: x[1], reverse=True)
    ])
    weights_df["Weight"] = weights_df["Weight"].apply(lambda x: f"{x:.2%}")
    st.dataframe(weights_df.reset_index(drop=True), use_container_width=True, hide_index=True)


# ==================== MAIN APP ====================

def main():
    if not check_password():
        return

    # Sidebar
    with st.sidebar:
        st.markdown("""
        <div style="padding:1rem 0.5rem 0.5rem 0.5rem; text-align:center;">
            <div style="font-size:1.4rem; font-weight:700; letter-spacing:-0.5px;">
                Fund Workstation
            </div>
            <div style="font-size:0.75rem; opacity:0.6; margin-top:0.2rem;">
                Investment Analytics
            </div>
        </div>
        """, unsafe_allow_html=True)
        st.divider()

        st.markdown("##### Data Source")

        # Check if shared folder is configured
        shared_files = list_shared_folder_files()
        has_shared = len(shared_files) > 0

        if has_shared:
            source = st.radio(
                "source", label_visibility="collapsed",
                options=["Shared Folder", "Upload File"],
                horizontal=True,
            )
        else:
            source = "Upload File"

        if source == "Shared Folder" and has_shared:
            file_options = {f.name: f for f in shared_files}
            selected_file = st.selectbox(
                "Select file", options=list(file_options.keys()),
                label_visibility="collapsed",
            )
            if selected_file:
                st.session_state["shared_file_path"] = file_options[selected_file]
                st.session_state.pop("uploaded_file", None)
        else:
            uploaded_file = st.file_uploader("Upload returns file", type=["csv", "json"],
                                             label_visibility="collapsed")
            if uploaded_file is not None:
                st.session_state["uploaded_file"] = uploaded_file
                st.session_state["file_type"] = uploaded_file.name.split(".")[-1].lower()
                st.session_state.pop("shared_file_path", None)

        st.divider()
        st.markdown("##### Navigation")
        page = st.radio(
            "nav", label_visibility="collapsed",
            options=["Dashboard", "Performance", "Risk Analysis",
                     "Fund Details", "Optimization"],
        )

    # Load data from shared folder or uploaded file
    funds = None

    if "shared_file_path" in st.session_state and st.session_state["shared_file_path"]:
        try:
            funds = load_funds_from_path(st.session_state["shared_file_path"])
        except Exception as e:
            st.error(f"Error reading shared file: {e}")
            return
    elif "uploaded_file" in st.session_state and st.session_state["uploaded_file"] is not None:
        try:
            st.session_state["uploaded_file"].seek(0)
            file_type = st.session_state.get("file_type", "csv")
            if file_type == "json":
                funds = load_funds_from_json(st.session_state["uploaded_file"])
            else:
                funds = load_funds_from_uploaded(st.session_state["uploaded_file"])
        except Exception as e:
            st.error(f"Error loading data: {e}")
            return

    if not funds:
        _render_empty_state()
        return

    fund_names = list(funds.keys())

    common_start, common_end = get_fund_date_range(funds)

    # Route pages
    if page == "Dashboard":
        render_overview(funds, fund_names)
    elif page == "Performance":
        render_performance(funds, fund_names, common_start, common_end)
    elif page == "Risk Analysis":
        render_risk(funds, fund_names)
    elif page == "Fund Details":
        render_fund_details(funds, fund_names)
    elif page == "Optimization":
        render_optimization(funds, fund_names)


def _render_empty_state():
    st.markdown("")
    st.markdown("""
    <div style="text-align:center; padding:5rem 2rem;">
        <div style="font-size:3.5rem; margin-bottom:1rem; opacity:0.3;">📊</div>
        <div style="font-size:1.5rem; font-weight:700; color:#2F2F2F; margin-bottom:0.5rem;">
            Upload your return data
        </div>
        <div style="font-size:0.95rem; color:#989A9C; max-width:500px; margin:0 auto 2rem auto;">
            Upload a CSV or JSON file with monthly returns to get started.
        </div>
    </div>
    """, unsafe_allow_html=True)

    with st.expander("Expected file formats"):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("""
            **CSV Format**
            ```
            date,FUND_A,FUND_B,INDEX
            31/1/2024,0.02,-0.01,0.015
            29/2/2024,0.03,0.02,0.01
            ```
            - First column: `date` (DD/MM/YYYY)
            - Other columns: fund monthly returns
            """)
        with col2:
            st.markdown("""
            **JSON Format**
            ```json
            {
              "FUND_A": [
                {"date": "31/1/2024", "value": 0.02},
                {"date": "29/2/2024", "value": 0.03}
              ]
            }
            ```
            """)


if __name__ == "__main__":
    main()
