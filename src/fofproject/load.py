from dotenv import load_dotenv
from pathlib import Path

# Load .env from this package directory so notebooks/scripts can run from any CWD.
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)
from typing import List, Dict
from fofproject.fund import Fund, FundDict
from fofproject.utils import compute_identifier  # noqa: F401 — re-exported
from fofproject.log import log, LOAD
from openai import OpenAI
from datetime import datetime
import pandas as pd
import calendar
import math
import os
import json
import fitz


SYSTEM_PROMPT = """
You are a precise financial data extractor.

OUTPUT CONTRACT (IMPORTANT):
- Return VALID JSON ONLY that conforms exactly to the JSON Schema below.
- If nothing is found, do not hallucinate.
- No explanations, no markdown, no code fences, no comments — only the JSON object.
- If a field is not supported by the source, obey the field-specific fallback rules below.
- Do not invent fields not present in the schema.
- Do not include additional properties.
- Be careful. Don't leave out any value, especially within the performance table.

SECTION-WISE INSTRUCTIONS

1) fund_name
Goal: Extract the most specific fund name from the file. Rules: • Convert to ALL CAPS. • Limit to ≤ 2 words (drop "Finance/Capital/Fund/LP/Partners/Ltd" etc).
Rules -
a. If only a company name fits, use the company name. Examples: "3W CHINA", "HAO", "TAIREN".
b. If you do not find anything similar to fund name, and think this might not be a fund sheet - add "ERROR" in the fund name.
c. If the name happens to be similar the following list, use the existing name instead: ['TAIREN','HAO','LEXINGTON','LIM','FOREST','WT CHINA','E20','3W GLOBAL','3W CHINA','3W HEALTHCARE','TIMEFOLIO','MONOLITH','PERSEVERANCE','NEO IVY','JH BIOTECH']. Some specific change are listed here: "Lim Japan Event Fund" → "LIM" - e.g. "水木清风特殊机会基金" → "FOREST",  "Janus Henderson Biotechnology" → "JH BIOTECH".

2) one_liner
Goal: A single-sentence summary of the fund. Capture the key edge, style and what makes it interesting or not. Keep it concise (≤ 2 sentences).

3) performance
Goal: Extract monthly performance as a time series.
Instruction strictly follows step-by-step:
a. Copy the monthly return table into a list of lists with the headers included as the first list item. If there's benchmark row in the table, only copy the row of the fund itself into a list of lists.
b. identify the first item as header, and the rest as value rows. Within the header, identify non-month header and the month header. - Identify month headers: these are strictly 12 values that represent months (January–December) in any form similar to the following:
  • Numeric: 1–12
  • English: Jan–Dec
  • Chinese: 一月–十二月
- Everything else in the header that is not a month should be categorized as a **non-month header** (e.g., "Year", "Fund", "YTD", "Inception", etc.). If the non-month header seems like one cell, then it should be one continous item. For example - "since inception", "since formation" etc.
c. Clean the value rows by removing empty values.
Rule: Retain all column headers (e.g., Jan–Dec, YTD), even if data is missing for some months in the value rows. In value rows, remove empty values that are similar to "NaN", "-", "", "ꟷ", or None.
d. Change the monthly header to the format of "%d/%m" and the % value to number like "1.23%" -> "0.0123".
The final output should be 3 list: a list of lists that includes the table, and a list of month header and a list of non-month header.
CRITICAL RULES:
- You MUST include ALL rows from the table, including partial inception year rows (the earliest year that may only have a few months of data). Never drop a row just because it has few values.
- Every value row MUST start with its year as the first element. If the year label appears at the start of a line merged with data (e.g. "2025 0.58 6.68"), the first token is the year.
- If there are two separate performance tables for different share classes (e.g., CNR vs CR, Class A vs Class D), only extract the FIRST table (the primary/unrestricted class).
Final output: three lists — (a) full table, (b) month headers, and (c) non-month headers. If you do not identify a monthly return performance table with timeseries data, then simply put the value as "[]". Do not treat the following as valid monthly performance tables: key metrics summary, annual return table that has no monthly timeseries performance.

4) geo_focus
Goal: Categorize the fund's geographical investment focus. Return exactly ONE value from this list: ["China","Developed Markets","Emerging Markets","Global","US"].
Rules:
a. Pick the single most representative region.
b. If the fund invests across 3+ diverse regions, return "Global".
c. If the fund is focused on a single country/region not in the list (e.g. Japan, Korea, Europe, Latin America, APAC), map it to the closest match: single developed-market country → "Developed Markets", single emerging-market country → "Emerging Markets".
d. If not found on the factsheet, return "".

5) strategy
Goal: Identify the fund's investment strategy type(s). Return a list with one or more values from: ["CTA","Credit LS","Equity LS","Multi-Strategy","Relative Value","SMID Cap","Systematic","Themed"].
Rules:
a. Select all strategies that clearly apply based on the factsheet.
b. If not stated or unclear from the factsheet, return [].
c. Map strategies not in the list to the closest match: activist → "Equity LS", distressed/special situation → "Credit LS", global macro → "Multi-Strategy", niche → "Themed".

6) asset_class
Goal: Identify the fund's asset class focus. Return a list with one or more values from: ["Commodities","Credit","Currencies (FX)","Digital Assets","Equities","Structured Products","Volatility"].
Rules:
a. Select all asset classes that clearly apply based on the factsheet.
b. If the fund is a general equity long/short fund, return ["Equities"].
c. Map asset classes not in the list to the closest match: convertible → "Credit", insurance linked → "Structured Products".
d. If not stated or unclear from the factsheet, return [].

7) ir_name
Goal: Extract the name of the primary investor relations contact or main representative mentioned on the factsheet. Return a full name as a string. If not found on the factsheet, return "".

8) email
Goal: Extract the primary contact email address shown on the factsheet. If not found, return "".

9) phone
Goal: Extract the primary contact phone number shown on the factsheet. If not found, return "".

10) base
Goal: Extract the fund's or manager's base location (city, country) as stated on the factsheet. Example: "Hong Kong", "New York, US", "Zurich, Switzerland". If not found, return "".

11) fund_inception
Goal: Extract the fund inception date as stated on the factsheet. Return as an ISO date string "YYYY-MM-DD". If only month and year are available, use the first day of the month (e.g., "March 2015" → "2015-03-01"). If only a year is available, use "YYYY-01-01". If not found on the factsheet, return "".

12) aum_size
Goal: Extract fund-level AUM in USD millions (number) as stated on the factsheet. Rules: Convert values such as "US$ 1,969.00mn" → 1969.00. If not found, return null.

13) return_pa
Goal: Extract the fund's annualized return (since inception or as stated on the factsheet). Return as a decimal (e.g., "12.5%" → 0.125). If not stated on the factsheet, return null.

14) volatility_pa
Goal: Extract the fund's annualized volatility/standard deviation as stated on the factsheet. Return as a decimal (e.g., "14.5%" → 0.145). If not stated on the factsheet, return null.

15) min_ticket
Goal: Extract the minimum investment/ticket size as stated on the factsheet, in thousands of USD. Convert to number in thousands (e.g., "$1,000,000" → 1000, "$500K" → 500, "$5M" → 5000). If not stated on the factsheet, return null.

16) net_exposure
Goal: Return the net exposure as stated on the factsheet in the format of an array. Use a single number within the list, if it is a number like [0], use 2 number to represent the range like [-0.2, 0.2]
Rules:
a. Convert values such as "50%" → 0.5
b. Always output as a JSON array.

17) net_return
Goal: Identify whether performance is net of fees. Rules: • true if the document explicitly states returns are after management/performance fees. • Else, false. • If true, use the applicable class fees for management_fee and performance_fee. • If false, still populate management_fee and performance_fee with the most common class fees in the document.

18) management_fee
Goal: Extract as a single decimal. Example: "1%" → 0.01. Rule: Use the fee matching the share class of the performance series (or the most common class if unclear).

19) performance_fee
Goal: Extract as a single decimal. Example: "20%" → 0.20. Rule: Use the fee matching the share class of the performance series (or the most common class if unclear).

20) suggested_benchmark
Goal: Based on the fund's geo_focus, strategy and asset_class, suggest the single most appropriate benchmark index from this list: {benchmark_list}.
Rules:
a. China-focused equity → "MSCI CHINA"
b. Japan-focused → "TOPIX"
c. US equity → "S&P 500"
d. Global equity → "MSCI WORLD"
e. Korea → "KOSPI"
f. Emerging markets → "MSCI EM"
g. Europe → "STOXX 600"
h. Semiconductor/tech themed → "SOX"
i. Healthcare themed → "US HEALTHCARE"
j. If unclear, default to "MSCI WORLD"
"""

RESPONSE_SCHEMA = """
{ "type": "object",
  "properties": {
    "fund_name": {
      "type": "string"
    },
    "one_liner": {
      "type": "string"
    },
    "performance": {
      "type": "object",
      "properties": {
        "table": {
          "type": "array",
          "items": {
            "type": "array",
            "items": {
              "type": "string"
            }
          }
        },
        "month_header": {
          "type": "array",
          "items": {
            "type": "string"
          }
        },
        "non_month_header": {
          "type": "array",
          "items": {
            "type": "string"
          }
        }
      },
      "required": ["table", "month_header", "non_month_header"],
      "additionalProperties": false
    },
    "geo_focus": {
      "type": "string",
      "enum": ["China","Developed Markets","Emerging Markets","Global","US",""]
    },
    "strategy": {
      "type": "array",
      "items": {
        "type": "string",
        "enum": ["CTA","Credit LS","Equity LS","Multi-Strategy","Relative Value","SMID Cap","Systematic","Themed"]
      }
    },
    "asset_class": {
      "type": "array",
      "items": {
        "type": "string",
        "enum": ["Commodities","Credit","Currencies (FX)","Digital Assets","Equities","Structured Products","Volatility"]
      }
    },
    "ir_name": {
      "type": "string"
    },
    "email": {
      "type": "string"
    },
    "phone": {
      "type": "string"
    },
    "base": {
      "type": "string"
    },
    "fund_inception": {
      "type": "string"
    },
    "aum_size": {
      "type": ["number", "null"]
    },
    "return_pa": {
      "type": ["number", "null"]
    },
    "volatility_pa": {
      "type": ["number", "null"]
    },
    "min_ticket": {
      "type": ["number", "null"]
    },
    "net_exposure": {
      "type": "array",
      "items": { "type": "number" },
      "maxItems": 2
    },
    "net_return": {
      "type": "boolean"
    },
    "management_fee": {
      "type": "number"
    },
    "performance_fee": {
      "type": "number"
    },
    "suggested_benchmark": {
      "type": "string"
    }
  },
  "required": [
    "fund_name",
    "one_liner",
    "performance",
    "geo_focus",
    "strategy",
    "asset_class",
    "ir_name",
    "email",
    "phone",
    "base",
    "fund_inception",
    "aum_size",
    "return_pa",
    "volatility_pa",
    "min_ticket",
    "net_exposure",
    "net_return",
    "management_fee",
    "performance_fee",
    "suggested_benchmark"
  ]
}
"""

ignored_funds = []


def _find_ytd_index(header_row, non_month_header):
    """Find the index of the YTD column in the original header row."""
    for i, h in enumerate(header_row):
        if h.upper().strip() in ("YTD", "Y.T.D.", "YEAR TO DATE", "年初至今"):
            return i
    return None


def _ytd_cross_validate_rows(
    cleaned_rows, year_rows, ytd_col_idx, fund_name, auto_scaled=False
):
    """Cross-validate monthly values against YTD using compounding.

    For each complete year (12 months):
      1. **Scale fix** – if values are in percentage form (e.g. 13.82 meaning
         13.82%) instead of decimal (0.1382), divide by 100.
      2. **Flag** – if compound return doesn't match YTD after scale fix,
         log a warning with the error magnitude.
    Modifies cleaned_rows in-place (scale corrections only).
    """
    from datetime import datetime as _dt

    # Group cleaned_rows by year
    by_year = {}
    for r in cleaned_rows:
        y = _dt.strptime(r["date"], "%d/%m/%Y").year
        by_year.setdefault(y, []).append(r)

    # Build year→YTD lookup from the raw rows
    ytd_by_year = {}
    for entry in year_rows:
        year, all_vals = next(iter(entry.items()))
        val_idx = ytd_col_idx - 1  # subtract 1 for the year column
        if 0 <= val_idx < len(all_vals):
            try:
                raw = all_vals[val_idx]
                ytd_val = float(raw.strip("%"))
                ytd_by_year[year] = ytd_val
            except (ValueError, TypeError):
                pass

    # Determine once whether YTD values are in percentage form (avg abs > 1.5)
    ytd_abs_vals = [abs(v) for v in ytd_by_year.values() if v != 0]
    ytd_is_pct = bool(ytd_abs_vals) and (sum(ytd_abs_vals) / len(ytd_abs_vals)) > 1.5

    def _compound(vals):
        return math.prod(1 + v for v in vals) - 1

    for year, entries in by_year.items():
        if len(entries) != 12 or year not in ytd_by_year:
            continue

        ytd_raw = ytd_by_year[year]
        monthly_vals = [e["value"] for e in entries]

        # Only attempt /100 scale correction if auto-detect didn't already handle it
        is_pct_scale = False
        if not auto_scaled:
            abs_vals = sorted(abs(v) for v in monthly_vals if v != 0)
            is_pct_scale = abs_vals and abs_vals[len(abs_vals) // 2] > 0.5
            if is_pct_scale:
                for e in entries:
                    e["value"] = round(e["value"] / 100, 4)
                monthly_vals = [e["value"] for e in entries]
                log.detail(
                    f"{fund_name} ({year}): applied /100 scale correction.",
                    phase=LOAD,
                )

        # Scale YTD: use the once-for-all ytd_is_pct decision, or auto_scaled flag
        if auto_scaled or ytd_is_pct:
            ytd_expected = ytd_raw / 100
        else:
            ytd_expected = ytd_raw
        compound = _compound(monthly_vals)
        error = abs(compound - ytd_expected)
        if error > 0.0002:
            log.detail(
                f"{fund_name} ({year}): YTD mismatch — compound={compound:.6f} vs expected={ytd_expected:.6f} (error={error:.6f}).",
                phase=LOAD,
            )


def process_performance(data):
    # GPT's own screening
    if data["fund_name"] == "ERROR":
        log.detail(
            f"{data['fund_name']}: no performance table found by GPT.", phase=LOAD
        )
        return []

    table = data["performance"]["table"]
    month_header = data["performance"]["month_header"]
    non_month_header = set(data["performance"]["non_month_header"])

    if len(month_header) != 12:
        log.warn(
            f"{data['fund_name']}: month header has {len(month_header)} entries (expected 12).",
            phase=LOAD,
        )

    if not table:
        log.detail(f"{data['fund_name']}: empty performance table.", phase=LOAD)
        return []

    # Extract header
    header = table[0]
    rows = table[1:]

    # Step 1: Remove non_month_header on the left
    for entry in header:
        if entry in non_month_header:
            continue
        else:
            idx = header.index(entry)
            header = header[idx:]
            break
    years = []
    raw_row_lengths = {}  # year -> original row length (before stripping empties)

    # Identify year column position
    def parse_yearly_performance(data_lists, year_counter=2025):
        """
        Takes the raw list of lists collected from GPT (idealy one list per year), where each sub-list starts with a year followed by performance values.
        Returns a list of dicts mapping {year: [values]}.

        Example:
        [
            ["2025","1.21%","-0.59%","-2.44%"],
            ["0.55%","-1.33%","2024"]
        ]
        ->
        [
            {2025: ["1.21%","-0.59%","-2.44%"]},
            {2024: ["0.55%","-1.33%"]}
        ]
        """
        results = []
        for data in data_lists:
            year = None
            values = []

            for item in data:
                value = item.strip()
                # skip the empty value, and do not append it to the results
                if not value:
                    continue
                # find the year and add it as key later
                # Strip common suffixes like * or # from year labels (e.g. "2021*")
                cleaned = value.rstrip("*#†‡ ")
                if cleaned.isdigit() and 1900 <= int(cleaned) <= 2100:
                    year = int(cleaned)
                    years.append(year)
                # find the value and add it as values
                else:
                    values.append(value)

            if year is None:
                year = year_counter
                years.append(year)
                year_counter -= 1
                log.detail(
                    f"No valid year found in row, defaulting to {year}.", phase=LOAD
                )
            raw_row_lengths[year] = len(data)
            results.append({year: values})

        return results

    rows = parse_yearly_performance(rows)
    earliest_year = min(years)
    latest_year = max(years)
    if earliest_year == latest_year:
        log.detail(
            f"{data['fund_name']}: only one year ({earliest_year}) found, skipping.",
            phase=LOAD,
        )
        return []

    # Clean non_month_header
    cleaned_rows = []

    # Derive non-month column count from full middle years (more robust than
    # counting from GPT's non_month_header list, which can be inconsistent).
    count_non_month = sum(1 for h in header if h in non_month_header)
    for entry in rows:
        y, v = next(iter(entry.items()))
        if y not in (earliest_year, latest_year) and len(v) > 12:
            count_non_month = len(v) - 12
            break

    for entry in rows:
        year, values = next(iter(entry.items()))  # unpack single-key dict

        # Only trim if earliest or latest year, becasue some cases the earliest and latest include the suffix but not in between
        if count_non_month > 0 and year in (earliest_year, latest_year):
            if len(values) >= count_non_month:
                values = values[:-count_non_month]

        # Ensure middle years always have 12
        if year not in (earliest_year, latest_year):
            if len(values) > 12:
                values = values[
                    :12
                ]  # drop last elements till 12, assume there are no values appended in the front
            elif len(values) < 12:
                log.warn(
                    f"{data['fund_name']}: fewer than 12 months in year {year}, skipping.",
                    phase=LOAD,
                )
                return []
        if year == earliest_year:
            # Assign backward from December
            month = 12
            for val in reversed(values):
                last_day = calendar.monthrange(year, month)[1]  # last day of month
                date_str = f"{last_day:02d}/{month:02d}/{year}"
                # convert string → float safely
                try:
                    num = float(val.strip("%")) / 100 if "%" in val else float(val)
                except Exception:
                    log.warn(
                        f"{data['fund_name']}: non-numeric value in latest month, skipping.",
                        phase=LOAD,
                    )
                    return []
                cleaned_rows.append({"date": date_str, "value": num})
                month -= 1
        else:
            # Assign forward from January
            for month, val in enumerate(values, start=1):
                last_day = calendar.monthrange(year, month)[1]
                date_str = f"{last_day:02d}/{month:02d}/{year}"
                try:
                    num = float(val.strip("%")) / 100 if "%" in val else float(val)
                except Exception:
                    log.warn(
                        f"{data['fund_name']}: non-numeric value in earliest month, skipping.",
                        phase=LOAD,
                    )
                    return []
                cleaned_rows.append({"date": date_str, "value": num})
                if year != latest_year and len(values) != 12:
                    log.warn(
                        f"{data['fund_name']}: year {year} has {len(values)} values (expected 12), skipping.",
                        phase=LOAD,
                    )
                    return []

    # Auto-detect percentage-scale values: if median |value| > 0.5, values are
    # likely in percentage form (e.g. 1.23 meaning 1.23%) and need dividing by 100
    auto_scaled = False
    if cleaned_rows:
        abs_vals = sorted(abs(r["value"]) for r in cleaned_rows if r["value"] != 0)
        if abs_vals:
            median_abs = abs_vals[len(abs_vals) // 2]
            if median_abs > 0.5:
                for r in cleaned_rows:
                    r["value"] = round(r["value"] / 100, 4)
                auto_scaled = True

    # --- YTD cross-validation ---
    # Identify YTD column from the non-month headers in the original header row.
    ytd_idx = _find_ytd_index(data["performance"]["table"][0], non_month_header)
    if ytd_idx is not None:
        _ytd_cross_validate_rows(
            cleaned_rows, rows, ytd_idx, data["fund_name"], auto_scaled
        )

    # Ensure all values are recorded to exactly 4 decimal places
    for r in cleaned_rows:
        r["value"] = round(r["value"], 4)

    return cleaned_rows


def _try_merge_performance(a, b, tolerance=1e-6):
    """Try to merge two result dicts whose performance overlaps numerically.

    Returns a merged result dict if the overlapping months match within
    *tolerance*, or ``None`` if they don't overlap or disagree.  The merged
    result keeps the identity (fund_name, etc.) of whichever result starts
    earlier, combines all performance months, and recomputes the identifier.
    """
    perf_a = a.get("performance", [])
    perf_b = b.get("performance", [])
    if not perf_a or not perf_b:
        return None

    map_a = {e["date"]: e["value"] for e in perf_a}
    map_b = {e["date"]: e["value"] for e in perf_b}

    overlap = set(map_a) & set(map_b)
    if not overlap:
        return None

    # Verify overlapping months match
    if not all(abs(map_a[d] - map_b[d]) <= tolerance for d in overlap):
        return None

    # Determine which starts earlier
    start_a = min(datetime.strptime(d, "%d/%m/%Y") for d in map_a)
    start_b = min(datetime.strptime(d, "%d/%m/%Y") for d in map_b)
    earlier, later_map = (a, map_b) if start_a <= start_b else (b, map_a)

    # Merge: start from earlier's data, add non-overlapping months from later
    merged_map = {e["date"]: e["value"] for e in earlier["performance"]}
    for d, v in later_map.items():
        if d not in merged_map:
            merged_map[d] = v

    merged_perf = [{"date": d, "value": v} for d, v in merged_map.items()]
    merged_perf.sort(key=lambda x: datetime.strptime(x["date"], "%d/%m/%Y"))

    merged = dict(earlier)
    merged["performance"] = merged_perf
    merged["identifier"] = compute_identifier(merged_perf)
    return merged


def _save_json_result(result, json_folder):
    """Save a result dict to a JSON file named by identifier.

    If a file with the same identifier already exists, keep the one
    with the longer performance track record.  If a file with a *different*
    identifier exists but overlapping performance matches, merge them.

    Skips saving if identifier/fund_name is missing or performance is empty.
    """
    # Skip if no meaningful identifier or fund_name
    identifier = result.get("identifier", result.get("fund_name", ""))
    if not identifier or not identifier.strip():
        log.detail("Skipping JSON save: no identifier or fund name found.", phase=LOAD)
        return

    # Skip if performance is empty or invalid
    perf = result.get("performance", [])
    if not (
        isinstance(perf, list) and perf and all(isinstance(item, dict) for item in perf)
    ):
        log.detail(
            f"Skipping JSON save for {identifier}: no valid performance data.",
            phase=LOAD,
        )
        return

    os.makedirs(json_folder, exist_ok=True)
    output_path = os.path.join(json_folder, f"{identifier}.json")

    if os.path.exists(output_path):
        with open(output_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        # Try merging with the same-identifier file
        merged = _try_merge_performance(existing, result)
        if merged:
            result = merged
            identifier = merged["identifier"]
            # Identifier may have changed after merge; clean up old file if needed
            new_output_path = os.path.join(json_folder, f"{identifier}.json")
            if new_output_path != output_path:
                os.remove(output_path)
                output_path = new_output_path
        else:
            existing_len = len(existing.get("performance", []))
            new_len = len(result.get("performance", []))
            if new_len <= existing_len:
                return  # existing has equal or longer track record, skip
    else:
        # Check all existing JSONs for a mergeable match
        for file_name in os.listdir(json_folder):
            if not file_name.lower().endswith(".json"):
                continue
            existing_path = os.path.join(json_folder, file_name)
            with open(existing_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            merged = _try_merge_performance(existing, result)
            if merged:
                result = merged
                identifier = merged["identifier"]
                # Remove the old file, will save under new identifier
                os.remove(existing_path)
                output_path = os.path.join(json_folder, f"{identifier}.json")
                break

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


def _has_valid_performance(result):
    """Return True if result contains a non-empty list of performance dicts."""
    val = result.get("performance")
    return (
        isinstance(val, list)
        and len(val) > 0
        and all(isinstance(item, dict) for item in val)
    )


def _collect_result(result, fund_name, results, no_perf_list, folder_path, save):
    """Append result to results and save JSON if performance is valid, otherwise record in no_perf_list."""
    if _has_valid_performance(result):
        results.append(result)
    else:
        no_perf_list.append(fund_name)
    if save:
        json_folder = os.path.join(folder_path, "json")
        _save_json_result(result, json_folder)


def extract_text_from_pdf(file_path):
    doc = fitz.open(file_path)
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text


def _build_system_prompt():
    """Build the GPT system prompt with benchmark list injected dynamically from BENCHMARK.csv."""
    from fofproject.fund import get_available_benchmarks

    try:
        benchmark_list = get_available_benchmarks()
    except Exception:
        benchmark_list = []
    prompt = SYSTEM_PROMPT.replace("{benchmark_list}", str(benchmark_list))
    return prompt + "\n\nJSON Schema:\n" + RESPONSE_SCHEMA


def _gpt_extract_from_file(client, file_path: str):
    """Upload a PDF and run GPT extraction via file upload (for image-based tables)."""
    system_prompt = _build_system_prompt()
    with open(file_path, "rb") as f:
        uploaded_file = client.files.create(file=f, purpose="assistants")

    response = client.responses.create(
        model="gpt-5.2",
        temperature=0,
        input=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": system_prompt,
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Extract the required data from this PDF file:",
                    },
                    {"type": "input_file", "file_id": uploaded_file.id},
                ],
            },
        ],
    )
    output_text = response.output_text.strip()
    data = json.loads(output_text)
    data["performance"] = process_performance(data)
    return data


def _gpt_extract_from_text(client, text: str):
    """Run GPT extraction on pre-extracted text."""
    system_prompt = _build_system_prompt()
    response = client.responses.create(
        model="gpt-4.1",
        temperature=0,
        input=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": f"Extract the required data from this file:\n{text}",
            },
        ],
    )
    output_text = response.output_text.strip()
    data = json.loads(output_text)
    data["performance"] = process_performance(data)
    return data


def gpt_process_pdf(file_path: str):
    """
    Upload a PDF and run GPT extraction according to the schema.
    Returns the parsed JSON or raw text if parsing fails.
    """
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    text = extract_text_from_pdf(file_path)
    # Cannot parse pdf into text, try uploading pdf directly
    if not text:
        data = _gpt_extract_from_file(client, file_path)
        log.detail(
            f"{data['fund_name']}: using PDF image extraction (values may be unstable).",
            phase=LOAD,
        )
        data["fund_name"] = f"{data['fund_name']} from_pdf"
    else:
        data = _gpt_extract_from_text(client, text)
        # Fallback: if text extraction yielded no performance, retry with PDF upload
        # (handles cases where performance table is embedded as an image)
        if not data.get("performance"):
            log.detail(
                f"{data['fund_name']}: text extraction found no performance, retrying with PDF upload.",
                phase=LOAD,
            )
            pdf_data = _gpt_extract_from_file(client, file_path)
            if pdf_data.get("performance"):
                # Keep non-performance fields from text extraction (usually more reliable)
                # but use performance from PDF upload
                data["performance"] = pdf_data["performance"]
    if isinstance(data.get("performance"), list) and data["performance"]:
        # Check if performance is a non-empty list and sort it
        data["performance"].sort(key=lambda x: datetime.strptime(x["date"], "%d/%m/%Y"))
    data["identifier"] = compute_identifier(data.get("performance", []))
    return data


def gpt_process_text(text: str):
    """
    From text and run GPT extraction according to the schema.
    Returns the parsed JSON or raw text if parsing fails.
    """
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    system_prompt = _build_system_prompt()
    response = client.responses.create(
        model="gpt-4.1",
        temperature=0,
        input=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": f"Extract the required data from this file:\n{text}",
            },
        ],
    )
    output_text = response.output_text.strip()
    data = json.loads(output_text)
    data["performance"] = process_performance(data)
    if isinstance(data.get("performance"), list) and data["performance"]:
        # Check if performance is a non-empty list and sort it
        data["performance"].sort(key=lambda x: datetime.strptime(x["date"], "%d/%m/%Y"))
    data["identifier"] = compute_identifier(data.get("performance", []))
    return data


def process_single_pdf(file_path, save=True, funds=None):
    """
    Process a single PDF by file path and optionally save the result as JSON.
    If *funds* dict is provided (must contain 'RDGFF'), also generates worst-performance
    comparison charts against RDGFF.
    Returns the parsed result dict.
    """
    if not os.path.isfile(file_path) or not file_path.lower().endswith(".pdf"):
        raise ValueError(f"Invalid PDF path: {file_path}")

    folder_path = os.path.dirname(file_path) or "."

    log.detail(f"Processing: {file_path}.", phase=LOAD)
    result = gpt_process_pdf(file_path)

    if save:
        json_folder = os.path.join(folder_path, "json")
        _save_json_result(result, json_folder)

    # Generate worst-performance comparison charts against RDGFF
    if funds is not None and "RDGFF" in funds and _has_valid_performance(result):
        fund_obj = init_funds([result]).get(result["fund_name"])
        if fund_obj:
            fund_obj.compare_worst_performance(
                funds["RDGFF"],
                title="Performance during our fund's top 10 drawdowns",
                n_worst=10,
                save=True,
            )
            fund_obj.compare_worst_performance(
                funds["RDGFF"],
                title="Entire performance compared with our fund's",
                n_worst=100,
                save=True,
            )

    return result


def process_pdfs_in_folder(
    folder_path="input", save=False, prefix_hint="_parsed from_"
):
    """
    Iterates through all PDFs in the same folder as this script (relative path).
    Returns a list of JSON results.
    """
    results = []
    no_perf_list = []
    for file_name in os.listdir(folder_path):
        if file_name.lower().endswith(".pdf"):
            file_path = os.path.join(folder_path, file_name)
            log.detail(f"Processing: {file_path}.", phase=LOAD)
            result = gpt_process_pdf(file_path)
            _collect_result(
                result, result["fund_name"], results, no_perf_list, folder_path, save
            )
            # Renaming of the pdf. file
            base, ext = os.path.splitext(file_name)
            if prefix_hint in base:
                # keep only the part after prefix_hint to make sure the file name will not stack up & Re-run with the same fund_name stored
                prefix, base = base.split(prefix_hint, 1)
                result["fund_name"] = prefix
            new_file_name = f"{result['fund_name']}{prefix_hint}{base}{ext}"
            new_path = os.path.join(folder_path, new_file_name)
            os.rename(file_path, new_path)
    # Record the files without performance for future re-run
    no_perf_path = os.path.join(folder_path, "No Performance Found.txt")
    with open(no_perf_path, "w") as f:
        f.write("\n".join(no_perf_list))
    return results


def rerun_no_perf_files(folder_path="input", save=False):
    """
    Reads 'No Performance Found.txt' in folder_path.
    Re-runs gpt_process_pdf for files that match the prefixes listed.
    Returns updated results.
    """
    no_perf_path = os.path.join(folder_path, "No Performance Found.txt")
    if not os.path.exists(no_perf_path):
        log.detail("No Performance Found.txt not found.", phase=LOAD)
        return []

    # Read fund_name prefixes from the file
    with open(no_perf_path, "r") as f:
        fund_prefixes = [line.strip() for line in f if line.strip()]

    if not fund_prefixes:  # file is empty or only blank lines
        log.detail("No entries found in No Performance Found.txt.", phase=LOAD)
        return []

    results = []
    still_no_perf = []

    for file_name in os.listdir(folder_path):
        if not file_name.lower().endswith(".pdf"):
            continue

        base, ext = os.path.splitext(file_name)

        # Check if this PDF matches any prefix in the list
        for prefix in fund_prefixes:
            if base.startswith(prefix):
                file_path = os.path.join(folder_path, file_name)
                log.detail(
                    f"Re-processing: {file_path} (no performance before).", phase=LOAD
                )
                result = gpt_process_pdf(file_path)

                # Check performance again
                val = result["performance"]
                if not (
                    isinstance(val, list)
                    and all(isinstance(item, dict) for item in val)
                ) or (isinstance(val, list) and not val):
                    still_no_perf.append(result["fund_name"])

                # Save JSON if required
                if save:
                    json_folder = os.path.join(folder_path, "json")
                    _save_json_result(result, json_folder)

                results.append(result)
                break  # stop after matching one prefix

    # Update the No Performance file with those still missing performance
    with open(no_perf_path, "w") as f:
        f.write("\n".join(still_no_perf))

    return results


def continue_running(folder_path="input", save=False, prefix_hint="_parsed from_"):
    """
    Processes only PDFs in the folder that do NOT already contain the prefix_hint in their name.
    Returns a list of JSON results.
    """
    results = []
    no_perf_list = []

    for file_name in os.listdir(folder_path):
        if file_name.lower().endswith(".pdf"):
            base, ext = os.path.splitext(file_name)

            # Skip files that already have the prefix_hint in the name
            if prefix_hint in base:
                continue

            file_path = os.path.join(folder_path, file_name)
            log.detail(f"Processing: {file_path}.", phase=LOAD)
            result = gpt_process_pdf(file_path)

            _collect_result(
                result, result["fund_name"], results, no_perf_list, folder_path, save
            )

            # Renaming the file with prefix_hint
            new_file_name = f"{result['fund_name']}{prefix_hint}{base}{ext}"
            new_path = os.path.join(folder_path, new_file_name)
            os.rename(file_path, new_path)

    # Record the files without performance for future re-run
    no_perf_path = os.path.join(folder_path, "No Performance Found.txt")
    with open(no_perf_path, "w") as f:
        f.write("\n".join(no_perf_list))

    return results


def load_saved_json(folder_path="input"):
    """
    Loads all JSON files saved in the 'json' subfolder of the given folder_path
    and returns them as a list of result-like objects.
    """
    results = []

    # Path where JSON files were saved
    json_folder = os.path.join(folder_path, "json")

    if not os.path.exists(json_folder):
        log.detail(f"No json/ folder found in {folder_path}.", phase=LOAD)
        return results

    # Iterate through all .json files and load them
    for file_name in os.listdir(json_folder):
        if file_name.lower().endswith(".json"):
            file_path = os.path.join(json_folder, file_name)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    result = json.load(f)
                    results.append(result)
            except Exception as e:
                log.error(f"Failed to load {file_name}: {e}.", phase=LOAD)
    return results


def results_to_csv(results, folder_path="input"):
    """
    Transforms a list of saved results into a CSV with:
    - First column: date (sorted ascending)
    - Following columns: each fund's performance values

    Parameters:
        results (list[dict]): The list of results loaded from JSON.
        output_path (str): Path to save the CSV file.
    """

    # Dictionary to collect data: {date: {fund_name: value}}
    data = {}
    output_path = os.path.join(folder_path, "returns.csv")
    for result in results:
        fund_name = result["fund_name"]

        perf_data = result.get("performance", [])
        # Skip if 'performance' is missing or is not a list
        if not isinstance(perf_data, list) or not perf_data:
            log.detail(f"Skipping {fund_name} (no performance data).", phase=LOAD)
            continue

        for perf in perf_data:
            # Normalize the date format (assuming dd/mm/yyyy)
            try:
                date = datetime.strptime(perf["date"], "%d/%m/%Y").date()
            except Exception as e:
                log.warn(
                    f"{fund_name}: invalid date format '{perf.get('date')}': {e}.",
                    phase=LOAD,
                )
                continue

            if date not in data:
                data[date] = {}
            data[date][fund_name] = perf["value"]

    df_new = pd.DataFrame.from_dict(data, orient="index").sort_index()
    df_new.index.name = "date"
    df_new.reset_index(inplace=True)
    df_new["date"] = pd.to_datetime(df_new["date"], dayfirst=True).dt.strftime(
        "%d/%m/%Y"
    )

    if os.path.exists(output_path):
        # Load the old CSV
        df_old = pd.read_csv(output_path)

        # Merge: keep latest values for overlapping dates
        df_combined = pd.concat([df_old, df_new], ignore_index=True)
        # Normalize old dates as well
        df_old["date"] = pd.to_datetime(df_old["date"], dayfirst=True).dt.strftime(
            "%d/%m/%Y"
        )
        # Drop duplicates on 'date', keeping the last occurrence (from df_new)
        df_combined = df_combined.drop_duplicates(subset="date", keep="last")

        # Sort by date if needed
        df_combined = df_combined.sort_values(by="date")

        log.detail(f"Updated existing CSV at {output_path}.", phase=LOAD)
    else:
        # If no CSV exists, just use the new data
        df_combined = df_new
        log.detail(f"Created new CSV at {output_path}.", phase=LOAD)

    # Save to CSV
    df_combined["date"] = pd.to_datetime(
        df_combined["date"], dayfirst=True
    ).dt.strftime("%d/%m/%Y")
    df_combined.to_csv(output_path, index=False)

    return df_combined


def init_funds(
    funds_data: List[Dict], benchmarks: Dict[str, Fund] = None
) -> Dict[str, Fund]:
    """Initialize Fund objects from a list of dicts.

    Args:
        funds_data (List[Dict]): List of fund-like dicts
        benchmarks (Dict[str, Fund], optional): Dict of benchmark Fund objects.
            If provided, auto-wires default_benchmark from suggested_benchmark_name.

    Returns:
        Dict{Fund_name: fund}: Successfully initialized Fund objects
    """
    initialized_funds = FundDict()
    for data in funds_data:
        try:
            fund = Fund(
                name=data["fund_name"],
                monthly_returns=data.get("performance"),
                one_liner=data.get("one_liner"),
                geo_focus=data.get("geo_focus"),
                strategy=data.get("strategy"),
                asset_class=data.get("asset_class"),
                identifier=data.get("identifier"),
                ir_name=data.get("ir_name"),
                email=data.get("email"),
                phone=data.get("phone"),
                base=data.get("base"),
                fund_inception=data.get("fund_inception"),
                aum_size=data.get("aum_size"),
                return_pa=data.get("return_pa"),
                volatility_pa=data.get("volatility_pa"),
                min_ticket=data.get("min_ticket"),
                net_exposure=data.get("net_exposure"),
                net_return=data.get("net_return"),
                management_fee=data.get("management_fee"),
                performance_fee=data.get("performance_fee"),
                suggested_benchmark_name=data.get("suggested_benchmark"),
            )
            initialized_funds[fund.name] = fund
        except ValueError as e:
            # Skip invalid funds and log the issue
            log.detail(
                f"{data.get('fund_name', 'UNKNOWN')}: skipped ({e}).", phase=LOAD
            )

    if benchmarks:
        from fofproject.fund import assign_benchmarks

        assign_benchmarks(initialized_funds, benchmarks)

    return initialized_funds


def offload_funds(funds: Dict[str, Fund]) -> List[Dict]:
    """Convert a dict of Fund objects back into a list of dicts.

    Args:
        funds (Dict[str, Fund]): Dict of Fund objects, keyed by fund name

    Returns:
        List[Dict]: List of fund-like dicts (same structure as input to init_funds)
    """
    results = []
    for fund in funds.values():
        string_returns = []
        for entry in fund.monthly_returns:
            string_returns.append(
                {
                    "date": entry["datetime"].strftime("%d/%m/%Y"),
                    "value": entry["value"],
                }
            )
        results.append(
            {
                "fund_name": fund.name,
                "one_liner": getattr(fund, "one_liner", None),
                "performance": string_returns,
                "geo_focus": getattr(fund, "geo_focus", None),
                "strategy": getattr(fund, "strategy", None),
                "asset_class": getattr(fund, "asset_class", None),
                "identifier": getattr(fund, "identifier", None),
                "ir_name": getattr(fund, "ir_name", None),
                "email": getattr(fund, "email", None),
                "phone": getattr(fund, "phone", None),
                "base": getattr(fund, "base", None),
                "fund_inception": getattr(fund, "fund_inception", None),
                "aum_size": getattr(fund, "aum_size", None),
                "return_pa": getattr(fund, "return_pa", None),
                "volatility_pa": getattr(fund, "volatility_pa", None),
                "min_ticket": getattr(fund, "min_ticket", None),
                "net_exposure": getattr(fund, "net_exposure", None),
                "net_return": getattr(fund, "net_return", None),
                "management_fee": getattr(fund, "management_fee", None),
                "performance_fee": getattr(fund, "performance_fee", None),
                "suggested_benchmark": getattr(fund, "suggested_benchmark_name", None),
            }
        )
    return results


def results_to_json(results, folder_path="input"):
    json_folder = os.path.join(folder_path, "json")
    for result in results:
        _save_json_result(result, json_folder)


def save_changes_in_fund(funds: Dict[str, Fund], folder_path="input"):
    # Preserve benchmark assignments before round-tripping
    benchmark_refs = {
        name: fund.default_benchmark
        for name, fund in funds.items()
        if fund.default_benchmark is not None
    }
    results = offload_funds(funds)
    results_to_csv(results=results, folder_path=folder_path)
    results_to_json(results=results, folder_path=folder_path)
    funds = init_funds(results)
    # Restore benchmark assignments
    for name, bm in benchmark_refs.items():
        if name in funds:
            funds[name].default_benchmark = bm
    return funds


def merge_funds(dict1, dict2) -> FundDict:
    """
    Merge two fund containers (FundDict or plain dict).
    Keep dict2's fund objects, but if a fund_name exists in both,
    overwrite only the 'monthly_returns' attribute from dict1.
    """
    merged = FundDict()
    merged.update(dict2)

    for fund_name, fund_obj in dict1.items():
        if fund_name in merged:
            # Only update monthly_returns
            merged[fund_name].monthly_returns = fund_obj.monthly_returns
        else:
            # If fund not in dict2, add it fully
            merged[fund_name] = fund_obj
    # Preserve benchmark assignments before round-tripping
    benchmark_refs = {
        name: fund.default_benchmark
        for name, fund in merged.items()
        if fund.default_benchmark is not None
    }
    # Reload so there is no stale computation of key metrics
    results = offload_funds(merged)
    merged = init_funds(results)
    # Restore benchmark assignments
    for name, bm in benchmark_refs.items():
        if name in merged:
            merged[name].default_benchmark = bm
    return merged


def load_all_data(
    base_path=None,
    benchmark_csv="BENCHMARK.csv",
    return_csv="RETURN DATA.csv",
    manual_csv="MANUAL OVERWRITE.csv",
    json_folders=None,
    firms_folder=None,
):
    """Load all fund data in sequence: benchmarks, CSV returns, JSON folders, manual overwrite.

    Parameters
    ----------
    base_path : str or Path, optional
        Root folder containing all input data. CSVs and JSON subfolders
        are resolved relative to this path.
        If None, uses the resolved default: NAS mount (/data/Input) if
        available, otherwise falls back to the local ``input/`` folder.
    benchmark_csv : str
        Filename of the benchmark CSV inside base_path.
    return_csv : str
        Filename of the portfolio returns CSV inside base_path.
    manual_csv : str
        Filename of the manual overwrite CSV inside base_path.
        Funds in this file will overwrite performance of matching funds.
    json_folders : list[str], optional
        List of subfolder names under base_path to load JSONs from.
        If None, auto-discovers all subfolders that contain a 'json/' directory.

    Returns
    -------
    dict
        Dictionary of {fund_name: Fund} with all data merged.
    """
    from fofproject.fund import load_benchmarks, input_monthly_returns
    from fofproject.paths import DEFAULT_INPUT_DIR, DEFAULT_OUTPUT_DIR

    if base_path is None:
        base_path = str(DEFAULT_INPUT_DIR)

    benchmark_path = os.path.join(base_path, benchmark_csv)
    return_path = os.path.join(base_path, return_csv)
    manual_path = os.path.join(base_path, manual_csv)

    # 1. Load benchmarks
    benchmarks = {}
    if os.path.exists(benchmark_path):
        benchmarks = load_benchmarks(benchmark_path)
        log.detail(
            f"Loaded {len(benchmarks)} benchmark(s) from {benchmark_path}.", phase=LOAD
        )
    else:
        log.warn(f"Benchmark file not found: {benchmark_path}.", phase=LOAD)

    # 2. Load portfolio returns from CSV (includes benchmarks)
    funds = FundDict()
    if os.path.exists(return_path):
        funds = input_monthly_returns(
            return_path, benchmark_csv=benchmark_path if benchmarks else None
        )
        log.detail(f"Loaded {len(funds)} fund(s) from {return_path}.", phase=LOAD)
    else:
        log.warn(f"Return CSV not found: {return_path}.", phase=LOAD)

    # 3. Load all JSON from firms_folder/<Firm>/<Fund>/json/
    firms_path = str(DEFAULT_OUTPUT_DIR) if firms_folder is None else os.path.join(base_path, firms_folder)
    if json_folders is None:
        # Auto-discover: firms_path/<firm>/<fund>/json/
        json_folders = []
        if os.path.isdir(firms_path):
            for firm_name in sorted(os.listdir(firms_path)):
                firm_dir = os.path.join(firms_path, firm_name)
                if not os.path.isdir(firm_dir):
                    continue
                for fund_name in sorted(os.listdir(firm_dir)):
                    fund_dir = os.path.join(firm_dir, fund_name)
                    if os.path.isdir(fund_dir) and os.path.isdir(os.path.join(fund_dir, "json")):
                        json_folders.append(os.path.join(firm_name, fund_name))

    json_metadata = {}  # Save JSON metadata to restore after performance merges
    _META_FIELDS = ("one_liner", "geo_focus", "strategy", "asset_class", "ir_name",
                     "email", "phone", "base", "fund_inception", "aum_size",
                     "min_ticket", "net_exposure", "suggested_benchmark_name",
                     "management_fee", "performance_fee")

    for folder_rel in json_folders:
        folder_path = os.path.join(firms_path, folder_rel)
        json_data = load_saved_json(folder_path=folder_path)
        if json_data:
            json_funds = init_funds(json_data, benchmarks=benchmarks or None)
            # Capture metadata from JSON (wins over CSV for metadata)
            for name, jf in json_funds.items():
                meta = {f: getattr(jf, f, None) for f in _META_FIELDS}
                meta = {f: v for f, v in meta.items() if v is not None}
                if name in json_metadata:
                    json_metadata[name].update(meta)
                else:
                    json_metadata[name] = meta
            funds = merge_funds(json_funds, funds)
            log.detail(
                f"Loaded {len(json_funds)} fund(s) from {folder_path}/json/.",
                phase=LOAD,
            )

    # 4. Manual overwrite CSV (overwrites performance of matching funds)
    if os.path.exists(manual_path):
        manual_funds = input_monthly_returns(manual_path)
        funds = merge_funds(manual_funds, funds)
        log.detail(
            f"Applied manual overwrite from {manual_path} ({len(manual_funds)} fund(s)).",
            phase=LOAD,
        )
    else:
        log.detail(f"Manual overwrite file not found: {manual_path}.", phase=LOAD)

    # 5. Restore JSON metadata (always takes priority for non-performance fields)
    for name, meta in json_metadata.items():
        if name in funds:
            for field, value in meta.items():
                setattr(funds[name], field, value)

    log.info(f"Total funds loaded: {len(funds)}.", phase=LOAD)
    return funds


def parse_from_marquee(
    url: str, fund_name: str = "", manager_name: str = "", show=False
):
    """
    Access a webpage that loads content via JavaScript and extract the full HTML after rendering.
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.common.exceptions import TimeoutException
    import time

    if not show:
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")  # Run in headless mode
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
    # Configure WebDriver (make sure you have the right ChromeDriver installed)
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service)
    wait = WebDriverWait(driver, 20)
    Mq_username = os.getenv("MQ_USERNAME")
    Mq_password = os.getenv("MQ_PASSWORD")
    requested_list = []
    try:
        driver.get(url)
        time.sleep(5)  # Initial wait for page to start loading
        # Check if the page requires login by title
        if "signin" in driver.title.lower() or "login" in driver.title.lower():
            print("Login required — proceeding with login automation...")
            time.sleep(3)
            # Fill username fields
            username_field = driver.find_element(By.NAME, "username")
            username_field.send_keys(Mq_username)
            button = driver.find_element(
                By.CSS_SELECTOR, "button[data-cy='gs-uitk-button__button']"
            )
            button.click()
            # Wait for password field to appear
            password_field = wait.until(
                EC.presence_of_element_located((By.NAME, "password"))
            )
            # Enter your password
            password_field.send_keys(Mq_password)
            button = driver.find_element(
                By.CSS_SELECTOR, "button[data-cy='gs-uitk-button__button']"
            )
            button.click()
            print("Login successful!")
        else:
            print("Already logged in — continuing...")

        # Wait until the table (or a specific element inside it) is present
        # Adjust the locator (By.XPATH / By.CSS_SELECTOR) to match your table
        time.sleep(30)  # Additional wait to ensure all JS has loaded
        # First: look for "Request Full Access" span
        try:
            buttons = wait.until(
                EC.presence_of_all_elements_located(
                    (
                        By.XPATH,
                        "//span[text()='Request Full Access' or text()='Full Access Requested']",
                    )
                )
            )
        except:
            buttons = []  # no buttons found in time
            print("No Request Full Access button found.")

        if buttons:  # Found request buttons
            page_html = False
            requested_list = [fund_name]
            for span in buttons:
                # Check if inside a clickable parent (button or link)
                try:
                    parent = span.find_element(
                        By.XPATH, "./ancestor::*[self::button or self::a]"
                    )
                    if parent.is_enabled() and parent.get_attribute("disabled") is None:
                        parent.click()
                        print("Clicked: Request Full Access")
                    else:
                        print("Full Access Requested")
                except:
                    print("Error finding clickable parent for Request Full Access")

        else:
            # Second: if no button, look for table
            try:
                tbody = wait.until(
                    EC.presence_of_element_located((By.XPATH, "//tbody"))
                )
                if tbody:
                    print("Table found.")
                    try:  # wait a bit more for full table load
                        card = driver.find_element(
                            By.XPATH,
                            "//div[contains(@class,'aurora-card')][.//div[@class='aurora-card-head-title' and contains(normalize-space(.), 'Fund(s)')]]",
                        )
                        # Then find the body inside that card
                        card_body = card.find_element(
                            By.XPATH, ".//div[@class='aurora-card-body']"
                        )
                        # Within that body, find all <a> tags
                        links = card_body.find_elements(By.XPATH, ".//a")

                        # Loop through all the links and collect href + displayed text
                        fund_options = []
                        for link in links:
                            href = link.get_attribute("href")
                            text = link.text.strip()
                            fund_options.append((href, text))
                        # Now, find the link that matches the manager_name (case-insensitive)
                        selected_link = None
                        for href, text in fund_options:
                            if manager_name.lower() in text.lower():
                                selected_link = href
                                print(
                                    f"Navigated to fund page for manager: {manager_name}"
                                )
                                break
                            elif fund_name.lower() in text.lower():
                                selected_link = href
                                print(f"Navigated to fund page for fund: {fund_name}")
                                break
                            else:
                                selected_link = fund_options[0][
                                    0
                                ]  # default to first option if no match
                                print(
                                    f"No exact match found; defaulting to first fund option: {fund_options[0][0]}"
                                )
                        if selected_link:
                            driver.get(selected_link)
                            time.sleep(25)  # wait for the new page to load
                            try:
                                buttons = wait.until(
                                    EC.presence_of_all_elements_located(
                                        (
                                            By.XPATH,
                                            "//span[text()='Request Full Access' or text()='Full Access Requested']",
                                        )
                                    )
                                )
                            except:
                                buttons = []  # no buttons found in time
                                print("No Request Full Access button found.")

                            if buttons:  # Found request buttons
                                page_html = False
                                requested_list = [fund_name]
                                for span in buttons:
                                    # Check if inside a clickable parent (button or link)
                                    try:
                                        parent = span.find_element(
                                            By.XPATH,
                                            "./ancestor::*[self::button or self::a]",
                                        )
                                        if (
                                            parent.is_enabled()
                                            and parent.get_attribute("disabled") is None
                                        ):
                                            parent.click()
                                            print("Clicked: Request Full Access")
                                        else:
                                            print("Full Access Requested")
                                    except:
                                        print(
                                            "Error finding clickable parent for Request Full Access"
                                        )
                            else:
                                # Second: if no button, look for table
                                try:
                                    tbody = wait.until(
                                        EC.presence_of_element_located(
                                            (By.XPATH, "//tbody")
                                        )
                                    )
                                    if tbody:
                                        try:
                                            table = driver.find_element(
                                                By.XPATH,
                                                "//div[@class='aurora-card-head-title' and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'return')]",
                                            )
                                            print(
                                                "Performance table found after selection page."
                                            )
                                            page_html = driver.execute_script(
                                                "return document.body.innerText;"
                                            )
                                        except Exception as e:
                                            print(
                                                "Performance table not found after selection but there is a table",
                                                e,
                                            )
                                            page_html = False
                                            requested_list = [fund_name]
                                except:
                                    print("Not Standard Table.")
                                    page_html = False
                                    requested_list = [fund_name]
                    except:
                        try:
                            table = driver.find_element(
                                By.XPATH,
                                "//div[@class='aurora-card-head-title' and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'return')]",
                            )
                            print("Performance table found.")
                            page_html = driver.execute_script(
                                "return document.body.innerText;"
                            )
                        except Exception as e:
                            print("Performance table not found but there is a table", e)
                            page_html = False
                            requested_list = [fund_name]
            except TimeoutException:
                print(
                    f"No table found or access requested for {fund_name} at {url} under given time."
                )
                page_html = False
                requested_list = [fund_name]

            except Exception as e:
                print(f"Error loading {fund_name} from {url}: {e}")
                page_html = False
                requested_list = [fund_name]

        # Give JavaScript a bit more time if necessary
        time.sleep(2)
        print("Scraper Session Completed.")

    finally:
        driver.quit()
        return page_html, requested_list


def get_link_from_html(folder_path=r"input\marquee", save=False, show=False):
    """
    Reads a text file containing URLs (one per line) and returns them as a list.
    """
    links = []
    empty_list = []
    no_perf_list = []
    results = []
    found_link = False
    from bs4 import BeautifulSoup

    # Read fund_name prefixes from the file
    for file_name in os.listdir(folder_path):
        if file_name.lower().endswith((".html", ".htm")):
            file_path = os.path.join(folder_path, file_name)
            # Load your saved email HTML
            with open(file_path, "rb") as f:
                html = f.read()
            soup = BeautifulSoup(html, "html.parser")
            for row in soup.find_all("tr", style=lambda s: s and "mso-yfti-irow:" in s):
                td = row.find("td")  # first td of the row
                td_cells = row.find_all("td")
                if not td:
                    continue

                link_tag = td.find("a")
                if not link_tag:
                    continue

                fund_name = link_tag.get_text(separator="", strip=True)
                fund_name = " ".join(fund_name.split()).upper()
                fund_link = link_tag["href"]

                # Found a valid marquee link
                if "marquee.gs.com" in fund_link.lower():
                    # Save the found link

                    if not found_link:
                        # --- Navigate back to the table ---
                        found_link = True
                        table = row.find_parent("table")

                        if table:
                            # Locate the first header row (mso-yfti-irow:0)
                            header_row = table.find(
                                "tr", style=lambda s: s and "mso-yfti-irow:0" in s
                            )
                            if header_row:
                                # Extract text from all cells in that header row
                                headers = [
                                    " ".join(
                                        cell.get_text(separator=" ", strip=True).split()
                                    )
                                    for cell in header_row.find_all(["td", "th"])
                                ]
                                matching_index = next(
                                    (
                                        i
                                        for i, h in enumerate(headers)
                                        if any(
                                            k in h.lower()
                                            for k in ["presenter", "manager"]
                                        )
                                    ),
                                    None,  # default if no match is found
                                )
                    presenter_text = td_cells[matching_index].get_text(
                        separator=" ", strip=True
                    )
                    links.append((fund_name, fund_link, presenter_text))
            links_path = os.path.join(folder_path, "Links & Names.txt")
            with open(links_path, "w") as f:
                for url, name, manager in links:
                    f.write(f"{url}\t{name}\t{manager}\n")
    # Parsing information from the links
    for fund_name, fund_link, manager_name in links:
        page_html, requested_list = parse_from_marquee(
            url=fund_link, fund_name=fund_name, manager_name=manager_name, show=show
        )
        if not page_html:
            print(f"No table found or access requested for {fund_name} at {fund_link}")
            empty_list = empty_list + requested_list
            no_table_path = os.path.join(folder_path, "Requested & No Table.txt")
            with open(no_table_path, "w") as f:
                f.write("\n".join(empty_list))
        else:
            result = gpt_process_text(page_html)
            _collect_result(result, fund_name, results, no_perf_list, folder_path, save)
            no_perf_path = os.path.join(folder_path, "No Performance Found.txt")
            with open(no_perf_path, "w") as f:
                f.write("\n".join(no_perf_list))
    return results


def rerun_no_table_list(folder_path=r"input\marquee", save=False, show=False):
    """
    Reads 'Requested & No Table.txt' in folder_path.
    Re-runs parse_from_marquee for files that match the prefixes listed.
    Returns updated results.
    """
    filter_list = []
    links = []
    results = []
    found_list = []
    no_perf_path = os.path.join(folder_path, "No Performance Found.txt")
    filter_list_path = os.path.join(folder_path, "Requested & No Table.txt")
    links_path = os.path.join(folder_path, "Links & Names.txt")
    if not (os.path.exists(filter_list_path) and os.path.exists(links_path)):
        print("One or both lists are missing.")
        return []
    with open(no_perf_path, "r") as f:
        no_perf_list = [line.strip() for line in f if line.strip()]
    with open(links_path, "r") as f:
        for line in f:
            fund_name, url, manager_name = line.strip().split(
                "\t", 2
            )  # split into 2 parts only
            links.append((fund_name, url, manager_name))
    with open(filter_list_path, "r") as f:
        filter_list = [line.strip() for line in f if line.strip()]

    for fund_name, fund_link, manager_name in links:
        if fund_name not in filter_list:
            continue  # skip anything not in the second list
        page_html = parse_from_marquee(
            url=fund_link, fund_name=fund_name, manager_name=manager_name, show=show
        )
        if not page_html:
            print(f"No table found or access requested for {fund_name} at {fund_link}")
        else:
            result = gpt_process_text(page_html)
            print(f"{result['fund_name']} processed successfully.")
            _collect_result(
                result, result["fund_name"], results, no_perf_list, folder_path, save
            )
            found_list = found_list + [fund_name]
            filter_list = [item for item in filter_list if item not in found_list]
            # Write the updated list back to the file
            with open(filter_list_path, "w") as f:
                f.write("\n".join(filter_list))
    return results
