import yfinance as yf
import pandas as pd

# Define indices and their Yahoo Finance tickers
indices = {
    "S&P 500": "^GSPC",
    "Dow Jones": "^DJI",
    "Nasdaq": "^IXIC",
    "Russell 2000": "^RUT",
    "MSCI World": "URTH",      # iShares MSCI World ETF proxy
    "MSCI China": "MCHI"       # iShares MSCI China ETF proxy
}

# Fetch data
data = {}
for name, ticker in indices.items():
    ticker_obj = yf.Ticker(ticker)
    hist = ticker_obj.history(period="5y", interval="1mo")  # last 1 month of data
    data[name] = hist
    print(f"\n{name} ({ticker})")
    print(hist.tail())

# Example: combining last closing prices into one DataFrame
latest_closes = {name: df["Close"].iloc[-1] for name, df in data.items()}
summary = pd.DataFrame(latest_closes, index=["Latest Close"])
print("\nSummary of latest closing prices:")
print(summary)
