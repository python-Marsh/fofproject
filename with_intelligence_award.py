import numpy as np
from src.fofproject.fund import Fund, input_monthly_returns, subset_of_funds, compare_funds
import pandas as pd

# -------------------------
# Step 1: Build a table
# -------------------------
records = []

# Use for manually update csv data of our portfolio and indices and merge with json file
award_calculation = input_monthly_returns(r"AWARD CALCULATION.csv")

for fund, obj in award_calculation.items():
    records.append({
        "fund": fund,
        "sharpe": obj.total_sharpe,
        "return": obj.total_cum_rtn
    })

df = pd.DataFrame(records)

print("Full universe:")
print(df)

# -------------------------
# Step 2: Compute Sharpe quartiles
# -------------------------
q75 = np.percentile(df["sharpe"], 75)

print(f"\nSharpe 75th percentile cutoff: {q75}")

# -------------------------
# Step 3: Screen top 25% Sharpe
# -------------------------
top_sharpe_df = df[df["sharpe"] >= q75]

print("\nFunds in top 25% Sharpe:")
print(top_sharpe_df)

# -------------------------
# Step 4: Select top 5 by return
# -------------------------
top_5_return_funds = (
    top_sharpe_df
    .sort_values(by="return", ascending=False)
    .head(5)
)

df.to_csv("full_universe.csv", index=False)
top_sharpe_df.to_csv("top_sharpe_quartile.csv", index=False)
top_5_return_funds.to_csv("top_5_return_top_sharpe.csv", index=False)

print("\nTop 5 return funds among top Sharpe quartile:")
print(top_5_return_funds)