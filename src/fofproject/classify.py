"""
Email Classification System for Hedge Fund Emails

This module classifies emails using GPT API to identify hedge fund related emails
and organizes them into firm-specific folders.

Key features:
1. GPT-based identification of hedge fund related emails
2. Firm name extraction from email address, content, and attachments
3. Deterministic firm name normalization with human-editable mappings
4. Automatic folder organization by firm
"""

from dotenv import load_dotenv
from pathlib import Path
import os
import json
import re
import shutil
import time
import base64
from datetime import datetime
from typing import Optional  # noqa: F401 - kept for potential future use
from openai import OpenAI
from urllib.parse import urlparse, urljoin

# Load .env from the same directory as this script
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

# =========================
# CONFIGURATION
# =========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Default paths (can be overridden)
DEFAULT_EMAIL_INPUT_DIR = Path(r"C:\Users\FOF Analyst\Desktop\fofproject\output\testing\email")
DEFAULT_OUTPUT_DIR = Path(r"C:\Users\FOF Analyst\Desktop\fofproject\output\testing\fund firm identifier")

# File names for persistent data
FIRM_MAPPINGS_FILE = "firm_name_mappings.json"  # Human-editable mappings
CLASSIFICATION_CACHE_FILE = "classification_cache.json"  # Cache of GPT classifications
CLASSIFICATION_REPORT_FILE = "classification_report.json"  # Full report of all classifications


def get_openai_client() -> OpenAI:
    """Get OpenAI client instance."""
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not found in environment variables")
    return OpenAI(api_key=OPENAI_API_KEY)


def load_firm_mappings(output_dir: Path) -> dict:
    """
    Load human-editable firm name mappings.

    Structure:
    {
        "canonical_names": {
            "SPRINGS CAPITAL": {
                "aliases": ["Springs Capital", "springs-capital", "Springs Capital (Hong Kong) Limited"],
                "description": "China-focused hedge fund",
                "funds": {
                    "fund_springs_china_alpha": {
                        "display_name": "Springs China Alpha Fund",
                        "aliases": ["China Alpha"],
                        "auto_added": "2026-02-24T..."
                    }
                }
            }
        },
        "email_overrides": {
            "john.doe@example.com": "FIRM NAME"
        },
        "domain_overrides": {
            "springscap.com": "SPRINGS CAPITAL"
        },
        "folder_reassignments": {
            "OLD FIRM NAME": "NEW FIRM NAME"
        }
    }
    """
    mappings_path = output_dir / FIRM_MAPPINGS_FILE

    if mappings_path.exists():
        with open(mappings_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data

    # Initialize with empty structure
    return {
        "canonical_names": {},
        "email_overrides": {},
        "domain_overrides": {},
        "folder_reassignments": {},
        "_metadata": {
            "created": datetime.now().isoformat(),
            "description": "Human-editable firm name mappings. Use email_overrides for specific addresses, domain_overrides for entire domains, and folder_reassignments to move all emails from one firm folder to another."
        }
    }


def save_firm_mappings(mappings: dict, output_dir: Path):
    """Save firm name mappings to file."""
    mappings_path = output_dir / FIRM_MAPPINGS_FILE
    mappings["_metadata"]["last_updated"] = datetime.now().isoformat()

    with open(mappings_path, 'w', encoding='utf-8') as f:
        json.dump(mappings, f, indent=2, ensure_ascii=False)


def sanitize_folder_name(name: str) -> str:
    """
    Sanitize a firm name for use as a folder name.

    - Removes characters invalid on Windows: < > : " / \\ | ? *
    - Normalizes unicode characters
    - Removes leading/trailing spaces and dots
    - Collapses multiple spaces
    - Limits length to 100 characters
    - Handles reserved Windows names
    """
    if not name:
        return "UNKNOWN"

    # Normalize unicode (e.g., convert accented chars to ASCII equivalents where possible)
    import unicodedata
    name = unicodedata.normalize('NFKD', name)

    # Remove invalid Windows folder characters
    name = re.sub(r'[<>:"/\\|?*]', '', name)

    # Remove control characters
    name = re.sub(r'[\x00-\x1f\x7f]', '', name)

    # Collapse multiple spaces and normalize whitespace
    name = re.sub(r'\s+', ' ', name).strip()

    # Remove leading/trailing dots and spaces (Windows restriction)
    name = name.strip('. ')

    # Handle Windows reserved names
    reserved_names = {'CON', 'PRN', 'AUX', 'NUL', 'COM1', 'COM2', 'COM3', 'COM4',
                      'COM5', 'COM6', 'COM7', 'COM8', 'COM9', 'LPT1', 'LPT2',
                      'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'}
    if name.upper() in reserved_names:
        name = f"_{name}_"

    # Limit length (Windows MAX_PATH consideration)
    if len(name) > 100:
        name = name[:100].strip()

    return name if name else "UNKNOWN"


def check_email_override(email_address: str, mappings: dict) -> str | None:
    """
    Check if an email address or its domain has an override.

    Priority:
    1. Exact email address match (email_overrides)
    2. Domain match (domain_overrides)

    Returns the firm name if override found, None otherwise.
    """
    if not email_address:
        return None

    email_lower = email_address.lower().strip()

    # Check exact email address first
    email_overrides = mappings.get("email_overrides", {})
    for override_email, firm in email_overrides.items():
        if email_lower == override_email.lower():
            return firm

    # Check domain
    if '@' in email_lower:
        domain = email_lower.split('@')[1]
        domain_overrides = mappings.get("domain_overrides", {})
        for override_domain, firm in domain_overrides.items():
            if domain == override_domain.lower():
                return firm

    return None


def apply_folder_reassignment(firm_name: str, mappings: dict) -> str:
    """
    Check if a firm name should be reassigned to a different folder.

    Returns the reassigned firm name, or original if no reassignment.
    """
    if not firm_name:
        return firm_name

    reassignments = mappings.get("folder_reassignments", {})

    # Check case-insensitive
    for old_name, new_name in reassignments.items():
        if firm_name.upper() == old_name.upper():
            return new_name

    return firm_name


def normalize_firm_name(name: str, mappings: dict) -> str:
    """
    Normalize a firm name to its canonical form.

    1. Check if name matches any canonical name
    2. Check if name matches any alias
    3. If no match, return cleaned version of original
    """
    if not name:
        return "UNKNOWN"

    name_lower = name.lower().strip()
    name_upper = name.upper().strip()

    # Check canonical names (case-insensitive)
    for canonical, info in mappings.get("canonical_names", {}).items():
        if name_lower == canonical.lower():
            return canonical

        # Check aliases
        aliases = info.get("aliases", [])
        for alias in aliases:
            if name_lower == alias.lower():
                return canonical

    # Clean and return new canonical name
    # Remove common suffixes and clean up
    cleaned = re.sub(r'\s*(Ltd\.?|Limited|LLC|Inc\.?|LP|LLP|Co\.?|Corporation|Corp\.?)\s*$', '', name, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    return cleaned.upper() if cleaned else name_upper


def add_firm_to_mappings(firm_name: str, aliases: list, mappings: dict) -> str:
    """
    Add a new firm to the mappings or update existing aliases.
    Returns the canonical name used.
    """
    canonical = normalize_firm_name(firm_name, mappings)

    if canonical not in mappings["canonical_names"]:
        mappings["canonical_names"][canonical] = {
            "aliases": [],
            "description": "",
            "funds": {},
            "auto_added": datetime.now().isoformat()
        }

    # Add new aliases
    existing_aliases = set(a.lower() for a in mappings["canonical_names"][canonical]["aliases"])
    for alias in aliases:
        if alias.lower() not in existing_aliases:
            mappings["canonical_names"][canonical]["aliases"].append(alias)

    return canonical


def extract_domain_hints(email_address: str) -> list:
    """Extract potential firm name hints from email domain."""
    if not email_address or '@' not in email_address:
        return []

    domain = email_address.split('@')[1].lower()

    # Remove common TLDs and extract meaningful parts
    domain_parts = domain.replace('.com', '').replace('.hk', '').replace('.sg', '').replace('.jp', '')
    domain_parts = domain_parts.replace('.co', '').replace('.net', '').replace('.org', '')

    hints = []

    # Split by common separators
    parts = re.split(r'[-_.]', domain_parts)
    for part in parts:
        if len(part) > 2 and part not in ['mail', 'email', 'info', 'contact', 'admin', 'www']:
            hints.append(part)

    return hints


def generate_firm_id(firm_name: str) -> str:
    """
    Generate a stable snake_case firm ID from canonical name.
    Example: "WMC CAPITAL" -> "firm_wmc_capital"
    """
    if not firm_name:
        return "firm_unknown"
    cleaned = re.sub(r'[^a-zA-Z0-9\s]', '', firm_name)
    parts = cleaned.lower().split()
    return "firm_" + "_".join(p for p in parts if p)


def generate_fund_id(fund_name: str) -> str:
    """
    Generate a stable snake_case fund ID from display name.
    Example: "Albemarle Shipping Fund" -> "fund_albemarle_shipping"
    """
    if not fund_name:
        return "fund_unknown"
    # Remove common suffixes before generating ID
    cleaned = re.sub(
        r'\s*(Fund|Strategy|Master|Feeder|Portfolio|Class\s*\w+|SP|Ltd\.?|Limited)\s*$',
        '', fund_name, flags=re.IGNORECASE
    )
    cleaned = re.sub(r'[^a-zA-Z0-9\s]', '', cleaned)
    parts = cleaned.lower().split()
    return "fund_" + "_".join(p for p in parts if p)


def add_fund_to_firm(
    firm_name: str,
    fund_display_name: str,
    aliases: list,
    mappings: dict
) -> str:
    """
    Register a fund under a firm in the mappings.
    Returns the generated fund_id.
    """
    canonical = normalize_firm_name(firm_name, mappings)

    if canonical not in mappings.get("canonical_names", {}):
        return ""

    firm_entry = mappings["canonical_names"][canonical]
    if "funds" not in firm_entry:
        firm_entry["funds"] = {}

    fund_id = generate_fund_id(fund_display_name)

    # Check if fund already exists (by ID or by alias match)
    if fund_id in firm_entry["funds"]:
        # Add new aliases if provided
        existing_aliases = set(a.lower() for a in firm_entry["funds"][fund_id].get("aliases", []))
        for alias in aliases:
            if alias.lower() not in existing_aliases and alias.lower() != firm_entry["funds"][fund_id]["display_name"].lower():
                firm_entry["funds"][fund_id]["aliases"].append(alias)
        return fund_id

    # Check if it matches an existing fund by alias
    for existing_id, fund_info in firm_entry["funds"].items():
        name_lower = fund_display_name.lower()
        if name_lower == fund_info["display_name"].lower():
            return existing_id
        for alias in fund_info.get("aliases", []):
            if name_lower == alias.lower():
                return existing_id

    # Create new fund entry
    firm_entry["funds"][fund_id] = {
        "display_name": fund_display_name,
        "aliases": [a for a in aliases if a.lower() != fund_display_name.lower()],
        "auto_added": datetime.now().isoformat()
    }

    return fund_id


def normalize_fund_name(name: str, firm_entry: dict):
    """
    Match a fund name against known funds in a firm entry.
    Returns (fund_id, display_name) if matched, None otherwise.
    """
    if not name or not firm_entry:
        return None

    name_lower = name.lower().strip()
    funds = firm_entry.get("funds", {})

    for fund_id, fund_info in funds.items():
        if name_lower == fund_info["display_name"].lower():
            return (fund_id, fund_info["display_name"])
        for alias in fund_info.get("aliases", []):
            if name_lower == alias.lower():
                return (fund_id, fund_info["display_name"])

    return None

def classify_email_with_gpt(
    client: OpenAI,
    email_metadata: dict,
    existing_firms: list,
    firm_mappings: dict = None
) -> dict:
    """
    Use GPT to classify an email and extract firm/fund information.

    Returns new-format dict with:
    - email_classification: {is_hedge_fund_related, confidence, email_type, from_third_party, source_priority, reasoning}
    - firm_name, firm_name_source
    - attachments: [{attachment_id, filename, mime_type, assigned_firm_id, assigned_fund_id, assignment, contains_monthly_net_performance_update}]
    - fund_related_links: [{url, description, link_type, assigned_firm_id, assigned_fund_id}]
    """
    # Build attachment listing for the prompt
    attachments_in_email = email_metadata.get("attachments", [])
    attachment_listing = json.dumps([
        {
            "id": a.get("id", ""),
            "filename": a.get("name", ""),
            "contentType": a.get("contentType", ""),
            "size": a.get("size", 0)
        }
        for a in attachments_in_email
    ], indent=2) if attachments_in_email else "[]"

    # Build the prompt
    system_prompt = """You are an analyst-classification engine specialized in hedge funds and asset management communications.

Your job is to:
1. Determine whether an email is related to a hedge fund or asset management firm.
2. Identify the CANONICAL ASSET MANAGEMENT FIRM NAME (the management company), NOT the fund name.
3. Identify specific FUND NAMES managed by the firm.
4. Classify each attachment and link with firm/fund assignment.
5. Classify the email type.

────────────────────────────────────────
DEFINITIONS
────────────────────────────────────────
Hedge fund / asset management related emails include:
- Monthly, quarterly, or annual performance updates
- Investor letters, newsletters, and reports
- Factsheets and tear sheets
- NAV estimates or statements
- Fund marketing and fundraising materials
- Subscription / redemption documents
- Due diligence materials
- Cap intro materials

Not hedge fund related:
- Personal emails
- IT / system notifications
- General broker marketing not tied to specific funds
- Generic news with no fund or manager reference

────────────────────────────────────────
ASSET MANAGEMENT FIRM IDENTIFICATION (CRITICAL)
────────────────────────────────────────
You must extract the ASSET MANAGEMENT FIRM (investment manager), not the fund.

FIRM ≠ FUND

Fund names often include:
- Strategy descriptors (e.g., Global Macro, Long/Short)
- Structures (Master, Feeder, SP, Class A)
- Geography or currency

Firm names are the management company and typically end with:
Capital, Asset Management, Investment Management, Advisors, Partners, Holdings

────────────────────────────────────────
FUND IDENTIFICATION
────────────────────────────────────────
In addition to identifying the FIRM, you must also identify specific FUND NAMES.

A single firm may manage multiple funds. Examples:
- WMC Capital manages "Albemarle Shipping Fund" and "WMC Global Macro Fund"
- Springs Capital manages "Springs China Alpha Fund"

For each fund mentioned in the email body, subject, or attachments:
- Extract the fund's display name
- List any alternative names or abbreviations as aliases

If no specific fund can be identified, return an empty array for funds_identified.

────────────────────────────────────────
PRIORITY HIERARCHY (MANDATORY)
────────────────────────────────────────
You MUST follow this hierarchy when identifying the firm:

HIGHEST:
- Explicitly stated asset management firm in the email body or attachment
  (e.g., signature, "Managed by", letterhead)

MEDIUM:
- Fund name identified → you must verify (using the web search tool calling) which firm manages that fund

MEDIUM-LOW:
- No verification possible → infer from domain or fund naming

LOWEST:
- Not hedge fund related or insufficient information

You MUST be able to explain:
- Which priority level was used
- What source was relied on
- What verification or reasoning was performed

────────────────────────────────────────
THIRD-PARTY INTERMEDIARIES
────────────────────────────────────────
Third-party intermediaries include:
- Cap intro firms
- Fund administrators
- Prime brokers
- Data providers

The email domain name may differ from the ASSET MANAGEMENT FIRM without implying a third-party intermediary (e.g., forwarded emails or simply when the AM firm name manages a materially different fund name).
To be more certain, the following are some examples of third-party firms (exhaustive):
- Cap Intro / Capital Introduction: - IConnections, With Intelligence, Agecroft Partners, Park Hill Group, Eaton Partners, HFM (Hedge Fund Manager)
- Fund Administrators: - CITCO, Apex Group, ApexConnect, SS&C Technologies, NAV Consulting, Trident Trust, Custom House, Alter Domus
- Prime Brokers (when sending cap intro or research): - Goldman Sachs (GS), Morgan Stanley (MS), Bank of America (BofA/BAML), JPMorgan, UBS, Credit Suisse, Barclays
- Other Intermediaries: - Marex, Preqin, eVestment, Bloomberg, Refinitiv, PivotalPath, HFR (Hedge Fund Research)

If an email is from a third-party intermediary and contains hedge fund content:
- Return the THIRD-PARTY firm name in "firm_name"
- Set from_third_party to the NAME of the third-party firm (e.g., "Morgan Stanley Client Services")
- If NOT from a third party, set from_third_party to false

────────────────────────────────────────
NAMING RULES
────────────────────────────────────────
- Use canonical, industry-recognized firm names
- Clean the "," "LLC", "Ltd.", "Inc.", "LP", "LLP", "Corporation", "Corp." suffixes when identifying the firm name, but recognize them as indicators of a firm name
- Expand acronyms when possible (e.g., E20 → E20 Capital)
- Do NOT guess firm names without justification
- If no firm can be identified, return an empty string

────────────────────────────────────────
ATTACHMENT CLASSIFICATION
────────────────────────────────────────
For each attachment in the email metadata, classify:
- Which firm it belongs to (use the firm name you identified)
- Which fund it belongs to (use the fund name if identifiable)
- Whether it contains monthly net performance data (factsheet with returns)
- Your confidence and evidence for the assignment

Base your classification on:
- The attachment filename (e.g., "Fund_Dec_2025_Factsheet.pdf")
- The email context (subject, body text mentioning attached documents)
- The email sender / firm already identified

────────────────────────────────────────
LINK DETECTION
────────────────────────────────────────
Identify ALL fund-related links (URLs, hyperlinks) in the email that could potentially contain fund-related information.
These include links to:
- Performance reports or factsheets (PDF downloads)
- Investor portals or data rooms
- Fund documentation or presentations
- NAV statements or account statements
- Due diligence materials
- Webinars or recordings that are related to the fund or firm

For EACH fund-related link found, extract:
- The actual URL
- A brief description of what the link appears to contain
- The link type category
- Which firm and fund it relates to

Ignore links that are:
- Non-fund related
- Simply linked to the asset management firm's homepage
- Identification or 3rd party tracking website links (e.g., email tracking pixels)
- Unsubscribe links
- Social media profile links

You must follow these rules exactly."""

    user_prompt = f"""Analyze the following email metadata and return the classification.

EMAIL METADATA:
{json.dumps(email_metadata, indent=2, default=str)}

ATTACHMENTS IN THIS EMAIL:
{attachment_listing}

EXISTING FIRMS (for de-duplication only):
{json.dumps(existing_firms[:20] if existing_firms else [], indent=2)}

OUTPUT REQUIREMENTS:
Return a STRICT JSON object with EXACTLY the following structure:

{{
  "email_classification": {{
    "is_hedge_fund_related": true or false,
    "confidence": number between 0 and 1,
    "email_type": "Monthly performance update" |
                  "Quarterly performance update" |
                  "Annual report" |
                  "Investor letter / newsletter" |
                  "Factsheet / tear sheet" |
                  "Marketing / fundraising" |
                  "Webinar / event invitation" |
                  "Subscription / redemption" |
                  "Due diligence" |
                  "Cap intro" |
                  "Other",
    "from_third_party": "Name of third-party firm" or false,
    "source_priority": "highest" | "medium" | "medium_low" | "lowest",
    "reasoning": "brief explanation following the priority hierarchy"
  }},
  "firm_name": "string",
  "firm_name_source": "email_content" | "attachment" | "web search" | "email_address" | "subject" | "unknown",
  "funds_identified": [
    {{
      "fund_name": "Full Fund Display Name",
      "fund_aliases": ["alias1", "alias2"]
    }}
  ],
  "attachments": [
    {{
      "attachment_id": "the attachment id from metadata",
      "filename": "the filename",
      "assigned_firm_name": "the firm name",
      "assigned_fund_name": "the fund name or empty string",
      "confidence": number between 0 and 1,
      "method": "attachment_content" | "email_context" | "filename_match",
      "evidence": "brief explanation",
      "contains_monthly_net_performance_update": true or false
    }}
  ],
  "fund_related_links": [
    {{
      "url": "the actual URL",
      "description": "brief description of link content",
      "link_type": "performance_report" | "factsheet" | "investor_portal" | "presentation" | "nav_statement" | "due_diligence" | "webinar" | "other",
      "assigned_firm_name": "the firm name",
      "assigned_fund_name": "the fund name or empty string"
    }}
  ]
}}

Return JSON only. No commentary.
"""

    try:
        response = client.responses.create(
            model="gpt-5.2",
            tools=[{"type": "web_search"}],
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )

        raw_result = json.loads(response.output[0].content[0].text)

        # Post-process: convert human-readable names to IDs
        firm_name = raw_result.get("firm_name", "")
        firm_id = generate_firm_id(firm_name) if firm_name else ""

        # Register funds if firm_mappings provided
        if firm_mappings and firm_name:
            canonical = normalize_firm_name(firm_name, firm_mappings)
            if canonical in firm_mappings.get("canonical_names", {}):
                for fund_info in raw_result.get("funds_identified", []):
                    fund_display = fund_info.get("fund_name", "")
                    fund_aliases = fund_info.get("fund_aliases", [])
                    if fund_display:
                        add_fund_to_firm(canonical, fund_display, fund_aliases, firm_mappings)

        # Build attachment metadata with IDs
        processed_attachments = []
        for att in raw_result.get("attachments", []):
            att_firm_name = att.get("assigned_firm_name", firm_name)
            att_fund_name = att.get("assigned_fund_name", "")

            # Look up mime_type from original email metadata
            mime_type = ""
            for orig_att in attachments_in_email:
                if orig_att.get("id") == att.get("attachment_id") or orig_att.get("name") == att.get("filename"):
                    mime_type = orig_att.get("contentType", "")
                    break

            processed_attachments.append({
                "attachment_id": att.get("attachment_id", ""),
                "filename": att.get("filename", ""),
                "mime_type": mime_type,
                "assigned_firm_id": generate_firm_id(att_firm_name) if att_firm_name else firm_id,
                "assigned_fund_id": generate_fund_id(att_fund_name) if att_fund_name else "",
                "assignment": {
                    "confidence": att.get("confidence", 0.0),
                    "method": att.get("method", "email_context"),
                    "evidence": att.get("evidence", "")
                },
                "contains_monthly_net_performance_update": att.get("contains_monthly_net_performance_update", False)
            })

        # Build links with IDs
        processed_links = []
        for link in raw_result.get("fund_related_links", []):
            link_firm_name = link.get("assigned_firm_name", firm_name)
            link_fund_name = link.get("assigned_fund_name", "")
            processed_links.append({
                "url": link.get("url", ""),
                "description": link.get("description", ""),
                "link_type": link.get("link_type", "other"),
                "assigned_firm_id": generate_firm_id(link_firm_name) if link_firm_name else firm_id,
                "assigned_fund_id": generate_fund_id(link_fund_name) if link_fund_name else ""
            })

        # Build final result in new format
        result = {
            "email_classification": raw_result.get("email_classification", {
                "is_hedge_fund_related": False,
                "confidence": 0.0,
                "email_type": "Other",
                "from_third_party": False,
                "source_priority": "lowest",
                "reasoning": ""
            }),
            "firm_name": firm_name,
            "firm_name_source": raw_result.get("firm_name_source", "unknown"),
            "attachments": processed_attachments,
            "fund_related_links": processed_links
        }

        return result

    except Exception as e:
        print(f"GPT classification error: {e}")
        return {
            "email_classification": {
                "is_hedge_fund_related": False,
                "confidence": 0.0,
                "email_type": "Other",
                "from_third_party": False,
                "source_priority": "lowest",
                "reasoning": f"Classification error: {str(e)}"
            },
            "firm_name": "",
            "firm_name_source": "error",
            "attachments": [],
            "fund_related_links": []
        }


def load_classification_cache(output_dir: Path) -> dict:
    """Load cached classifications to avoid re-processing."""
    cache_path = output_dir / CLASSIFICATION_CACHE_FILE

    if cache_path.exists():
        with open(cache_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    return {}


def save_classification_cache(cache: dict, output_dir: Path):
    """Save classification cache."""
    cache_path = output_dir / CLASSIFICATION_CACHE_FILE

    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def copy_email_to_firm_folder(
    email_folder: Path,
    firm_name: str,
    output_dir: Path,
) -> Path:
    """
    Copy an email folder to the firm's folder in the output directory.

    Folder structure: output_dir/[FIRM NAME]/[email_folder]/

    Returns the destination path.
    """
    # Sanitize firm name for folder using dedicated function
    safe_firm_name = sanitize_folder_name(firm_name)

    firm_folder = output_dir / safe_firm_name
    firm_folder.mkdir(parents=True, exist_ok=True)

    # Copy the entire email folder
    dest_folder = firm_folder / email_folder.name

    if dest_folder.exists():
        shutil.rmtree(dest_folder)

    shutil.copytree(email_folder, dest_folder)

    return dest_folder


def classify_and_organize_emails(
    email_input_dir: Path = None,
    output_dir: Path = None,
    force_reclassify: bool = False
) -> dict:
    """
    Main function to classify and organize all emails.

    Args:
        email_input_dir: Directory containing email folders
        output_dir: Directory for organized firm folders
        force_reclassify: If True, ignore cache and reclassify all

    Returns:
        Classification report
    """
    email_input_dir = email_input_dir or DEFAULT_EMAIL_INPUT_DIR
    output_dir = output_dir or DEFAULT_OUTPUT_DIR

    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load existing data
    firm_mappings = load_firm_mappings(output_dir)
    classification_cache = {} if force_reclassify else load_classification_cache(output_dir)

    # Initialize OpenAI client
    client = get_openai_client()

    # Get list of existing canonical firm names
    existing_firms = list(firm_mappings.get("canonical_names", {}).keys())

    # Results tracking
    report = {
        "run_timestamp": datetime.now().isoformat(),
        "total_emails": 0,
        "hedge_fund_related": 0,
        "non_hedge_fund": 0,
        "errors": 0,
        "firms_found": {},
        "classifications": []
    }

    # Find all email folders
    email_folders = [
        f for f in email_input_dir.iterdir()
        if f.is_dir() and (f / "metadata.json").exists()
    ]

    print(f"Found {len(email_folders)} email folders to process")
    print(f"Output directory: {output_dir}")
    print("-" * 60)

    for i, email_folder in enumerate(email_folders):
        report["total_emails"] += 1

        # Load email metadata
        metadata_path = email_folder / "metadata.json"
        try:
            with open(metadata_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
        except Exception as e:
            print(f"Error loading {email_folder.name}: {e}")
            report["errors"] += 1
            continue

        email_id = metadata.get("id", email_folder.name)
        subject = metadata.get("subject", "No Subject")
        from_address = metadata.get("from", {}).get("emailAddress", {}).get("address", "")

        # Check for email/domain override (priority over GPT classification)
        override_firm = check_email_override(from_address, firm_mappings)
        if override_firm:
            override_source = "email_override" if from_address.lower() in [e.lower() for e in firm_mappings.get("email_overrides", {}).keys()] else "domain_override"
            override_detail = 'email: ' + from_address if override_source == "email_override" else ('domain: ' + from_address.split('@')[1] if '@' in from_address else 'unknown')
            classification = {
                "email_classification": {
                    "is_hedge_fund_related": True,
                    "confidence": 1.0,
                    "email_type": "unknown",
                    "from_third_party": False,
                    "source_priority": "highest",
                    "reasoning": f"Assigned via override rule for {override_detail}"
                },
                "firm_name": override_firm,
                "firm_name_source": override_source,
                "attachments": [],
                "fund_related_links": []
            }
            print(f"[{i+1}/{len(email_folders)}] (override) {subject[:50]}...")
        # Check cache
        elif email_id in classification_cache:
            classification = classification_cache[email_id]
            print(f"[{i+1}/{len(email_folders)}] (cached) {subject[:50]}...")
        else:
            # Classify with GPT
            print(f"[{i+1}/{len(email_folders)}] Classifying: {subject[:50]}...")
            classification = classify_email_with_gpt(
                client,
                metadata,
                existing_firms,
                firm_mappings
            )

            # Apply attachment/link rule: force hedge fund related if attachments or links exist
            email_cls = classification.get("email_classification", {})
            if metadata.get("hasAttachments") or classification.get("fund_related_links"):
                email_cls["is_hedge_fund_related"] = True
                classification["email_classification"] = email_cls

            # Cache the result
            classification_cache[email_id] = classification

        # Process classification result
        email_cls = classification.get("email_classification", {})
        classification_entry = {
            "email_id": email_id,
            "email_folder": email_folder.name,
            "subject": subject,
            "from": metadata.get("from", {}).get("emailAddress", {}).get("address", ""),
            **classification
        }

        if email_cls.get("is_hedge_fund_related"):
            report["hedge_fund_related"] += 1

            # Get the firm name (single string)
            raw_firm_name = classification.get("firm_name", "")
            from_third_party = email_cls.get("from_third_party", False)

            if raw_firm_name:
                # Normalize the firm name
                canonical_name = normalize_firm_name(raw_firm_name, firm_mappings)

                # Apply folder reassignment if configured
                pre_reassign_name = canonical_name
                canonical_name = apply_folder_reassignment(canonical_name, firm_mappings)
                reassignment = None
                if canonical_name != pre_reassign_name:
                    reassignment = {pre_reassign_name: canonical_name}

                # Add to mappings if new (only add domain hints for non-third-party emails)
                if not from_third_party:
                    domain_hints = extract_domain_hints(
                        metadata.get("from", {}).get("emailAddress", {}).get("address", "")
                    )
                    aliases = [raw_firm_name] + domain_hints
                else:
                    aliases = [raw_firm_name]
                add_firm_to_mappings(canonical_name, aliases, firm_mappings)

                # Update existing firms list for future classifications
                if canonical_name not in existing_firms:
                    existing_firms.append(canonical_name)

                # Track firm in report
                if canonical_name not in report["firms_found"]:
                    report["firms_found"][canonical_name] = {
                        "email_count": 0,
                        "emails": [],
                        "from_third_party": from_third_party
                    }
                report["firms_found"][canonical_name]["email_count"] += 1
                report["firms_found"][canonical_name]["emails"].append({
                    "folder": email_folder.name,
                    "subject": subject
                })

                classification_entry["canonical_firm_name"] = canonical_name
                if reassignment:
                    classification_entry["reassignment"] = reassignment

                # Copy to firm folder
                dest = copy_email_to_firm_folder(email_folder, canonical_name, output_dir)
                classification_entry["destination"] = str(dest)
                folder_display = sanitize_folder_name(canonical_name)
                print(f"    -> Copied to: {folder_display}/")
            else:
                report["hedge_fund_related"] -= 1  # Correction: no firm name found
                report["non_hedge_fund"] += 1
                print(f"    -> Hedge fund related but no firm name identified")
        else:
            report["non_hedge_fund"] += 1
            print(f"    -> Not hedge fund related (confidence: {email_cls.get('confidence', 0):.2f})")

        report["classifications"].append(classification_entry)

    # Save updated data
    save_firm_mappings(firm_mappings, output_dir)
    save_classification_cache(classification_cache, output_dir)

    # Save report
    report_path = output_dir / CLASSIFICATION_REPORT_FILE
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # Print summary
    print("\n" + "=" * 60)
    print("CLASSIFICATION SUMMARY")
    print("=" * 60)
    print(f"Total emails processed: {report['total_emails']}")
    print(f"Hedge fund related: {report['hedge_fund_related']}")
    print(f"Non-hedge fund: {report['non_hedge_fund']}")
    print(f"Errors: {report['errors']}")
    print(f"\nFirms identified: {len(report['firms_found'])}")

    for firm, info in sorted(report["firms_found"].items()):
        print(f"  - {firm}: {info['email_count']} email(s)")

    print(f"\nReport saved to: {report_path}")
    print(f"Firm mappings saved to: {output_dir / FIRM_MAPPINGS_FILE}")

    return report


def get_processed_folders(output_dir: Path) -> set:
    """
    Get set of email folder names that have already been processed.
    Reads from classification report to determine what's been processed.
    """
    report_path = output_dir / CLASSIFICATION_REPORT_FILE
    processed = set()

    if report_path.exists():
        try:
            with open(report_path, 'r', encoding='utf-8') as f:
                report = json.load(f)
                for entry in report.get("classifications", []):
                    if "email_folder" in entry:
                        processed.add(entry["email_folder"])
        except Exception:
            pass

    return processed


def classify_new_emails(
    email_input_dir: Path = None,
    output_dir: Path = None
) -> dict:
    """
    Classify only new/unprocessed email folders.

    Returns:
        dict with keys: new_folders_found, classifications
    """
    email_input_dir = email_input_dir or DEFAULT_EMAIL_INPUT_DIR
    output_dir = output_dir or DEFAULT_OUTPUT_DIR

    # Get already processed folders
    processed_folders = get_processed_folders(output_dir)

    # Find all current email folders
    all_folders = [
        f for f in email_input_dir.iterdir()
        if f.is_dir() and (f / "metadata.json").exists()
    ]

    # Filter to only new folders
    new_folders = [f for f in all_folders if f.name not in processed_folders]

    if not new_folders:
        return {"new_folders_found": 0, "classifications": []}

    print(f"\nFound {len(new_folders)} new email folder(s) to classify")

    # Process only the new folders
    # We'll do this by temporarily filtering what classify_and_organize_emails processes
    output_dir.mkdir(parents=True, exist_ok=True)

    firm_mappings = load_firm_mappings(output_dir)
    classification_cache = load_classification_cache(output_dir)
    client = get_openai_client()
    existing_firms = list(firm_mappings.get("canonical_names", {}).keys())

    results = []

    for i, email_folder in enumerate(new_folders):
        # Load email metadata
        metadata_path = email_folder / "metadata.json"
        try:
            with open(metadata_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
        except Exception as e:
            print(f"Error loading {email_folder.name}: {e}")
            continue

        email_id = metadata.get("id", email_folder.name)
        subject = metadata.get("subject", "No Subject")
        from_address = metadata.get("from", {}).get("emailAddress", {}).get("address", "")

        # Check for email/domain override
        override_firm = check_email_override(from_address, firm_mappings)
        if override_firm:
            override_source = "email_override" if from_address.lower() in [e.lower() for e in firm_mappings.get("email_overrides", {}).keys()] else "domain_override"
            classification = {
                "email_classification": {
                    "is_hedge_fund_related": True,
                    "confidence": 1.0,
                    "email_type": "unknown",
                    "from_third_party": False,
                    "source_priority": "highest",
                    "reasoning": "Assigned via override rule"
                },
                "firm_name": override_firm,
                "firm_name_source": override_source,
                "attachments": [],
                "fund_related_links": []
            }
            print(f"[{i+1}/{len(new_folders)}] (override) {subject[:50]}...")
        else:
            # Classify with GPT
            print(f"[{i+1}/{len(new_folders)}] Classifying: {subject[:50]}...")
            classification = classify_email_with_gpt(client, metadata, existing_firms, firm_mappings)

            # Apply attachment/link rule: force hedge fund related if attachments or links exist
            email_cls = classification.get("email_classification", {})
            if metadata.get("hasAttachments") or classification.get("fund_related_links"):
                email_cls["is_hedge_fund_related"] = True
                classification["email_classification"] = email_cls

            classification_cache[email_id] = classification

        # Process result
        email_cls = classification.get("email_classification", {})
        raw_firm_name = classification.get("firm_name", "")
        from_third_party = email_cls.get("from_third_party", False)

        if email_cls.get("is_hedge_fund_related") and raw_firm_name:
            canonical_name = normalize_firm_name(raw_firm_name, firm_mappings)
            canonical_name = apply_folder_reassignment(canonical_name, firm_mappings)

            # Add to mappings (only add domain hints for non-third-party emails)
            if not from_third_party:
                domain_hints = extract_domain_hints(from_address)
                aliases = [raw_firm_name] + domain_hints
            else:
                aliases = [raw_firm_name]
            add_firm_to_mappings(canonical_name, aliases, firm_mappings)

            if canonical_name not in existing_firms:
                existing_firms.append(canonical_name)

            # Copy to firm folder
            dest = copy_email_to_firm_folder(email_folder, canonical_name, output_dir)
            folder_display = sanitize_folder_name(canonical_name)
            print(f"    -> Copied to: {folder_display}/")

            results.append({
                "email_folder": email_folder.name,
                "subject": subject,
                "firm": canonical_name,
                "destination": str(dest),
                "from_third_party": from_third_party
            })
        else:
            reason = "Not hedge fund related" if not email_cls.get("is_hedge_fund_related") else "No firm name identified"
            print(f"    -> Skipped: {reason}")
            results.append({
                "email_folder": email_folder.name,
                "subject": subject,
                "firm": None,
                "reason": reason
            })

    # Save updated data
    save_firm_mappings(firm_mappings, output_dir)
    save_classification_cache(classification_cache, output_dir)

    # Update the report with new classifications
    report_path = output_dir / CLASSIFICATION_REPORT_FILE
    if report_path.exists():
        with open(report_path, 'r', encoding='utf-8') as f:
            report = json.load(f)
    else:
        report = {"classifications": [], "firms_found": {}}

    for result in results:
        report["classifications"].append({
            "email_folder": result["email_folder"],
            "subject": result["subject"],
            "canonical_firm_name": result.get("firm"),
            "from_third_party": result.get("from_third_party", False),
            "processed_at": datetime.now().isoformat()
        })

    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    return {"new_folders_found": len(new_folders), "classifications": results}


def monitor_and_classify(
    email_input_dir: Path = None,
    output_dir: Path = None,
    poll_interval: int = 30,
    run_once: bool = False
):
    """
    Continuously monitor for new email folders and classify them automatically.

    Args:
        email_input_dir: Directory to monitor for new email folders
        output_dir: Directory for organized firm folders
        poll_interval: Seconds between checks (default: 30)
        run_once: If True, check once and exit. If False, run continuously.
    """
    email_input_dir = email_input_dir or DEFAULT_EMAIL_INPUT_DIR
    output_dir = output_dir or DEFAULT_OUTPUT_DIR

    print("=" * 60)
    print("EMAIL FOLDER MONITOR")
    print("=" * 60)
    print(f"Monitoring: {email_input_dir}")
    print(f"Output to:  {output_dir}")
    print(f"Poll interval: {poll_interval} seconds")
    if not run_once:
        print("Press Ctrl+C to stop monitoring")
    print("-" * 60)

    try:
        while True:
            # Check for new folders
            result = classify_new_emails(email_input_dir, output_dir)

            if result["new_folders_found"] > 0:
                print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                      f"Processed {result['new_folders_found']} new email(s)")

                # Summary of what was classified
                for item in result["classifications"]:
                    firm = item.get("firm")
                    if firm:
                        print(f"  + {item['subject'][:40]}... -> {firm}")
                    else:
                        print(f"  - {item['subject'][:40]}... ({item.get('reason', 'skipped')})")
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] No new emails found", end='\r')

            if run_once:
                print("\nSingle check completed.")
                break

            # Wait before next check
            time.sleep(poll_interval)

    except KeyboardInterrupt:
        print("\n\nMonitoring stopped by user.")

    return result if run_once else None


def add_email_override(
    email_address: str,
    firm_name: str,
    output_dir: Path = None
):
    """
    Add an override for a specific email address.
    All emails from this address will be assigned to the specified firm.

    Args:
        email_address: The full email address (e.g., "john.doe@example.com")
        firm_name: The firm name to assign
        output_dir: Output directory
    """
    output_dir = output_dir or DEFAULT_OUTPUT_DIR

    mappings = load_firm_mappings(output_dir)
    mappings["email_overrides"][email_address.lower()] = firm_name
    save_firm_mappings(mappings, output_dir)

    print(f"Added email override: {email_address} -> {firm_name}")
    print("Run classify_and_organize_emails() again to apply the change.")


def add_domain_override(
    domain: str,
    firm_name: str,
    output_dir: Path = None
):
    """
    Add an override for an entire email domain.
    All emails from this domain will be assigned to the specified firm.

    Args:
        domain: The email domain without @ (e.g., "springscap.com")
        firm_name: The firm name to assign
        output_dir: Output directory
    """
    output_dir = output_dir or DEFAULT_OUTPUT_DIR

    # Remove @ if provided
    domain = domain.lstrip('@').lower()

    mappings = load_firm_mappings(output_dir)
    mappings["domain_overrides"][domain] = firm_name
    save_firm_mappings(mappings, output_dir)

    print(f"Added domain override: @{domain} -> {firm_name}")
    print("All future emails from this domain will be assigned to {firm_name}.")
    print("Run classify_and_organize_emails() again to apply the change.")


def reassign_firm(
    old_firm_name: str,
    new_firm_name: str,
    output_dir: Path = None
):
    """
    Reassign/rename a firm to a new name, merging if the new name already exists.

    This combined function handles:
    1. If both old and new firms exist: merges them, keeping the new name as canonical
    2. If only old firm exists: renames it to the new name (creates new entry)
    3. In both cases: adds folder reassignment and the old name becomes an alias
    4. Automatically moves all emails from old folder to new folder
    5. Deletes the old folder after moving

    Args:
        old_firm_name: The original firm name to reassign/remove
        new_firm_name: The target firm name (will be created if doesn't exist)
        output_dir: Output directory
    """
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    mappings = load_firm_mappings(output_dir)
    canonical_names = mappings.get("canonical_names", {})

    # Find the old firm (case-insensitive)
    old_firm_key = None
    for key in canonical_names:
        if key.lower() == old_firm_name.lower():
            old_firm_key = key
            break

    # Find the new firm (case-insensitive)
    new_firm_key = None
    for key in canonical_names:
        if key.lower() == new_firm_name.lower():
            new_firm_key = key
            break

    # Collect all aliases from old firm (if exists)
    old_aliases = set()
    if old_firm_key:
        old_aliases = set(canonical_names[old_firm_key].get("aliases", []))
        old_aliases.add(old_firm_key)  # Add old canonical name as alias

    if new_firm_key:
        # Case 1: Both firms exist - merge them
        new_aliases = set(canonical_names[new_firm_key].get("aliases", []))
        combined_aliases = old_aliases.union(new_aliases)
        # Remove the new canonical name from aliases if present
        combined_aliases.discard(new_firm_key)

        canonical_names[new_firm_key]["aliases"] = list(combined_aliases)

        if old_firm_key:
            del canonical_names[old_firm_key]
            print(f"Merged '{old_firm_key}' into existing firm '{new_firm_key}'")
        else:
            print(f"Added '{old_firm_name}' as alias to existing firm '{new_firm_key}'")

        print(f"Combined aliases: {canonical_names[new_firm_key]['aliases']}")
    else:
        # Case 2: New firm doesn't exist - create it (rename scenario)
        new_canonical = new_firm_name.upper()

        # Remove new canonical from aliases if somehow present
        old_aliases.discard(new_canonical)

        canonical_names[new_canonical] = {
            "aliases": list(old_aliases),
            "description": "",
            "auto_added": datetime.now().isoformat()
        }

        if old_firm_key:
            # Copy description if it existed
            if canonical_names[old_firm_key].get("description"):
                canonical_names[new_canonical]["description"] = canonical_names[old_firm_key]["description"]
            del canonical_names[old_firm_key]
            print(f"Renamed '{old_firm_key}' to '{new_canonical}'")
        else:
            print(f"Created new firm '{new_canonical}' with alias '{old_firm_name}'")

        if old_aliases:
            print(f"Aliases: {list(old_aliases)}")

        new_firm_key = new_canonical

    # Add folder reassignment so future classifications redirect properly
    mappings["folder_reassignments"][old_firm_name.upper()] = new_firm_key or new_firm_name.upper()

    save_firm_mappings(mappings, output_dir)

    print(f"\nFolder reassignment added: {old_firm_name.upper()} -> {new_firm_key or new_firm_name.upper()}")

    # Auto-reorganize: Move emails from old folder to new folder and delete old folder
    old_folder_name = sanitize_folder_name(old_firm_name)
    new_folder_name = sanitize_folder_name(new_firm_key or new_firm_name.upper())

    emails_moved = 0
    folder_deleted = False

    search_paths = [
        output_dir / old_folder_name,
    ]

    new_folder_path = output_dir / new_folder_name

    for old_folder_path in search_paths:
        if old_folder_path.exists() and old_folder_path.is_dir():
            new_folder_path.mkdir(parents=True, exist_ok=True)

            for item in old_folder_path.iterdir():
                if item.is_dir():
                    dest_path = new_folder_path / item.name
                    if dest_path.exists():
                        shutil.rmtree(dest_path)
                    shutil.move(str(item), str(dest_path))
                    emails_moved += 1

            try:
                shutil.rmtree(old_folder_path)
                folder_deleted = True
                print(f"\nFolder reorganization complete:")
                print(f"  - Moved {emails_moved} email(s) to '{new_folder_name}/'")
                print(f"  - Deleted old folder: {old_folder_path.relative_to(output_dir)}/")
            except Exception as e:
                print(f"\nWarning: Could not delete old folder '{old_folder_path}': {e}")
            break  # Found and processed, stop searching

    if not folder_deleted:
        print(f"\nNo existing folder found for '{old_folder_name}' - no files to move.")


def list_overrides(output_dir: Path = None) -> dict:
    """List all configured overrides."""
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    mappings = load_firm_mappings(output_dir)

    print("\n" + "=" * 50)
    print("CONFIGURED OVERRIDES")
    print("=" * 50)

    email_overrides = mappings.get("email_overrides", {})
    if email_overrides:
        print("\nEmail Overrides (specific addresses):")
        for email, firm in sorted(email_overrides.items()):
            print(f"  {email} -> {firm}")
    else:
        print("\nNo email overrides configured.")

    domain_overrides = mappings.get("domain_overrides", {})
    if domain_overrides:
        print("\nDomain Overrides (all from domain):")
        for domain, firm in sorted(domain_overrides.items()):
            print(f"  @{domain} -> {firm}")
    else:
        print("\nNo domain overrides configured.")

    folder_reassignments = mappings.get("folder_reassignments", {})
    if folder_reassignments:
        print("\nFolder Reassignments:")
        for old_name, new_name in sorted(folder_reassignments.items()):
            print(f"  {old_name} -> {new_name}")
    else:
        print("\nNo folder reassignments configured.")

    return {
        "email_overrides": email_overrides,
        "domain_overrides": domain_overrides,
        "folder_reassignments": folder_reassignments
    }


def remove_override(
    override_type: str,
    key: str,
    output_dir: Path = None
):
    """
    Remove an override.

    Args:
        override_type: One of "email", "domain", or "folder"
        key: The email address, domain, or old firm name to remove
        output_dir: Output directory
    """
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    mappings = load_firm_mappings(output_dir)

    type_map = {
        "email": "email_overrides",
        "domain": "domain_overrides",
        "folder": "folder_reassignments"
    }

    if override_type not in type_map:
        print(f"Invalid override type: {override_type}")
        print("Use one of: email, domain, folder")
        return

    section = type_map[override_type]
    key_lower = key.lower() if override_type in ["email", "domain"] else key.upper()

    if key_lower in mappings.get(section, {}):
        del mappings[section][key_lower]
        save_firm_mappings(mappings, output_dir)
        print(f"Removed {override_type} override: {key}")
    else:
        print(f"Override not found: {key}")

def list_firms(output_dir: Path = None) -> dict:
    """List all known firms, their aliases, and their funds."""
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    mappings = load_firm_mappings(output_dir)

    print("\nKnown Firms:")
    print("-" * 40)

    for canonical, info in sorted(mappings.get("canonical_names", {}).items()):
        aliases = info.get("aliases", [])
        funds = info.get("funds", {})
        print(f"\n{canonical} (firm_id: {generate_firm_id(canonical)})")
        if aliases:
            print(f"  Aliases: {', '.join(aliases)}")
        if info.get("description"):
            print(f"  Description: {info['description']}")
        if funds:
            print(f"  Funds ({len(funds)}):")
            for fund_id, fund_info in funds.items():
                fund_aliases = fund_info.get("aliases", [])
                alias_str = f" (aliases: {', '.join(fund_aliases)})" if fund_aliases else ""
                print(f"    - {fund_info['display_name']} [{fund_id}]{alias_str}")

    return mappings.get("canonical_names", {})


def list_firm_aliases(firm_name: str, output_dir: Path = None) -> list:
    """
    List all aliases for a specific firm.

    Args:
        firm_name: The canonical firm name to look up
        output_dir: Output directory

    Returns:
        List of aliases for the firm
    """
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    mappings = load_firm_mappings(output_dir)
    canonical_names = mappings.get("canonical_names", {})

    # Find the firm (case-insensitive)
    firm_key = None
    for key in canonical_names:
        if key.lower() == firm_name.lower():
            firm_key = key
            break

    if not firm_key:
        print(f"Firm '{firm_name}' not found in mappings.")
        return []

    aliases = canonical_names[firm_key].get("aliases", [])

    print(f"\nAliases for '{firm_key}':")
    print("-" * 40)
    if aliases:
        for i, alias in enumerate(aliases, 1):
            print(f"  {i}. {alias}")
    else:
        print("  (No aliases defined)")

    # Also show fund aliases
    funds = canonical_names[firm_key].get("funds", {})
    if funds:
        print(f"\nFunds under '{firm_key}':")
        for fund_id, fund_info in funds.items():
            print(f"\n  {fund_info['display_name']} [{fund_id}]")
            fund_aliases = fund_info.get("aliases", [])
            if fund_aliases:
                for j, fa in enumerate(fund_aliases, 1):
                    print(f"    {j}. {fa}")
            else:
                print(f"    (No fund aliases)")

    return aliases


def delete_firm_alias(firm_name: str, alias_to_delete: str, output_dir: Path = None) -> bool:
    """
    Delete a specific alias from a firm.

    Args:
        firm_name: The canonical firm name
        alias_to_delete: The alias to remove
        output_dir: Output directory

    Returns:
        True if alias was deleted, False otherwise
    """
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    mappings = load_firm_mappings(output_dir)
    canonical_names = mappings.get("canonical_names", {})

    # Find the firm (case-insensitive)
    firm_key = None
    for key in canonical_names:
        if key.lower() == firm_name.lower():
            firm_key = key
            break

    if not firm_key:
        print(f"Firm '{firm_name}' not found in mappings.")
        return False

    aliases = canonical_names[firm_key].get("aliases", [])

    # Find and remove the alias (case-insensitive match)
    alias_found = None
    for alias in aliases:
        if alias.lower() == alias_to_delete.lower():
            alias_found = alias
            break

    if alias_found:
        aliases.remove(alias_found)
        canonical_names[firm_key]["aliases"] = aliases
        save_firm_mappings(mappings, output_dir)
        print(f"Deleted alias '{alias_found}' from firm '{firm_key}'.")
        return True
    else:
        print(f"Alias '{alias_to_delete}' not found for firm '{firm_key}'.")
        return False


def add_firm_alias(firm_name: str, new_alias: str, output_dir: Path = None) -> bool:
    """
    Add a new alias to a firm.

    Args:
        firm_name: The canonical firm name
        new_alias: The alias to add
        output_dir: Output directory

    Returns:
        True if alias was added, False otherwise
    """
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    mappings = load_firm_mappings(output_dir)
    canonical_names = mappings.get("canonical_names", {})

    # Find the firm (case-insensitive)
    firm_key = None
    for key in canonical_names:
        if key.lower() == firm_name.lower():
            firm_key = key
            break

    if not firm_key:
        print(f"Firm '{firm_name}' not found in mappings.")
        return False

    aliases = canonical_names[firm_key].get("aliases", [])

    # Check if alias already exists (case-insensitive)
    for alias in aliases:
        if alias.lower() == new_alias.lower():
            print(f"Alias '{new_alias}' already exists for firm '{firm_key}'.")
            return False

    # Add the new alias
    aliases.append(new_alias)
    canonical_names[firm_key]["aliases"] = aliases
    save_firm_mappings(mappings, output_dir)
    print(f"Added alias '{new_alias}' to firm '{firm_key}'.")
    return True


def manage_firm_aliases(output_dir: Path = None):
    """
    Interactive menu to manage firm aliases (list and delete).
    """
    output_dir = output_dir or DEFAULT_OUTPUT_DIR

    print("\n" + "=" * 50)
    print("MANAGE FIRM ALIASES")
    print("=" * 50)

    # First, list all firms
    mappings = load_firm_mappings(output_dir)
    canonical_names = mappings.get("canonical_names", {})

    if not canonical_names:
        print("No firms found in mappings.")
        return

    print("\nAvailable firms:")
    for i, firm in enumerate(sorted(canonical_names.keys()), 1):
        alias_count = len(canonical_names[firm].get("aliases", []))
        print(f"  {i}. {firm} ({alias_count} aliases)")

    firm_name = input("\nEnter firm name to manage aliases: ").strip()
    if not firm_name:
        print("No firm name provided.")
        return

    # List aliases for the firm
    aliases = list_firm_aliases(firm_name, output_dir)

    print("\nOptions:")
    print("  1. Add a new alias")
    print("  2. Delete a specific alias")
    print("  3. Exit")

    choice = input("\nEnter choice (1-3): ").strip()

    if choice == "1":
        new_alias = input("Enter new alias to add: ").strip()
        if new_alias:
            add_firm_alias(firm_name, new_alias, output_dir)
        else:
            print("No alias provided.")
    elif choice == "2":
        if not aliases:
            print("No aliases to delete.")
            return
        alias_to_delete = input("Enter alias to delete: ").strip()
        if alias_to_delete:
            delete_firm_alias(firm_name, alias_to_delete, output_dir)
        else:
            print("No alias provided.")
    elif choice == "3":
        print("Exiting alias management.")
    else:
        print("Invalid choice.")


def list_firm_funds(firm_name: str, output_dir: Path = None) -> dict:
    """List all funds under a specific firm."""
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    mappings = load_firm_mappings(output_dir)
    canonical_names = mappings.get("canonical_names", {})

    # Find the firm (case-insensitive)
    firm_key = None
    for key in canonical_names:
        if key.lower() == firm_name.lower():
            firm_key = key
            break

    if not firm_key:
        print(f"Firm '{firm_name}' not found in mappings.")
        return {}

    funds = canonical_names[firm_key].get("funds", {})

    print(f"\nFunds under '{firm_key}':")
    print("-" * 40)
    if funds:
        for fund_id, fund_info in funds.items():
            print(f"\n  {fund_info['display_name']}")
            print(f"    ID: {fund_id}")
            fund_aliases = fund_info.get("aliases", [])
            if fund_aliases:
                print(f"    Aliases: {', '.join(fund_aliases)}")
            else:
                print(f"    Aliases: (none)")
    else:
        print("  (No funds registered)")

    return funds


def add_fund_alias_to_firm(firm_name: str, fund_id: str, alias: str, output_dir: Path = None) -> bool:
    """Add an alias to a specific fund within a firm."""
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    mappings = load_firm_mappings(output_dir)
    canonical_names = mappings.get("canonical_names", {})

    firm_key = None
    for key in canonical_names:
        if key.lower() == firm_name.lower():
            firm_key = key
            break

    if not firm_key:
        print(f"Firm '{firm_name}' not found.")
        return False

    funds = canonical_names[firm_key].get("funds", {})
    if fund_id not in funds:
        print(f"Fund '{fund_id}' not found under firm '{firm_key}'.")
        return False

    fund_aliases = funds[fund_id].get("aliases", [])
    if alias.lower() in [a.lower() for a in fund_aliases]:
        print(f"Alias '{alias}' already exists for fund '{funds[fund_id]['display_name']}'.")
        return False

    fund_aliases.append(alias)
    funds[fund_id]["aliases"] = fund_aliases
    save_firm_mappings(mappings, output_dir)
    print(f"Added alias '{alias}' to fund '{funds[fund_id]['display_name']}' under '{firm_key}'.")
    return True


def delete_fund_alias_from_firm(firm_name: str, fund_id: str, alias: str, output_dir: Path = None) -> bool:
    """Delete an alias from a specific fund within a firm."""
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    mappings = load_firm_mappings(output_dir)
    canonical_names = mappings.get("canonical_names", {})

    firm_key = None
    for key in canonical_names:
        if key.lower() == firm_name.lower():
            firm_key = key
            break

    if not firm_key:
        print(f"Firm '{firm_name}' not found.")
        return False

    funds = canonical_names[firm_key].get("funds", {})
    if fund_id not in funds:
        print(f"Fund '{fund_id}' not found under firm '{firm_key}'.")
        return False

    fund_aliases = funds[fund_id].get("aliases", [])
    alias_found = None
    for a in fund_aliases:
        if a.lower() == alias.lower():
            alias_found = a
            break

    if alias_found:
        fund_aliases.remove(alias_found)
        funds[fund_id]["aliases"] = fund_aliases
        save_firm_mappings(mappings, output_dir)
        print(f"Deleted alias '{alias_found}' from fund '{funds[fund_id]['display_name']}'.")
        return True
    else:
        print(f"Alias '{alias}' not found for fund '{funds[fund_id]['display_name']}'.")
        return False


def manage_fund_aliases(output_dir: Path = None):
    """Interactive menu to manage fund aliases within a firm."""
    output_dir = output_dir or DEFAULT_OUTPUT_DIR

    print("\n" + "=" * 50)
    print("MANAGE FUND ALIASES")
    print("=" * 50)

    mappings = load_firm_mappings(output_dir)
    canonical_names = mappings.get("canonical_names", {})

    if not canonical_names:
        print("No firms found in mappings.")
        return

    # Show firms with fund counts
    print("\nAvailable firms:")
    for i, firm in enumerate(sorted(canonical_names.keys()), 1):
        fund_count = len(canonical_names[firm].get("funds", {}))
        print(f"  {i}. {firm} ({fund_count} funds)")

    firm_name = input("\nEnter firm name: ").strip()
    if not firm_name:
        print("No firm name provided.")
        return

    funds = list_firm_funds(firm_name, output_dir)
    if not funds:
        return

    fund_id = input("\nEnter fund ID to manage: ").strip()
    if not fund_id or fund_id not in funds:
        print("Invalid fund ID.")
        return

    print("\nOptions:")
    print("  1. Add a fund alias")
    print("  2. Delete a fund alias")
    print("  3. Exit")

    choice = input("\nEnter choice (1-3): ").strip()

    if choice == "1":
        new_alias = input("Enter new alias: ").strip()
        if new_alias:
            add_fund_alias_to_firm(firm_name, fund_id, new_alias, output_dir)
        else:
            print("No alias provided.")
    elif choice == "2":
        alias_to_delete = input("Enter alias to delete: ").strip()
        if alias_to_delete:
            delete_fund_alias_from_firm(firm_name, fund_id, alias_to_delete, output_dir)
        else:
            print("No alias provided.")
    elif choice == "3":
        print("Exiting fund alias management.")
    else:
        print("Invalid choice.")


# =========================
# AGENTIC LINK DOWNLOADER
# =========================

# Environment variables for authentication
INVESTOR_EMAIL = os.getenv("INVESTOR_EMAIL", "")
INVESTOR_NAME = os.getenv("INVESTOR_NAME", "")
INVESTOR_COMPANY = os.getenv("INVESTOR_COMPANY", "")
CITCO_EMAIL = os.getenv("CITCO_EMAIL", "")
CITCO_PASSWORD = os.getenv("CITCO_PASSWORD", "")
MARQUEE_USERNAME = os.getenv("MARQUEE_USERNAME", "")
MARQUEE_PASSWORD = os.getenv("MARQUEE_PASSWORD", "")

# Download directory
DEFAULT_DOWNLOAD_DIR = Path(r"C:\Users\FOF Analyst\Desktop\fofproject\output\testing\downloads")


def setup_selenium_driver(download_dir: Path, headless: bool = False):
    """
    Set up Selenium WebDriver with Chrome for interactive browsing.

    Args:
        download_dir: Directory to save downloaded files
        headless: Whether to run in headless mode (default: False for agentic interaction)

    Returns:
        Configured WebDriver instance
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from webdriver_manager.chrome import ChromeDriverManager

    # Ensure download directory exists
    download_dir.mkdir(parents=True, exist_ok=True)

    chrome_options = Options()

    # Configure download behavior
    prefs = {
        "download.default_directory": str(download_dir),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
        "plugins.always_open_pdf_externally": True,  # Download PDFs instead of opening
    }
    chrome_options.add_experimental_option("prefs", prefs)

    if headless:
        chrome_options.add_argument("--headless=new")

    # Standard options for stability
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.implicitly_wait(10)

    return driver


def capture_screenshot_base64(driver) -> str:
    """Capture current page screenshot and return as base64 string."""
    return driver.get_screenshot_as_base64()


def analyze_page_with_vision(
    client: OpenAI,
    screenshot_base64: str,
    page_url: str,
    page_source: str,
    goal: str,
    previous_actions: list = None
) -> dict:
    """
    Use GPT-4 Vision to analyze the current page and determine next action.

    Returns:
        dict with keys:
        - action: "click" | "fill_input" | "download_complete" | "navigate" | "wait" | "scroll" | "give_up"
        - target: CSS selector or description of element to interact with
        - value: Value to input (for fill_input action)
        - reasoning: Explanation of the decision
    """
    # Truncate page source to avoid token limits
    page_source_truncated = page_source[:15000] if len(page_source) > 15000 else page_source

    previous_actions_str = ""
    if previous_actions:
        previous_actions_str = "\n".join([f"- {a}" for a in previous_actions[-5:]])

    system_prompt = """You are a web navigation agent helping to download fund-related documents.
Your task is to analyze the current page and determine the best action to achieve the goal.

You have access to:
- A screenshot of the current page
- The page HTML source (truncated)
- The current URL
- Previous actions taken

Available actions:
1. "click" - Click on a button, link, or element. Provide CSS selector in "target".
2. "fill_input" - Fill in a form field. Provide CSS selector in "target" and text in "value".
3. "download_complete" - The file download has been triggered or completed.
4. "navigate" - Navigate to a different URL. Provide URL in "target".
5. "wait" - Wait for page to load or element to appear. Provide seconds in "value".
6. "scroll" - Scroll the page. Provide "down", "up", or pixel amount in "value".
7. "give_up" - Unable to proceed (page requires login we can't provide, CAPTCHA, etc.)

For CSS selectors, prefer:
- ID selectors: #submit-button
- Unique class combinations: .download-btn.primary
- Data attributes: [data-action="download"]
- Text-based: button:contains("Download") or a[href*=".pdf"]

Common patterns to recognize:
- Email input fields: input[type="email"], input[name*="email"]
- Download buttons: Contains "download", "get", "access" text
- PDF links: a[href$=".pdf"], a[href*="download"]
- Submit buttons: button[type="submit"], input[type="submit"]"""

    user_prompt = f"""Goal: {goal}

Current URL: {page_url}

Previous actions taken:
{previous_actions_str if previous_actions_str else "None yet"}

Page HTML (truncated):
```html
{page_source_truncated}
```

Analyze the screenshot and HTML to determine the next action. Consider:
1. Is there a direct download link visible?
2. Is there a form requiring email/name input?
3. Is there a login wall we cannot bypass?
4. Has a download already been triggered?

Respond with a JSON object:
{{
  "action": "click" | "fill_input" | "download_complete" | "navigate" | "wait" | "scroll" | "give_up",
  "target": "CSS selector or URL",
  "value": "input value or wait seconds",
  "reasoning": "brief explanation"
}}"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{screenshot_base64}",
                                "detail": "high"
                            }
                        }
                    ]
                }
            ],
            max_tokens=1000,
            temperature=0.1
        )

        result_text = response.choices[0].message.content
        # Extract JSON from response
        json_match = re.search(r'\{[\s\S]*\}', result_text)
        if json_match:
            return json.loads(json_match.group())
        else:
            return {
                "action": "give_up",
                "target": "",
                "value": "",
                "reasoning": f"Could not parse response: {result_text[:200]}"
            }

    except Exception as e:
        return {
            "action": "give_up",
            "target": "",
            "value": "",
            "reasoning": f"Vision analysis error: {str(e)}"
        }


def execute_action(driver, action: dict, investor_email: str = "", investor_name: str = "", investor_company: str = "") -> bool:
    """
    Execute the action determined by the vision model.

    Returns:
        True if action was successful, False otherwise
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException, NoSuchElementException
    from selenium.webdriver.common.action_chains import ActionChains

    action_type = action.get("action", "")
    target = action.get("target", "")
    value = action.get("value", "")

    try:
        if action_type == "click":
            # Try multiple selector strategies
            element = None
            selectors_to_try = [target]

            # Add fallback selectors based on common patterns
            if "download" in target.lower():
                selectors_to_try.extend([
                    "a[href*='download']",
                    "button:contains('Download')",
                    "[class*='download']",
                    "a[href$='.pdf']"
                ])

            for selector in selectors_to_try:
                try:
                    # Handle :contains pseudo-selector (not standard CSS)
                    if ":contains(" in selector:
                        text = re.search(r":contains\(['\"](.+?)['\"]\)", selector).group(1)
                        tag = selector.split(":")[0] or "*"
                        element = driver.find_element(By.XPATH, f"//{tag}[contains(text(), '{text}')]")
                    else:
                        element = WebDriverWait(driver, 5).until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                        )
                    if element:
                        break
                except (TimeoutException, NoSuchElementException):
                    continue

            if element:
                # Scroll element into view
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
                time.sleep(0.5)
                element.click()
                time.sleep(2)  # Wait for page reaction
                return True
            else:
                print(f"    Could not find element: {target}")
                return False

        elif action_type == "fill_input":
            element = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, target))
            )
            element.clear()

            # Determine what value to fill
            fill_value = value
            target_lower = target.lower()
            if not fill_value or fill_value == "email":
                if "email" in target_lower or "@" in value:
                    fill_value = investor_email
                elif "name" in target_lower:
                    fill_value = investor_name
                elif "company" in target_lower or "firm" in target_lower or "organization" in target_lower:
                    fill_value = investor_company

            element.send_keys(fill_value)
            time.sleep(0.5)
            return True

        elif action_type == "navigate":
            driver.get(target)
            time.sleep(3)
            return True

        elif action_type == "wait":
            wait_time = int(value) if value.isdigit() else 3
            time.sleep(wait_time)
            return True

        elif action_type == "scroll":
            if value == "down":
                driver.execute_script("window.scrollBy(0, 500);")
            elif value == "up":
                driver.execute_script("window.scrollBy(0, -500);")
            else:
                try:
                    pixels = int(value)
                    driver.execute_script(f"window.scrollBy(0, {pixels});")
                except ValueError:
                    driver.execute_script("window.scrollBy(0, 500);")
            time.sleep(1)
            return True

        elif action_type == "download_complete":
            return True

        elif action_type == "give_up":
            return False

    except Exception as e:
        print(f"    Action execution error: {e}")
        return False

    return False


def check_for_new_downloads(download_dir: Path, known_files: set, timeout: int = 30) -> list:
    """
    Check for new files in the download directory.

    Args:
        download_dir: Directory to check
        known_files: Set of filenames that existed before download attempt
        timeout: Maximum seconds to wait for download

    Returns:
        List of new file paths
    """
    start_time = time.time()
    new_files = []

    while time.time() - start_time < timeout:
        current_files = set(f.name for f in download_dir.iterdir() if f.is_file())
        new_file_names = current_files - known_files

        # Filter out incomplete downloads (.crdownload, .tmp, etc.)
        complete_new_files = [
            download_dir / f for f in new_file_names
            if not f.endswith(('.crdownload', '.tmp', '.part', '.partial'))
        ]

        if complete_new_files:
            return complete_new_files

        # Check if there's an in-progress download
        in_progress = [f for f in new_file_names if f.endswith(('.crdownload', '.tmp', '.part'))]
        if in_progress:
            time.sleep(2)  # Wait for download to complete
            continue

        time.sleep(1)

    return new_files


def download_link_agentic(
    driver,
    client: OpenAI,
    link_info: dict,
    download_dir: Path,
    firm_name: str,
    max_actions: int = 10
) -> dict:
    """
    Attempt to download a file from a fund-related link using agentic navigation.

    Args:
        driver: Selenium WebDriver instance
        client: OpenAI client for vision analysis
        link_info: Dict with url, description, link_type
        download_dir: Directory to save downloads
        firm_name: Firm name for organizing downloads
        max_actions: Maximum number of actions to take before giving up

    Returns:
        dict with status and file path if successful
    """
    url = link_info.get("url", "")
    description = link_info.get("description", "")
    link_type = link_info.get("link_type", "other")

    if not url:
        return {"status": "error", "message": "No URL provided"}

    # Create firm-specific download folder
    firm_download_dir = download_dir / sanitize_folder_name(firm_name)
    firm_download_dir.mkdir(parents=True, exist_ok=True)

    # Track existing files
    existing_files = set(f.name for f in firm_download_dir.iterdir() if f.is_file())

    # Load investor info from env
    investor_email = INVESTOR_EMAIL
    investor_name = INVESTOR_NAME
    investor_company = INVESTOR_COMPANY

    result = {
        "url": url,
        "description": description,
        "link_type": link_type,
        "status": "pending",
        "downloaded_files": [],
        "actions_taken": []
    }

    try:
        print(f"    Navigating to: {url[:80]}...")
        driver.get(url)
        time.sleep(3)

        # Check if this is a direct file download (PDF, Excel, etc.)
        current_url = driver.current_url
        if any(current_url.lower().endswith(ext) for ext in ['.pdf', '.xlsx', '.xls', '.doc', '.docx', '.ppt', '.pptx']):
            # Direct download - wait for file
            new_files = check_for_new_downloads(firm_download_dir, existing_files)
            if new_files:
                result["status"] = "success"
                result["downloaded_files"] = [str(f) for f in new_files]
                result["actions_taken"].append("Direct file download")
                return result

        # Agentic navigation loop
        previous_actions = []
        goal = f"Download the {description or link_type} document. If email is required, use: {investor_email}"

        for i in range(max_actions):
            # Capture current state
            screenshot = capture_screenshot_base64(driver)
            page_source = driver.page_source
            current_url = driver.current_url

            # Analyze with vision
            action = analyze_page_with_vision(
                client,
                screenshot,
                current_url,
                page_source,
                goal,
                previous_actions
            )

            action_desc = f"{action['action']}: {action.get('target', '')[:50]} - {action['reasoning'][:50]}"
            previous_actions.append(action_desc)
            result["actions_taken"].append(action_desc)
            print(f"      Action {i+1}: {action['action']} - {action['reasoning'][:60]}")

            # Check for completion or give up
            if action["action"] == "download_complete":
                new_files = check_for_new_downloads(firm_download_dir, existing_files)
                if new_files:
                    result["status"] = "success"
                    result["downloaded_files"] = [str(f) for f in new_files]
                else:
                    result["status"] = "uncertain"
                    result["message"] = "Download may have completed but no new files detected"
                return result

            elif action["action"] == "give_up":
                result["status"] = "failed"
                result["message"] = action["reasoning"]
                return result

            # Execute action
            success = execute_action(
                driver, action,
                investor_email, investor_name, investor_company
            )

            if not success:
                print(f"      Action failed, continuing...")

            # Check for new downloads after each action
            new_files = check_for_new_downloads(firm_download_dir, existing_files, timeout=5)
            if new_files:
                result["status"] = "success"
                result["downloaded_files"] = [str(f) for f in new_files]
                return result

            time.sleep(1)

        # Max actions reached
        result["status"] = "max_actions_reached"
        result["message"] = f"Reached maximum of {max_actions} actions without completing download"

    except Exception as e:
        result["status"] = "error"
        result["message"] = str(e)

    return result


def download_fund_links(
    output_dir: Path = None,
    download_dir: Path = None,
    firm_filter: str = None,
    link_type_filter: str = None,
    max_links: int = None,
    headless: bool = False,
    skip_downloaded: bool = True
) -> dict:
    """
    Main function to download files from fund-related links in the classification cache.

    Args:
        output_dir: Directory containing classification cache
        download_dir: Directory to save downloaded files
        firm_filter: Only process links from this firm (case-insensitive)
        link_type_filter: Only process links of this type
        max_links: Maximum number of links to process
        headless: Run browser in headless mode
        skip_downloaded: Skip links that have already been downloaded

    Returns:
        Report of download attempts
    """
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    download_dir = download_dir or DEFAULT_DOWNLOAD_DIR

    # Ensure directories exist
    output_dir.mkdir(parents=True, exist_ok=True)
    download_dir.mkdir(parents=True, exist_ok=True)

    # Load classification cache
    cache = load_classification_cache(output_dir)

    if not cache:
        print("No classification cache found. Run email classification first.")
        return {"status": "error", "message": "No cache"}

    # Load download history
    download_history_path = download_dir / "download_history.json"
    if download_history_path.exists():
        with open(download_history_path, 'r', encoding='utf-8') as f:
            download_history = json.load(f)
    else:
        download_history = {"downloaded_urls": [], "attempts": []}

    # Collect all links to process
    links_to_process = []

    for email_id, classification in cache.items():
        email_cls = classification.get("email_classification", {})
        if not email_cls.get("is_hedge_fund_related"):
            continue

        firm_name = classification.get("firm_name", "UNKNOWN")

        # Apply firm filter
        if firm_filter and firm_filter.lower() not in firm_name.lower():
            continue

        fund_links = classification.get("fund_related_links", [])

        for link in fund_links:
            url = link.get("url", "")
            link_type = link.get("link_type", "other")

            if not url:
                continue

            # Apply link type filter
            if link_type_filter and link_type != link_type_filter:
                continue

            # Skip already downloaded
            if skip_downloaded and url in download_history["downloaded_urls"]:
                continue

            links_to_process.append({
                "email_id": email_id,
                "firm_name": firm_name,
                "link": link,
                "subject": classification.get("subject", "")
            })

    # Apply max links limit
    if max_links:
        links_to_process = links_to_process[:max_links]

    print("\n" + "=" * 60)
    print("FUND LINK DOWNLOADER")
    print("=" * 60)
    print(f"Links to process: {len(links_to_process)}")
    print(f"Download directory: {download_dir}")
    print(f"Headless mode: {headless}")
    print("-" * 60)

    if not links_to_process:
        print("No links to process.")
        return {"status": "complete", "processed": 0}

    # Check for required env variables
    if not INVESTOR_EMAIL:
        print("\nWarning: INVESTOR_EMAIL not set in .env file.")
        print("Some portals may require email for access.")

    # Initialize OpenAI client and browser
    client = get_openai_client()
    driver = None

    report = {
        "run_timestamp": datetime.now().isoformat(),
        "total_links": len(links_to_process),
        "successful": 0,
        "failed": 0,
        "errors": 0,
        "results": []
    }

    try:
        driver = setup_selenium_driver(download_dir, headless)

        for i, item in enumerate(links_to_process):
            link_info = item["link"]
            firm_name = item["firm_name"]
            url = link_info.get("url", "")

            print(f"\n[{i+1}/{len(links_to_process)}] {firm_name}")
            print(f"    URL: {url[:70]}...")
            print(f"    Type: {link_info.get('link_type', 'unknown')}")

            result = download_link_agentic(
                driver,
                client,
                link_info,
                download_dir,
                firm_name
            )

            result["email_id"] = item["email_id"]
            result["firm_name"] = firm_name
            result["subject"] = item["subject"]
            report["results"].append(result)

            if result["status"] == "success":
                report["successful"] += 1
                download_history["downloaded_urls"].append(url)
                print(f"    ✓ Downloaded: {len(result.get('downloaded_files', []))} file(s)")
            elif result["status"] == "failed":
                report["failed"] += 1
                print(f"    ✗ Failed: {result.get('message', 'Unknown error')}")
            else:
                report["errors"] += 1
                print(f"    ? {result['status']}: {result.get('message', '')}")

            # Save attempt to history
            download_history["attempts"].append({
                "url": url,
                "firm": firm_name,
                "status": result["status"],
                "timestamp": datetime.now().isoformat()
            })

            # Brief pause between downloads
            time.sleep(2)

    except Exception as e:
        print(f"\nFatal error: {e}")
        report["fatal_error"] = str(e)

    finally:
        if driver:
            driver.quit()

    # Save download history
    with open(download_history_path, 'w', encoding='utf-8') as f:
        json.dump(download_history, f, indent=2)

    # Save report
    report_path = download_dir / f"download_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # Print summary
    print("\n" + "=" * 60)
    print("DOWNLOAD SUMMARY")
    print("=" * 60)
    print(f"Total links processed: {report['total_links']}")
    print(f"Successful downloads: {report['successful']}")
    print(f"Failed: {report['failed']}")
    print(f"Errors/Uncertain: {report['errors']}")
    print(f"\nReport saved to: {report_path}")
    print(f"Download history saved to: {download_history_path}")

    return report


def list_pending_links(output_dir: Path = None, download_dir: Path = None) -> list:
    """
    List all fund-related links that haven't been downloaded yet.
    """
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    download_dir = download_dir or DEFAULT_DOWNLOAD_DIR

    cache = load_classification_cache(output_dir)

    # Load download history
    download_history_path = download_dir / "download_history.json"
    downloaded_urls = set()
    if download_history_path.exists():
        with open(download_history_path, 'r', encoding='utf-8') as f:
            history = json.load(f)
            downloaded_urls = set(history.get("downloaded_urls", []))

    pending = []

    print("\n" + "=" * 60)
    print("PENDING FUND LINKS")
    print("=" * 60)

    for email_id, classification in cache.items():
        email_cls = classification.get("email_classification", {})
        if not email_cls.get("is_hedge_fund_related"):
            continue

        firm_name = classification.get("firm_name", "UNKNOWN")
        fund_links = classification.get("fund_related_links", [])

        for link in fund_links:
            url = link.get("url", "")
            if url and url not in downloaded_urls:
                pending.append({
                    "firm": firm_name,
                    "url": url,
                    "type": link.get("link_type", "unknown"),
                    "description": link.get("description", "")
                })

    if pending:
        # Group by firm
        by_firm = {}
        for item in pending:
            firm = item["firm"]
            if firm not in by_firm:
                by_firm[firm] = []
            by_firm[firm].append(item)

        for firm, links in sorted(by_firm.items()):
            print(f"\n{firm} ({len(links)} link(s)):")
            for link in links:
                print(f"  - [{link['type']}] {link['url'][:60]}...")
                if link['description']:
                    print(f"    {link['description'][:80]}")
    else:
        print("\nNo pending links found.")

    print(f"\nTotal pending links: {len(pending)}")
    return pending


def get_all_undownloaded_links(output_dir: Path = None, download_dir: Path = None) -> list:
    """
    Get all undownloaded links including:
    - Links that haven't been attempted
    - Links that failed during agentic download
    - Links with errors

    Returns list of dicts with link info and status.
    """
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    download_dir = download_dir or DEFAULT_DOWNLOAD_DIR

    cache = load_classification_cache(output_dir)

    # Load download history
    download_history_path = download_dir / "download_history.json"
    downloaded_urls = set()
    failed_urls = {}  # url -> failure info

    if download_history_path.exists():
        with open(download_history_path, 'r', encoding='utf-8') as f:
            history = json.load(f)
            downloaded_urls = set(history.get("downloaded_urls", []))

            # Track failed attempts
            for attempt in history.get("attempts", []):
                url = attempt.get("url", "")
                status = attempt.get("status", "")
                if status not in ["success"] and url not in downloaded_urls:
                    failed_urls[url] = {
                        "status": status,
                        "timestamp": attempt.get("timestamp", ""),
                        "firm": attempt.get("firm", "")
                    }

    undownloaded = []

    for email_id, classification in cache.items():
        email_cls = classification.get("email_classification", {})
        if not email_cls.get("is_hedge_fund_related"):
            continue

        firm_name = classification.get("firm_name", "UNKNOWN")
        fund_links = classification.get("fund_related_links", [])
        subject = classification.get("subject", "")

        for link in fund_links:
            url = link.get("url", "")
            if not url:
                continue

            if url in downloaded_urls:
                continue

            # Determine status
            if url in failed_urls:
                status = failed_urls[url]["status"]
                last_attempt = failed_urls[url]["timestamp"]
            else:
                status = "not_attempted"
                last_attempt = None

            undownloaded.append({
                "email_id": email_id,
                "firm": firm_name,
                "subject": subject,
                "url": url,
                "link_type": link.get("link_type", "unknown"),
                "description": link.get("description", ""),
                "status": status,
                "last_attempt": last_attempt
            })

    return undownloaded


def interactive_download_manager(
    output_dir: Path = None,
    download_dir: Path = None,
    firm_filter: str = None,
    status_filter: str = None
):
    """
    Interactive human-in-the-loop download manager.

    Opens each undownloaded link in a browser and allows manual download,
    then lets the user mark the link's status.

    Args:
        output_dir: Directory containing classification cache
        download_dir: Directory for downloads
        firm_filter: Only show links from this firm
        status_filter: Only show links with this status (not_attempted, failed, error, etc.)
    """
    import webbrowser

    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    download_dir = download_dir or DEFAULT_DOWNLOAD_DIR

    # Get all undownloaded links
    all_links = get_all_undownloaded_links(output_dir, download_dir)

    # Apply filters
    if firm_filter:
        all_links = [l for l in all_links if firm_filter.lower() in l["firm"].lower()]

    if status_filter:
        all_links = [l for l in all_links if l["status"] == status_filter]

    if not all_links:
        print("\nNo undownloaded links found matching the criteria.")
        return

    # Load download history for updating
    download_history_path = download_dir / "download_history.json"
    if download_history_path.exists():
        with open(download_history_path, 'r', encoding='utf-8') as f:
            download_history = json.load(f)
    else:
        download_history = {"downloaded_urls": [], "attempts": [], "manual_downloads": []}

    # Ensure manual_downloads key exists
    if "manual_downloads" not in download_history:
        download_history["manual_downloads"] = []

    print("\n" + "=" * 70)
    print("INTERACTIVE DOWNLOAD MANAGER (Human-in-the-Loop)")
    print("=" * 70)
    print(f"\nTotal undownloaded links: {len(all_links)}")
    print("\nControls:")
    print("  [d] Mark as DOWNLOADED - File was successfully downloaded manually")
    print("  [s] SKIP - Skip this link for now (will show again next time)")
    print("  [f] Mark as FAILED - Could not download (won't attempt again)")
    print("  [i] IGNORE - Permanently ignore this link (not relevant)")
    print("  [o] OPEN again - Re-open the link in browser")
    print("  [n] NEXT - Go to next link without marking")
    print("  [q] QUIT - Exit the interactive manager")
    print("  [l] LIST - Show remaining links")
    print("-" * 70)

    # Group by firm for better organization
    by_firm = {}
    for link in all_links:
        firm = link["firm"]
        if firm not in by_firm:
            by_firm[firm] = []
        by_firm[firm].append(link)

    # Flatten back to list but organized by firm
    organized_links = []
    for firm in sorted(by_firm.keys()):
        organized_links.extend(by_firm[firm])

    processed = 0
    downloaded = 0
    skipped = 0
    failed = 0
    ignored = 0

    i = 0
    while i < len(organized_links):
        link = organized_links[i]

        print(f"\n[{i+1}/{len(organized_links)}] {'=' * 50}")
        print(f"Firm:        {link['firm']}")
        print(f"Subject:     {link['subject'][:60]}..." if len(link['subject']) > 60 else f"Subject:     {link['subject']}")
        print(f"Type:        {link['link_type']}")
        print(f"Description: {link['description'][:70]}..." if len(link['description']) > 70 else f"Description: {link['description']}")
        print(f"Status:      {link['status']}")
        if link['last_attempt']:
            print(f"Last attempt: {link['last_attempt']}")
        print(f"\nURL: {link['url']}")

        # Open in browser
        print("\nOpening link in browser...")
        try:
            webbrowser.open(link['url'])
        except Exception as e:
            print(f"Could not open browser: {e}")
            print("Please copy the URL above and open it manually.")

        # Wait for user input
        while True:
            action = input("\nAction [d/s/f/i/o/n/q/l]: ").strip().lower()

            if action == 'd':
                # Mark as downloaded
                if link['url'] not in download_history["downloaded_urls"]:
                    download_history["downloaded_urls"].append(link['url'])
                download_history["manual_downloads"].append({
                    "url": link['url'],
                    "firm": link['firm'],
                    "timestamp": datetime.now().isoformat(),
                    "method": "manual"
                })
                # Save immediately
                with open(download_history_path, 'w', encoding='utf-8') as f:
                    json.dump(download_history, f, indent=2)
                print(f"✓ Marked as downloaded")
                downloaded += 1
                processed += 1
                i += 1
                break

            elif action == 's':
                # Skip - don't update anything
                print("→ Skipped (will show again next time)")
                skipped += 1
                i += 1
                break

            elif action == 'f':
                # Mark as permanently failed
                download_history["attempts"].append({
                    "url": link['url'],
                    "firm": link['firm'],
                    "status": "manual_failed",
                    "timestamp": datetime.now().isoformat(),
                    "method": "manual"
                })
                with open(download_history_path, 'w', encoding='utf-8') as f:
                    json.dump(download_history, f, indent=2)
                print("✗ Marked as failed")
                failed += 1
                processed += 1
                i += 1
                break

            elif action == 'i':
                # Mark as ignored (add to downloaded to prevent future processing)
                if link['url'] not in download_history["downloaded_urls"]:
                    download_history["downloaded_urls"].append(link['url'])
                download_history["manual_downloads"].append({
                    "url": link['url'],
                    "firm": link['firm'],
                    "timestamp": datetime.now().isoformat(),
                    "method": "ignored",
                    "reason": "Marked as not relevant by user"
                })
                with open(download_history_path, 'w', encoding='utf-8') as f:
                    json.dump(download_history, f, indent=2)
                print("⊘ Marked as ignored (won't appear again)")
                ignored += 1
                processed += 1
                i += 1
                break

            elif action == 'o':
                # Re-open link
                print("Re-opening link in browser...")
                try:
                    webbrowser.open(link['url'])
                except Exception as e:
                    print(f"Could not open browser: {e}")

            elif action == 'n':
                # Next without marking
                print("→ Moving to next link")
                i += 1
                break

            elif action == 'q':
                # Quit
                print("\nExiting interactive manager...")
                print(f"\nSession Summary:")
                print(f"  Downloaded: {downloaded}")
                print(f"  Skipped:    {skipped}")
                print(f"  Failed:     {failed}")
                print(f"  Ignored:    {ignored}")
                print(f"  Remaining:  {len(organized_links) - i}")
                return

            elif action == 'l':
                # List remaining links
                remaining = organized_links[i:]
                print(f"\n--- Remaining {len(remaining)} links ---")
                current_firm = None
                for j, rem_link in enumerate(remaining):
                    if rem_link['firm'] != current_firm:
                        current_firm = rem_link['firm']
                        print(f"\n{current_firm}:")
                    print(f"  {i+j+1}. [{rem_link['link_type']}] {rem_link['url'][:50]}...")

            else:
                print("Invalid action. Use: d=downloaded, s=skip, f=failed, i=ignore, o=open, n=next, q=quit, l=list")

    # End of list
    print("\n" + "=" * 70)
    print("INTERACTIVE DOWNLOAD MANAGER - COMPLETE")
    print("=" * 70)
    print(f"\nFinal Summary:")
    print(f"  Total processed: {processed}")
    print(f"  Downloaded:      {downloaded}")
    print(f"  Skipped:         {skipped}")
    print(f"  Failed:          {failed}")
    print(f"  Ignored:         {ignored}")
    print(f"\nDownload history saved to: {download_history_path}")


def batch_mark_downloaded(
    urls: list = None,
    firm_name: str = None,
    output_dir: Path = None,
    download_dir: Path = None
):
    """
    Batch mark multiple URLs as downloaded.

    Args:
        urls: List of URLs to mark as downloaded
        firm_name: If provided, mark all undownloaded links from this firm
        output_dir: Directory containing classification cache
        download_dir: Directory for downloads
    """
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    download_dir = download_dir or DEFAULT_DOWNLOAD_DIR

    # Load download history
    download_history_path = download_dir / "download_history.json"
    if download_history_path.exists():
        with open(download_history_path, 'r', encoding='utf-8') as f:
            download_history = json.load(f)
    else:
        download_history = {"downloaded_urls": [], "attempts": [], "manual_downloads": []}

    if "manual_downloads" not in download_history:
        download_history["manual_downloads"] = []

    urls_to_mark = urls or []

    # If firm_name provided, get all undownloaded links for that firm
    if firm_name:
        all_links = get_all_undownloaded_links(output_dir, download_dir)
        firm_links = [l for l in all_links if firm_name.lower() in l["firm"].lower()]
        urls_to_mark.extend([l["url"] for l in firm_links])

    if not urls_to_mark:
        print("No URLs to mark.")
        return

    marked_count = 0
    for url in urls_to_mark:
        if url not in download_history["downloaded_urls"]:
            download_history["downloaded_urls"].append(url)
            download_history["manual_downloads"].append({
                "url": url,
                "timestamp": datetime.now().isoformat(),
                "method": "batch_manual"
            })
            marked_count += 1
            print(f"✓ {url[:60]}...")

    with open(download_history_path, 'w', encoding='utf-8') as f:
        json.dump(download_history, f, indent=2)

    print(f"\nMarked {marked_count} URL(s) as downloaded.")


def show_download_status(output_dir: Path = None, download_dir: Path = None):
    """
    Show a summary of download status for all links.
    """
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    download_dir = download_dir or DEFAULT_DOWNLOAD_DIR

    cache = load_classification_cache(output_dir)

    # Load download history
    download_history_path = download_dir / "download_history.json"
    downloaded_urls = set()
    manual_downloads = []
    attempts = []

    if download_history_path.exists():
        with open(download_history_path, 'r', encoding='utf-8') as f:
            history = json.load(f)
            downloaded_urls = set(history.get("downloaded_urls", []))
            manual_downloads = history.get("manual_downloads", [])
            attempts = history.get("attempts", [])

    # Count by status
    total_links = 0
    by_firm = {}

    for email_id, classification in cache.items():
        email_cls = classification.get("email_classification", {})
        if not email_cls.get("is_hedge_fund_related"):
            continue

        firm_name = classification.get("firm_name", "UNKNOWN")
        fund_links = classification.get("fund_related_links", [])

        if firm_name not in by_firm:
            by_firm[firm_name] = {"total": 0, "downloaded": 0, "pending": 0, "failed": 0}

        for link in fund_links:
            url = link.get("url", "")
            if not url:
                continue

            total_links += 1
            by_firm[firm_name]["total"] += 1

            if url in downloaded_urls:
                by_firm[firm_name]["downloaded"] += 1
            else:
                # Check if failed
                failed_statuses = ["failed", "error", "manual_failed", "max_actions_reached"]
                is_failed = any(
                    a.get("url") == url and a.get("status") in failed_statuses
                    for a in attempts
                )
                if is_failed:
                    by_firm[firm_name]["failed"] += 1
                else:
                    by_firm[firm_name]["pending"] += 1

    # Calculate totals
    total_downloaded = sum(f["downloaded"] for f in by_firm.values())
    total_pending = sum(f["pending"] for f in by_firm.values())
    total_failed = sum(f["failed"] for f in by_firm.values())

    print("\n" + "=" * 70)
    print("DOWNLOAD STATUS SUMMARY")
    print("=" * 70)

    print(f"\nOverall:")
    print(f"  Total links:      {total_links}")
    print(f"  Downloaded:       {total_downloaded} ({100*total_downloaded/total_links:.1f}%)" if total_links > 0 else "  Downloaded:       0")
    print(f"  Pending:          {total_pending}")
    print(f"  Failed:           {total_failed}")

    # Manual vs automated
    manual_count = len([m for m in manual_downloads if m.get("method") != "ignored"])
    ignored_count = len([m for m in manual_downloads if m.get("method") == "ignored"])
    automated_count = total_downloaded - manual_count - ignored_count

    print(f"\nDownload Methods:")
    print(f"  Automated:        {automated_count}")
    print(f"  Manual:           {manual_count}")
    print(f"  Ignored:          {ignored_count}")

    print(f"\nBy Firm:")
    print("-" * 70)
    print(f"{'Firm':<35} {'Total':>8} {'Done':>8} {'Pending':>8} {'Failed':>8}")
    print("-" * 70)

    for firm in sorted(by_firm.keys()):
        stats = by_firm[firm]
        if stats["total"] > 0:
            print(f"{firm[:35]:<35} {stats['total']:>8} {stats['downloaded']:>8} {stats['pending']:>8} {stats['failed']:>8}")

    print("-" * 70)


def main():
    """Main entry point."""
    import sys

    print("=" * 60)
    print("HEDGE FUND EMAIL CLASSIFIER")
    print("=" * 60)
    print("\nSelect mode:")
    print("  1. Classify and organize all emails")
    print("  2. Force reclassify all (ignore cache)")
    print("  3. List known firms and funds")
    print("  4. List all overrides")
    print("  5. Add email override (specific address -> firm)")
    print("  6. Add domain override (all from domain -> firm)")
    print("  7. Reassign/rename firm (old firm -> new firm, merges if new exists)")
    print("  8. Monitor for new emails (continuous)")
    print("  9. Check for new emails (one-time)")
    print(" 10. Manage firm aliases (list/delete)")
    print(" 11. Manage fund aliases (list/add/delete)")
    print(" 12. Download fund-related links (agentic)")
    print(" 13. List pending fund links")
    print(" 14. Interactive download manager (human-in-the-loop)")
    print(" 15. Show download status summary")
    print(" 16. Batch mark links as downloaded")
    print()

    if len(sys.argv) > 1:
        mode = sys.argv[1]
    else:
        mode = input("Enter mode (1-16): ").strip()

    if mode == "1":
        classify_and_organize_emails()
    elif mode == "2":
        classify_and_organize_emails(force_reclassify=True)
    elif mode == "3":
        list_firms()
    elif mode == "4":
        list_overrides()
    elif mode == "5":
        email = input("Enter email address: ").strip()
        firm = input("Enter firm name to assign: ").strip()
        if email and firm:
            add_email_override(email, firm)
        else:
            print("Email and firm name are required.")
    elif mode == "6":
        domain = input("Enter domain (without @): ").strip()
        firm = input("Enter firm name to assign: ").strip()
        if domain and firm:
            add_domain_override(domain, firm)
        else:
            print("Domain and firm name are required.")
    elif mode == "7":
        old_firm = input("Enter old firm name to reassign/remove: ").strip()
        new_firm = input("Enter new firm name (will be created if doesn't exist): ").strip()
        if old_firm and new_firm:
            reassign_firm(old_firm, new_firm)
        else:
            print("Both firm names are required.")
    elif mode == "8":
        interval = input("Poll interval in seconds (default 30): ").strip()
        interval = int(interval) if interval.isdigit() else 30
        monitor_and_classify(poll_interval=interval, run_once=False)
    elif mode == "9":
        monitor_and_classify(run_once=True)
    elif mode == "10":
        manage_firm_aliases()
    elif mode == "11":
        manage_fund_aliases()
    elif mode == "12":
        print("\n--- FUND LINK DOWNLOADER ---")
        firm_filter = input("Filter by firm name (or press Enter for all): ").strip() or None
        link_type = input("Filter by link type (or press Enter for all): ").strip() or None
        max_links_input = input("Max links to process (or press Enter for all): ").strip()
        max_links = int(max_links_input) if max_links_input.isdigit() else None
        headless_input = input("Run in headless mode? (y/n, default n): ").strip().lower()
        headless = headless_input == 'y'

        download_fund_links(
            firm_filter=firm_filter,
            link_type_filter=link_type,
            max_links=max_links,
            headless=headless
        )
    elif mode == "13":
        list_pending_links()
    elif mode == "14":
        print("\n--- INTERACTIVE DOWNLOAD MANAGER ---")
        print("This will open each undownloaded link in your browser")
        print("and let you manually mark each as downloaded.\n")
        firm_filter = input("Filter by firm name (or press Enter for all): ").strip() or None
        print("\nStatus filters: not_attempted, failed, error, max_actions_reached")
        status_filter = input("Filter by status (or press Enter for all): ").strip() or None
        interactive_download_manager(firm_filter=firm_filter, status_filter=status_filter)
    elif mode == "15":
        show_download_status()
    elif mode == "16":
        print("\n--- BATCH MARK AS DOWNLOADED ---")
        print("Options:")
        print("  1. Mark all links from a specific firm")
        print("  2. Mark specific URLs")
        batch_choice = input("Enter choice (1-2): ").strip()

        if batch_choice == "1":
            firm_name = input("Enter firm name: ").strip()
            if firm_name:
                confirm = input(f"Mark ALL undownloaded links from '{firm_name}' as downloaded? (y/n): ").strip().lower()
                if confirm == 'y':
                    batch_mark_downloaded(firm_name=firm_name)
                else:
                    print("Cancelled.")
            else:
                print("Firm name required.")
        elif batch_choice == "2":
            print("Enter URLs to mark as downloaded (one per line, empty line to finish):")
            urls = []
            while True:
                url = input().strip()
                if not url:
                    break
                urls.append(url)
            if urls:
                batch_mark_downloaded(urls=urls)
            else:
                print("No URLs provided.")
        else:
            print("Invalid choice.")
    else:
        print("Invalid mode.")


if __name__ == "__main__":
    main()
