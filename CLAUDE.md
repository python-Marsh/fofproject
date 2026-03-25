# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Fund-of-funds (FOF) investment analysis toolkit for tracking portfolio holdings, calculating performance metrics, generating visualizations, extracting data from fund factsheets, classifying emails, populating report templates, and syncing data to Notion.

## Build & Run Commands

```bash
# Install dependencies
poetry install

# Run scripts
poetry run python automation.py
poetry run python with_intelligence_award.py

# Jupyter notebook
poetry run jupyter lab fund_workstation.ipynb

# Run tests
poetry run pytest
```

## Architecture

### Core Package: `src/fofproject/`

- **fund.py** - `Fund` class is the central data model. Holds monthly returns timeseries and computes metrics (annualized return, sharpe/sortino ratios, beta, volatility, max drawdown, correlation). Methods like `export_key_metrics_table(end_month, ...)` and `export_monthly_table(end_month, ...)` generate PNG outputs. `input_monthly_returns()` loads funds from CSV. `subset_of_funds()` filters fund dictionaries.

- **batch.py** - Batch visualization utilities. `plot_cumulative_returns()` creates multi-fund comparison charts with support for blur, aspect_lock, custom_ticks, and toggle parameters. `plot_fund_correlation_heatmap()` generates correlation matrices. Style configurations (`STYLE_DICT`) control Plotly output for different formats (pptx, excel, default).

- **load.py** - PDF factsheet parser using OpenAI. Extracts fund name, performance tables, manager info, AUM, fees via structured prompts and JSON schema. Key functions: `gpt_process_pdf()`, `process_single_pdf()`, `process_pdfs_in_folder()`, `init_funds()`. Also supports Marquee HTML parsing via `parse_from_marquee()`.

- **classify.py** - Email classification and organization system. Monitors Outlook emails, classifies them using GPT (`classify_email_with_gpt()`), extracts firm/fund names, and organizes artifacts into folder structures. Maintains a persistent `firm_fund_mappings.json` registry. Key functions: `classify_and_organize_emails()`, `monitoring()`, `sync_moved_artifacts()`. Uses OpenAI Agents SDK with `WebSearchTool`.

- **performance.py** - Processes unprocessed PDF artifacts flagged by classify.py as containing monthly net performance updates. Calls `load.process_single_pdf()` to extract fund data. Key function: `process_performance_updates()`.

- **document.py** - Template engine for populating Word (.docx) and PowerPoint (.pptx) documents with computed fund data, charts, and metrics tables. `TemplateEngine` class resolves template expressions like `{fund.inception_date}`. Key functions: `generate_factsheet()`, `generate_presentation()`.

- **notion.py** - Notion integration that monitors local firm/fund folder structure and syncs artifacts to Notion databases. Auto-discovers databases, creates/updates pages, uploads files with retry logic. Key function: `sync_firm_fund_structure()`.

- **mvo.py** - Mean-Variance Optimization using pypfopt. `minimum_variance_analysis()` computes optimal portfolio weights with modes: "Minimum Variance", "Maximum Sharpe", "Target Return".

- **connection.py** - Microsoft Graph API integration for Outlook email download and monitoring using MSAL auth with Selenium-automated device flow. Key functions: `download_all_emails()`, `download_top_emails()`, `monitor_emails()`.

- **utils.py** - Lightweight utilities: `parse_month()`, `in_notebook()`, `list_of_dicts_to_df()`, `hex_to_rgba()`.

### Root-Level Scripts

- **automation.py** - Automated monthly reporting. `presentation_data_update(month)` generates comprehensive performance metrics, PNG tables, and cumulative return plots for the portfolio.

- **with_intelligence_award.py** - Award calculation script. Screens funds by Sharpe ratio quartile and ranks by return.

- **car_registration.py** - Car registration form submission automation (unrelated to fund analysis).

### Data Flow

1. **Input**: `RETURN DATA.csv` (monthly returns), PDF factsheets in `input/` subfolders, Outlook emails via Graph API
2. **Classification**: Emails classified and organized into firm/fund folders via classify.py
3. **Extraction**: Fund objects created via `input_monthly_returns()`, `load_from_pdf()`, or `process_performance_updates()`
4. **Output**: PNG charts saved to `output/`, DOCX/PPTX reports via document.py, Notion pages via notion.py

### Key Functions

```python
# Load funds from CSV
funds = input_monthly_returns("RETURN DATA.csv", performance_fee=0.2, management_fee=0.01)

# Get subset for plotting
selected = subset_of_funds(funds, ['RDGFF', 'MSCI CHINA'])

# Generate cumulative return chart
plot_cumulative_returns(funds=selected, end_month="2025-11", style="excel", save=True)

# Export metrics table (end_month is required)
funds['RDGFF'].export_key_metrics_table(end_month="2025-11", benchmark_fund=funds['MSCI CHINA'], save=True)

# Classify and organize emails
classify_and_organize_emails(email_input_dir, output_dir)

# Process performance PDFs
process_performance_updates(output_dir)

# Generate factsheet from template
generate_factsheet(template_path, output_path, funds, config)
```

### Environment Variables (in `src/fofproject/.env`)

- `OPENAI_API_KEY` - Required for load.py, classify.py (OpenAI GPT calls)
- `TENANT_ID`, `CLIENT_ID` - Required for connection.py (Azure AD)
- `GRAPH_EMAIL`, `GRAPH_PASSWORD` - Required for connection.py (Outlook Graph API)
- `NOTION_SECRET` - Required for notion.py (Notion API)
- `ID_NUMBER`, `CAR_NUMBER`, `EMAIL` - Required for car_registration.py

## Code Patterns

- Date parsing uses `parse_month()` utility which accepts "YYYY-MM" strings
- Monthly returns stored as list of dicts: `{"date": "01/01/2024", "value": 0.05}`
- Fund names use ALL CAPS convention (e.g., "RDGFF", "MSCI CHINA", "HAO")
- Visualizations support bilingual output via `language="en"` or `language="cn"` parameter
- Email classification uses `firm_fund_mappings.json` as persistent registry
- Document templates use `{fund.metric_name}` expression syntax

## Testing

Tests are in `tests/` using pytest:
- `tests/unit/test_extraction.py` - Regression tests for PDF factsheet extraction against expected JSON fixtures in `tests/fixtures/extraction/`
- `tests/unit/test_rename_detection.py` - Tests for `sync_moved_artifacts()` registry-based file monitoring
- `tests/conftest.py` - Shared fixtures for mappings, email metadata, and temp directories

## Dependencies (Key)

- **Data**: pandas, numpy, plotly, matplotlib
- **Finance**: yfinance, pypfopt
- **PDF**: pymupdf (fitz)
- **AI**: openai, openai-agents
- **Documents**: python-docx, python-pptx, fpdf2
- **Web/API**: requests, selenium, beautifulsoup4
- **Auth**: msal, python-dotenv
- **Monitoring**: watchdog
- **Dev**: pytest, pytest-cov, ruff
