"""
Email Classification System for Hedge Fund Emails (Agentic Version)

This module classifies emails using an agentic workflow to identify hedge fund
related emails and organizes them into firm-specific folders.

Key features:
1. Two-phase classification:
   - Phase 1: Quick classification to determine if email is hedge fund related
   - Phase 2: Agentic workflow for firm name identification

2. Agentic Firm Lookup Workflow:
   - Finding Agent: Uses knowledge base/web search to find official firm name
   - Grading Agent: Evaluates reasoning quality and assigns confidence score
   - Orchestrator: Manages retries with decreasing thresholds

3. Confidence Threshold Logic:
   - Initial threshold: 90%
   - Decreases by 5% each retry: 90 -> 85 -> 80 -> 75 -> 70
   - Minimum threshold: 70%
   - Maximum attempts: 10
   - Items below 70% after all attempts are flagged for manual review

4. Flagged Items Management:
   - Low confidence results saved to flagged_low_confidence.json
   - Manual review and resolution workflow
   - Best available result still used (with flag)

5. Additional features:
   - Firm name normalization with human-editable mappings
   - Email/domain overrides for known senders
   - Automatic folder organization by firm
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


# =========================
# AGENTIC FIRM LOOKUP SYSTEM
# =========================

class AgenticFirmLookupResult:
    """Result from the agentic firm lookup workflow."""
    def __init__(
        self,
        firm_name: str,
        confidence_score: int,
        finding_reasoning: str,
        grading_reasoning: str,
        attempts: int,
        flagged: bool = False,
        all_attempts: list = None
    ):
        self.firm_name = firm_name
        self.confidence_score = confidence_score
        self.finding_reasoning = finding_reasoning
        self.grading_reasoning = grading_reasoning
        self.attempts = attempts
        self.flagged = flagged
        self.all_attempts = all_attempts or []

    def to_dict(self) -> dict:
        return {
            "firm_name": self.firm_name,
            "confidence_score": self.confidence_score,
            "finding_reasoning": self.finding_reasoning,
            "grading_reasoning": self.grading_reasoning,
            "attempts": self.attempts,
            "flagged": self.flagged,
            "all_attempts": self.all_attempts
        }


def finding_agent(
    client: OpenAI,
    email_domain: str,
    sender_name: str,
    email_subject: str,
    body_preview: str,
    attachment_names: list,
    previous_feedback: dict = None
) -> dict:
    """
    Finding Agent: Uses internet search to find the official firm name.

    Args:
        client: OpenAI client
        email_domain: Domain from email address
        sender_name: Sender's display name
        email_subject: Email subject line
        body_preview: Preview of email body
        attachment_names: List of attachment filenames
        previous_feedback: Optional feedback from Grading Agent on previous attempt
            Contains: concerns, advice, previous_proposed_name, previous_score

    Returns a dict with:
    - proposed_firm_name: The official firm name found
    - reasoning: Explanation of why this is the official name
    - evidence: What clues/searches led to this conclusion
    - is_acronym_expansion: Whether an acronym was expanded
    - is_fund_name: Whether the name appears to be a fund name (should be False)
    """
    system_prompt = """You are a Finding Agent specialized in identifying official asset management firm names.

Your task is to take clues from an email (domain, sender name, subject, content) and use your knowledge
to search and identify the OFFICIAL REGISTERED NAME of the firm.

CRITICAL RULES:
1. You MUST use your knowledge base (simulating internet search) to find the official firm name
2. DO NOT simply extract text from the email - verify it against your knowledge
3. The firm name should be the ASSET MANAGEMENT COMPANY, not a fund name
4. Expand acronyms to their full official names (e.g., "MS" -> "Morgan Stanley")
5. Explain your reasoning clearly so a Grading Agent can evaluate it

DISTINGUISHING FIRM vs FUND:
- FUND names include: strategy descriptors (Long/Short, Global Macro), geographic focus (Asia, China),
  structure terms (Master Fund, Feeder), class designators (Class A, USD Class)
- FIRM names are management companies, usually ending with: Capital, Asset Management, Partners,
  Advisors, Investment Management, Holdings

Your response must explain:
1. What clues you gathered from the email
2. What search you performed in your knowledge base
3. Why you believe this is the official registered name
4. Why it is NOT a fund name or acronym

Return your analysis as JSON."""

    # Build user prompt with optional feedback from previous attempt
    user_prompt = f"""Find the official firm name based on these email clues:

Email Domain: {email_domain}
Sender Name: {sender_name}
Subject: {email_subject}
Body Preview: {body_preview[:500]}
Attachments: {', '.join(attachment_names) if attachment_names else 'None'}
"""

    # Add feedback from Grading Agent if this is a retry
    if previous_feedback:
        user_prompt += f"""
=== FEEDBACK FROM PREVIOUS ATTEMPT ===
Your previous answer "{previous_feedback.get('previous_proposed_name', 'N/A')}" received a score of {previous_feedback.get('previous_score', 'N/A')}%.

GRADING AGENT'S CONCERNS:
{previous_feedback.get('concerns', 'None provided')}

GRADING AGENT'S ADVICE:
{previous_feedback.get('advice', 'None provided')}

Please address these concerns and try a different approach or provide stronger evidence.
======================================
"""

    user_prompt += """
Return a JSON object with:
- proposed_firm_name: string (the official firm name, empty string if not found)
- reasoning: string (detailed explanation of your search and findings)
- evidence: string (what clues and knowledge base results led to this)
- is_acronym_expansion: boolean (true if you expanded an acronym)
- is_fund_name: boolean (should be false - set true if you suspect this might be a fund name)
- search_performed: string (describe what you searched for in your knowledge base)
"""

    try:
        response = client.chat.completions.create(
            model="gpt-5.2",  # Using GPT-4o for web search simulation
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"},
        )

        result = json.loads(response.choices[0].message.content)
        return result

    except Exception as e:
        print(f"Finding Agent error: {e}")
        return {
            "proposed_firm_name": "",
            "reasoning": f"Error: {str(e)}",
            "evidence": "",
            "is_acronym_expansion": False,
            "is_fund_name": False,
            "search_performed": ""
        }


def grading_agent(
    client: OpenAI,
    proposed_firm_name: str,
    finding_reasoning: str,
    finding_evidence: str,
    email_domain: str,
    sender_name: str
) -> dict:
    """
    Grading Agent: Evaluates the Finding Agent's reasoning and assigns a confidence score.

    Returns a dict with:
    - confidence_score: int (0-100)
    - grading_reasoning: Explanation of the grade
    - concerns: Any concerns about the proposed name
    - advice: Suggestions for the Finding Agent to improve on next attempt
    """
    system_prompt = """You are a Grading Agent that evaluates firm name identification results.

Your task is to review the Finding Agent's proposed firm name and reasoning, then assign a
confidence score from 0-100.

IMPORTANT: You must also provide constructive advice for the Finding Agent. If the score is below
the threshold, your advice will be sent back to the Finding Agent for their next attempt.

GRADING CRITERIA:
- 90-100: Highly confident - clear match between domain/sender and a well-known firm, strong evidence
- 80-89: Confident - good evidence, minor uncertainties
- 70-79: Moderately confident - reasonable evidence but some gaps
- 60-69: Low confidence - weak evidence, multiple interpretations possible
- Below 60: Very low confidence - insufficient evidence, likely wrong

EVALUATE:
1. Does the reasoning make logical sense?
2. Was a proper search performed (not just extracting text from email)?
3. Is the proposed name likely an official firm name (not a fund name)?
4. Were acronyms properly expanded?
5. Does the domain/sender align with the proposed firm?

BE OBJECTIVE - you don't know what the passing threshold is. Just give your honest assessment.

ADVICE GUIDELINES:
- Be specific about what additional searches or verification steps could help
- Suggest alternative interpretations of the domain or sender name
- Point out if there might be a parent company or different official name
- Recommend checking for acronym expansions if applicable

Return your analysis as JSON."""

    user_prompt = f"""Grade this firm identification:

PROPOSED FIRM NAME: {proposed_firm_name}

FINDING AGENT'S REASONING:
{finding_reasoning}

EVIDENCE PROVIDED:
{finding_evidence}

ORIGINAL EMAIL CLUES:
- Domain: {email_domain}
- Sender: {sender_name}

Return a JSON object with:
- confidence_score: integer from 0-100
- grading_reasoning: string (explain why you assigned this score)
- concerns: string (any concerns about the identification, empty if none)
- strengths: string (what was done well)
- advice: string (specific suggestions for how the Finding Agent could improve their search or verification - this will be sent to them if they need to retry)
"""

    try:
        response = client.chat.completions.create(
            model="gpt-5.2",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"},
        )

        result = json.loads(response.choices[0].message.content)
        # Ensure confidence_score is an integer
        result["confidence_score"] = int(result.get("confidence_score", 0))
        return result

    except Exception as e:
        print(f"Grading Agent error: {e}")
        return {
            "confidence_score": 0,
            "grading_reasoning": f"Error: {str(e)}",
            "concerns": "Grading failed",
            "strengths": ""
        }


def agentic_firm_lookup(
    client: OpenAI,
    email_domain: str,
    sender_name: str,
    email_subject: str,
    body_preview: str,
    attachment_names: list,
    initial_threshold: int = 90,
    threshold_decrement: int = 5,
    minimum_threshold: int = 70,
    max_attempts: int = 10
) -> AgenticFirmLookupResult:
    """
    Orchestrator: Manages the agentic workflow with retry logic.

    Workflow:
    1. Finding Agent searches for official firm name
    2. Grading Agent evaluates the result
    3. If confidence >= threshold, return result
    4. If confidence < threshold, decrease threshold by 5% and retry
    5. Threshold decreases from 90 -> 85 -> 80 -> 75 -> 70 (minimum)
    6. After max 10 attempts, if still below 70, flag and use best result

    Args:
        client: OpenAI client
        email_domain: Domain from email address
        sender_name: Sender's display name
        email_subject: Email subject line
        body_preview: Preview of email body
        attachment_names: List of attachment filenames
        initial_threshold: Starting confidence threshold (default 90)
        threshold_decrement: How much to decrease threshold each attempt (default 5)
        minimum_threshold: Lowest threshold allowed (default 70)
        max_attempts: Maximum number of attempts (default 10)

    Returns:
        AgenticFirmLookupResult with firm name, confidence, reasoning, and metadata
    """
    all_attempts = []
    best_result = None
    best_score = -1
    current_threshold = initial_threshold
    previous_feedback = None  # Feedback from Grading Agent to pass to Finding Agent

    for attempt in range(1, max_attempts + 1):
        print(f"    [Agentic Lookup] Attempt {attempt}/{max_attempts}, threshold: {current_threshold}%")

        # Step 1: Finding Agent (with feedback from previous attempt if available)
        finding_result = finding_agent(
            client=client,
            email_domain=email_domain,
            sender_name=sender_name,
            email_subject=email_subject,
            body_preview=body_preview,
            attachment_names=attachment_names,
            previous_feedback=previous_feedback
        )

        proposed_name = finding_result.get("proposed_firm_name", "")
        finding_reasoning = finding_result.get("reasoning", "")
        finding_evidence = finding_result.get("evidence", "")

        if not proposed_name:
            print(f"    [Agentic Lookup] Finding Agent returned no firm name, retrying...")
            all_attempts.append({
                "attempt": attempt,
                "threshold": current_threshold,
                "proposed_name": "",
                "confidence_score": 0,
                "finding_reasoning": finding_reasoning,
                "grading_reasoning": "No firm name proposed",
                "feedback_given": previous_feedback
            })
            # Set feedback for next attempt
            previous_feedback = {
                "previous_proposed_name": "",
                "previous_score": 0,
                "concerns": "No firm name was proposed",
                "advice": "Try searching for the domain name or sender name in your knowledge base. Look for any company or organization associated with this email."
            }
            # Lower threshold and retry
            current_threshold = max(current_threshold - threshold_decrement, minimum_threshold)
            continue

        # Step 2: Grading Agent
        grading_result = grading_agent(
            client=client,
            proposed_firm_name=proposed_name,
            finding_reasoning=finding_reasoning,
            finding_evidence=finding_evidence,
            email_domain=email_domain,
            sender_name=sender_name
        )

        confidence_score = grading_result.get("confidence_score", 0)
        grading_reasoning = grading_result.get("grading_reasoning", "")
        grading_concerns = grading_result.get("concerns", "")
        grading_advice = grading_result.get("advice", "")

        # Record this attempt
        attempt_record = {
            "attempt": attempt,
            "threshold": current_threshold,
            "proposed_name": proposed_name,
            "confidence_score": confidence_score,
            "finding_reasoning": finding_reasoning,
            "grading_reasoning": grading_reasoning,
            "concerns": grading_concerns,
            "strengths": grading_result.get("strengths", ""),
            "advice": grading_advice,
            "feedback_given": previous_feedback
        }
        all_attempts.append(attempt_record)

        # Track best result
        if confidence_score > best_score:
            best_score = confidence_score
            best_result = attempt_record

        print(f"    [Agentic Lookup] Proposed: '{proposed_name}', Score: {confidence_score}%")

        # Step 3: Check if passes threshold
        if confidence_score >= current_threshold:
            print(f"    [Agentic Lookup] PASSED (score {confidence_score} >= threshold {current_threshold})")
            return AgenticFirmLookupResult(
                firm_name=proposed_name,
                confidence_score=confidence_score,
                finding_reasoning=finding_reasoning,
                grading_reasoning=grading_reasoning,
                attempts=attempt,
                flagged=False,
                all_attempts=all_attempts
            )

        # Step 4: Prepare feedback for next attempt
        print(f"    [Agentic Lookup] FAILED (score {confidence_score} < threshold {current_threshold})")
        print(f"    [Agentic Lookup] Passing feedback to Finding Agent for next attempt...")
        previous_feedback = {
            "previous_proposed_name": proposed_name,
            "previous_score": confidence_score,
            "concerns": grading_concerns,
            "advice": grading_advice
        }

        # Lower threshold for next attempt
        current_threshold = max(current_threshold - threshold_decrement, minimum_threshold)

    # After max attempts, check if we have any result above minimum threshold
    if best_result and best_score >= minimum_threshold:
        print(f"    [Agentic Lookup] Using best result after {max_attempts} attempts: '{best_result['proposed_name']}' ({best_score}%)")
        return AgenticFirmLookupResult(
            firm_name=best_result["proposed_name"],
            confidence_score=best_score,
            finding_reasoning=best_result["finding_reasoning"],
            grading_reasoning=best_result["grading_reasoning"],
            attempts=max_attempts,
            flagged=False,
            all_attempts=all_attempts
        )

    # Flag as low confidence - use best available result anyway
    if best_result:
        print(f"    [Agentic Lookup] FLAGGED: Best score {best_score}% below minimum {minimum_threshold}%")
        return AgenticFirmLookupResult(
            firm_name=best_result["proposed_name"],
            confidence_score=best_score,
            finding_reasoning=best_result["finding_reasoning"],
            grading_reasoning=best_result["grading_reasoning"] + f" [FLAGGED: Below {minimum_threshold}% confidence after {max_attempts} attempts]",
            attempts=max_attempts,
            flagged=True,
            all_attempts=all_attempts
        )

    # No valid result at all
    print(f"    [Agentic Lookup] FLAGGED: No valid firm name found after {max_attempts} attempts")
    return AgenticFirmLookupResult(
        firm_name="",
        confidence_score=0,
        finding_reasoning="No firm name could be identified",
        grading_reasoning=f"[FLAGGED: No valid result after {max_attempts} attempts]",
        attempts=max_attempts,
        flagged=True,
        all_attempts=all_attempts
    )


FLAGGED_ITEMS_FILE = "flagged_low_confidence.json"  # Items that need manual review


def load_flagged_items(output_dir: Path) -> list:
    """Load flagged items that need manual review."""
    flagged_path = output_dir / FLAGGED_ITEMS_FILE

    if flagged_path.exists():
        with open(flagged_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    return []


def save_flagged_item(
    output_dir: Path,
    email_folder: str,
    subject: str,
    from_address: str,
    agentic_result: dict
):
    """Save a flagged item for manual review."""
    flagged_items = load_flagged_items(output_dir)

    flagged_items.append({
        "email_folder": email_folder,
        "subject": subject,
        "from_address": from_address,
        "flagged_at": datetime.now().isoformat(),
        "best_firm_name": agentic_result.get("firm_name", ""),
        "best_confidence_score": agentic_result.get("confidence_score", 0),
        "attempts": agentic_result.get("attempts", 0),
        "all_attempts": agentic_result.get("all_attempts", []),
        "status": "pending_review"
    })

    flagged_path = output_dir / FLAGGED_ITEMS_FILE
    with open(flagged_path, 'w', encoding='utf-8') as f:
        json.dump(flagged_items, f, indent=2, ensure_ascii=False)

    print(f"    [FLAGGED] Added to {FLAGGED_ITEMS_FILE} for manual review")


def list_flagged_items(output_dir: Path = None) -> list:
    """List all flagged items that need manual review."""
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    flagged_items = load_flagged_items(output_dir)

    print("\n" + "=" * 60)
    print("FLAGGED ITEMS - MANUAL REVIEW NEEDED")
    print("=" * 60)

    if not flagged_items:
        print("\nNo flagged items pending review.")
        return []

    pending = [item for item in flagged_items if item.get("status") == "pending_review"]
    print(f"\nTotal flagged: {len(flagged_items)}, Pending review: {len(pending)}")
    print("-" * 60)

    for i, item in enumerate(pending):
        print(f"\n[{i+1}] {item['subject'][:50]}...")
        print(f"    From: {item['from_address']}")
        print(f"    Best guess: '{item['best_firm_name']}' ({item['best_confidence_score']}% confidence)")
        print(f"    Attempts: {item['attempts']}")
        print(f"    Flagged at: {item['flagged_at']}")

    return flagged_items


def resolve_flagged_item(
    index: int,
    correct_firm_name: str,
    output_dir: Path = None
):
    """Manually resolve a flagged item with the correct firm name."""
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    flagged_items = load_flagged_items(output_dir)

    pending = [item for item in flagged_items if item.get("status") == "pending_review"]

    if index < 1 or index > len(pending):
        print(f"Invalid index. Use 1-{len(pending)}")
        return

    item = pending[index - 1]
    item["status"] = "resolved"
    item["resolved_at"] = datetime.now().isoformat()
    item["resolved_firm_name"] = correct_firm_name

    flagged_path = output_dir / FLAGGED_ITEMS_FILE
    with open(flagged_path, 'w', encoding='utf-8') as f:
        json.dump(flagged_items, f, indent=2, ensure_ascii=False)

    print(f"Resolved: '{item['subject'][:40]}...' -> {correct_firm_name}")


def classify_email_with_gpt(
    client: OpenAI,
    email_metadata: dict,
    existing_firms: list = None,  # Kept for API compatibility, not used in agentic workflow
    attachment_names: list = None
) -> dict:
    """
    Use GPT to classify an email, then use agentic workflow for firm identification.

    This function uses a two-phase approach:
    1. Quick classification to determine if email is hedge fund related
    2. Agentic workflow (Finding Agent + Grading Agent) for firm name identification

    Args:
        client: OpenAI client
        email_metadata: Email metadata dict
        existing_firms: (Unused) Kept for backward compatibility
        attachment_names: Optional list of attachment names

    Returns:
    {
        "is_hedge_fund_related": bool,
        "confidence": float,
        "firm_name": str,
        "firm_name_source": str,  # "email_address", "email_content", "attachment", "subject", "agentic_lookup"
        "reasoning": str,
        "email_type": str,
        "is_third_party": bool,
        "agentic_result": dict  # Full agentic workflow result
    }
    """
    _ = existing_firms  # Explicitly mark as unused
    # Prepare context
    subject = email_metadata.get("subject", "")
    from_info = email_metadata.get("from", {}).get("emailAddress", {})
    from_name = from_info.get("name", "")
    from_address = from_info.get("address", "")
    body_preview = email_metadata.get("bodyPreview", "")[:1000]  # Limit body size

    attachments = email_metadata.get("attachments", [])
    attachment_names = attachment_names or [a.get("name", "") for a in attachments]

    # Extract email domain
    email_domain = from_address.split('@')[1] if '@' in from_address else ""

    # ========================================
    # PHASE 1: Quick Classification
    # ========================================
    classification_prompt = """You are an expert at identifying hedge fund and investment fund related emails.

Determine if this email is hedge fund/investment fund related and classify its type.

THIRD-PARTY INTERMEDIARIES (cap intro, fund admin, prime brokers):
- IConnections, With Intelligence, Agecroft Partners, Park Hill Group, Eaton Partners
- CITCO, Apex Group, SS&C Technologies, NAV Consulting, Trident Trust
- Goldman Sachs, Morgan Stanley, JPMorgan, UBS, Credit Suisse, Barclays (when doing cap intro)
- Preqin, eVestment, Bloomberg, PivotalPath, HFR

HEDGE FUND RELATED:
- Monthly/quarterly performance updates
- Investor letters and newsletters
- Factsheets, NAV estimates
- Fund marketing materials
- Due diligence materials
- Cap intro presentations

NOT HEDGE FUND RELATED:
- General broker marketing (unless about specific funds)
- Personal emails, IT notifications
- General news without fund content

Return JSON with: is_hedge_fund_related, confidence (0-1), is_third_party, email_type"""

    user_prompt = f"""Classify this email:

Subject: {subject}
From: {from_name} <{from_address}>
Body Preview: {body_preview[:500]}
Attachments: {', '.join(attachment_names) if attachment_names else 'None'}

Return JSON:
- is_hedge_fund_related: boolean
- confidence: float (0-1)
- is_third_party: boolean
- email_type: one of "newsletter", "monthly_update", "factsheet", "admin", "marketing", "meeting_invite", "cap_intro", "other"
"""

    try:
        # Phase 1: Quick classification
        response = client.chat.completions.create(
            model="gpt-4o-mini",  # Using smaller model for quick classification
            messages=[
                {"role": "system", "content": classification_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"},
        )

        classification = json.loads(response.choices[0].message.content)

        # If not hedge fund related, return early
        if not classification.get("is_hedge_fund_related", False):
            return {
                "is_hedge_fund_related": False,
                "confidence": classification.get("confidence", 0.0),
                "is_third_party": classification.get("is_third_party", False),
                "firm_name": "",
                "firm_name_source": "not_applicable",
                "reasoning": "Email not identified as hedge fund related",
                "email_type": classification.get("email_type", "other"),
                "agentic_result": None
            }

        # ========================================
        # PHASE 2: Agentic Firm Name Lookup
        # ========================================
        print(f"    [Phase 2] Starting agentic firm name lookup...")

        agentic_result = agentic_firm_lookup(
            client=client,
            email_domain=email_domain,
            sender_name=from_name,
            email_subject=subject,
            body_preview=body_preview,
            attachment_names=attachment_names,
            initial_threshold=90,
            threshold_decrement=5,
            minimum_threshold=70,
            max_attempts=10
        )

        # Build the final result
        firm_name = agentic_result.firm_name
        reasoning = f"Finding Agent: {agentic_result.finding_reasoning}\n\nGrading Agent: {agentic_result.grading_reasoning}"

        if agentic_result.flagged:
            reasoning = f"[FLAGGED - LOW CONFIDENCE]\n{reasoning}"

        return {
            "is_hedge_fund_related": True,
            "confidence": agentic_result.confidence_score / 100.0,  # Convert to 0-1 scale
            "is_third_party": classification.get("is_third_party", False),
            "firm_name": firm_name,
            "firm_name_source": "agentic_lookup",
            "reasoning": reasoning,
            "email_type": classification.get("email_type", "other"),
            "agentic_result": agentic_result.to_dict()
        }

    except Exception as e:
        print(f"GPT classification error: {e}")
        return {
            "is_hedge_fund_related": False,
            "confidence": 0.0,
            "is_third_party": False,
            "firm_name": "",
            "firm_name_source": "error",
            "reasoning": f"Classification error: {str(e)}",
            "email_type": "other",
            "agentic_result": None
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
        "flagged_low_confidence": 0,
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

            # Check if result was flagged by agentic workflow
            agentic_result = classification.get("agentic_result")
            if agentic_result and agentic_result.get("flagged"):
                report["flagged_low_confidence"] += 1
                if not dry_run:
                    save_flagged_item(
                        output_dir=output_dir,
                        email_folder=email_folder.name,
                        subject=subject,
                        from_address=from_address,
                        agentic_result=agentic_result
                    )

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
    if report['flagged_low_confidence'] > 0:
        print(f"Flagged (low confidence): {report['flagged_low_confidence']} - see {FLAGGED_ITEMS_FILE}")
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
    print("HEDGE FUND EMAIL CLASSIFIER (AGENTIC)")
    print("=" * 60)
    print("\nThis version uses an agentic workflow for firm identification:")
    print("  - Finding Agent: Searches for official firm name")
    print("  - Grading Agent: Evaluates confidence (threshold: 90% -> 70%)")
    print("  - Low confidence results are flagged for manual review")
    print()
    print("Select mode:")
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
    print(" 11. List flagged items (low confidence)")
    print(" 12. Resolve flagged item")
    print()

    if len(sys.argv) > 1:
        mode = sys.argv[1]
    else:
        mode = input("Enter mode (1-12): ").strip()

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
    elif mode == "11":
        list_flagged_items()
    elif mode == "12":
        list_flagged_items()
        print()
        idx = input("Enter item number to resolve (or 'q' to cancel): ").strip()
        if idx.lower() != 'q' and idx.isdigit():
            firm = input("Enter correct firm name: ").strip()
            if firm:
                resolve_flagged_item(int(idx), firm)
            else:
                print("Firm name is required.")
    else:
        print("Invalid mode.")


if __name__ == "__main__":
    main()
