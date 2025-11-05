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
Goal: Extract the most specific fund name from the file. Rules: • Convert to ALL CAPS. • Limit to ≤ 2 words (drop “Finance/Capital/Fund/LP/Partners/Ltd” etc). 
Rules -
a. If only a company name fits, use the company name. Examples: "3W CHINA", "HAO", "TAIREN". 
b. If you do not find anything similar to fund name, and think this might not be a fund sheet - add "ERROR" in the fund name. 
c. If the name happens to be similar the following list, use the existing name instead: ['TAIREN','HAO','LEXINGTON','LIM','FOREST','WT CHINA','E20','3W GLOBAL','3W CHINA','3W HEALTHCARE','TIMEFOLIO','MONOLITH','PERSEVERANCE','NEO IVY','JH BIOTECH']. Some specific change are listed here: "Lim Japan Event Fund" → "LIM" - e.g. "水木清风特殊机会基金" → "FOREST",  "Janus Henderson Biotechnology" → "JH BIOTECH".


2) fund_des
Goal: Summarize the fund in ≤5 lines. Try include style, edge, manager, and key metrics. End with your rating (a scale of 1 to 5) and overall view of the fund.

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
Final output: three lists — (a) full table, (b) month headers, and (c) non-month headers. If you do not identify a monthly return performance table with timeseries data, then simply put the value as "[]". Do not treat the following as valid monthly performance tables: key metrics summary, annual return table that has no monthly timeseries performance.

4) investment_location 
Goal: Categorize geographical focus. Allowed values, strictly a list with one or more of the following values: ["latin_america","north_america","europe","middle_east_africa","south_asia","apac"]. Rule: If ≥ 3 regions are mentioned, return ["global"]. Otherwise, return 1–2 relevant tags. 

5) investment_strategy 
Goal: Identify strategy type(s). Allowed values, strictly a list with one or more of the following values: ["equity_ls","event_driven","multi-strat","special_situation","quantitative","market_neutral","convertible_arbitrage","global_macro","vol_arb","stat_arb","cta","activists","commodities","fixed_income"]. Fallback: If unclear, return "NaN" (a single string, not an array). 

6) investment_sector 
Goal: Identify sector focus. Allowed values, strictly a list with one or more of the following values: ["equity_diversified","equity_energy","equity_industrials","equity_healthcare","equity_TMT","equity_finance","equity_consumer","commodities","fixed_income"]. Fallback: If unclear, return "NaN" (a single string, not an array). Rule: If ≥ 4 sectors are mentioned, return ["equity_diversified"].

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
        print(f"No performance table found by GPT in {data["fund_name"]}")
        return []

    table = data["performance"]["table"]
    month_header = data["performance"]["month_header"]
    non_month_header = set(data["performance"]["non_month_header"])
    
    if len(month_header) != 12:
        print(f"Wrongly assigned a month header of {len(month_header)} from {data["fund_name"]}, would result in extra values in earliest and latest months")

    if not table:
        print(f"Empty values in performance table of {data["fund_name"]}")
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
    # Identify year column position
    def parse_yearly_performance(data_lists, year_counter = 2025):
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
    if earliest_year == latest_year:
        print(f"Only one year {earliest_year} found in table of {data['fund_name']}, returned empty list")
        return []

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
            elif len(values) < 12:
                print(f"Less than 12 months found in table of {data['fund_name']}, returned empty list")
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
                    print(f"Non-number found in {data['fund_name']}'s latest month, returned empty list")
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
                    print(f"Non-number found in {data['fund_name']}'s earliest month, returned empty list")
                    return []
                cleaned_rows.append({"date": date_str, "value": num})
                if year != latest_year and len(values) != 12:
                    print(f"Parsing error: Year {year} has {len(values)} values (expected 12).")
                    return []
    return cleaned_rows  

def extract_text_from_pdf(file_path):
    doc = fitz.open(file_path)
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
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
        with open(file_path, "rb") as f:
            uploaded_file = client.files.create(
                file=f,
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
        data = json.loads(output_text)
        data["performance"] = process_performance(data)
    if isinstance(data.get('performance'), list) and data['performance']:
    # Check if performance is a non-empty list and sort it
      data['performance'].sort(key=lambda x:datetime.strptime(x["date"], "%d/%m/%Y"))
    return data

def gpt_process_text(text: str):
    """
    From text and run GPT extraction according to the schema.
    Returns the parsed JSON or raw text if parsing fails.
    """
    client = OpenAI(
      api_key=os.getenv("OPENAI_API_KEY")
    )
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
    data = json.loads(output_text)
    data["performance"] = process_performance(data)
    if isinstance(data.get('performance'), list) and data['performance']:
    # Check if performance is a non-empty list and sort it
      data['performance'].sort(key=lambda x:datetime.strptime(x["date"], "%d/%m/%Y"))
    return data

def process_pdfs_in_folder(folder_path="input", save=False, identifier = "_parsed from_"):
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
            # Renaming of the pdf. file
            base, ext = os.path.splitext(file_name)
            if identifier in base:
                # keep only the part after "identifier" to make sure the file name will not stack up & Re-run with the same fund_name stored
                prefix, base = base.split(identifier, 1)
                result['fund_name'] = prefix
            new_file_name = f"{result['fund_name']}{identifier}{base}{ext}"
            new_path = os.path.join(folder_path, new_file_name)
            os.rename(file_path, new_path)
            results.append(result)
            if save:
              # Create "json" folder inside folder_path if it doesn't exist
              json_folder = os.path.join(folder_path, "json")
              os.makedirs(json_folder, exist_ok=True)

              # Save """decide on result or results""" to a JSON file inside the "json" folder
              output_path = os.path.join(json_folder, f"{result['fund_name']}.json")
              with open(output_path, "w", encoding="utf-8") as f:
                  json.dump(result, f, ensure_ascii=False, indent=2)
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
        print("No Performance Found.txt not found.")
        return []

    # Read fund_name prefixes from the file
    with open(no_perf_path, "r") as f:
        fund_prefixes = [line.strip() for line in f if line.strip()]

    if not fund_prefixes:  # file is empty or only blank lines
        print("No entries found in No Performance Found.txt.")
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
                print(f"Re-processing (no performance before): {file_path}")
                result = gpt_process_pdf(file_path)

                # Check performance again
                val = result['performance']
                if not (isinstance(val, list) and all(isinstance(item, dict) for item in val)) or (isinstance(val, list) and not val):
                    still_no_perf.append(result['fund_name'])

                # Save JSON if required
                if save:
                    json_folder = os.path.join(folder_path, "json")
                    os.makedirs(json_folder, exist_ok=True)
                    output_path = os.path.join(json_folder, f"{result['fund_name']}.json")
                    with open(output_path, "w", encoding="utf-8") as f_out:
                        json.dump(result, f_out, ensure_ascii=False, indent=2)

                results.append(result)
                break  # stop after matching one prefix

    # Update the No Performance file with those still missing performance
    with open(no_perf_path, "w") as f:
        f.write("\n".join(still_no_perf))

    return results

def continue_running(folder_path="input", save=False, identifier = "_parsed from_"):
    """
    Processes only PDFs in the folder that do NOT already contain the identifier in their name.
    Returns a list of JSON results.
    """
    results = []
    no_perf_list = []

    for file_name in os.listdir(folder_path):
        if file_name.lower().endswith(".pdf"):
            base, ext = os.path.splitext(file_name)

            # Skip files that already have the identifier in the name
            if identifier in base:
                continue

            file_path = os.path.join(folder_path, file_name)
            print(f"Processing: {file_path}")
            result = gpt_process_pdf(file_path)

            # Checking if there's performance recorded
            val = result['performance']
            if not (isinstance(val, list) and all(isinstance(item, dict) for item in val)) or (isinstance(val, list) and not val):
                no_perf_list.append(result['fund_name'])

            # Renaming the file with identifier
            new_file_name = f"{result['fund_name']}{identifier}{base}{ext}"
            new_path = os.path.join(folder_path, new_file_name)
            os.rename(file_path, new_path)

            results.append(result)

            if save:
                # Create "json" folder inside folder_path if it doesn't exist
                json_folder = os.path.join(folder_path, "json")
                os.makedirs(json_folder, exist_ok=True)

                # Save results to a JSON file inside the "json" folder
                output_path = os.path.join(json_folder, f"{result['fund_name']}.json")
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)

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
        if not isinstance(perf_data, list) or not perf_data:
            print(f"⚠️ Skipping {fund_name} (no performance data).")
            continue

        for perf in perf_data:
            # Normalize the date format (assuming dd/mm/yyyy)
            try:
                date = datetime.strptime(perf["date"], "%d/%m/%Y").date()
            except Exception as e:
                print(f"⚠️ Skipping due to wrong date format '{perf.get('date')}' for {fund_name}: {e}")
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
    funds = init_funds(results)
    return funds

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
    # Reload so there is no stale computation of key metrics
    results = offload_funds(merged)
    merged = init_funds(results)
    return merged

def parse_from_marquee(url: str, fund_name: str = "", manager_name:str = "", show=False):
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
    Mq_username=os.getenv("MQ_USERNAME")
    Mq_password=os.getenv("MQ_PASSWORD")
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
            button = driver.find_element(By.CSS_SELECTOR, "button[data-cy='gs-uitk-button__button']")
            button.click()
            # Wait for password field to appear
            password_field = wait.until(
                EC.presence_of_element_located((By.NAME, "password"))
            )
            # Enter your password
            password_field.send_keys(Mq_password)
            button = driver.find_element(By.CSS_SELECTOR, "button[data-cy='gs-uitk-button__button']")
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
                EC.presence_of_all_elements_located((By.XPATH,"//span[text()='Request Full Access' or text()='Full Access Requested']"))
            )
        except:
            buttons = []   # no buttons found in time
            print("No Request Full Access button found.")

        if buttons:  # Found request buttons
            page_html = False
            requested_list = [fund_name]
            for span in buttons:
                # Check if inside a clickable parent (button or link)
                try:
                    parent = span.find_element(By.XPATH, "./ancestor::*[self::button or self::a]")
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
                tbody = wait.until(EC.presence_of_element_located((By.XPATH, "//tbody")))
                if tbody:
                    print("Table found.")
                    try:  # wait a bit more for full table load
                        card = driver.find_element(
                            By.XPATH,
                            "//div[contains(@class,'aurora-card')][.//div[@class='aurora-card-head-title' and contains(normalize-space(.), 'Fund(s)')]]"
                        )
                        # Then find the body inside that card
                        card_body = card.find_element(By.XPATH, ".//div[@class='aurora-card-body']")
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
                                print(f"Navigated to fund page for manager: {manager_name}")
                                break
                            elif fund_name.lower() in text.lower():
                                selected_link = href
                                print(f"Navigated to fund page for fund: {fund_name}")
                                break
                            else:
                                selected_link = fund_options[0][0]  # default to first option if no match
                                print(f"No exact match found; defaulting to first fund option: {fund_options[0][0]}")
                        if selected_link:
                            driver.get(selected_link)
                            time.sleep(25)  # wait for the new page to load
                            try:
                                buttons = wait.until(
                                    EC.presence_of_all_elements_located((By.XPATH,"//span[text()='Request Full Access' or text()='Full Access Requested']"))
                                )
                            except:
                                buttons = []   # no buttons found in time
                                print("No Request Full Access button found.")

                            if buttons:  # Found request buttons
                                page_html = False
                                requested_list = [fund_name]
                                for span in buttons:
                                    # Check if inside a clickable parent (button or link)
                                    try:
                                        parent = span.find_element(By.XPATH, "./ancestor::*[self::button or self::a]")
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
                                    tbody = wait.until(EC.presence_of_element_located((By.XPATH, "//tbody")))
                                    if tbody:
                                        try:
                                            table = driver.find_element(
                                                By.XPATH,
                                                "//div[@class='aurora-card-head-title' and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'return')]"
                                            )
                                            print("Performance table found after selection page.")
                                            page_html = driver.execute_script("return document.body.innerText;")
                                        except Exception as e:
                                            print("Performance table not found after selection but there is a table", e)
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
                                "//div[@class='aurora-card-head-title' and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'return')]"
                            )
                            print("Performance table found.")
                            page_html = driver.execute_script("return document.body.innerText;")
                        except Exception as e:
                            print("Performance table not found but there is a table", e)
                            page_html = False
                            requested_list = [fund_name]
            except TimeoutException:
                print(f"No table found or access requested for {fund_name} at {url} under given time.")
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

def get_link_from_html(folder_path=r"input\marquee",  save=False, show=False):
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
                            header_row = table.find("tr", style=lambda s: s and "mso-yfti-irow:0" in s)
                            if header_row:
                                # Extract text from all cells in that header row
                                headers = [
                                    " ".join(cell.get_text(separator=" ", strip=True).split())
                                    for cell in header_row.find_all(["td", "th"])
                                ]
                                matching_index = next(
                                    (i for i, h in enumerate(headers)
                                    if any(k in h.lower() for k in ["presenter", "manager"])),
                                    None  # default if no match is found
                                )
                    presenter_text = td_cells[matching_index].get_text(separator=" ", strip=True)
                    links.append((fund_name, fund_link, presenter_text))
            links_path = os.path.join(folder_path, "Links & Names.txt")
            with open(links_path, "w") as f:
                for url, name, manager in links:
                    f.write(f"{url}\t{name}\t{manager}\n")
    # Parsing information from the links
    for fund_name, fund_link, manager_name in links:
        page_html, requested_list = parse_from_marquee(url=fund_link, fund_name=fund_name, manager_name=manager_name,show=show)
        if not page_html:
            print(f"No table found or access requested for {fund_name} at {fund_link}")
            empty_list = empty_list + requested_list
            no_table_path = os.path.join(folder_path, "Requested & No Table.txt")
            with open(no_table_path, "w") as f:
                f.write("\n".join(empty_list))
        else:
            result = gpt_process_text(page_html)
            # Checking if there's performance recorded
            val = result['performance']
            if not (isinstance(val, list) and all(isinstance(item, dict) for item in val)) or (isinstance(val, list) and not val)  :
                no_perf_list = no_perf_list + [fund_name]
            no_perf_path = os.path.join(folder_path, "No Performance Found.txt")
            with open(no_perf_path, "w") as f:
                f.write("\n".join(no_perf_list))    
            results.append(result)
            if save:
              # Create "json" folder inside folder_path if it doesn't exist
              json_folder = os.path.join(folder_path, "json")
              os.makedirs(json_folder, exist_ok=True)
              # Save result to a JSON file inside the "json" folder
              output_path = os.path.join(json_folder, f"{result['fund_name']}.json")
              with open(output_path, "w", encoding="utf-8") as f:
                  json.dump(result, f, ensure_ascii=False, indent=2)
    return results

def rerun_no_table_list (folder_path=r"input\marquee", save=False, show = False):
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
            fund_name, url, manager_name= line.strip().split("\t", 2)  # split into 2 parts only
            links.append(( fund_name, url, manager_name))
    with open(filter_list_path, "r") as f:
        filter_list = [line.strip() for line in f if line.strip()]
    
    for fund_name, fund_link, manager_name in links:
        if fund_name not in filter_list:
            continue  # skip anything not in the second list
        page_html = parse_from_marquee(url=fund_link, fund_name=fund_name, manager_name=manager_name, show=show)
        if not page_html:
            print(f"No table found or access requested for {fund_name} at {fund_link}")
        else:
            result = gpt_process_text(page_html)
            # Checking if there's performance recorded
            print(f"{result['fund_name']} processed successfully.")
            val = result['performance']
            if not (isinstance(val, list) and all(isinstance(item, dict) for item in val)) or (isinstance(val, list) and not val)  :
                no_perf_list = no_perf_list + [result['fund_name']]
            found_list = found_list + [fund_name]
            filter_list = [item for item in filter_list if item not in found_list]
            # Write the updated list back to the file
            with open(filter_list_path, "w") as f:
                f.write("\n".join(filter_list))
            results.append(result)
            if save:
                # Create "json" folder inside folder_path if it doesn't exist
                json_folder = os.path.join(folder_path, "json")
                os.makedirs(json_folder, exist_ok=True)
                # Save result to a JSON file inside the "json" folder
                output_path = os.path.join(json_folder, f"{result['fund_name']}.json")
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
    return results



rerun_no_table_list(save = True, show=True)