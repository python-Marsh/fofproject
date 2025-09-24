from dotenv import load_dotenv
load_dotenv()
from typing import List, Dict
from fofproject.fund import Fund
from openai import OpenAI
from datetime import datetime
import pandas as pd
import calendar
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
Goal: Extract the most specific fund name from the file. Rules: • Convert to ALL CAPS. • Limit to ≤ 2 words (drop “Finance/Capital/Fund/LP/Partners/Ltd” etc). • If only a company name fits, use the company name. Examples: "3W CHINA", "HAO", "TAIREN". If you do not find anything similar to fund name, and think this might not be a fund sheet - add "ERROR" in the fund name. If the name happens to be in the existing list, use the existing name instead: ['TAIREN','HAO','LEXINGTON','LIM','FOREST','WT LS','E20','3W GLOBAL','3W CHINA','3W HEALTHCARE','TIMEFOLIO','MONOLITH','PERSEVERANCE','NEO IVY','JH BIOTECH']

2) fund_des
Goal: Summarize the fund in ≤5 lines. Try include style, edge, manager, and key metrics. End with your rating (a scale of 1 to 5) and overall view of the fund.

3) performance 
Goal: Extract monthly performance as a time series. 
Instruction strictly follows step-by-step: 
a. Copy the monthly return table into a list of lists with the headers included as the first list item. If there's benchmark row in the table, only copy the row of the fund itself into a list of lists. 
b. identify the first item as header, and the rest as value rows. Within the header, identify non-month header and the month header. If the header seems like one cell, then it should be one item. identify if the non-month header should be one continuous header value. For example - "since inception", "since formation" etc. 
c. Clean the value rows by removing empty values. 
Rule: Retain all column headers (e.g., Jan–Dec, YTD), even if data is missing for some months in the value rows. In value rows, remove empty values that are similar to "NaN", "-", "", "ꟷ", or None.
d. Change the monthly header to the format of "%d/%m" and the % value to number like "1.23%" -> "0.0123". 
The final output should be 3 list: a list of lists that includes the table, and a list of month header and a list of non-month header. 
Final output: three lists — (a) full table, (b) month headers, and (c) non-month headers. If you do not identify a monthly return performance table with timeseries data, then simply put the value as "[]". Do not treat the following as valid monthly performance tables: key metrics summary, annual return table that has no monthly timeseries performance.

4) investment_location 
Goal: Categorize geographical focus. Allowed values: ["latin_america","north_america","europe","middle_east_africa","south_asia","apac"]. Rule: If ≥ 3 regions are mentioned, return ["global"]. Otherwise, return 1–2 relevant tags. 

5) investment_strategy 
Goal: Identify strategy type(s). Allowed values: ["equity_ls","event_driven","multi-strat","special_situation","quantitative","market_neutral","convertible_arbitrage","global_macro","vol_arb","stat_arb","cta","activists","commodities","fixed_income"]. Fallback: If unclear, return "NaN" (a single string, not an array). 

6) investment_sector 
Goal: Identify sector focus. Allowed values: ["equity_diversified","equity_energy","equity_industrials","equity_healthcare","equity_TMT","equity_finance","equity_consumer","commodities","fixed_income"]. Fallback: If unclear, return "NaN" (a single string, not an array). Rule: If ≥ 4 sectors are mentioned, return ["equity_diversified"].

7) manager_names
Goal: Collect an array of unique fund manager names mentioned in the file. Keep names clean and consistent (full names, no duplicates).

8) manager_profiles
Goal: Build a object keyed directly by each manager’s name. Each value must contain: a. summary: 1–2 lines on role/mandate b. location: city/region the manager is based (add country if available) c. years_of_experience: whole years (non-negative number)
Rules - 
a. Only derive characteristics for managers whose names appear in manager_names.
b. If a profile cannot be found for a name, append "NaN" as its value instead of leaving it blank.

9) contact
Goal: Collect one primary contact as a JSON object. Must include: a. name: full name b. location: city/region (add country if available) c. email: email address d. number: phone number
Rules
a. If a field is missing, use an empty string "".
b. Return only one contact (the main representative).

10) aum_size 
Goal: Extract fund-level AUM in USD millions (number). Rules: Convert values such as "US$ 1,969.00mn" → 1969.00. 

11) net_exposure 
Goal: Return the net exposure in the format of an array. Use a single number within the list, if it is a number like [0], use 2 number to represent the range like [-0.2, 0.2] 
Rules: 
a. Convert values such as "50%" → 0.5 
b. Always output as a JSON array.

12) net_return 
Goal: Identify whether performance is net of fees. Rules: • true if the document explicitly states returns are after management/performance fees. • Else, false. • If true, use the applicable class fees for management_fee and performance_fee. • If false, still populate management_fee and performance_fee with the most common class fees in the document. 

13) management_fee 
Goal: Extract as a single decimal. Example: "1%" → 0.01. Rule: Use the fee matching the share class of the performance series (or the most common class if unclear).

14) performance_fee 
Goal: Extract as a single decimal. Example: "20%" → 0.20. Rule: Use the fee matching the share class of the performance series (or the most common class if unclear).
"""

RESPONSE_SCHEMA = """
{ "type": "object",
  "properties": {
    "fund_name": {
      "type": "string"
    },
    "fund_des": {
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
      "required": ["table", "month_header", "non_month_header"]
      "additionalProperties": false
    },
    "investment_location": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "investment_strategy": {
      "type": ["array", "string"],
      "items": {
        "type": "string"
      }
    },
    "investment_sector": {
      "type": ["array", "string"],
      "items": {
        "type": "string"
      }
    },
    "manager_names": {
      "type": "array",
      "items": { "type": "string"},
      "uniqueItems": true
    },
    "manager_profiles": {
      "type": "object",
      "patternProperties": {
        "^.+$": {
          "type": "object",
          "properties": {
            "summary": { "type": "string" },
            "location": { "type": "string" },
            "years_of_experience": { "type": "number"}
          },
          "required": ["summary", "location", "years_of_experience"],
        }
      }
    },
    "contact": {
      "type": "object",
      "properties": {
        "name": { "type": "string" },
        "location": { "type": "string" },
        "email": { "type": "string", "format": "email" },
        "number": { "type": "string" }
      },
      "required": ["name", "location", "email", "number"],
    },
    "aum_size": {
      "type": "number"
    },
    "net_exposure": {
      "type": "array",
      "items": { "type": "number" },
      "maxItems": 2
    }
    "net_return": {
      "type": "boolean"
    },
    "management_fee": {
      "type": "number"
    },
    "performance_fee": {
      "type": "number"
    }
  },
  "required": [
    "fund_name",
    "fund_des",
    "performance",
    "investment_location",
    "investment_strategy",
    "investment_sector",
    "manager_names",
    "manager_profiles",
    "contact",
    "aum_size",
    "net_exposure",
    "net_return",
    "management_fee",
    "performance_fee"
  ]
}
"""

ignored_funds = []

def process_performance(data):
    
    # GPT's own screening
    if data['fund_name'] == "ERROR":
        print(f"No performance table found in {data["fund_name"]}")
        return "No Performance Found"

    table = data["performance"]["table"]
    month_header = data["performance"]["month_header"]
    non_month_header = set(data["performance"]["non_month_header"])
    
    if not table:
        print(f"No performance table found in {data["fund_name"]}")
        return "No Performance Found"

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
    # Identify year column position
    def parse_yearly_performance(data_lists):
        """
        Takes a list of lists, where each sub-list starts with a year followed by performance values.
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
        year_counter = 2025
        for data in data_lists:
            year = None
            values = []
            
            for item in data:
                value = item.strip()
                # skip the empty value, and do not append it to the results
                if not value:
                    continue
                # find the year and add it as key later
                if value.isdigit() and 1900 <= int(value) <= 2100:
                    year = int(value)
                    years.append(year)
                # find the value and add it as values
                else:
                    values.append(value)
            
            if year is None:
              year = year_counter
              years.append(year)
              year_counter -= 1
              print(f"No valid year found in {data}, appending {year}")
            results.append({year: values})
        
        return results
    rows = parse_yearly_performance(rows)
    earliest_year = min(years)
    latest_year = max(years)

    # Clean non_month_header
    cleaned_rows = []

    # Count how many non-month headers exist in the header
    count_non_month = sum(1 for h in header if h in non_month_header)

    for entry in rows:
        year, values = next(iter(entry.items()))  # unpack single-key dict

        # Only trim if earliest or latest year, becasue some cases the earliest and latest include the suffix but not in between
        if count_non_month > 0 and year in (earliest_year, latest_year):
            if len(values) >= count_non_month:
                values = values[:-count_non_month]

        # Ensure middle years always have 12
        if year not in (earliest_year, latest_year):
            if len(values) > 12:
                values = values[:12] # drop last elements till 12, assume there are no values appended in the front
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
                    print(f"No standard table found in {data['fund_name']}, returned empty list")
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
                    print(f"No standard table found in {data['fund_name']}, returned empty list")
                    return []
                cleaned_rows.append({"date": date_str, "value": num})
                if year != latest_year and len(values) != 12:
                    print(f"Parsing error: Year {year} has {len(values)} values (expected 12).")
    return cleaned_rows  

def extract_text_from_pdf(file_path):
    doc = fitz.open(file_path)
    text = ""
    for page in doc:
        text += page.get_text()
    return text

def gpt_process_pdf(file_path: str):
    """
    Upload a PDF and run GPT extraction according to the schema.
    Returns the parsed JSON or raw text if parsing fails.
    """
    client = OpenAI(
      api_key=os.getenv("OPENAI_API_KEY")
    )
    text = extract_text_from_pdf(file_path)
    # Cannot parse pdf into text, try uploading pdf directly
    if not text:
        uploaded_file = client.files.create(
          file=open(file_path, "rb"),  
          purpose="assistants"
        )

        response = client.responses.create(
          model="gpt-4.1-mini",
          temperature=0,
          input=[
              {
                  "role": "system",
                  "content": [
                      {"type": "input_text", "text": SYSTEM_PROMPT + "\n\nJSON Schema:\n" + RESPONSE_SCHEMA}
                  ]
              },
              {
                  "role": "user",
                  "content": [
                      {"type": "input_text", "text": "Extract the required data from this PDF file:"},
                      {"type": "input_file", "file_id": uploaded_file.id} 
                  ]
              }
          ]
        )
        output_text = response.output_text.strip()
        data = json.loads(output_text)
        data["performance"] = process_performance(data)
        print(f"{data['fund_name']} uses pdf screening. Values can be unstable...")
        data['fund_name'] = f"{data['fund_name']} from_pdf"
    else:
        response = client.responses.create(
            model="gpt-4.1-mini",
            temperature=0,
            input=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT + "\n\nJSON Schema:\n" + RESPONSE_SCHEMA
                },
                {
                    "role": "user",
                    "content": f"Extract the required data from this file:\n{text}"
                }
            ]
        )
        output_text = response.output_text.strip()
        print(output_text)
        data = json.loads(output_text)
        data["performance"] = process_performance(data)
    data['performance'].sort(key=lambda x:datetime.strptime(x["date"], "%d/%m/%Y"))
    print(data)
    return data

def process_pdfs_in_folder(folder_path="input", save=False):
    """
    Iterates through all PDFs in the same folder as this script (relative path).
    Returns a list of JSON results.
    """
    results = []
    no_perf_list =[]
    for file_name in os.listdir(folder_path):
        if file_name.lower().endswith(".pdf"):
            file_path = os.path.join(folder_path, file_name)
            print(f"Processing: {file_path}")
            result = gpt_process_pdf(file_path)
            # Checking if there's performance recorded
            val = result['performance']
            if not (isinstance(val, list) and all(isinstance(item, dict) for item in val)) or (isinstance(val, list) and not val)  :
                no_perf_list = no_perf_list + [result['fund_name']]
            results.append(result)
            if save:
              # Create "json" folder inside folder_path if it doesn't exist
              json_folder = os.path.join(folder_path, "json")
              os.makedirs(json_folder, exist_ok=True)

              # Save """decide on result or results""" to a JSON file inside the "json" folder
              output_path = os.path.join(json_folder, f"{result['fund_name']}.json")
              with open(output_path, "w", encoding="utf-8") as f:
                  json.dump(result, f, ensure_ascii=False, indent=2)
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
        print(f"No 'json' folder found in {folder_path}.")
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
                print(f"⚠️ Error loading {file_name}: {e}")
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
        if not isinstance(perf_data, list):
            print(f"⚠️ Skipping {fund_name} (no performance data).")
            continue

        for perf in perf_data:
            # Normalize the date format (assuming dd/mm/yyyy)
            try:
                date = datetime.strptime(perf["date"], "%d/%m/%Y").date()
            except Exception as e:
                print(f"⚠️ Skipping bad date '{perf.get('date')}' for {fund_name}: {e}")
                continue
            
            if date not in data:
                data[date] = {}
            data[date][fund_name] = perf["value"]

    df_new = pd.DataFrame.from_dict(data, orient="index").sort_index()
    df_new.index.name = "date"
    df_new.reset_index(inplace=True)
    df_new["date"] = pd.to_datetime(df_new["date"], dayfirst=True).dt.strftime("%d/%m/%Y")

    if os.path.exists(output_path):
        # Load the old CSV
        df_old = pd.read_csv(output_path)

        # Merge: keep latest values for overlapping dates
        df_combined = pd.concat([df_old, df_new], ignore_index=True)
        # Normalize old dates as well
        df_old["date"] = pd.to_datetime(df_old["date"], dayfirst=True).dt.strftime("%d/%m/%Y")
        # Drop duplicates on 'date', keeping the last occurrence (from df_new)
        df_combined = df_combined.drop_duplicates(subset="date", keep="last")

        # Sort by date if needed
        df_combined = df_combined.sort_values(by="date")

        print(f"🔄 Existing CSV found. Updated with new data and saved to {output_path}")
    else:
        # If no CSV exists, just use the new data
        df_combined = df_new
        print(f"✅ No existing CSV found. Created new CSV at {output_path}")

    # Save to CSV
    df_combined["date"] = pd.to_datetime(df_combined["date"], dayfirst=True).dt.strftime("%d/%m/%Y")
    df_combined.to_csv(output_path, index=False)

    return df_combined

def init_funds(funds_data: List[Dict]) -> Dict[str, Fund]:
    """Initialize Fund objects from a list of dicts.

    Args:
        funds_data (List[Dict]): List of fund-like dicts

    Returns:
        Dict{Fund_name: fund}: Successfully initialized Fund objects
    """
    initialized_funds = {}
    for data in funds_data:
        try:
            fund = Fund(
                name=data["fund_name"],
                monthly_returns=data.get("performance"),
                fund_des=data.get("fund_des"),
                investment_location=data.get("investment_location"),
                investment_strategy=data.get("investment_strategy"),
                investment_sector=data.get("investment_sector"),
                manager_names=data.get("manager_names"),
                manager_profiles=data.get("manager_profiles"),
                contact=data.get("contact"),
                aum_size=data.get("aum_size"),
                net_exposure=data.get("net_exposure"),
                net_return=data.get("net_return"),
                management_fee=data.get("management_fee"),
                performance_fee=data.get("performance_fee"),
            )
            initialized_funds[f'{fund.name}'] = fund
        except ValueError as e:
            # Skip invalid funds and log the issue
            print(f"Skipping fund {data.get('fund_name', 'UNKNOWN')}: {e}")
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
        results.append({
            "fund_name": fund.name,
            "fund_des": getattr(fund, "fund_des", None),
            "performance": string_returns,
            "investment_location": getattr(fund, "investment_location", None),
            "investment_strategy": getattr(fund, "investment_strategy", None),
            "investment_sector": getattr(fund, "investment_sector", None),
            "manager_names": getattr(fund, "manager_names", None),
            "manager_profiles": getattr(fund, "manager_profiles", None),
            "contact": getattr(fund, "contact", None),
            "aum_size": getattr(fund, "aum_size", None),
            "net_exposure": getattr(fund, "net_exposure", None),
            "net_return": getattr(fund, "net_return", None),
            "management_fee": getattr(fund, "management_fee", None),
            "performance_fee": getattr(fund, "performance_fee", None),
        })
    return results

def results_to_json(results, folder_path="input"):
    for result in results:
      # Create "json" folder inside folder_path if it doesn't exist
      json_folder = os.path.join(folder_path, "json")
      os.makedirs(json_folder, exist_ok=True)

      # Save """decide on result or results""" to a JSON file inside the "json" folder
      output_path = os.path.join(json_folder, f"{result['fund_name']}.json")
      with open(output_path, "w", encoding="utf-8") as f:
          json.dump(result, f, ensure_ascii=False, indent=2)

def save_changes_in_fund(funds: Dict[str, Fund], folder_path = "input"):
    results = offload_funds(funds)
    results_to_csv(results=results, folder_path=folder_path)
    results_to_json(results=results, folder_path=folder_path)

def merge_funds(dict1: dict, dict2: dict) -> dict:
    """
    Merge two dictionaries of {fund_name: fund_object}.
    Keep dict2's fund objects, but if a fund_name exists in both,
    overwrite only the 'monthly_returns' attribute from dict1.
    """
    merged = dict2.copy()
    
    for fund_name, fund_obj in dict1.items():
        if fund_name in merged:
            # Only update monthly_returns
            merged[fund_name].monthly_returns = fund_obj.monthly_returns
        else:
            # If fund not in dict2, add it fully
            merged[fund_name] = fund_obj
    
    return merged



