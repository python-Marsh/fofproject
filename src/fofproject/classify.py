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
from datetime import datetime
from typing import Optional  # noqa: F401 - kept for potential future use
from openai import OpenAI

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
                "description": "China-focused hedge fund"
            }
        },
        "email_overrides": {
            "john.doe@example.com": "FIRM NAME"  # Specific email address -> firm
        },
        "domain_overrides": {
            "springscap.com": "SPRINGS CAPITAL"  # All emails from domain -> firm
        },
        "folder_reassignments": {
            "OLD FIRM NAME": "NEW FIRM NAME"  # Reassign all from old folder to new
        }
    }
    """
    mappings_path = output_dir / FIRM_MAPPINGS_FILE

    if mappings_path.exists():
        with open(mappings_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Migrate old structure if needed
            if "manual_overrides" in data and "email_overrides" not in data:
                data["email_overrides"] = data.pop("manual_overrides")
            if "domain_overrides" not in data:
                data["domain_overrides"] = {}
            if "folder_reassignments" not in data:
                data["folder_reassignments"] = {}
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


def classify_email_with_gpt(
    client: OpenAI,
    email_metadata: dict,
    existing_firms: list
) -> dict:
    """
    Use GPT to classify an email and extract firm information.

    Returns:
    {
        "is_hedge_fund_related": bool,
        "confidence": float,
        "firm_names": list[str],  # Can be multiple firms for third-party emails
        "firm_name_source": str,  # "email_address", "email_content", "attachment", "subject"
        "reasoning": str,
        "email_type": str,  # "newsletter", "monthly_update", "factsheet", "admin", "marketing", "other"
        "is_third_party": bool  # True if from cap intro, fund admin, prime broker, etc.
    }
    """
    # Build the prompt
    system_prompt = """You are an analyst-classification engine specialized in hedge funds and asset management communications.

Your job is to:
1. Determine whether an email is related to a hedge fund or asset management firm.
2. Identify the CANONICAL ASSET MANAGEMENT FIRM NAME (the management company), NOT the fund name.
3. Classify the email type.

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
PRIORITY HIERARCHY (MANDATORY)
────────────────────────────────────────
You MUST follow this hierarchy when identifying the firm:

HIGHEST:
- Explicitly stated asset management firm in the email body or attachment
  (e.g., signature, “Managed by”, letterhead)

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
- Return the THIRD-PARTY firm name
- Set is_third_party = true
- Do NOT return underlying hedge fund names

────────────────────────────────────────
NAMING RULES
────────────────────────────────────────
- Use canonical, industry-recognized firm names
- Clean the "," "LLC", "Ltd.", "Inc.", "LP", "LLP", "Corporation", "Corp." suffixes when identifying the firm name, but recognize them as indicators of a firm name
- Expand acronyms when possible (e.g., E20 → E20 Capital)
- Do NOT guess firm names without justification
- If no firm can be identified, return an empty string

You must follow these rules exactly."""

    user_prompt = f"""Analyze the following email metadata and return the classification.

EMAIL METADATA:
{json.dumps(email_metadata, indent=2, default=str)}

EXISTING FIRMS (for de-duplication only):
{json.dumps(existing_firms[:20] if existing_firms else [], indent=2)}

OUTPUT REQUIREMENTS:
Return a STRICT JSON object with EXACTLY the following fields:

{{
  "is_hedge_fund_related": true or false,
  "confidence": number between 0 and 1,
  "is_third_party": true or false,
  "firm_name": "string",
  "firm_name_source": "email_content" | "attachment" | "web search"  | "email_address" | "subject" | "unknown",
  "source_priority": "highest" | "medium" | "medium_low" | "lowest",
  "reasoning": "brief explanation following the priority hierarchy",
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
                "Other"
}}

Return JSON only. No commentary.
"""



    try:
        response = client.chat.completions.create(
            model="gpt-5.2",  # Using GPT-5.2 for better knowledge/reasoning capabilities
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"},
        )

        result = json.loads(response.choices[0].message.content)
        return result

    except Exception as e:
        print(f"GPT classification error: {e}")
        return {
            "is_hedge_fund_related": False,
            "confidence": 0.0,
            "is_third_party": False,
            "firm_name": "",
            "firm_name_source": "error",
            "source_priority": "lowest",
            "reasoning": f"Classification error: {str(e)}",
            "email_type": "other"
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
    is_third_party: bool = False
) -> Path:
    """
    Copy an email folder to the firm's folder in the output directory.

    Folder structure:
    - output_dir/hedge funds/[FIRM NAME]/ - for direct hedge fund emails
    - output_dir/3rd parties/[FIRM NAME]/ - for third-party intermediary emails

    Returns the destination path.
    """
    # Sanitize firm name for folder using dedicated function
    safe_firm_name = sanitize_folder_name(firm_name)

    # Determine parent folder based on whether it's a third-party email
    parent_folder = "3rd parties" if is_third_party else "hedge funds"

    firm_folder = output_dir / parent_folder / safe_firm_name
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
            classification = {
                "is_hedge_fund_related": True,
                "confidence": 1.0,
                "is_third_party": False,
                "firm_name": override_firm,
                "firm_name_source": "email_override" if from_address.lower() in [e.lower() for e in firm_mappings.get("email_overrides", {}).keys()] else "domain_override",
                "source_priority": "highest",
                "reasoning": f"Assigned via override rule for {'email: ' + from_address if from_address.lower() in [e.lower() for e in firm_mappings.get('email_overrides', {}).keys()] else 'domain: ' + from_address.split('@')[1] if '@' in from_address else 'unknown'}",
                "email_type": "unknown"
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
                existing_firms
            )

            # Cache the result
            classification_cache[email_id] = classification

        # Process classification result
        classification_entry = {
            "email_id": email_id,
            "email_folder": email_folder.name,
            "subject": subject,
            "from": metadata.get("from", {}).get("emailAddress", {}).get("address", ""),
            **classification
        }

        if classification.get("is_hedge_fund_related"):
            report["hedge_fund_related"] += 1

            # Get the firm name (single string)
            raw_firm_name = classification.get("firm_name", "")
            is_third_party = classification.get("is_third_party", False)

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
                if not is_third_party:
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
                        "is_third_party": is_third_party
                    }
                report["firms_found"][canonical_name]["email_count"] += 1
                report["firms_found"][canonical_name]["emails"].append({
                    "folder": email_folder.name,
                    "subject": subject
                })

                classification_entry["canonical_firm_name"] = canonical_name
                classification_entry["is_third_party"] = is_third_party
                if reassignment:
                    classification_entry["reassignment"] = reassignment

                # Copy to firm folder (using sanitized name and parent folder based on third-party status)
                dest = copy_email_to_firm_folder(email_folder, canonical_name, output_dir, is_third_party)
                classification_entry["destination"] = str(dest)
                parent_folder = "3rd parties" if is_third_party else "hedge funds"
                folder_display = sanitize_folder_name(canonical_name)
                print(f"    -> Copied to: {parent_folder}/{folder_display}/")
            else:
                report["hedge_fund_related"] -= 1  # Correction: no firm name found
                report["non_hedge_fund"] += 1
                print(f"    -> Hedge fund related but no firm name identified")
        else:
            report["non_hedge_fund"] += 1
            print(f"    -> Not hedge fund related (confidence: {classification.get('confidence', 0):.2f})")

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
            classification = {
                "is_hedge_fund_related": True,
                "confidence": 1.0,
                "is_third_party": False,
                "firm_name": override_firm,
                "firm_name_source": "email_override" if from_address.lower() in [e.lower() for e in firm_mappings.get("email_overrides", {}).keys()] else "domain_override",
                "source_priority": "highest",
                "reasoning": "Assigned via override rule",
                "email_type": "unknown"
            }
            print(f"[{i+1}/{len(new_folders)}] (override) {subject[:50]}...")
        else:
            # Classify with GPT
            print(f"[{i+1}/{len(new_folders)}] Classifying: {subject[:50]}...")
            classification = classify_email_with_gpt(client, metadata, existing_firms)
            classification_cache[email_id] = classification

        # Process result - get single firm_name
        raw_firm_name = classification.get("firm_name", "")
        is_third_party = classification.get("is_third_party", False)

        if classification.get("is_hedge_fund_related") and raw_firm_name:
            canonical_name = normalize_firm_name(raw_firm_name, firm_mappings)
            canonical_name = apply_folder_reassignment(canonical_name, firm_mappings)

            # Add to mappings (only add domain hints for non-third-party emails)
            if not is_third_party:
                domain_hints = extract_domain_hints(from_address)
                aliases = [raw_firm_name] + domain_hints
            else:
                aliases = [raw_firm_name]
            add_firm_to_mappings(canonical_name, aliases, firm_mappings)

            if canonical_name not in existing_firms:
                existing_firms.append(canonical_name)

            # Copy to firm folder with parent folder based on third-party status
            dest = copy_email_to_firm_folder(email_folder, canonical_name, output_dir, is_third_party)
            parent_folder = "3rd parties" if is_third_party else "hedge funds"
            folder_display = sanitize_folder_name(canonical_name)
            print(f"    -> Copied to: {parent_folder}/{folder_display}/")

            results.append({
                "email_folder": email_folder.name,
                "subject": subject,
                "firm": canonical_name,
                "destination": str(dest),
                "is_third_party": is_third_party
            })
        else:
            reason = "Not hedge fund related" if not classification.get("is_hedge_fund_related") else "No firm name identified"
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
            "is_third_party": result.get("is_third_party", False),
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
                        parent_folder = "3rd parties" if item.get("is_third_party") else "hedge funds"
                        print(f"  + {item['subject'][:40]}... -> {parent_folder}/{firm}")
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
    # Check both parent directories (hedge funds and 3rd parties)
    old_folder_name = sanitize_folder_name(old_firm_name)
    new_folder_name = sanitize_folder_name(new_firm_key or new_firm_name.upper())

    emails_moved = 0
    folders_processed = []

    # Check in both parent directories
    for parent_dir in ["hedge funds", "3rd parties"]:
        old_folder_path = output_dir / parent_dir / old_folder_name
        new_folder_path = output_dir / parent_dir / new_folder_name

        if old_folder_path.exists() and old_folder_path.is_dir():
            # Create new folder if it doesn't exist
            new_folder_path.mkdir(parents=True, exist_ok=True)

            # Move all email subfolders from old to new
            for item in old_folder_path.iterdir():
                if item.is_dir():
                    dest_path = new_folder_path / item.name
                    if dest_path.exists():
                        # If destination exists, merge by removing old and copying
                        shutil.rmtree(dest_path)
                    shutil.move(str(item), str(dest_path))
                    emails_moved += 1

            # Delete the old folder
            try:
                shutil.rmtree(old_folder_path)
                folders_processed.append(f"{parent_dir}/{old_folder_name}")
            except Exception as e:
                print(f"\nWarning: Could not delete old folder '{parent_dir}/{old_folder_name}': {e}")

    if folders_processed:
        print(f"\nFolder reorganization complete:")
        print(f"  - Moved {emails_moved} email(s) to '{new_folder_name}/'")
        for folder in folders_processed:
            print(f"  - Deleted old folder: {folder}/")
    else:
        print(f"\nNo existing folder found for '{old_folder_name}' in hedge funds or 3rd parties - no files to move.")


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
    """List all known firms and their aliases."""
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    mappings = load_firm_mappings(output_dir)

    print("\nKnown Firms:")
    print("-" * 40)

    for canonical, info in sorted(mappings.get("canonical_names", {}).items()):
        aliases = info.get("aliases", [])
        print(f"\n{canonical}")
        if aliases:
            print(f"  Aliases: {', '.join(aliases)}")
        if info.get("description"):
            print(f"  Description: {info['description']}")

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


def switch_firm_category(output_dir: Path = None):
    """
    Switch a firm between hedge fund and 3rd party categories.
    Moves all emails from one category folder to the other and reclassifies.
    """
    output_dir = output_dir or DEFAULT_OUTPUT_DIR

    print("\n" + "=" * 50)
    print("SWITCH FIRM CATEGORY")
    print("=" * 50)

    # Show existing folders in both categories
    hedge_funds_dir = output_dir / "hedge funds"
    third_parties_dir = output_dir / "3rd parties"

    print("\nCurrent hedge fund firms:")
    if hedge_funds_dir.exists():
        hf_firms = [f.name for f in hedge_funds_dir.iterdir() if f.is_dir()]
        for firm in sorted(hf_firms):
            print(f"  - {firm}")
    else:
        print("  (none)")

    print("\nCurrent 3rd party firms:")
    if third_parties_dir.exists():
        tp_firms = [f.name for f in third_parties_dir.iterdir() if f.is_dir()]
        for firm in sorted(tp_firms):
            print(f"  - {firm}")
    else:
        print("  (none)")

    firm_name = input("\nEnter firm name to switch: ").strip()
    if not firm_name:
        print("No firm name provided.")
        return

    safe_firm_name = sanitize_folder_name(firm_name)

    # Determine current category
    hf_path = hedge_funds_dir / safe_firm_name
    tp_path = third_parties_dir / safe_firm_name

    if hf_path.exists() and hf_path.is_dir():
        current_category = "hedge funds"
        new_category = "3rd parties"
        source_path = hf_path
        dest_path = tp_path
    elif tp_path.exists() and tp_path.is_dir():
        current_category = "3rd parties"
        new_category = "hedge funds"
        source_path = tp_path
        dest_path = hf_path
    else:
        print(f"Firm '{firm_name}' not found in either 'hedge funds' or '3rd parties' folders.")
        return

    print(f"\nFirm '{safe_firm_name}' is currently in '{current_category}'.")
    confirm = input(f"Move to '{new_category}'? (y/n): ").strip().lower()

    if confirm != 'y':
        print("Operation cancelled.")
        return

    # Create destination directory if needed
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # Move the firm folder
    if dest_path.exists():
        # Merge: move all email subfolders from source to dest
        emails_moved = 0
        for item in source_path.iterdir():
            if item.is_dir():
                item_dest = dest_path / item.name
                if item_dest.exists():
                    shutil.rmtree(item_dest)
                shutil.move(str(item), str(item_dest))
                emails_moved += 1
        # Remove empty source folder
        try:
            shutil.rmtree(source_path)
        except Exception as e:
            print(f"Warning: Could not delete source folder: {e}")
        print(f"Merged {emails_moved} email(s) into existing '{new_category}/{safe_firm_name}/'")
    else:
        # Simple move
        shutil.move(str(source_path), str(dest_path))
        email_count = len([f for f in dest_path.iterdir() if f.is_dir()])
        print(f"Moved '{safe_firm_name}' with {email_count} email(s) to '{new_category}/'")

    # Update classification cache to reflect new is_third_party status
    cache = load_classification_cache(output_dir)
    new_is_third_party = (new_category == "3rd parties")
    updated_count = 0

    for email_id, classification in cache.items():
        firm = classification.get("firm_name", "")
        normalized = normalize_firm_name(firm, load_firm_mappings(output_dir))
        if normalized.lower() == firm_name.upper().lower() or sanitize_folder_name(normalized) == safe_firm_name:
            classification["is_third_party"] = new_is_third_party
            updated_count += 1

    save_classification_cache(cache, output_dir)

    print(f"\nCategory switch complete:")
    print(f"  - Firm: {safe_firm_name}")
    print(f"  - From: {current_category}")
    print(f"  - To: {new_category}")
    print(f"  - Cache entries updated: {updated_count}")


def main():
    """Main entry point."""
    import sys

    print("=" * 60)
    print("HEDGE FUND EMAIL CLASSIFIER")
    print("=" * 60)
    print("\nSelect mode:")
    print("  1. Classify and organize all emails")
    print("  2. Force reclassify all (ignore cache)")
    print("  3. List known firms")
    print("  4. List all overrides")
    print("  5. Add email override (specific address -> firm)")
    print("  6. Add domain override (all from domain -> firm)")
    print("  7. Reassign/rename firm (old firm -> new firm, merges if new exists)")
    print("  8. Monitor for new emails (continuous)")
    print("  9. Check for new emails (one-time)")
    print(" 10. Manage firm aliases (list/delete)")
    print(" 11. Switch firm between hedge fund and 3rd party")
    print()

    if len(sys.argv) > 1:
        mode = sys.argv[1]
    else:
        mode = input("Enter mode (1-11): ").strip()

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
        switch_firm_category()
    else:
        print("Invalid mode.")


if __name__ == "__main__":
    main()
