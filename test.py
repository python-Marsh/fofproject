#!/usr/bin/env python3
"""
Monte Carlo comparison of two stocks over a customizable number of YEARS.

You provide:
- Names for Stock A and Stock B
- Average return (mu) per year (decimal, e.g., 0.08 = 8%)
- Volatility (sigma) per year (decimal, e.g., 0.20 = 20%)
- Years to simulate (integer)
- Number of simulations
- Optional random seed

Assumptions:
- Arithmetic RETURNS each year are i.i.d. ~ Normal(mu, sigma) for each stock
- A and B are independent (easy to extend to correlated returns if needed)

Outputs:
1) PER-YEAR comparisons ("mark every time A is bigger than B"):
   - For each year t: P(A_t > B_t), E[A_t - B_t], E[A_t - B_t | A_t > B_t]
2) FINAL (multi-year compounded) comparison:
   - Probability A_final > B_final
   - E[A_final - B_final]
   - E[A_final - B_final | A_final > B_final]
   - E[B_final - A_final | B_final > A_final]
   - E[|A_final - B_final|]
   - Percentiles of (A_final - B_final)

Notes:
- This sim treats inputs as ANNUAL parameters. If you want daily/weekly horizons,
  convert mu, sigma to that frequency or extend the code to take a frequency.
- "Return" means arithmetic period return (e.g., 0.05 = +5%). Compounding uses
  (1 + r1) * (1 + r2) * ... - 1.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Any
import numpy as np
import sys

@dataclass
class StockSpec:
    name: str
    mu: float      # expected arithmetic return per year (decimal)
    sigma: float   # volatility of arithmetic return per year (decimal)

@dataclass
class PerYearStats:
    p_a_gt_b: np.ndarray            # shape (years,)
    mean_diff: np.ndarray           # E[A_t - B_t] per year
    mean_diff_when_a_wins: np.ndarray  # E[A_t - B_t | A_t > B_t] per year

@dataclass
class FinalStats:
    p_a_final_gt_b_final: float
    mean_final_diff: float
    mean_final_diff_when_a_wins: float
    mean_final_diff_when_b_wins: float
    mean_abs_final_diff: float
    diff_percentiles: Dict[str, float]

@dataclass
class SimResult:
    per_year: PerYearStats
    final: FinalStats

# ------------------------------ Core Simulation ------------------------------

def simulate_paths(
    a: StockSpec,
    b: StockSpec,
    years: int,
    n_sims: int = 100_000,
    seed: Optional[int] = 42,
) -> SimResult:
    if years <= 0:
        raise ValueError("years must be positive")
    if n_sims <= 0:
        raise ValueError("n_sims must be positive")
    if a.sigma < 0 or b.sigma < 0:
        raise ValueError("volatility (sigma) must be non-negative")

    rng = np.random.default_rng(seed)

    # Generate annual returns: shape (n_sims, years)
    a_ret = rng.normal(loc=a.mu, scale=a.sigma, size=(n_sims, years))
    b_ret = rng.normal(loc=b.mu, scale=b.sigma, size=(n_sims, years))

    # ----------------------- Per-year comparison metrics ----------------------
    diff_year = a_ret - b_ret  # shape (n_sims, years)

    mask_a_gt_b_year = diff_year > 0
    # Probability A > B per year
    p_a_gt_b = mask_a_gt_b_year.mean(axis=0)
    # Mean (A - B) per year
    mean_diff = diff_year.mean(axis=0)
    # Mean (A - B | A > B) per year, handle years with zero wins
    mean_diff_when_a_wins = np.zeros(years)
    for t in range(years):
        wins = mask_a_gt_b_year[:, t]
        mean_diff_when_a_wins[t] = diff_year[wins, t].mean() if wins.any() else 0.0

    per_year_stats = PerYearStats(
        p_a_gt_b=p_a_gt_b,
        mean_diff=mean_diff,
        mean_diff_when_a_wins=mean_diff_when_a_wins,
    )

    # --------------------- Final compounded comparison -----------------------
    # Final cumulative returns (compound each path)
    a_final = np.prod(1 + a_ret, axis=1) - 1
    b_final = np.prod(1 + b_ret, axis=1) - 1

    diff_final = a_final - b_final
    mask_a_final_gt = diff_final > 0
    mask_b_final_gt = diff_final < 0

    mean_final_diff_when_a_wins = diff_final[mask_a_final_gt].mean() if mask_a_final_gt.any() else 0.0
    mean_final_diff_when_b_wins = (-diff_final[mask_b_final_gt]).mean() if mask_b_final_gt.any() else 0.0

    percentiles = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    pct_vals = np.percentile(diff_final, percentiles)

    final_stats = FinalStats(
        p_a_final_gt_b_final=float(mask_a_final_gt.mean()),
        mean_final_diff=float(diff_final.mean()),
        mean_final_diff_when_a_wins=float(mean_final_diff_when_a_wins),
        mean_final_diff_when_b_wins=float(mean_final_diff_when_b_wins),
        mean_abs_final_diff=float(np.abs(diff_final).mean()),
        diff_percentiles={f"{p}%": float(v) for p, v in zip(percentiles, pct_vals)},
    )

    return SimResult(per_year=per_year_stats, final=final_stats)

# ------------------------------- I/O Helpers ---------------------------------

def parse_float(prompt: str) -> float:
    while True:
        try:
            return float(input(prompt).strip())
        except ValueError:
            print("Please enter a valid number.", file=sys.stderr)

def parse_int(prompt: str) -> int:
    while True:
        try:
            val = int(float(input(prompt).strip()))  # accept '1e5' style too
            if val <= 0:
                raise ValueError
            return val
        except ValueError:
            print("Please enter a positive integer.", file=sys.stderr)

# --------------------------------- Pretty Print -------------------------------

def pretty_print(a: StockSpec, b: StockSpec, years: int, res: SimResult) -> None:
    print(f"\n=== Monte Carlo Comparison over {years} year(s): {a.name} vs {b.name} ===")
    print("Assumption: yearly arithmetic returns ~ Normal(mu, sigma), independent.\n")
    print(f"{a.name}: mu={a.mu:.4f}, sigma={a.sigma:.4f}")
    print(f"{b.name}: mu={b.mu:.4f}, sigma={b.sigma:.4f}\n")

    # Per-year table
    header = (
        "Year  P(A>B)    E[A-B]      E[A-B | A>B]"
    )
    print("Per-year comparisons (A_t vs B_t):")
    print(header)
    for t in range(years):
        print(
            f"{t+1:>4}  "
            f"{res.per_year.p_a_gt_b[t]:>6.2%}  "
            f"{res.per_year.mean_diff[t]:>+10.6f}  "
            f"{res.per_year.mean_diff_when_a_wins[t]:>+14.6f}"
        )

    # Final stats
    f = res.final
    print("\nFinal (multi-year compounded) comparison:")
    print(f"Probability {a.name} final > {b.name} final: {f.p_a_final_gt_b_final:.4%}")
    print(f"E[Final {a.name} - {b.name}]: {f.mean_final_diff:+.6f}")
    print(f"E[Final {a.name} - {b.name} | {a.name} wins]: {f.mean_final_diff_when_a_wins:+.6f}")
    print(f"E[Final {b.name} - {a.name} | {b.name} wins]: {f.mean_final_diff_when_b_wins:+.6f}")
    print(f"E[|Final diff|]: {f.mean_abs_final_diff:.6f}\n")

    print("Percentiles of Final (A - B):")
    for k, v in f.diff_percentiles.items():
        print(f"  {k:>4}: {v:+.6f}")
    print()

# ----------------------------------- main() ----------------------------------

def main() -> None:
    print("Enter inputs for the Monte Carlo simulation (annual parameters).\n")
    a_name = input("Name for Stock A: ").strip() or "Stock A"
    b_name = input("Name for Stock B: ").strip() or "Stock B"

    print("\n-- Enter average RETURNS (mu) per YEAR as decimals (e.g., 0.08 for 8%) --")
    a_mu = parse_float(f"{a_name} average return (mu): ")
    b_mu = parse_float(f"{b_name} average return (mu): ")

    print("\n-- Enter VOLATILITIES (sigma) per YEAR as decimals (e.g., 0.20 for 20%) --")
    a_sigma = parse_float(f"{a_name} volatility (sigma): ")
    b_sigma = parse_float(f"{b_name} volatility (sigma): ")

    years = parse_int("\nNumber of YEARS to simulate (e.g., 10): ")
    n_sims = parse_int("Number of simulations (e.g., 100000): ")

    seed_input = input("Random seed (blank for default 42): ").strip()
    seed = int(float(seed_input)) if seed_input else 42

    a = StockSpec(name=a_name, mu=a_mu, sigma=a_sigma)
    b = StockSpec(name=b_name, mu=b_mu, sigma=b_sigma)

    res = simulate_paths(a, b, years=years, n_sims=n_sims, seed=seed)
    pretty_print(a, b, years, res)

if __name__ == "__main__":
    main()
