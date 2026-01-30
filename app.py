"""
Fund Workstation - Streamlit UI
A web interface for fund-of-funds investment analysis toolkit.
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime
from dateutil.relativedelta import relativedelta

# Import core functions from the project
from src.fofproject.fund import Fund, input_monthly_returns, subset_of_funds, compare_funds
from src.fofproject.batch import plot_cumulative_returns

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
            del st.session_state["password"]  # Don't store password
        else:
            st.session_state["password_correct"] = False

    # First run or password not correct
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

    # Password correct
    if st.session_state["password_correct"]:
        return True

    # Password incorrect
    st.markdown("## 🔐 Fund Workstation")
    st.text_input(
        "Password",
        type="password",
        on_change=password_entered,
        key="password"
    )
    st.error("😕 Password incorrect. Please try again.")
    return False


# Custom CSS for better styling
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: 700;
        color: #2F2F2F;
        margin-bottom: 1rem;
    }
    .metric-card {
        background-color: #f8f9fa;
        border-radius: 10px;
        padding: 1rem;
        margin: 0.5rem 0;
    }
    .stMetric {
        background-color: #f8f9fa;
        padding: 1rem;
        border-radius: 8px;
    }
</style>
""", unsafe_allow_html=True)


@st.cache_data
def load_funds_from_csv(file_path: str):
    """Load funds from CSV file with caching."""
    return input_monthly_returns(file_path)


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
            funds[col] = Fund(
                name=col,
                monthly_returns=returns,
                performance_fee=0.2,
                management_fee=0.01,
            )
    return funds


def get_fund_date_range(funds: dict):
    """Get the common date range across all funds."""
    if not funds:
        return None, None

    start_dates = []
    end_dates = []
    for fund in funds.values():
        if fund.inception_date:
            start_dates.append(fund.inception_date)
        if fund.latest_date:
            end_dates.append(fund.latest_date)

    return max(start_dates) if start_dates else None, min(end_dates) if end_dates else None


def create_correlation_heatmap(funds: dict, min_overlap: int = 12):
    """Create correlation heatmap for selected funds."""
    # Build DataFrame of monthly returns
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

    # Create heatmap
    funds_order = list(corr.columns)
    z = corr.loc[funds_order, funds_order].values

    text = np.empty_like(z, dtype=object)
    for i in range(len(funds_order)):
        for j in range(len(funds_order)):
            r = z[i, j]
            text[i, j] = f"{r:.2f}" if not pd.isna(r) else "–"

    fig = go.Figure(data=go.Heatmap(
        z=z,
        x=funds_order,
        y=funds_order,
        colorscale="RdBu_r",
        zmin=-1,
        zmax=1,
        zmid=0,
        colorbar=dict(title="ρ"),
        text=text,
        texttemplate="%{text}",
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

    # Filter out None funds
    valid_funds = {k: v for k, v in funds.items() if v is not None}
    if not valid_funds:
        return None

    # Parse dates
    start_dt = datetime.strptime(start_month, "%Y-%m") if start_month else None
    end_dt = datetime.strptime(end_month, "%Y-%m") if end_month else None

    # Get common date range
    all_start = [f.monthly_returns[0]["month"] for f in valid_funds.values()]
    all_end = [f.monthly_returns[-1]["month"] for f in valid_funds.values()]

    if start_dt is None or start_dt < max(all_start):
        start_dt = max(all_start)
    if end_dt is None or end_dt > min(all_end):
        end_dt = min(all_end)

    prev_month = start_dt - relativedelta(months=1)

    # Color palette
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
            x=months,
            y=cum_returns,
            mode="lines",
            name=name,
            line=dict(color=color, width=2 if idx > 0 else 3),
        ))

    fig.update_layout(
        title=f"Cumulative Returns ({start_dt.strftime('%Y-%m')} to {end_dt.strftime('%Y-%m')})",
        xaxis_title="Date",
        yaxis_title="Cumulative Return",
        yaxis_tickformat=".0%",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
        height=500,
    )

    # Add zero line
    fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)

    return fig


def main():
    # Check password first
    if not check_password():
        return

    # Header
    st.markdown('<p class="main-header">📊 Fund Workstation</p>', unsafe_allow_html=True)
    st.markdown("Fund-of-funds investment analysis toolkit")

    # Sidebar
    with st.sidebar:
        st.header("⚙️ Settings")

        # File upload
        st.markdown("### Upload Data")
        uploaded_file = st.file_uploader("Upload returns CSV", type=["csv"])

        if uploaded_file is not None:
            st.session_state["uploaded_file"] = uploaded_file
            st.success(f"File loaded!")

        st.divider()
        st.markdown("### Navigation")
        page = st.radio(
            "Select View",
            ["📋 Fund Comparison", "📈 Performance Chart", "🔗 Correlation Analysis", "🔍 Fund Details"],
            label_visibility="collapsed"
        )

    # Load funds from uploaded file or session state
    if "uploaded_file" not in st.session_state or st.session_state["uploaded_file"] is None:
        st.warning("Please upload a CSV file with monthly returns data.")
        st.markdown("""
        ### Expected CSV Format
        The CSV should have:
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
        # Reset file position and load
        st.session_state["uploaded_file"].seek(0)
        funds = load_funds_from_uploaded(st.session_state["uploaded_file"])
        fund_names = list(funds.keys())
    except Exception as e:
        st.error(f"Error loading data: {e}")
        st.info("Please check your CSV format matches the expected structure.")
        return

    # Define categories
    our_portfolio = ['TAIREN', 'HAO', 'LEXINGTON', 'LIM', 'FOREST', 'WT CHINA', 'E20',
                     '3W GLOBAL', '3W CHINA', '3W HEALTHCARE', 'TIMEFOLIO', 'MONOLITH',
                     'PERSEVERANCE', 'NEO IVY', 'JH BIOTECH', 'RDGFF', 'NEW RDGFF']
    our_indices = ['EUREKAHEDGE WORLD', 'MSCI CHINA', 'MSCI WORLD', 'EUREKAHEDGE ASIA',
                   'TOPIX', 'S&P 500', 'SOX', 'KOSPI', 'TAIEX', 'MSCI EM', 'RUSSELL 2000',
                   'STOXX 600', 'STOXX 50', 'FTSE UK', 'US HEALTHCARE', 'US FINANCIAL',
                   'US ENERGY', 'COMMODITY']

    # Get date range
    common_start, common_end = get_fund_date_range(funds)

    # ==================== FUND COMPARISON ====================
    if "Fund Comparison" in page:
        st.header("📋 Fund Comparison")

        col1, col2 = st.columns([3, 1])
        with col1:
            st.markdown("Compare key metrics across all loaded funds.")
        with col2:
            exclude_portfolio = st.checkbox("Exclude Portfolio Funds", value=False)
            exclude_indices = st.checkbox("Exclude Index Funds", value=False)

        # Build comparison DataFrame
        df = compare_funds(funds)

        # Apply filters
        if exclude_portfolio:
            df = df[~df["Name"].isin(our_portfolio)]
        if exclude_indices:
            df = df[~df["Name"].isin(our_indices)]

        # Select columns to display
        display_cols = st.multiselect(
            "Select columns to display",
            options=df.columns.tolist(),
            default=["Name", "Annualized Return", "Volatility", "Sharpe Ratio",
                     "Sortino Ratio", "Max Drawdown", "# Months"]
        )

        if display_cols:
            display_df = df[display_cols].copy()

            # Format numeric columns
            for col in display_df.columns:
                if col in ["Annualized Return", "Volatility", "Max Drawdown", "Cumulative Return", "Positive Months"]:
                    display_df[col] = display_df[col].apply(lambda x: f"{x:.2%}" if pd.notna(x) else "–")
                elif col in ["Sharpe Ratio", "Sortino Ratio"]:
                    display_df[col] = display_df[col].apply(lambda x: f"{x:.2f}" if pd.notna(x) else "–")

            # Sort options
            sort_col = st.selectbox("Sort by", options=display_cols, index=display_cols.index("Sharpe Ratio") if "Sharpe Ratio" in display_cols else 0)
            ascending = st.checkbox("Ascending", value=False)

            # Re-sort on original df before formatting
            sorted_df = df[display_cols].sort_values(by=sort_col, ascending=ascending)

            # Format after sorting
            for col in sorted_df.columns:
                if col in ["Annualized Return", "Volatility", "Max Drawdown", "Cumulative Return", "Positive Months"]:
                    sorted_df[col] = sorted_df[col].apply(lambda x: f"{x:.2%}" if pd.notna(x) else "–")
                elif col in ["Sharpe Ratio", "Sortino Ratio"]:
                    sorted_df[col] = sorted_df[col].apply(lambda x: f"{x:.2f}" if pd.notna(x) else "–")

            st.dataframe(sorted_df.reset_index(drop=True), width="stretch", height=500)

            # Download button
            csv = df[display_cols].to_csv(index=False)
            st.download_button(
                label="📥 Download as CSV",
                data=csv,
                file_name="fund_comparison.csv",
                mime="text/csv"
            )

    # ==================== PERFORMANCE CHART ====================
    elif "Performance Chart" in page:
        st.header("📈 Cumulative Performance")

        # Fund selection
        col1, col2 = st.columns(2)
        with col1:
            available_funds = [f for f in fund_names if f in funds]
            selected_funds = st.multiselect(
                "Select funds to plot",
                options=available_funds,
                default=[f for f in ["RDGFF", "MSCI CHINA", "S&P 500"] if f in available_funds][:3]
            )

        with col2:
            # Quick select buttons
            st.markdown("**Quick Select:**")
            qcol1, qcol2 = st.columns(2)
            with qcol1:
                if st.button("Portfolio Funds"):
                    selected_funds = [f for f in our_portfolio if f in funds]
            with qcol2:
                if st.button("All Indices"):
                    selected_funds = [f for f in our_indices if f in funds]

        # Date range
        st.markdown("**Date Range:**")
        dcol1, dcol2 = st.columns(2)
        with dcol1:
            start_date = st.date_input(
                "Start Date",
                value=common_start if common_start else datetime(2020, 1, 1),
                min_value=datetime(1998, 1, 1),
                max_value=datetime.now()
            )
        with dcol2:
            end_date = st.date_input(
                "End Date",
                value=common_end if common_end else datetime.now(),
                min_value=datetime(1998, 1, 1),
                max_value=datetime.now()
            )

        if selected_funds:
            funds_to_plot = subset_of_funds(funds, selected_funds)

            start_str = start_date.strftime("%Y-%m")
            end_str = end_date.strftime("%Y-%m")

            fig = create_cumulative_chart(funds_to_plot, start_str, end_str)
            if fig:
                st.plotly_chart(fig, width="stretch")

                # Show summary stats for selected period
                st.markdown("### Period Statistics")
                stats_data = []
                for name, fund in funds_to_plot.items():
                    if fund is None:
                        continue
                    try:
                        cum_ret = fund.cumulative_return(start_str, end_str)
                        ann_ret = fund.annualized_return(start_str, end_str)
                        vol = fund.volatility(start_str, end_str)
                        sharpe = fund.sharpe_ratio(start_str, end_str)
                        mdd = fund.max_drawdown(start_str, end_str)
                        stats_data.append({
                            "Fund": name,
                            "Cumulative": f"{cum_ret:.2%}",
                            "Annualized": f"{ann_ret:.2%}",
                            "Volatility": f"{vol:.2%}",
                            "Sharpe": f"{sharpe:.2f}",
                            "Max DD": f"{mdd:.2%}"
                        })
                    except Exception:
                        pass

                if stats_data:
                    st.dataframe(pd.DataFrame(stats_data), width="stretch")
        else:
            st.info("Please select at least one fund to plot.")

    # ==================== CORRELATION ANALYSIS ====================
    elif "Correlation" in page:
        st.header("🔗 Correlation Analysis")

        col1, col2 = st.columns([3, 1])
        with col1:
            available_funds = [f for f in fund_names if f in funds]
            selected_funds = st.multiselect(
                "Select funds for correlation analysis",
                options=available_funds,
                default=[f for f in ["RDGFF", "MSCI CHINA", "S&P 500", "HAO", "EUREKAHEDGE WORLD"] if f in available_funds][:5]
            )
        with col2:
            min_overlap = st.number_input("Min. Overlap (months)", value=12, min_value=3, max_value=60)

        if len(selected_funds) >= 2:
            funds_to_analyze = subset_of_funds(funds, selected_funds)

            fig, corr_df = create_correlation_heatmap(funds_to_analyze, min_overlap=min_overlap)

            if fig:
                st.plotly_chart(fig, width="stretch")

                st.markdown("### Correlation Matrix")
                st.dataframe(corr_df.style.format("{:.2f}").background_gradient(cmap="RdBu_r", vmin=-1, vmax=1),
                            width="stretch")
        else:
            st.info("Please select at least 2 funds for correlation analysis.")

    # ==================== FUND DETAILS ====================
    elif "Fund Details" in page:
        st.header("🔍 Fund Details")

        selected_fund = st.selectbox("Select a fund", options=fund_names)

        if selected_fund and selected_fund in funds:
            fund = funds[selected_fund]

            # Key metrics in columns
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

            # Fund info
            st.markdown("### Fund Information")
            info_col1, info_col2 = st.columns(2)
            with info_col1:
                st.markdown(f"**Inception Date:** {fund.inception_date.strftime('%Y-%m-%d') if fund.inception_date else '–'}")
                st.markdown(f"**Latest Date:** {fund.latest_date.strftime('%Y-%m-%d') if fund.latest_date else '–'}")
            with info_col2:
                st.markdown(f"**Management Fee:** {fund.management_fee:.2%}" if fund.management_fee else "**Management Fee:** –")
                st.markdown(f"**Performance Fee:** {fund.performance_fee:.2%}" if fund.performance_fee else "**Performance Fee:** –")

            st.divider()

            # Monthly returns table
            st.markdown("### Monthly Returns")

            # Build monthly returns pivot table
            returns_data = []
            for entry in fund.monthly_returns:
                returns_data.append({
                    "Year": entry["month"].year,
                    "Month": entry["month"].strftime("%b"),
                    "Return": entry["value"]
                })

            if returns_data:
                returns_df = pd.DataFrame(returns_data)
                pivot = returns_df.pivot(index="Year", columns="Month", values="Return")

                # Reorder months
                month_order = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
                pivot = pivot.reindex(columns=[m for m in month_order if m in pivot.columns])

                # Calculate YTD for each year
                pivot["YTD"] = pivot.apply(lambda row: (1 + row.dropna()).prod() - 1, axis=1)

                # Format
                styled_pivot = pivot.style.format("{:.2%}", na_rep="–").background_gradient(
                    cmap="RdYlGn", vmin=-0.1, vmax=0.1, axis=None
                )
                st.dataframe(styled_pivot, width="stretch")

            # Compare with benchmark
            st.divider()
            st.markdown("### Compare with Benchmark")
            benchmark = st.selectbox(
                "Select benchmark",
                options=[f for f in fund_names if f != selected_fund],
                index=fund_names.index("MSCI CHINA") if "MSCI CHINA" in fund_names else 0
            )

            if benchmark and benchmark in funds:
                bench_fund = funds[benchmark]

                # Find overlapping period
                start = max(fund.inception_date, bench_fund.inception_date)
                end = min(fund.latest_date, bench_fund.latest_date)
                start_str = start.strftime("%Y-%m")
                end_str = end.strftime("%Y-%m")

                compare_data = {
                    "Metric": ["Annualized Return", "Volatility", "Sharpe Ratio", "Sortino Ratio", "Max Drawdown"],
                    selected_fund: [
                        f"{fund.annualized_return(start_str, end_str):.2%}",
                        f"{fund.volatility(start_str, end_str):.2%}",
                        f"{fund.sharpe_ratio(start_str, end_str):.2f}",
                        f"{fund.sortino_ratio(start_str, end_str):.2f}",
                        f"{fund.max_drawdown(start_str, end_str):.2%}",
                    ],
                    benchmark: [
                        f"{bench_fund.annualized_return(start_str, end_str):.2%}",
                        f"{bench_fund.volatility(start_str, end_str):.2%}",
                        f"{bench_fund.sharpe_ratio(start_str, end_str):.2f}",
                        f"{bench_fund.sortino_ratio(start_str, end_str):.2f}",
                        f"{bench_fund.max_drawdown(start_str, end_str):.2%}",
                    ]
                }
                st.dataframe(pd.DataFrame(compare_data), width="stretch")

                # Plot comparison
                funds_to_plot = subset_of_funds(funds, [selected_fund, benchmark])
                fig = create_cumulative_chart(funds_to_plot, start_str, end_str)
                if fig:
                    st.plotly_chart(fig, width="stretch")


if __name__ == "__main__":
    main()
