import pandas as pd
import plotly.io as pio
import plotly.graph_objects as go
from src.fofproject.fund import Fund, input_monthly_returns, subset_of_funds
from fofproject.batch import plot_cumulative_returns, plot_fund_correlation_heatmap
from src.fofproject.mvo import minimum_variance_analysis
from src.fofproject.load import load_saved_json, init_funds, process_pdfs_in_folder, save_changes_in_fund, rerun_no_perf_files

# # Names of our portfolio & benchmark indices
# our_portfolio = ['TAIREN','HAO','LEXINGTON','LIM','FOREST','WT LS','E20','3W GLOBAL','3W CHINA','3W HEALTHCARE','TIMEFOLIO','MONOLITH','PERSEVERANCE','NEO IVY','JH BIOTECH']
# our_index = ['MSCI CHINA','TOPIX','S&P 500','MSCI WORLD', 'EUREKAHEDGE']

# Names of our portfolio & benchmark indices
our_portfolio = ['TAIREN','HAO','LEXINGTON','LIM','FOREST','WT LS','E20','3W GLOBAL','3W CHINA','3W HEALTHCARE','TIMEFOLIO','MONOLITH','PERSEVERANCE','NEO IVY','JH BIOTECH']
our_index = ['MSCI CHINA','TOPIX','S&P 500','MSCI WORLD', 'EUREKAHEDGE']

test = load_saved_json(folder_path=r"input\portfolio")
print(type(test))
Funds = init_funds(test)
# print(Funds)
funds_to_be_plot = subset_of_funds(Funds, ['RDGFF', 'EUREKAHEDGE','MSCI CHINA', 'HAO','TAIREN', 'LEXINGTON', 'LIM','FOREST'])
minimum_variance_analysis(funds=funds_to_be_plot)

# test = load_saved_json(folder_path=r"input")
# print(type(test))
# json_fund = init_funds(test)
# print(json_fund)

# gpt_fund = rerun_no_perf_files(folder_path=r"input\test",save=True)

# # Initialize and load data
# funds = input_monthly_returns(r"RETURN DATA.csv", performance_fee=0.2, management_fee=0.01)
# # funds_to_be_plot = subset_of_funds(funds, ['RDGFF', 'EUREKAHEDGE','MSCI CHINA'])

# start_month = "2019-12"
# end_month = "2025-7"

# # funds['HAO'].export_monthly_table(language = "en")
# funds['RDGFF'].export_monthly_table(benchmark = funds['MSCI CHINA'], benchmark_name = "MSCI\nChina" ,language = "en", inception_column = True)

# for lan in ["en", "cn"]:
#     plot = plot_cumulative_returns(
#     funds=subset_of_funds(funds, ['RDGFF', 'EUREKAHEDGE','MSCI CHINA']),   # subset based on this group
#     title="Performance Since Inception",
#     start_month=None,
#     end_month=end_month,
#     style="excel",
#     language= lan,
#     blur=True,
#     aspect_lock=True,
#     save=True
#     )

print("Done")