"""Test script: load all fund data and export a comparison table to CSV."""

import pandas as pd
from fofproject.load import load_all_data


def main():
    funds = load_all_data()
    print(f"Loaded {len(funds)} fund(s): {list(funds.keys())}")

    if not funds:
        print("No funds loaded — check that input data exists.")
        return

    rows = []
    for name, f in funds.items():
        rows.append(
            {
                "Fund": name,
                "Inception": f.monthly_returns[0]["month"].strftime("%Y-%m")
                if f.monthly_returns
                else None,
                "Latest Month": f.monthly_returns[-1]["month"].strftime("%Y-%m")
                if f.monthly_returns
                else None,
                "Months": len(f.monthly_returns),
                "identifier": f.identifier,
                "Ann. Return": f.total_ann_rtn,
                "Ann. Volatility": f.total_vol,
                "Sharpe": f.total_sharpe,
                "Sortino": f.total_sortino,
                "Max Drawdown": f.total_max_dd,
            }
        )

    df = pd.DataFrame(rows)
    print(df.to_string(index=False))

    df.to_csv("output/fund_comparison.csv", index=False)
    print("\nSaved to output/fund_comparison.csv")


if __name__ == "__main__":
    main()
