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

## OpenAI Agents SDK

The project includes `openai-agents` for building multi-agent AI workflows.

### Installation

Already installed via: `poetry add openai-agents`

Requires `OPENAI_API_KEY` environment variable.

### Core Concepts

1. **Agents** - LLMs configured with instructions, tools, guardrails, and handoffs
2. **Tools** - Functions agents can call (use `@function_tool` decorator)
3. **Handoffs** - Transfer control between specialized agents
4. **Sessions** - Maintain conversation history across runs
5. **Tracing** - Built-in debugging and tracking

### Basic Agent Example

```python
from agents import Agent, Runner

agent = Agent(
    name="Assistant",
    instructions="You are a helpful assistant."
)

# Synchronous execution
result = Runner.run_sync(agent, "What is 2+2?")
print(result.final_output)

# Async execution
result = await Runner.run(agent, "What is 2+2?")
```

### Adding Tools (Function Calling)

```python
from agents import Agent, Runner, function_tool

@function_tool
def get_weather(city: str) -> str:
    """Get weather for a city."""
    return f"The weather in {city} is sunny."

@function_tool
def calculate_return(principal: float, rate: float, years: int) -> float:
    """Calculate compound return."""
    return principal * (1 + rate) ** years

agent = Agent(
    name="Financial Assistant",
    instructions="Help with financial calculations.",
    tools=[get_weather, calculate_return],
)

result = Runner.run_sync(agent, "Calculate return on $10000 at 7% for 10 years")
```

### Multi-Agent Handoffs

```python
from agents import Agent, Runner

# Specialist agents
research_agent = Agent(
    name="Research Agent",
    instructions="You research and analyze fund performance data."
)

calculation_agent = Agent(
    name="Calculation Agent",
    instructions="You perform financial calculations and statistics."
)

# Triage agent routes to specialists
triage_agent = Agent(
    name="Triage Agent",
    instructions="Route questions: research queries to Research Agent, calculations to Calculation Agent.",
    handoffs=[research_agent, calculation_agent],
)

result = Runner.run_sync(triage_agent, "What's the Sharpe ratio formula?")
```

### Sessions (Memory Persistence)

```python
from agents import Agent, Runner, SQLiteSession

agent = Agent(name="Assistant", instructions="You are helpful.")

# Create persistent session
session = SQLiteSession("user_123", "conversations.db")

# First interaction
result = await Runner.run(agent, "My favorite fund is RDGFF", session=session)

# Second interaction remembers context
result = await Runner.run(agent, "What's my favorite fund?", session=session)
# Output: "Your favorite fund is RDGFF"
```

### Structured Output

```python
from pydantic import BaseModel
from agents import Agent, Runner

class FundAnalysis(BaseModel):
    fund_name: str
    recommendation: str
    risk_level: str

agent = Agent(
    name="Analyst",
    instructions="Analyze funds and provide structured recommendations.",
    output_type=FundAnalysis,  # Forces structured output
)

result = Runner.run_sync(agent, "Analyze a conservative bond fund")
analysis: FundAnalysis = result.final_output
```

### Guardrails (Input/Output Validation)

```python
from agents import Agent, Runner, InputGuardrail, GuardrailFunctionOutput

async def check_appropriate_query(ctx, agent, input_data):
    # Return True if input is appropriate
    if "inappropriate" in input_data.lower():
        return GuardrailFunctionOutput(
            output_info={"reason": "Inappropriate content"},
            tripwire_triggered=True
        )
    return GuardrailFunctionOutput(tripwire_triggered=False)

agent = Agent(
    name="Safe Assistant",
    instructions="You are helpful.",
    input_guardrails=[InputGuardrail(guardrail_function=check_appropriate_query)],
)
```

### Agent Loop Behavior

When `Runner.run()` executes:
1. Calls LLM with agent instructions + message history
2. If response has tool calls → executes tools, loops back to step 1
3. If response has handoff → transfers to new agent, loops
4. If response is plain text (no tools/handoffs) → returns as final output
