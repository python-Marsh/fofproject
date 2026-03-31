# estimate_moments(returns_wide: DataFrame, method: str="hist") -> Tuple[mu:Series, cov:DataFrame]

# mean_variance_opt(mu, cov, target: str="sharpe", bounds: Dict|tuple=(0,1), budget: float=1.0, constraints: Dict=None) -> Series[weight]

# risk_parity(cov: DataFrame, bounds=(0,1)) -> Series[weight]

# rebalance_to_optimal(current_weights: Series, target_weights: Series, turnover_limit: float=None) -> DataFrame[asset_id, trade_w]

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from pypfopt import EfficientFrontier, expected_returns, risk_models
from fofproject.utils import hex_to_rgba

def minimum_variance_analysis(funds: dict, mode="Minimum Variance", target_return=0.14, title=None):
    """
    Perform minimum variance analysis on the given funds.

    Parameters
    ----------
    funds : dict
        A dictionary where keys are fund names and values are Fund objects with monthly returns.
    mode : str
        Should be one of "Minimum Variance", "Maximum Sharpe", or "Target Return".
    target_return : float
    title : str | None
        Title for the plot. If None, a default title based on mode is used.

    Returns
    -------
    None
        Prints the portfolio weights and performance metrics for different optimization strategies.
    """
    # Step 1: Validate inputs
    if len(funds) < 2:
        raise ValueError("At least 2 funds are required for optimization.")

    valid_modes = {"Minimum Variance", "Maximum Sharpe", "Target Return"}
    if mode not in valid_modes:
        raise ValueError(f"Invalid mode '{mode}'. Must be one of: {', '.join(valid_modes)}")

    # Step 2: Prepare the returns DataFrame
    series = {}
    start_dates = []
    end_dates = []

    for name, f in funds.items():
        s = pd.Series(
            {
                e["month"]: float(e["value"])
                for e in f.monthly_returns
                if e.get("value") is not None
            }
        ).sort_index()

        if s.empty:
            raise ValueError(f"Fund '{name}' has no valid return data.")

        series[name] = s
        start_dates.append(s.index.min())
        end_dates.append(s.index.max())

    # Combine into a DataFrame
    returns_df = pd.DataFrame(series)

    # Find common overlapping range
    common_start = max(start_dates)  # latest start date
    common_end = min(end_dates)      # earliest end date

    if common_start > common_end:
        raise ValueError(
            "Selected funds have no overlapping date range. "
            "Check that the funds share at least some common months."
        )

    # Filter to common range and drop rows with any NaN (gaps within range)
    filtered = returns_df.loc[common_start:common_end].dropna(how="any")
    n_months = len(filtered)

    if n_months < 3:
        raise ValueError(
            f"Only {n_months} overlapping month(s) with complete data across all selected funds. "
            "At least 3 are required for a meaningful covariance estimate."
        )

    # Annualized arithmetic mean returns (consistent with sample covariance * 12)
    mu = filtered.mean() * 12

    # Guard against NaN/Inf in expected returns
    if mu.isna().any() or np.isinf(mu).any():
        bad = mu[mu.isna() | np.isinf(mu)].index.tolist()
        raise ValueError(
            f"Could not compute annualized return for: {', '.join(bad)}. "
            "This may happen if a fund lost 100%+ in the period."
        )

    # Annualized covariance matrix: sample covariance of monthly returns × 12
    S = filtered.cov() * 12

    # Guard against NaN in covariance matrix
    if S.isna().any().any():
        raise ValueError(
            "Covariance matrix contains NaN values. "
            "The selected funds may have insufficient overlapping data."
        )

    # Step 3: Optimize portfolios
    ef = EfficientFrontier(mu, S)
    if mode == "Minimum Variance":
        weights = ef.min_volatility()
        annual_rtn, annual_vol, annual_sharpe = ef.portfolio_performance(verbose=True)

    elif mode == "Maximum Sharpe":
        weights = ef.max_sharpe()
        annual_rtn, annual_vol, annual_sharpe = ef.portfolio_performance(verbose=True)

    elif mode == "Target Return":
        # Validate target is within feasible range
        min_ret = float(mu.min())
        max_ret = float(mu.max())
        if target_return < min_ret or target_return > max_ret:
            raise ValueError(
                f"Target return {target_return:.2%} is outside the feasible range "
                f"[{min_ret:.2%}, {max_ret:.2%}] of the selected funds."
            )
        weights = ef.efficient_return(target_return=target_return)
        annual_rtn, annual_vol, annual_sharpe = ef.portfolio_performance(verbose=True)

    # ---------- simple bar chart of weights ----------
    color = "#C1AE94"  # keep your house style
    fig = go.Figure(
        data=go.Bar(
            x=list(weights.keys()),
            y=list(weights.values()),
            marker=dict(
                color="rgba(193,174,148,0.75)", line=dict(color=color, width=1.0)
            ),
            hovertemplate="<b>%{x}</b><br>weight = %{y:.2%}<extra></extra>",
            width=0.5,
        )
    )
    fig.update_layout(
        title=dict(
            text=f"<b>{title}</b>" if title else f"<b>Efficient Frontier - {mode}</b>",
            font=dict(size=22),
            x=0.5,
            xanchor="center",
            y=0.925,
            yanchor="middle",
        ),
        template="plotly_white",
        margin=dict(l=60, r=80, t=100, b=60),
        xaxis=dict(showgrid=False, tickangle=45),
        yaxis=dict(title="Weight", tickformat=".0%"),
    )

    stats = {
        "n_months": n_months,
        "Volatility": annual_vol,
        "Expected Return": annual_rtn,
        "Sharpe": annual_sharpe,
    }
    text = "<br>".join([f"{k}: {v:.4f}" if isinstance(v, float) else f"{k}: {v}" 
                    for k, v in stats.items()])
    
    fig.add_annotation(
        text=text,
        xref="paper", yref="paper",
        x=0.98,
        y=0.98,
        xanchor="right",
        yanchor="top",
        showarrow=False,
        align="right",
        bgcolor="#F6F6F7",
        bordercolor=hex_to_rgba(color, 0.9),
        borderwidth=1,
    )
    fig.show()
    return fig, weights, stats

# def minimum_variance_analysis(
#     funds: dict,
#     *,
#     long_only: bool = True,  # long-only (projected GD) or allow shorts (closed-form)
#     min_common_months: int = 12,  # require at least this many shared months across chosen funds
#     annualization: int = 12,  # 12 for monthly data
#     ridge: float = 1e-8,  # small diagonal jitter for numerical stability
#     title: str | None = None,
# ):
#     """
#     Build the global minimum-variance (GMV) portfolio from monthly returns in `funds`.

#     - Aligns on the **intersection** of months across the chosen funds so covariance is well-defined.
#     - If `long_only=True`, solves min 0.5 w'Σw  s.t. w>=0, 1'w=1 via projected gradient descent.
#     - If `long_only=False`, uses the closed-form GMV solution: w* ∝ Σ^{-1} 1.

#     Returns
#     -------
#     fig : plotly.graph_objects.Figure
#         Bar chart of portfolio weights.
#     weights : pd.Series
#         Portfolio weights indexed by fund name.
#     stats : dict
#         {'n_months', 'ann_vol', 'ann_ret', 'cov', 'mu', 'used_returns'}
#     """
#     # ---------- assemble wide table of returns ----------
#     names = list(funds.keys())
#     if not names:
#         raise ValueError("No funds provided.")

#     data = {}
#     for name in names:
#         f = funds[name]
#         s = pd.Series(
#             {
#                 e["month"]: float(e["value"])
#                 for e in f.monthly_returns
#                 if e.get("value") is not None
#             }
#         ).sort_index()
#         data[name] = s
#     wide = pd.DataFrame(data)

#     # Keep only months where **all** chosen funds have data (intersection)
#     used = wide.dropna(how="any")
#     n_months = len(used)
#     if n_months < min_common_months:
#         raise ValueError(
#             f"Not enough overlapping months across selected funds: {n_months} < {min_common_months}."
#         )

#     # Sample means (monthly) and covariance (monthly)
#     mu = used.mean()
#     cov = used.cov().astype(float)
#     # Numerical safety
#     cov = cov + np.eye(cov.shape[0]) * ridge

#     # ---------- solve for GMV weights ----------
#     m = len(mu)
#     ones = np.ones(m)

#     if not long_only:
#         # Closed-form GMV: w ∝ Σ^{-1} 1
#         inv = np.linalg.pinv(cov.values)  # pinv is robust if Σ is near-singular
#         w = inv @ ones
#         denom = ones @ inv @ ones
#         if denom <= 0:
#             raise ValueError("Covariance matrix appears ill-conditioned for GMV.")
#         w = w / denom
#     else:
#         # Long-only GMV via projected gradient descent on the simplex {w>=0, 1'w=1}
#         # Objective f(w) = 0.5 w'Σw; grad = Σw
#         Sigma = cov.values
#         # Lipschitz constant (largest eigenvalue) for step size
#         try:
#             L = float(np.linalg.eigvalsh(Sigma).max())
#         except Exception:
#             L = float(np.linalg.norm(Sigma, 2))
#         step = 1.0 / (L + 1e-12)

#         # Start from equal-weight
#         w = np.ones(m) / m

#         def project_to_simplex(v: np.ndarray, z: float = 1.0) -> np.ndarray:
#             """Euclidean projection onto {w >= 0, sum w = z} (Duchi et al., 2008)."""
#             if z <= 0:
#                 return np.zeros_like(v)
#             u = np.sort(v)[::-1]
#             cssv = np.cumsum(u)
#             rho = np.nonzero(u - (cssv - z) / (np.arange(1, len(u) + 1)) > 0)[0]
#             if len(rho) == 0:
#                 # All non-positive -> return uniform
#                 return np.ones_like(v) * (z / len(v))
#             rho = rho[-1]
#             theta = (cssv[rho] - z) / (rho + 1.0)
#             wproj = np.maximum(v - theta, 0.0)
#             return wproj  # sums to z by construction

#         max_iter, tol = 5000, 1e-9
#         for _ in range(max_iter):
#             w_old = w
#             grad = Sigma @ w
#             w = w - step * grad
#             w = project_to_simplex(w, 1.0)
#             if np.linalg.norm(w - w_old, 1) < tol:
#                 break

#     weights = pd.Series(w, index=mu.index)

#     # ---------- portfolio stats ----------
#     port_var_m = float(w.T @ cov.values @ w)
#     port_vol_ann = np.sqrt(port_var_m) * np.sqrt(annualization)
#     port_ret_ann = float(mu @ weights) * annualization

#     # ---------- simple bar chart of weights ----------
#     color = "#C1AE94"  # keep your house style
#     fig = go.Figure(
#         data=go.Bar(
#             x=weights.index,
#             y=weights.values,
#             marker=dict(
#                 color="rgba(193,174,148,0.75)", line=dict(color=color, width=1.0)
#             ),
#             hovertemplate="<b>%{x}</b><br>weight = %{y:.2%}<extra></extra>",
#         )
#     )
#     fig.update_layout(
#         title=dict(
#             text=f"<b>{title or 'Global Minimum-Variance Portfolio'}</b>",
#             x=0.5,
#             xanchor="center",
#         ),
#         template="plotly_white",
#         font=dict(family="Montserrat, Roboto", size=14, color="#53565A"),
#         margin=dict(l=60, r=40, t=80, b=60),
#         xaxis=dict(showgrid=False, tickangle=45),
#         yaxis=dict(title="Weight", tickformat=".0%"),
#     )

#     stats = {
#         "n_months": n_months,
#         "ann_vol": port_vol_ann,
#         "ann_ret": port_ret_ann,
#         "cov": cov,
#         "mu": mu,
#         "used_returns": used,
#     }
#     fig.show()
#     return fig, weights, stats
