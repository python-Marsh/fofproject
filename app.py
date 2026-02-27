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

# Page configuration
st.set_page_config(
    page_title="Fund Workstation",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)


def check_password():
    """Returns True if the user has entered a correct password."""

    def password_entered():
        """Checks whether a password entered by the user is correct."""
        if st.session_state["password"] == st.secrets["password"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.markdown("## 🔐 Fund Workstation")
        st.markdown("Please enter the password to access the dashboard.")
        st.text_input(
            "Password",
            type="password",
            on_change=password_entered,
            key="password"
        )
        return False

    if st.session_state["password_correct"]:
        return True

    st.markdown("## 🔐 Fund Workstation")
    st.text_input(
        "Password",
        type="password",
        on_change=password_entered,
        key="password"
    )
    st.error("😕 Password incorrect. Please try again.")
    return False


# Custom CSS
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: 700;
        color: #2F2F2F;
        margin-bottom: 1rem;
    }
    .stMetric {
        background-color: #f8f9fa;
        padding: 1rem;
        border-radius: 8px;
    }
</style>
""", unsafe_allow_html=True)


# ==================== FUND CLASS (STANDALONE) ====================

class Fund:
    """Standalone Fund class for Streamlit deployment."""

    def __init__(self, name: str, monthly_returns: list, performance_fee: float = 0.2, management_fee: float = 0.01):
        self.name = name
        self.performance_fee = performance_fee
        self.management_fee = management_fee

        # Process returns
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

        # Compute basic properties
        self.inception_date = min(e["month"] for e in self.monthly_returns) if self.monthly_returns else None
        self.latest_date = max(e["month"] for e in self.monthly_returns) if self.monthly_returns else None
        self.num_months = len(self.monthly_returns)

        # Compute metrics
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
        """Parse month string or datetime."""
        if m is None:
            return None
        if isinstance(m, str):
            parts = m.split("-")
            return datetime(int(parts[0]), int(parts[1]), 1)
        return m

    def cumulative_return(self, start_month, end_month) -> float:
        start_month = self._parse_month(start_month)
        end_month = self._parse_month(end_month)
        value = 1.0
        for entry in self.monthly_returns:
            if start_month <= entry["month"] <= end_month:
                value *= 1 + float(entry["value"])
        return value - 1.0

    def annualized_return(self, start_month, end_month) -> float:
        cumulative = self.cumulative_return(start_month, end_month)
        start_date = self._parse_month(start_month)
        end_date = self._parse_month(end_month)
        months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month) + 1
        return (1 + cumulative) ** (12 / months) - 1

    def volatility(self, start_month=None, end_month=None) -> float:
        start_month = self._parse_month(start_month)
        end_month = self._parse_month(end_month)
        vals = []
        for entry in self.monthly_returns:
            m = entry["month"]
            if (start_month is None or start_month <= m) and (end_month is None or m <= end_month):
                vals.append(float(entry["value"]))
        if not vals:
            return 0.0
        s = pd.Series(vals, dtype="float64")
        monthly_vol = float(s.std(ddof=1))
        return monthly_vol * math.sqrt(12.0)

    def sharpe_ratio(self, start_month=None, end_month=None, risk_free_rate=0.0) -> float:
        ann_return = self.annualized_return(start_month, end_month)
        vol = self.volatility(start_month, end_month)
        if vol == 0.0:
            return 0.0
        return (ann_return - risk_free_rate) / vol

    def sortino_ratio(self, start_month=None, end_month=None, risk_free_rate=0.0) -> float:
        start_month = self._parse_month(start_month)
        end_month = self._parse_month(end_month)
        vals = [
            float(entry["value"])
            for entry in self.monthly_returns
            if (start_month is None or start_month <= entry["month"])
            and (end_month is None or entry["month"] <= end_month)
        ]
        if not vals:
            return 0.0
        s = np.array(vals)
        monthly_rf = (1 + risk_free_rate) ** (1 / 12) - 1
        downside = np.minimum(0, s - monthly_rf)
        downside_deviation = np.sqrt((np.sum(downside**2) / (len(s) + 1))) * np.sqrt(12)
        if downside_deviation == 0:
            return 0.0
        ann_return = self.annualized_return(start_month, end_month)
        return (ann_return - risk_free_rate) / downside_deviation

    def max_drawdown(self, start_month=None, end_month=None) -> float:
        start_dt = self._parse_month(start_month)
        end_dt = self._parse_month(end_month)
        values = []
        cum_value = 1.0
        for entry in self.monthly_returns:
            entry_dt = entry["month"]
            if start_dt <= entry_dt <= end_dt:
                cum_value *= 1 + float(entry["value"])
                values.append(cum_value - 1.0)
        if not values:
            return 0.0
        cumulative = np.array(values)
        running_max = np.maximum.accumulate(cumulative)
        drawdowns = (running_max - cumulative) / (1 + running_max)
        return float(np.max(drawdowns))

    def positive_months(self, start_month=None, end_month=None) -> float:
        start_month = self._parse_month(start_month)
        end_month = self._parse_month(end_month)
        positive = 0
        total = 0
        for entry in self.monthly_returns:
            m = entry["month"]
            if (start_month is None or start_month <= m) and (end_month is None or m <= end_month):
                total += 1
                if float(entry["value"]) > 0:
                    positive += 1
        return positive / total if total > 0 else 0.0


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
                funds[col] = Fund(
                    name=col,
                    monthly_returns=returns,
                    performance_fee=0.2,
                    management_fee=0.01,
                )
            except Exception:
                pass  # Skip funds that fail to parse
    return funds


def compare_funds(fund_dict):
    """Create comparison DataFrame from funds."""
    data = []
    for name, fund in fund_dict.items():
        data.append({
            "Name": fund.name,
            "# Months": fund.num_months,
            "Cumulative Return": fund.total_cum_rtn,
            "Annualized Return": fund.total_ann_rtn,
            "Volatility": fund.total_vol,
            "Sharpe Ratio": fund.total_sharpe,
            "Sortino Ratio": fund.total_sortino,
            "Max Drawdown": fund.total_max_dd,
            "Positive Months": fund.total_pos_months,
        })
    return pd.DataFrame(data)


def get_fund_date_range(funds: dict):
    """Get the common date range across all funds."""
    if not funds:
        return None, None
    start_dates = [f.inception_date for f in funds.values() if f.inception_date]
    end_dates = [f.latest_date for f in funds.values() if f.latest_date]
    return max(start_dates) if start_dates else None, min(end_dates) if end_dates else None


def create_correlation_heatmap(funds: dict, min_overlap: int = 12):
    """Create correlation heatmap for selected funds."""
    series = {}
    for name, f in funds.items():
        if f is None:
            continue
        s = pd.Series({
            e["month"]: float(e["value"])
            for e in f.monthly_returns
            if e.get("value") is not None
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
            text[i, j] = f"{r:.2f}" if not pd.isna(r) else "–"

    fig = go.Figure(data=go.Heatmap(
        z=z, x=funds_order, y=funds_order,
        colorscale="RdBu_r", zmin=-1, zmax=1, zmid=0,
        colorbar=dict(title="ρ"),
        text=text, texttemplate="%{text}",
    ))
    fig.update_layout(
        title="Fund Return Correlations",
        height=max(400, len(funds_order) * 40),
        width=max(500, len(funds_order) * 50),
    )
    return fig, corr


def create_cumulative_chart(funds: dict, start_month: str = None, end_month: str = None):
    """Create cumulative returns chart using Plotly."""
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
    colors = ["#2F2F2F", "#C1AE94", "#53565A", "#DACEBF", "#989A9C", "#81B29A", "#8CA3A0", "#A59BA0"]

    fig = go.Figure()

    for idx, (name, fund) in enumerate(valid_funds.items()):
        months = []
        cum_returns = []
        cum_value = 1.0

        for entry in fund.monthly_returns:
            m = entry["month"]
            if prev_month < m <= end_dt:
                cum_value *= (1 + float(entry["value"]))
                months.append(m)
                cum_returns.append(cum_value - 1.0)

        color = colors[idx % len(colors)]
        fig.add_trace(go.Scatter(
            x=months, y=cum_returns, mode="lines", name=name,
            line=dict(color=color, width=3 if idx == 0 else 2),
        ))

    fig.update_layout(
        title=f"Cumulative Returns ({start_dt.strftime('%Y-%m')} to {end_dt.strftime('%Y-%m')})",
        xaxis_title="Date", yaxis_title="Cumulative Return",
        yaxis_tickformat=".0%", hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
        height=500,
    )
    fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
    return fig


# ==================== MAIN APP ====================

def main():
    if not check_password():
        return

    st.markdown('<p class="main-header">📊 Fund Workstation</p>', unsafe_allow_html=True)
    st.markdown("Fund-of-funds investment analysis toolkit")

    # Sidebar
    with st.sidebar:
        st.header("⚙️ Settings")
        st.markdown("### Upload Data")
        uploaded_file = st.file_uploader("Upload returns CSV", type=["csv"])

        if uploaded_file is not None:
            st.session_state["uploaded_file"] = uploaded_file
            st.success("File loaded!")

        st.divider()
        st.markdown("### Navigation")
        page = st.radio(
            "Select View",
            ["📋 Fund Comparison", "📈 Performance Chart", "🔗 Correlation Analysis", "🔍 Fund Details"],
            label_visibility="collapsed"
        )

    # Load funds
    if "uploaded_file" not in st.session_state or st.session_state["uploaded_file"] is None:
        st.warning("Please upload a CSV file with monthly returns data.")
        st.markdown("""
        ### Expected CSV Format
        - First column: `date` (format: DD/MM/YYYY)
        - Other columns: Fund names with monthly return values

        Example:
        ```
        date,FUND_A,FUND_B,INDEX
        31/1/2024,0.02,-0.01,0.015
        29/2/2024,0.03,0.02,0.01
        ```
        """)
        return

    try:
        st.session_state["uploaded_file"].seek(0)
        funds = load_funds_from_uploaded(st.session_state["uploaded_file"])
        fund_names = list(funds.keys())
    except Exception as e:
        st.error(f"Error loading data: {e}")
        return

    common_start, common_end = get_fund_date_range(funds)

    # ==================== FUND COMPARISON ====================
    if "Fund Comparison" in page:
        st.header("📋 Fund Comparison")
        df = compare_funds(funds)

        display_cols = st.multiselect(
            "Select columns to display",
            options=df.columns.tolist(),
            default=["Name", "Annualized Return", "Volatility", "Sharpe Ratio", "Sortino Ratio", "Max Drawdown", "# Months"]
        )

        if display_cols:
            sort_col = st.selectbox("Sort by", options=display_cols,
                                   index=display_cols.index("Sharpe Ratio") if "Sharpe Ratio" in display_cols else 0)
            ascending = st.checkbox("Ascending", value=False)
            sorted_df = df[display_cols].sort_values(by=sort_col, ascending=ascending)

            # Format
            for col in sorted_df.columns:
                if col in ["Annualized Return", "Volatility", "Max Drawdown", "Cumulative Return", "Positive Months"]:
                    sorted_df[col] = sorted_df[col].apply(lambda x: f"{x:.2%}" if pd.notna(x) else "–")
                elif col in ["Sharpe Ratio", "Sortino Ratio"]:
                    sorted_df[col] = sorted_df[col].apply(lambda x: f"{x:.2f}" if pd.notna(x) else "–")

            st.dataframe(sorted_df.reset_index(drop=True), height=500)

    # ==================== PERFORMANCE CHART ====================
    elif "Performance Chart" in page:
        st.header("📈 Cumulative Performance")

        selected_funds = st.multiselect("Select funds to plot", options=fund_names, default=fund_names[:3])

        col1, col2 = st.columns(2)
        with col1:
            start_date = st.date_input("Start Date", value=common_start or datetime(2020, 1, 1))
        with col2:
            end_date = st.date_input("End Date", value=common_end or datetime.now())

        if selected_funds:
            funds_to_plot = {k: funds[k] for k in selected_funds if k in funds}
            fig = create_cumulative_chart(funds_to_plot, start_date.strftime("%Y-%m"), end_date.strftime("%Y-%m"))
            if fig:
                st.plotly_chart(fig, use_container_width=True)

    # ==================== CORRELATION ANALYSIS ====================
    elif "Correlation" in page:
        st.header("🔗 Correlation Analysis")

        selected_funds = st.multiselect("Select funds", options=fund_names, default=fund_names[:5])
        min_overlap = st.number_input("Min. Overlap (months)", value=12, min_value=3, max_value=60)

        if len(selected_funds) >= 2:
            funds_to_analyze = {k: funds[k] for k in selected_funds if k in funds}
            fig, corr_df = create_correlation_heatmap(funds_to_analyze, min_overlap=min_overlap)
            if fig:
                st.plotly_chart(fig, use_container_width=True)
                st.markdown("### Correlation Matrix")
                st.dataframe(corr_df.style.format("{:.2f}").background_gradient(cmap="RdBu_r", vmin=-1, vmax=1))

    # ==================== FUND DETAILS ====================
    elif "Fund Details" in page:
        st.header("🔍 Fund Details")

        selected_fund = st.selectbox("Select a fund", options=fund_names)

        if selected_fund and selected_fund in funds:
            fund = funds[selected_fund]

            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Annualized Return", f"{fund.total_ann_rtn:.2%}" if fund.total_ann_rtn else "–")
            with col2:
                st.metric("Volatility", f"{fund.total_vol:.2%}" if fund.total_vol else "–")
            with col3:
                st.metric("Sharpe Ratio", f"{fund.total_sharpe:.2f}" if fund.total_sharpe else "–")
            with col4:
                st.metric("Max Drawdown", f"{fund.total_max_dd:.2%}" if fund.total_max_dd else "–")

            col5, col6, col7, col8 = st.columns(4)
            with col5:
                st.metric("Sortino Ratio", f"{fund.total_sortino:.2f}" if fund.total_sortino else "–")
            with col6:
                st.metric("Cumulative Return", f"{fund.total_cum_rtn:.2%}" if fund.total_cum_rtn else "–")
            with col7:
                st.metric("Positive Months", f"{fund.total_pos_months:.1%}" if fund.total_pos_months else "–")
            with col8:
                st.metric("# Months", fund.num_months)

            st.divider()

            # Monthly returns table
            st.markdown("### Monthly Returns")
            returns_data = [{"Year": e["month"].year, "Month": e["month"].strftime("%b"), "Return": e["value"]}
                          for e in fund.monthly_returns]

            if returns_data:
                returns_df = pd.DataFrame(returns_data)
                pivot = returns_df.pivot(index="Year", columns="Month", values="Return")
                month_order = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
                pivot = pivot.reindex(columns=[m for m in month_order if m in pivot.columns])
                pivot["YTD"] = pivot.apply(lambda row: (1 + row.dropna()).prod() - 1, axis=1)
                st.dataframe(pivot.style.format("{:.2%}", na_rep="–").background_gradient(cmap="RdYlGn", vmin=-0.1, vmax=0.1))


if __name__ == "__main__":
    main()
