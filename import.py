from dotenv import load_dotenv
load_dotenv()

from openai import OpenAI
from datetime import datetime
import calendar
import os
import json
import fitz


SYSTEM_PROMPT = """
You are a precise financial data extractor. 

OUTPUT CONTRACT (IMPORTANT): 
- Return VALID JSON ONLY that conforms exactly to the JSON Schema below. 
- No explanations, no markdown, no code fences, no comments — only the JSON object. 
- If a field is not supported by the source, obey the field-specific fallback rules below. 
- Do not invent fields not present in the schema. 
- Do not include additional properties. 
- Be careful. Don't leave out any value, especially within the performance table.

SECTION-WISE INSTRUCTIONS 

1) fund_name 
Goal: Extract the most specific fund name from the file. Rules: • Convert to ALL CAPS. • Limit to ≤ 2 words (drop “Finance/Capital/Fund/LP/Partners/Ltd” etc). • If only a company name fits, use the company name. Examples: "3W CHINA", "HAO", "TAIREN". 

2) performance 
Goal: Extract monthly performance as a time series. 
Instruction strictly follows step-by-step: 
a. Copy the monthly return table into a list of list with the headers included as the first list item. If there's benchmark row in the table, only copy the row of the fund itself into a list of list. Drop all the value that is similar to "NaN" empty or "-" or None. Don't leave any value or items as None, delete the item entirely 
b. identify the first item as header, identify non-month header and the month header. If the header seems like one cell, then it should be one item. identify if the non-month header should be one continuous header value. For example - "since inception", "since formation" etc. 
c. Change the monthly header to the format of "%d/%m" and the % value to number like "1.23%" -> "0.0123". 
The final output should be 3 list: a list of lists that includes the table, and a list of month header and a list of non-month header. 
Final output: three lists — (a) full table, (b) month headers, and (c) non-month headers. 

3) investment_location 
Goal: Categorize geographical focus. Allowed values: ["latin_america","north_america","europe","middle_east_africa","south_asia","apac"]. Rule: If ≥ 3 regions are mentioned, return ["global"]. Otherwise, return 1–2 relevant tags. 

4) investment_strategy 
Goal: Identify strategy type(s). Allowed values: ["equity_ls","event_driven","multi-strat","special_situation","quantitative","market_neutral","convertible_arbitrage","global_macro","vol_arb","stat_arb","cta","activists","commodities","fixed_income"]. Fallback: If unclear, return "NaN" (a single string, not an array). 

5) investment_sector 
Goal: Identify sector focus. Allowed values: ["equity_diversified","equity_healthcare","equity_TMT","equity_finance","equity_consumer","commodities","fixed_income"]. Fallback: If unclear, return "NaN" (a single string, not an array). 

6) aum_size 
Goal: Extract fund-level AUM in USD millions (number). Rules: Convert values such as "US$ 1,969.00mn" → 1969.00. 

7) net_return 
Goal: Identify whether performance is net of fees. Rules: • true if the document explicitly states returns are after management/performance fees. • Else, false. • If true, use the applicable class fees for management_fee and performance_fee. • If false, still populate management_fee and performance_fee with the most common class fees in the document. 

8) management_fee 
Goal: Extract as a single decimal. Example: "1%" → 0.01. Rule: Use the fee matching the share class of the performance series (or the most common class if unclear).

9) performance_fee 
Goal: Extract as a single decimal. Example: "20%" → 0.20. Rule: Use the fee matching the share class of the performance series (or the most common class if unclear).
"""

RESPONSE_SCHEMA = """
{ "type": "object",
  "properties": {
    "fund_name": {
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
    "aum_size": {
      "type": "number"
    },
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
    "performance",
    "investment_location",
    "investment_strategy",
    "investment_sector",
    "aum_size",
    "net_return",
    "management_fee",
    "performance_fee"
  ]
}
"""

client = OpenAI(
  api_key=os.getenv("OPENAI_API_KEY")
)

def process_performance(data):
    table = data["performance"]["table"]
    month_header = data["performance"]["month_header"]
    non_month_header = set(data["performance"]["non_month_header"])
    
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
        
        for data in data_lists:
            year = None
            values = []
            
            for item in data:
                value = item.strip()
                if value.isdigit() and 1900 <= int(value) <= 2100:
                    year = int(value)
                    years.append(year)
                else:
                    values.append(value)
            
            if year is None:
                raise ValueError(f"No valid year found in {data}")
            
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
                num = float(val.strip("%")) / 100 if "%" in val else float(val)
                cleaned_rows.append({"date": date_str, "value": num})
                month -= 1
        else:
            # Assign forward from January
            for month, val in enumerate(values, start=1):
                last_day = calendar.monthrange(year, month)[1]
                date_str = f"{last_day:02d}/{month:02d}/{year}"
                num = float(val.strip("%")) / 100 if "%" in val else float(val)
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

def process_pdf(file_path: str):
    """
    Upload a PDF and run GPT extraction according to the schema.
    Returns the parsed JSON or raw text if parsing fails.
    """
    text = extract_text_from_pdf(file_path)
    response = client.responses.create(
        model="gpt-5-nano",
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
    return data

def process_pdfs_in_current_folder():
    """
    Iterates through all PDFs in the same folder as this script (relative path).
    Returns a list of JSON results.
    """
    folder_path = "."   # relative path to current directory
    results = []

    for file_name in os.listdir(folder_path):
        if file_name.lower().endswith(".pdf"):
            file_path = os.path.join(folder_path, file_name)
            print(f"Processing: {file_path}")
            result = process_pdf(file_path)
            results.append(result)
    return results

file_path = r"input\BASSWOOD_US_EquityLS_6.99% rtn.pdf"
jj_sonson = process_pdf(file_path)
print(jj_sonson)

# sample_fund_data = {
#     "fund_name": "ASIAN TECHNOLOGY",
#     "performance": {
#         "table": [
#             ["01/01","01/02","01/03","01/04","01/05","01/06","01/07","01/08","01/09","01/10","01/11","01/12","since_inception","Year"],
#             ["-0.0486","-0.0878","-0.0595","0.0235","0.0101","0.0935","-0.0773","2025"],
#             ["0.0718","0.1738","0.0239","0.0038","0.0624","0.0303","-0.0069","0.0903","0.0183","-0.0352","0.0347","0.1552","0.1552","0.1552","2024"],
#             ["0.1062","-0.0576","0.0999","-0.0661","0.093","0.0541","0.2873","-0.1612","-0.0937","-0.0375","0.0672","0.0319","0.1552","2023"],
#             ["-0.165","-0.0492","-0.0535","-0.2016","0.0835","-0.1756","-0.0594","0.0738","-0.1436","-0.1903","0.3106","-0.1301","0.1552","2022"],
#             ["0.0436","0.1209","-0.0311","0.0779","-0.0707","0.0849","0.0132","-0.0144","-0.038","0.0323","0.0721","0.058","0.1552","2021"],
#             ["-0.119","0.1154","-0.1976","0.1572","0.0298","0.2574","0.1768","-0.1035","0.0407","0.0105","0.1126","0.0778","0.1552","2020"],
#             ["-0.0576","0.0901","-0.0093","0.0001","-0.1246","0.1161","0.15","0.0272","0.0774","0.0906","-0.052","0.0474","0.1552","2019"],
#             ["0.0252","0.0698","-0.0381","-0.0763","0.1378","0.0923","-0.0565","-0.1221","-0.0533","-0.0632","-0.0184","-0.0096","0.1552","2018"],
#             ["0.0673","0.0664","0.0429","0.0137","-0.0172","0.0289","0.0706","0.038","-0.0051","0.0928","0.0076","-0.0717","0.1552","2017"],
#             ["-0.0241","0.0091","0.0324","3.3%","-0.0154","0.0036","0.0381","0.0647","0.0089","-0.0505","-0.0492","0.0252","0.1552","2016"],
#             ["0.0148","0.024","0.0682","0.0736","0.0375","-0.0433","-0.1047","-0.0087","0.0036","0.0511","0.0335","0.04","0.1552","2015"],
#             ["-6.34%","0.0486","0.0458","0.0057","0.0521","-0.0207","-0.0095","0.0072","-0.0104","0.0507","2014"]
#         ],
#         "month_header": ["01/01","01/02","01/03","01/04","01/05","01/06","01/07","01/08","01/09","01/10","01/11","01/12"],
#         "non_month_header": ["Year","since_inception"]
#     },
#     "investment_location": ["global"],
#     "investment_strategy": ["equity_ls"],
#     "investment_sector": ["equity_TMT"],
#     "aum_size": 79.0,
#     "net_return": True,
#     "management_fee": 0.015,
#     "performance_fee": 0.2
# }



