# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Fund-of-funds (FOF) investment analysis toolkit for tracking portfolio holdings, calculating performance metrics, generating visualizations, and extracting data from fund factsheets.

## Build & Run Commands

```bash
# Install dependencies
poetry install

# Run scripts
poetry run python automation.py
poetry run python monte_carlo.py

# Jupyter notebook
poetry run jupyter lab fund_workstation.ipynb
```

## Architecture

### Core Package: `src/fofproject/`

- **fund.py** - `Fund` class is the central data model. Holds monthly returns timeseries and computes metrics (annualized return, sharpe/sortino ratios, beta, volatility, max drawdown, correlation). Methods like `export_key_metrics_table()` and `export_monthly_table()` generate PNG outputs.

- **batch.py** - Batch visualization utilities. `plot_cumulative_returns()` creates multi-fund comparison charts. Style configurations (`STYLE_DICT`) control Plotly output for different formats (pptx, excel, clean).

- **load.py** - PDF factsheet parser using OpenAI. Extracts fund name, performance tables, manager info, AUM, fees via structured prompts and JSON schema. Converts extracted data into `Fund` objects.

- **mvo.py** - Mean-Variance Optimization using pypfopt. `minimum_variance_analysis()` computes optimal portfolio weights.

- **connection.py** - Microsoft Graph API integration for Outlook email categorization using MSAL auth.

### Data Flow

1. **Input**: `RETURN DATA.csv` (monthly returns), PDF factsheets in `input/` subfolders
2. **Processing**: Fund objects created via `input_monthly_returns()` or `load_from_pdf()`
3. **Output**: PNG charts saved to `output/`

### Key Functions

```python
# Load funds from CSV
funds = input_monthly_returns("RETURN DATA.csv", performance_fee=0.2, management_fee=0.01)

# Get subset for plotting
selected = subset_of_funds(funds, ['RDGFF', 'MSCI CHINA'])

# Generate cumulative return chart
plot_cumulative_returns(funds=selected, end_month="2025-11", style="excel", save=True)

# Export metrics table
funds['RDGFF'].export_key_metrics_table(benchmark_fund=funds['MSCI CHINA'], save=True)
```

### Environment Variables (in `src/fofproject/.env`)

Required for load.py: `OPENAI_API_KEY`
Required for connection.py: `TENANT_ID`, `CLIENT_ID`
Required for car_registration.py: `ID_NUMBER`, `CAR_NUMBER`, `EMAIL`

## Code Patterns

- Date parsing uses `parse_month()` utility which accepts "YYYY-MM" strings
- Monthly returns stored as list of dicts: `{"date": "01/01/2024", "value": 0.05}`
- Fund names use ALL CAPS convention (e.g., "RDGFF", "MSCI CHINA", "HAO")
- Visualizations support bilingual output via `language="en"` or `language="cn"` parameter
