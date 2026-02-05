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
    existing_firms: list,
    attachment_names: list = None
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
    # Prepare context
    subject = email_metadata.get("subject", "")
    from_info = email_metadata.get("from", {}).get("emailAddress", {})
    from_name = from_info.get("name", "")
    from_address = from_info.get("address", "")
    body_preview = email_metadata.get("bodyPreview", "")[:1000]  # Limit body size

    attachments = email_metadata.get("attachments", [])
    attachment_names = attachment_names or [a.get("name", "") for a in attachments]

    # Build the prompt
    system_prompt = """You are an expert at identifying hedge fund and investment fund related emails.

Your task is to:
1. Determine if an email is related to either a hedge fund or asset management firm
2. Extract the AM FIRM NAME (asset management company), NOT the fund name. This is the hardest part.
3. Classify the type of email

------------------------------------------------------------------------------------------------------------------------------

To determine if the email is hedge fund related, consider these as hedge fund related:
- Monthly/quarterly performance updates
- Webinar or macro event invitations from fund managers
- Investor letters and newsletters
- Factsheets and tear sheets
- NAV estimates and statements
- Fund marketing materials
- Subscription/redemption documents
- Due diligence materials
- Cap intro presentations featuring multiple managers

NOT hedge fund related:
- General marketing from brokers (unless it's about specific funds)
- Meeting invitations non-related to any hedge fund
- Personal emails
- IT/system notifications
- General news without fund-specific content

------------------------------------------------------------------------------------------------------------------------------

To extract the AM FIRM NAME, follow these steps:
1. Gather clues from the email. We are not trying to find who is sending the email, but whom is this email related to:
   - Attachment filenames and contents
   - Investment manager names mentioned in email content
   - Sender's name and title
   - Signature block with company information
   - Email domain (e.g., @springscap.com might be "Springs Capital")

2. MANDATORY SEARCH STEP - Search the internet for the official firm name:
   - Take the fund name and SEARCH the internet to find the linked company name
   - If you have a person's name like David Johnson, SEARCH DAVID JOHNSON LINKEDIN to verify which firm they work for
   - Cross-reference any hints (domain, manager name, fund name) to find the official entity
   - DO NOT guess or directly use fund name from the email without this MANDATORY verification step
   - Example: If domain is "citadel.com", search your knowledge to confirm the official name is "CITADEL LLC"

    FIRM NAMES are typically:
   - The management company or investment advisor
   - Usually ends with: Capital, Asset Management, Partners, Advisors, Investment Management, Holdings
   - Examples: "Citadel", "Bridgewater Associates", "Two Sigma", "Point72 Asset Management"
   
   You must extract the FIRM NAME (the management company), NOT the fund name. These are different:

    FUND NAMES typically include:
   - Strategy descriptors: "Equity Long/Short", "Global Macro", "Credit Opportunities", "Multi-Strategy"
   - Geographic focus: "European", "Asia Pacific", "China Focused", "Emerging Markets"
   - Structure terms: "Master Fund", "Feeder Fund", "Offshore Fund", "Onshore Fund"
   - Class designators: "Class A", "Series B", "USD Class"
    
    If no firm name is explicitly stated, strip out fund-specific descriptors from fund name to get the core firm name (e.g., "ABC Asia Equity Long/Short Fund" -> "ABC Capital").

3. Decide the firm name - The firm name will be used as a folder name, so use the canonical/official name that would be recognized industry-wide.
    Be very careful with acronyms as firms often use abbreviated names. Look up for clues if you identify potential acronyms:
        - "MS" likely means "Morgan Stanley"
        - "GS" likely means "Goldman Sachs"
        - "JPM" or "JP" likely means "JPMorgan"
        - "UBS", "HSBC", "CS" (Credit Suisse) are common abbreviations
        - Two or three letter abbreviations in email domains or signatures often represent well-known firms

CRITICAL - INFORMATION SOURCE PRIORITY:
You MUST follow this priority hierarchy when identifying the Asset Management (AM) firm:

**HIGHEST PRIORITY - Explicit Statement:**
The email content or an attachment explicitly states which AM firm manages the fund.
- Look for "Managed by:", "Investment Manager:", "Fund Manager:", explicit firm signatures within the attachments or email body
- If explicitly stated, use this as the definitive answer
- Set firm_name_source to "email_content" or "attachment"

**MEDIUM PRIORITY - External Verification:**
No AM firm is explicitly mentioned in the email.
- First, identify the FUND NAME from: the attachment file name, the attachement content, the email body, or the email domain
- Then, SEARCH your knowledge base/external sources to find which AM firm manages that specific fund
- Only use this if you can find reliable external confirmation linking the fund to its managing firm
- Set firm_name_source to "email_content" or "attachment" with reasoning explaining the external verification

**MEDIUM-LOW PRIORITY - Inference Without Verification:**
No firm can be confirmed through external sources.
- Infer the AM firm directly from:
  * The fund name (e.g., "ABC Asia Fund" likely managed by "ABC Capital" or "ABC Asset Management")
  * The sender's email domain (e.g., @springscap.com likely means "Springs Capital")
- Use this ONLY when external verification performed or is not possible
- Set firm_name_source to "email_address" or "subject"

**LOWEST PRIORITY - Non-Hedge Fund:**
- Neither a firm name nor a fund name is available
- The email domain does not provide a reliable indication of fund ownership
- This should normally be classified as NOT hedge fund related (is_hedge_fund_related: false)

In your reasoning, ALWAYS explain:
1. Which priority level you used (HIGHEST/MEDIUM/MEDIUM-LOW/LOWEST)
2. What information source you relied on
3. What search/verification you performed to identify the official name

-----------------------------------------------------------------------------------------------------------------------------------------------

THIRD-PARTY INTERMEDIARIES:
These firms are intermediaries (cap intro, fund admin, prime brokers, placement agents, data providers):

Cap Intro / Capital Introduction:
- IConnections, With Intelligence, Agecroft Partners, Park Hill Group, Eaton Partners, HFM (Hedge Fund Manager)

Fund Administrators:
- CITCO, Apex Group, ApexConnect, SS&C Technologies, NAV Consulting, Trident Trust, Custom House, Alter Domus

Prime Brokers (when sending cap intro or research):
- Goldman Sachs (GS), Morgan Stanley (MS), Bank of America (BofA/BAML), JPMorgan, UBS, Credit Suisse, Barclays

Other Intermediaries:
- Marex, Preqin, eVestment, Bloomberg, Refinitiv, PivotalPath, HFR (Hedge Fund Research)

IMPORTANT FOR THIRD-PARTY EMAILS:
When an email is FROM a third-party intermediary AND contains hedge fund related content:
- Return the THIRD-PARTY SENDER'S firm name (e.g., "ICONNECTIONS", "CITCO", "APEX GROUP")
- Do NOT extract the underlying hedge funds mentioned in the email content
- Set is_third_party to true
- The email will be filed under the third-party sender's name

------------------------------------------------------------------------------------------------------------------------------

Return your analysis as JSON."""

    user_prompt = f"""
Analyze the following email and return a STRICT JSON object only.

EMAIL DETAILS
-------------
Subject: {subject}

From: {from_name} <{from_address}>

Body Preview:
{body_preview}

Attachments:
{', '.join(attachment_names) if attachment_names else 'None'}

Known existing firms in our database:
{', '.join(existing_firms[:20]) if existing_firms else 'None yet'}

TASK OBJECTIVES
---------------
1. Determine whether this email is related to a hedge fund or asset management firm.
2. Identify the CANONICAL ASSET MANAGEMENT FIRM NAME (the management company), NOT the fund name.
3. Classify the email type.

IMPORTANT CLARIFICATIONS
------------------------
- Do NOT return the fund name.
- The firm name should be the official, industry-recognized asset manager.
- If the email is from a THIRD-PARTY INTERMEDIARY (cap intro, fund admin, prime broker, etc.), return the THIRD-PARTY firm's name and set is_third_party = true.
- You MUST follow the priority hierarchy below when identifying the firm.

FIRM IDENTIFICATION PRIORITY HIERARCHY (MANDATORY)
-------------------------------------------------
highest:
- Explicit statement in email body or attachment.

medium:
- Fund name identified; verified externally to find the manager.

medium_low:
- Inference from domain or naming when verification is not possible.

lowest:
- Not hedge fund related or insufficient information.

OUTPUT FORMAT (STRICT JSON)
---------------------------
Return a JSON object with EXACTLY these fields:

{{
  "is_hedge_fund_related": true or false,
  "confidence": number between 0 and 1,
  "is_third_party": true or false,
  "firm_name": "string",
  "firm_name_source": "email_content" | "attachment" | "email_address" | "subject" | "unknown",
  "source_priority": "highest" | "medium" | "medium_low" | "lowest",
  "reasoning": "string",
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

Do NOT include explanations outside the JSON.
Do NOT guess firm names without justification.
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
    force_reclassify: bool = False,
    dry_run: bool = False
) -> dict:
    """
    Main function to classify and organize all emails.

    Args:
        email_input_dir: Directory containing email folders
        output_dir: Directory for organized firm folders
        force_reclassify: If True, ignore cache and reclassify all
        dry_run: If True, don't actually copy files

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
                if not dry_run:
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
    if not dry_run:
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




def main():
    """Main entry point."""
    import sys

    print("=" * 60)
    print("HEDGE FUND EMAIL CLASSIFIER")
    print("=" * 60)
    print("\nSelect mode:")
    print("  1. Classify and organize all emails")
    print("  2. Classify only (dry run - no file copying)")
    print("  3. Force reclassify all (ignore cache)")
    print("  4. List known firms")
    print("  5. List all overrides")
    print("  6. Add email override (specific address -> firm)")
    print("  7. Add domain override (all from domain -> firm)")
    print("  8. Reassign/rename firm (old firm -> new firm, merges if new exists)")
    print("  9. Monitor for new emails (continuous)")
    print(" 10. Check for new emails (one-time)")
    print()

    if len(sys.argv) > 1:
        mode = sys.argv[1]
    else:
        mode = input("Enter mode (1-10): ").strip()

    if mode == "1":
        classify_and_organize_emails()
    elif mode == "2":
        classify_and_organize_emails(dry_run=True)
    elif mode == "3":
        classify_and_organize_emails(force_reclassify=True)
    elif mode == "4":
        list_firms()
    elif mode == "5":
        list_overrides()
    elif mode == "6":
        email = input("Enter email address: ").strip()
        firm = input("Enter firm name to assign: ").strip()
        if email and firm:
            add_email_override(email, firm)
        else:
            print("Email and firm name are required.")
    elif mode == "7":
        domain = input("Enter domain (without @): ").strip()
        firm = input("Enter firm name to assign: ").strip()
        if domain and firm:
            add_domain_override(domain, firm)
        else:
            print("Domain and firm name are required.")
    elif mode == "8":
        old_firm = input("Enter old firm name to reassign/remove: ").strip()
        new_firm = input("Enter new firm name (will be created if doesn't exist): ").strip()
        if old_firm and new_firm:
            reassign_firm(old_firm, new_firm)
        else:
            print("Both firm names are required.")
    elif mode == "9":
        interval = input("Poll interval in seconds (default 30): ").strip()
        interval = int(interval) if interval.isdigit() else 30
        monitor_and_classify(poll_interval=interval, run_once=False)
    elif mode == "10":
        monitor_and_classify(run_once=True)
    else:
        print("Invalid mode.")


if __name__ == "__main__":
    main()
