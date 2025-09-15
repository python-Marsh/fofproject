import pandas as pd
import plotly.graph_objects as go
from src.fofproject.fund import Fund, input_monthly_returns, subset_of_funds
from src.fofproject.plot import plot_cumulative_returns, plot_fund_correlation_heatmap
from src.fofproject.mvo import minimum_variance_analysis

# Initialize and load data
funds = input_monthly_returns(r"RETURN DATA.csv", performance_fee=0.2, management_fee=0.01)
# funds_to_be_plot = subset_of_funds(funds, ['RDGFF', 'EUREKAHEDGE','MSCI CHINA'])
funds_to_be_plot = subset_of_funds(funds, ['RDGFF', 'EUREKAHEDGE','MSCI CHINA', 'HAO','TAIREN', 'LEXINGTON', 'LIM','FOREST'])
start_month = "2019-12"
end_month = "2020-7"

# funds['RDGFF'].plot_monthly_return_distribution()

# funds["LEXINGTON"].export_monthly_table(language ="cn")
# funds['HAO'].summary_of_a_fund(funds['MSCI CHINA'],language="en")


# for name, fund in funds_to_be_plot.items():
#     print(
#         f"Fund: {name}, return during the period {funds[name].annualized_return('2020-05', end_month):.2f}, "
#         f"volatility during the period {funds[name].volatility('2020-05', end_month):.2f}, "
#         f"sharpe during the period {funds[name].sharpe_ratio('2020-05', end_month):.2f}"
#     )

# list_of_plots = [
#     ['RDGFF', 'EUREKAHEDGE', 'MSCI CHINA'],
#     ['HAO', 'MSCI CHINA'],
#     ['TAIREN', 'MSCI CHINA', 'MSCI WORLD'],
#     ['LEXINGTON', 'MSCI WORLD', 'S&P 500'],
#     ['LIM', 'EUREKAHEDGE', 'TOPIX'],
#     ['FOREST', 'EUREKAHEDGE'],
# ]

# funds['HAO'].export_key_metrics_table(language ="en", end_month=end_month, benchmark_fund = funds["MSCI CHINA"], metrics = ["cagr","vol","sharpe","sortino","beta"],horizontal = True)


# for names in list_of_plots:
#     # Optional: skip if any required series are missing
#     missing = [n for n in names if n not in funds]
#     if missing:
#         print(f"Skipping {names}: missing {missing} in `funds`.")
#         continue
    # for years_to_plot in ['2018','2020','2022']:
    #     if years_to_plot =='2022': 
    #         end_month="2022-12"
    #         start_month="2022-1" 

    #     elif years_to_plot =='2020': 
    #         end_month="2020-12"
    #         start_month="2020-1" 

    #     elif years_to_plot =='2018': 
    #         end_month="2018-12"
    #         start_month="2018-1" 

            # print(f"Generating cumulative return plot for {', '.join(names)} in {years_to_plot}...")

plot = plot_cumulative_returns(
    funds=subset_of_funds(funds, ['RDGFF', 'MSCI WORLD', 'MSCI CHINA']),   # subset based on this group
    title="",
    start_month=start_month,
    end_month=end_month,
    style="default",
    language="en",
    blur=True,
    aspect_lock=False,
    custom_ticks=False
    )




# Correlation heatmap
# fig, corr_df, overlap_df = plot_fund_correlation_heatmap(funds_to_be_plot, method="pearson", min_overlap=12)
# fig.show()

# # 基金回报历史走势

# # MVO analysis
# fig, w, stats = minimum_variance_analysis(
#     funds=funds_to_be_plot,
#     long_only=True,
#     min_common_months=36,
#     title="GMV (Long-only, ≥36 common months)"
# )

# # Cumulative returns
# fig = plot_cumulative_returns(
#     funds=funds_to_be_plot,
#     title=title,
#     start_month=start_month,
#     end_month=end_month,
#     style="modern_dark",
#     language="en",
#     blur=True
# )
# for name, fund in funds.items():
#     print(f"Running function for {name}")
#     fund.annualized_return(start_month, end_month)


# l = "plot_monthly_return_distribution()"
# fig = eval(f"funds['LEXINGTON'].{l}")


