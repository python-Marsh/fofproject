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
from html import unescape
from datetime import datetime
from typing import Optional  # noqa: F401 - kept for potential future use
from openai import OpenAI
from urllib.parse import urlparse, urljoin, parse_qsl, urlencode

# Load .env from the same directory as this script
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

# =========================
# CONFIGURATION
# =========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Default paths (can be overridden) — resolved relative to project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_EMAIL_INPUT_DIR = _PROJECT_ROOT / "output" / "testing" / "email"
DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "output" / "testing" / "fund firm identifier"

# File names for persistent data
FIRM_MAPPINGS_FILE = "firm_fund_mappings.json"  # Human-editable mappings
CLASSIFICATION_CACHE_FILE = "classification_cache.json"  # Cache of GPT classifications
CLASSIFICATION_REPORT_FILE = (
    "classification_report.json"  # Full report of all classifications
)

_GENERIC_FIRM_WORDS = frozenset(
    {
        "capital",
        "management",
        "advisors",
        "advisory",
        "partners",
        "investments",
        "investment",
        "asset",
        "assets",
        "fund",
        "funds",
        "group",
        "holdings",
        "financial",
        "securities",
        "global",
        "international",
        "associates",
        "ventures",
        "strategies",
        "strategy",
        "research",
        "solutions",
    }
)


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
        with open(mappings_path, "r", encoding="utf-8") as f:
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
            "description": "Human-editable firm name mappings. Use email_overrides for specific addresses, domain_overrides for entire domains, and folder_reassignments to move all emails from one firm folder to another.",
        },
    }


def save_firm_mappings(mappings: dict, output_dir: Path):
    """Save firm name mappings to file."""
    mappings_path = output_dir / FIRM_MAPPINGS_FILE
    mappings["_metadata"]["last_updated"] = datetime.now().isoformat()

    with open(mappings_path, "w", encoding="utf-8") as f:
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

    name = unicodedata.normalize("NFKD", name)

    # Remove invalid Windows folder characters
    name = re.sub(r'[<>:"/\\|?*]', "", name)

    # Remove control characters
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)

    # Collapse multiple spaces and normalize whitespace
    name = re.sub(r"\s+", " ", name).strip()

    # Remove leading/trailing dots and spaces (Windows restriction)
    name = name.strip(". ")

    # Handle Windows reserved names
    reserved_names = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }
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
    if "@" in email_lower:
        domain = email_lower.split("@")[1]
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
    cleaned = re.sub(
        r"\s*(Ltd\.?|Limited|LLC|Inc\.?|LP|LLP|Co\.?|Corporation|Corp\.?)\s*$",
        "",
        name,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned_upper = cleaned.upper() if cleaned else name_upper

    # Substring match against existing canonicals and aliases.
    # Skip only if ALL words are generic financial terms (e.g., "Capital Management").
    cleaned_words = cleaned_upper.split()
    all_generic = all(w.lower() in _GENERIC_FIRM_WORDS for w in cleaned_words)

    if not all_generic:
        best_match = None
        best_overlap = 0
        for canonical, info in mappings.get("canonical_names", {}).items():
            canonical_upper = canonical.upper()
            if cleaned_upper in canonical_upper or canonical_upper in cleaned_upper:
                overlap = min(len(cleaned_upper), len(canonical_upper))
                if overlap > best_overlap or (
                    overlap == best_overlap
                    and best_match is not None
                    and len(canonical) > len(best_match)
                ):
                    best_overlap = overlap
                    best_match = canonical
            for alias in info.get("aliases", []):
                alias_upper = alias.upper()
                if cleaned_upper in alias_upper or alias_upper in cleaned_upper:
                    overlap = min(len(cleaned_upper), len(alias_upper))
                    if overlap > best_overlap or (
                        overlap == best_overlap
                        and best_match is not None
                        and len(canonical) > len(best_match)
                    ):
                        best_overlap = overlap
                        best_match = canonical
        if best_match:
            return best_match

    return cleaned_upper


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
            "auto_added": datetime.now().isoformat(),
        }

    # Add new aliases
    existing_aliases = set(
        a.lower() for a in mappings["canonical_names"][canonical]["aliases"]
    )
    for alias in aliases:
        if alias.lower() not in existing_aliases:
            mappings["canonical_names"][canonical]["aliases"].append(alias)

    return canonical


def extract_domain_hints(email_address: str) -> list:
    """Extract potential firm name hints from email domain."""
    if not email_address or "@" not in email_address:
        return []

    domain = email_address.split("@")[1].lower()

    # Remove common TLDs and extract meaningful parts
    domain_parts = (
        domain.replace(".com", "")
        .replace(".hk", "")
        .replace(".sg", "")
        .replace(".jp", "")
    )
    domain_parts = (
        domain_parts.replace(".co", "").replace(".net", "").replace(".org", "")
    )

    hints = []

    # Split by common separators
    parts = re.split(r"[-_.]", domain_parts)
    for part in parts:
        if len(part) > 2 and part not in [
            "mail",
            "email",
            "info",
            "contact",
            "admin",
            "www",
        ]:
            hints.append(part)

    return hints


def generate_fund_id(fund_name: str) -> str:
    """
    Generate a stable snake_case fund ID from display name.
    Example: "Albemarle Shipping Fund" -> "fund_albemarle_shipping"
    """
    if not fund_name:
        return "fund_unknown"
    # Remove common suffixes before generating ID
    cleaned = re.sub(
        r"\s*(Fund|Strategy|Master|Feeder|Portfolio|Class\s*\w+|SP|Ltd\.?|Limited)\s*$",
        "",
        fund_name,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", "", cleaned)
    parts = cleaned.lower().split()
    return "fund_" + "_".join(p for p in parts if p)


def add_fund_to_firm(
    firm_name: str, fund_display_name: str, aliases: list, mappings: dict
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
        existing_aliases = set(
            a.lower() for a in firm_entry["funds"][fund_id].get("aliases", [])
        )
        for alias in aliases:
            if (
                alias.lower() not in existing_aliases
                and alias.lower()
                != firm_entry["funds"][fund_id]["display_name"].lower()
            ):
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
        "auto_added": datetime.now().isoformat(),
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


def _resolve_from_numbered_list(user_input: str, items: list) -> str | None:
    """
    Resolve user input against a numbered list of items.

    If user_input is a valid integer index (1-based), return the item at that index.
    Otherwise return user_input as-is for name-based matching.
    Returns None if index is out of range.
    """
    if user_input.isdigit():
        idx = int(user_input)
        if 1 <= idx <= len(items):
            return items[idx - 1]
        return None
    return user_input


def _interactive_firm_picker(
    prompt: str = "Select a firm",
    output_dir: Path = None,
    allow_new: bool = False,
    page_size: int = 15,
) -> str | None:
    """
    Interactive paginated firm picker with search.

    Shows firms in pages of `page_size`. The user can:
      - Enter a number to select a firm from the current page
      - Enter 'n' / 'p' to go to the next / previous page
      - Enter 's' to search/filter firms by keyword
      - Enter 'a' to show all (reset search filter)
      - Type a firm name directly
      - If allow_new, type a name that doesn't exist to use it as-is

    Returns the selected firm name, or None if cancelled.
    """
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    mappings = load_firm_mappings(output_dir)
    all_firms = sorted(mappings.get("canonical_names", {}).keys())

    if not all_firms and not allow_new:
        print("No firms found in mappings.")
        return None

    filtered = all_firms
    page = 0
    search_term = ""

    while True:
        total = len(filtered)
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages - 1)
        start = page * page_size
        end = min(start + page_size, total)
        page_items = filtered[start:end]

        print(f"\n--- {prompt} ---")
        if search_term:
            print(
                f'  Filter: "{search_term}" ({total} match{"es" if total != 1 else ""})'
            )
        else:
            print(f"  {total} firm(s) total")
        print(f"  Page {page + 1}/{total_pages}")
        print()

        for i, firm in enumerate(page_items, start + 1):
            print(f"  {i:>3}. {firm}")

        print()
        hints = ["#=select", "n=next", "p=prev", "s=search", "a=all", "q=cancel"]
        if allow_new:
            hints.append("or type a new name")
        print(f"  [{' | '.join(hints)}]")

        raw = input("> ").strip()
        if not raw:
            continue

        if raw.lower() == "q":
            return None
        if raw.lower() == "n":
            if page < total_pages - 1:
                page += 1
            else:
                print("Already on last page.")
            continue
        if raw.lower() == "p":
            if page > 0:
                page -= 1
            else:
                print("Already on first page.")
            continue
        if raw.lower() == "a":
            filtered = all_firms
            search_term = ""
            page = 0
            continue
        if raw.lower() == "s":
            kw = input("Search keyword: ").strip()
            if kw:
                search_term = kw
                filtered = [f for f in all_firms if kw.lower() in f.lower()]
                page = 0
                if not filtered:
                    print(f'No firms matching "{kw}". Showing all.')
                    filtered = all_firms
                    search_term = ""
            continue

        # Try index selection (against the full filtered list, not just page)
        resolved = _resolve_from_numbered_list(raw, filtered)
        if resolved is not None and resolved in mappings.get("canonical_names", {}):
            return resolved

        # Try name match (case-insensitive)
        for firm in all_firms:
            if raw.lower() == firm.lower():
                return firm

        if allow_new:
            return raw

        print(f"  '{raw}' not found. Try a number, search, or exact name.")


ASSIGNMENT_CONFIDENCE_THRESHOLD = 0.75
FIRM_RECOVERY_CONFIDENCE_FLOOR = 0.50
NEEDS_REVIEW_FOLDER = "_NEEDS_REVIEW"
REASON_CODE_OTHER = "other"


def _strip_html_tags(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"<[^>]+>", " ", text)


def _truncate_text(text: str, max_len: int = 5000) -> str:
    if not text:
        return ""
    return text if len(text) <= max_len else text[:max_len]


def normalize_url(url: str, base_url: str = "") -> str:
    """Normalize a URL for deduplication and classification context."""
    if not url:
        return ""
    raw = unescape(url).strip()
    if not raw:
        return ""

    if raw.startswith("//"):
        raw = "https:" + raw

    normalized = urljoin(base_url, raw) if base_url else raw
    parsed = urlparse(normalized)
    if parsed.scheme.lower() not in {"http", "https"}:
        return ""
    if not parsed.netloc:
        return ""

    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    path = parsed.path or "/"
    path = re.sub(r"/{2,}", "/", path)
    if path != "/" and path.endswith("/"):
        path = path[:-1]

    tracking_params = {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "gclid",
        "fbclid",
        "mc_cid",
        "mc_eid",
        "mkt_tok",
        "trk",
        "spm",
        "ref",
    }
    cleaned_params = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in tracking_params:
            continue
        cleaned_params.append((key, value))
    query = urlencode(cleaned_params, doseq=True)

    return f"{parsed.scheme.lower()}://{netloc}{path}" + (f"?{query}" if query else "")


def _is_non_content_link(url: str, context_text: str = "") -> bool:
    """Filter obvious non-content links before sending to GPT.

    Token and domain checks run against the URL only (not the surrounding
    email text) to avoid false positives from words like "tracking" or
    "privacy" that appear in legitimate finance emails.
    """
    url_lower = url.lower()
    if any(url_lower.startswith(p) for p in ("mailto:", "cid:", "javascript:", "tel:")):
        return True
    if any(
        token in url_lower
        for token in (
            "unsubscribe",
            "optout",
            "opt-out",
            "privacy-policy",
            "pixel",
            "tracking",
            "track.me",
            "openrate",
        )
    ):
        return True
    parsed_host = urlparse(url_lower).netloc
    social_domains = (
        "linkedin.com",
        "twitter.com",
        "x.com",
        "facebook.com",
        "instagram.com",
        "youtube.com",
    )
    if any(parsed_host.endswith(domain) for domain in social_domains):
        return True
    # Filter image/asset URLs that are never fund documents
    if re.search(r"\.(png|gif|jpg|jpeg|ico|svg|woff2?|css|js)(\?|$)", url_lower):
        return True
    return False


def _looks_like_homepage(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.path in ("", "/") and not parsed.query


def _build_link_context(
    subject: str, body_preview: str, anchor_text: str, url: str
) -> str:
    parsed = urlparse(url)
    path_hint = parsed.path.strip("/")[:80]
    return " | ".join(
        p
        for p in [
            f"subject={subject}",
            f"body_preview={body_preview}",
            f"anchor={anchor_text}",
            f"host={parsed.netloc.lower()}",
            f"path_hint={path_hint}",
        ]
        if p
    )


def extract_links_with_filter_log(
    metadata: dict, email_id: str = ""
) -> tuple[list, list]:
    """Extract and dedupe links from email HTML body and text previews.

    Returns (links, filter_log). filter_log is transient (not persisted) and
    documents which links were filtered out and why.

    Each filter_log entry: {"raw_url", "normalized_url", "reason", "anchor_text"}
    Reasons: "invalid_url", "non_content_link", "homepage_filtered", "duplicate"
    """
    subject = metadata.get("subject", "") or ""
    body_preview = metadata.get("bodyPreview", "") or ""
    body = metadata.get("body", {}) or {}
    body_html = (
        body.get("content", "")
        if body.get("contentType", "html").lower() == "html"
        else ""
    )
    body_text = _strip_html_tags(body_html)

    # Extract <base href="..."> so relative URLs can be resolved
    base_url = ""
    if body_html:
        base_match = re.search(
            r'<base[^>]+href\s*=\s*["\']([^"\']+)["\']', body_html, re.IGNORECASE
        )
        if base_match:
            base_url = base_match.group(1)

    raw_candidates = []

    anchor_pattern = re.compile(
        r"<a[^>]+href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for href, anchor_html in anchor_pattern.findall(body_html):
        anchor_text = re.sub(
            r"\s+", " ", _strip_html_tags(unescape(anchor_html))
        ).strip()
        raw_candidates.append(
            {
                "raw_url": href,
                "anchor_text": anchor_text,
                "source": "html_anchor",
            }
        )

    plain_text = "\n".join([body_text, body_preview])
    url_pattern = re.compile(r"(https?://[^\s<>\"]+)")
    for match in url_pattern.findall(plain_text):
        raw_candidates.append(
            {
                "raw_url": match,
                "anchor_text": "",
                "source": "plain_text",
            }
        )

    seen = set()
    links = []
    filter_log = []
    link_idx = 0
    for candidate in raw_candidates:
        raw_url = candidate.get("raw_url", "")
        anchor_text = candidate.get("anchor_text", "")
        normalized = normalize_url(raw_url, base_url)
        if not normalized:
            filter_log.append(
                {
                    "raw_url": raw_url,
                    "normalized_url": "",
                    "reason": "invalid_url",
                    "anchor_text": anchor_text,
                }
            )
            continue

        context = _build_link_context(
            subject=subject,
            body_preview=body_preview[:220],
            anchor_text=anchor_text,
            url=normalized,
        )
        if _is_non_content_link(normalized, context):
            filter_log.append(
                {
                    "raw_url": raw_url,
                    "normalized_url": normalized,
                    "reason": "non_content_link",
                    "anchor_text": anchor_text,
                }
            )
            continue
        if (
            _looks_like_homepage(normalized)
            and "fund" not in context.lower()
            and "investor" not in context.lower()
        ):
            filter_log.append(
                {
                    "raw_url": raw_url,
                    "normalized_url": normalized,
                    "reason": "homepage_filtered",
                    "anchor_text": anchor_text,
                }
            )
            continue
        if normalized in seen:
            filter_log.append(
                {
                    "raw_url": raw_url,
                    "normalized_url": normalized,
                    "reason": "duplicate",
                    "anchor_text": anchor_text,
                }
            )
            continue
        seen.add(normalized)

        link_idx += 1
        prefix = f"{email_id[-8:]}:" if email_id else ""
        links.append(
            {
                "artifact_id": f"{prefix}link:{link_idx}",
                "url": normalized,
                "anchor_text": anchor_text,
                "description_context": context,
                "source": candidate.get("source", "plain_text"),
            }
        )

    return links, filter_log


def extract_attachment_candidates(metadata: dict, email_id: str = "") -> list:
    """Extract attachment candidates and contextual descriptions for GPT."""
    attachments = metadata.get("attachments", []) or []
    subject = metadata.get("subject", "") or ""
    body_preview = metadata.get("bodyPreview", "") or ""

    prefix = f"{email_id[-8:]}:" if email_id else ""
    candidates = []
    for idx, att in enumerate(attachments, 1):
        filename = att.get("name", "")
        mime_type = att.get("contentType", "")
        is_inline = bool(att.get("isInline", False))

        candidates.append(
            {
                "artifact_id": f"{prefix}att:{idx}",
                "filename": filename,
                "mime_type": mime_type,
                "size": att.get("size", 0),
                "is_inline": is_inline,
                "description_context": " | ".join(
                    p
                    for p in [
                        f"subject={subject}",
                        f"body_preview={body_preview[:220]}",
                        f"filename={filename}",
                        f"mime_type={mime_type}",
                    ]
                    if p
                ),
            }
        )

    return candidates


def build_artifact_candidates(
    metadata: dict, include_filter_log: bool = False, email_id: str = ""
) -> dict:
    """Build deterministic artifact candidates from email metadata.

    When include_filter_log=True, also returns _link_filter_log (transient,
    not persisted to cache) documenting links filtered out at the Python level.
    """
    links, filter_log = extract_links_with_filter_log(metadata, email_id=email_id)
    result = {
        "attachments": extract_attachment_candidates(metadata, email_id=email_id),
        "links": links,
    }
    if include_filter_log:
        result["_link_filter_log"] = filter_log
    return result


def _contains_monthly_net_update(filename: str) -> bool:
    lower = (filename or "").lower()
    return bool(
        re.search(
            r"(monthly|mtd|month[-_ ]end|factsheet|tear[-_ ]?sheet|net|nav|performance)",
            lower,
        )
    )


def _blank_attachment_artifact() -> dict:
    """Canonical field set for an attachment artifact. Single source of truth."""
    return {
        "artifact_id": "",
        "filename": "",
        "mime_type": "",
        "assigned_firm_name": "",
        "assigned_fund_name": "",
        "confidence": 0.0,
        "method": "",
        "evidence": "",
        "reason_code": REASON_CODE_OTHER,
        "contains_monthly_net_performance_update": False,
        "_original_firm_name": "",
        "_original_fund_name": "",
        "firm_recovery_method": "",
    }


def _blank_link_artifact() -> dict:
    """Canonical field set for a link artifact. Single source of truth."""
    return {
        "artifact_id": "",
        "url": "",
        "description": "",
        "link_type": "other",
        "assigned_firm_name": "",
        "assigned_fund_name": "",
        "confidence": 0.0,
        "method": "",
        "evidence": "",
        "reason_code": REASON_CODE_OTHER,
        "_original_firm_name": "",
        "_original_fund_name": "",
        "firm_recovery_method": "",
    }


def _condense_skipped_artifact(artifact: dict) -> dict:
    """Condense a non-HF skipped/omitted artifact for cache storage.

    Returns a minimal dict with only the fields needed for audit/debugging.
    Full artifacts in included_* lists are NOT condensed.
    """
    base = {
        "artifact_id": artifact.get("artifact_id", ""),
        "reason": artifact.get("reason_code", REASON_CODE_OTHER),
        "method": artifact.get("method", ""),
        "evidence": artifact.get("evidence", ""),
    }
    if artifact.get("filename"):
        base["filename"] = artifact.get("filename", "")
    else:
        base["url"] = artifact.get("url", "")
    return base


def _make_override_artifact_assignments(
    candidates: dict, override_firm: str, override_detail: str
) -> dict:
    """Build artifact assignments for override-path emails with full documentation."""
    evidence = f"Assigned via override rule for {override_detail}"

    included_attachments = []
    for att in candidates.get("attachments", []):
        payload = _blank_attachment_artifact()
        payload.update(
            {
                "artifact_id": att.get("artifact_id", ""),
                "filename": att.get("filename", ""),
                "mime_type": att.get("mime_type", ""),
                "assigned_firm_name": override_firm,
                "confidence": 1.0,
                "method": "override",
                "evidence": evidence,
                "reason_code": "fund_document",
                "_original_firm_name": override_firm,
                "contains_monthly_net_performance_update": _contains_monthly_net_update(
                    att.get("filename", "")
                ),
            }
        )
        included_attachments.append(payload)

    included_links = []
    for link in candidates.get("links", []):
        payload = _blank_link_artifact()
        payload.update(
            {
                "artifact_id": link.get("artifact_id", ""),
                "url": link.get("url", ""),
                "description": link.get("description_context", ""),
                "assigned_firm_name": override_firm,
                "confidence": 1.0,
                "method": "override",
                "evidence": evidence,
                "reason_code": "fund_document",
                "_original_firm_name": override_firm,
            }
        )
        included_links.append(payload)

    total_att = len(candidates.get("attachments", []))
    total_links = len(candidates.get("links", []))
    return {
        "included_attachments": included_attachments,
        "included_links": included_links,
        "skipped_attachments": [],
        "skipped_links": [],
        "summary": {
            "total_attachments": total_att,
            "total_links": total_links,
            "included_count": len(included_attachments) + len(included_links),
            "skipped_count": 0,
        },
    }


def _make_empty_artifact_assignments(
    total_attachments: int = 0, total_links: int = 0
) -> dict:
    return {
        "included_attachments": [],
        "included_links": [],
        "skipped_attachments": [],
        "skipped_links": [],
        "summary": {
            "total_attachments": total_attachments,
            "total_links": total_links,
            "included_count": 0,
            "skipped_count": 0,
        },
    }


def _is_assignment_uncertain(confidence: float, evidence: str) -> bool:
    evidence_lower = (evidence or "").strip().lower()
    weak_tokens = (
        "unclear",
        "ambiguous",
        "guess",
        "not sure",
        "insufficient",
        "unknown",
    )
    return (
        confidence < ASSIGNMENT_CONFIDENCE_THRESHOLD
        or not evidence_lower
        or any(t in evidence_lower for t in weak_tokens)
    )


def _extract_firm_from_third_party(tp_string: str, firm_mappings: dict) -> str:
    """
    Extract a firm name from a from_third_party string.

    Examples:
        "Rose Mac Cullagh (WMC Capital Ltd)" -> "WMC CAPITAL"
        "investlife.com (LIFE)" -> "LIFE"
    """
    if not tp_string or not firm_mappings:
        return ""

    canonical_names = firm_mappings.get("canonical_names", {})

    # Try parenthetical content first: "Person (Firm Name Ltd)"
    candidates = []
    paren_match = re.search(r"\(([^)]+)\)", tp_string)
    if paren_match:
        candidates.append(paren_match.group(1).strip())
    candidates.append(tp_string.strip())

    for candidate in candidates:
        candidate_lower = candidate.lower()
        for canonical, info in canonical_names.items():
            if (
                canonical.lower() in candidate_lower
                or candidate_lower in canonical.lower()
            ):
                return canonical
            for alias in info.get("aliases", []):
                if alias.lower() in candidate_lower or candidate_lower in alias.lower():
                    return canonical

    # Fallback: normalize the parenthetical content
    if paren_match:
        normalized = normalize_firm_name(paren_match.group(1).strip(), firm_mappings)
        if normalized and normalized != "UNKNOWN":
            return normalized

    return ""


def _recover_firm_name(
    artifact: dict,
    all_artifacts: list,
    email_metadata: dict,
    firm_mappings: dict,
) -> str:
    """
    Attempt to recover a firm name for an artifact whose name was blanked by uncertainty.

    Recovery chain:
    1. Sibling artifacts from the same email that have a firm name
    2. from_third_party field on the email classification
    3. Sender domain against domain_overrides
    4. Sender domain hints matched against canonical names
    5. Artifact evidence/description scanned for known firm names
    """
    if not firm_mappings:
        return ""

    # Strategy 1: Sibling artifact with firm name
    for sibling in all_artifacts:
        if sibling is artifact:
            continue
        sibling_firm = sibling.get("assigned_firm_name", "")
        if sibling_firm:
            return sibling_firm

    # Strategy 2: from_third_party field
    from_tp = email_metadata.get("_from_third_party", False)
    if from_tp and isinstance(from_tp, str):
        firm_from_tp = _extract_firm_from_third_party(from_tp, firm_mappings)
        if firm_from_tp:
            return firm_from_tp

    # Strategy 3: Domain overrides
    sender = (
        email_metadata.get("from", {}).get("emailAddress", {}).get("address", "") or ""
    ).lower()
    if sender and "@" in sender:
        domain = sender.split("@")[1]
        domain_overrides = firm_mappings.get("domain_overrides", {})
        for override_domain, firm in domain_overrides.items():
            if domain == override_domain.lower():
                return firm

    # Strategy 4: Domain hints matched against canonical names
    hints = extract_domain_hints(sender)
    canonical_names = firm_mappings.get("canonical_names", {})
    for hint in hints:
        hint_lower = hint.lower()
        for canonical, info in canonical_names.items():
            if hint_lower in canonical.lower():
                return canonical
            for alias in info.get("aliases", []):
                if hint_lower in alias.lower():
                    return canonical

    # Strategy 5: Scan evidence/description for known firm names
    searchable_text = " ".join(
        [
            artifact.get("evidence", ""),
            artifact.get("description", ""),
            artifact.get("description_context", ""),
            artifact.get("filename", ""),
        ]
    ).lower()
    if searchable_text.strip():
        for canonical, info in canonical_names.items():
            if canonical.lower() in searchable_text:
                return canonical
            for alias in info.get("aliases", []):
                if len(alias) > 3 and alias.lower() in searchable_text:
                    return canonical

    return ""


def _web_search_firm_for_fund(fund_name: str, client: OpenAI = None) -> str:
    """
    Use OpenAI web search to discover which firm manages a given fund.

    Called when a fund name is known but the firm name could not be resolved
    through local recovery strategies (sibling artifacts, domain hints, etc.).

    Returns the firm name (uppercase) if found, empty string otherwise.
    """
    if not fund_name or not fund_name.strip():
        return ""

    if client is None:
        try:
            client = get_openai_client()
        except ValueError:
            return ""

    prompt = (
        f"What is the name of the investment management firm or asset manager "
        f'that manages the fund called "{fund_name}"? '
        f"Reply with ONLY the firm name, nothing else. "
        f'If you cannot determine the firm, reply with exactly "UNKNOWN".'
    )

    try:
        response = client.responses.create(
            model="gpt-4o-mini",
            tools=[{"type": "web_search"}],
            input=[{"role": "user", "content": prompt}],
        )
        result_text = response.output[-1].content[0].text.strip()

        if not result_text or result_text.upper() == "UNKNOWN":
            return ""

        # Clean up the result — remove trailing punctuation, quotes, periods
        result_text = result_text.strip(".\"'")
        # Take only the first line if multi-line
        result_text = result_text.split("\n")[0].strip()

        if len(result_text) > 100 or len(result_text) < 2:
            return ""

        return result_text.upper()
    except Exception as e:
        print(f"Web search for firm failed (fund='{fund_name}'): {e}")
        return ""


def _default_classification(reason: str = "") -> dict:
    return {
        "email_classification": {
            "is_hedge_fund_related": False,
            "confidence": 0.0,
            "email_type": "Other",
            "from_third_party": False,
            "source_priority": "lowest",
            "reasoning": reason,
        },
        "firm_name": "",
        "artifact_assignments": _make_empty_artifact_assignments(),
    }


def _finalize_artifact_classification(
    raw_result: dict,
    candidates: dict,
    firm_mappings: dict = None,
    email_metadata: dict = None,
) -> dict:
    candidate_map = {}
    for att in candidates.get("attachments", []):
        candidate_map[att["artifact_id"]] = att
    for link in candidates.get("links", []):
        candidate_map[link["artifact_id"]] = link

    included_attachments = []
    included_links = []
    skipped_attachments = []
    skipped_links = []

    raw_artifacts = raw_result.get("artifacts", [])
    for record in raw_artifacts:
        artifact_id = str(record.get("artifact_id", "")).strip()
        if artifact_id not in candidate_map:
            continue

        source = candidate_map[artifact_id]
        is_attachment = "filename" in source
        confidence = float(record.get("confidence", 0.0) or 0.0)
        evidence = record.get("evidence", "") or ""
        method = record.get("method", "email_context") or "email_context"
        reason_code = record.get("reason_code", REASON_CODE_OTHER) or REASON_CODE_OTHER
        is_related = bool(record.get("is_hedge_fund_related", False))

        firm_name = (record.get("assigned_firm_name", "") or "").strip()
        fund_name = (record.get("assigned_fund_name", "") or "").strip()
        original_firm_name = firm_name
        original_fund_name = fund_name
        if _is_assignment_uncertain(confidence, evidence):
            firm_name = ""
            fund_name = ""

        if is_attachment:
            payload = _blank_attachment_artifact()
            payload.update(
                {
                    "artifact_id": artifact_id,
                    "filename": source.get("filename", ""),
                    "mime_type": source.get("mime_type", ""),
                    "assigned_firm_name": firm_name,
                    "assigned_fund_name": fund_name,
                    "confidence": confidence,
                    "method": method,
                    "evidence": evidence,
                    "reason_code": reason_code,
                    "contains_monthly_net_performance_update": bool(
                        record.get(
                            "contains_monthly_net_performance_update",
                            _contains_monthly_net_update(source.get("filename", "")),
                        )
                    ),
                    "_original_firm_name": original_firm_name,
                    "_original_fund_name": original_fund_name,
                }
            )
            if is_related:
                included_attachments.append(payload)
            else:
                skipped_attachments.append(payload)
        else:
            payload = _blank_link_artifact()
            payload.update(
                {
                    "artifact_id": artifact_id,
                    "url": source.get("url", ""),
                    "description": record.get(
                        "description", source.get("description_context", "")
                    ),
                    "link_type": record.get("link_type", "other"),
                    "assigned_firm_name": firm_name,
                    "assigned_fund_name": fund_name,
                    "confidence": confidence,
                    "method": method,
                    "evidence": evidence,
                    "reason_code": reason_code,
                    "_original_firm_name": original_firm_name,
                    "_original_fund_name": original_fund_name,
                }
            )
            if is_related:
                included_links.append(payload)
            else:
                skipped_links.append(payload)

    # --- Reconciliation pass: catch candidates the LLM omitted ---
    returned_ids = set()
    for record in raw_artifacts:
        returned_ids.add(str(record.get("artifact_id", "")).strip())

    for candidate_id, source in candidate_map.items():
        if candidate_id in returned_ids:
            continue
        is_attachment = "filename" in source
        if is_attachment:
            payload = _blank_attachment_artifact()
            payload.update(
                {
                    "artifact_id": candidate_id,
                    "filename": source.get("filename", ""),
                    "mime_type": source.get("mime_type", ""),
                    "confidence": 0.0,
                    "method": "llm_omitted",
                    "evidence": "LLM did not return this artifact; flagged for review",
                    "reason_code": REASON_CODE_OTHER,
                }
            )
            skipped_attachments.append(payload)
        else:
            payload = _blank_link_artifact()
            payload.update(
                {
                    "artifact_id": candidate_id,
                    "url": source.get("url", ""),
                    "description": source.get("description_context", ""),
                    "link_type": "other",
                    "confidence": 0.0,
                    "method": "llm_omitted",
                    "evidence": "LLM did not return this artifact; flagged for review",
                    "reason_code": REASON_CODE_OTHER,
                }
            )
            skipped_links.append(payload)

    # --- Third-party firm name correction ---
    # If GPT identified a third-party sender, blank any artifact firm names that
    # match the intermediary so they get recovered to the actual fund manager.
    raw_email_cls_for_recovery = raw_result.get("email_classification", {}) or {}
    tp_value = raw_email_cls_for_recovery.get("from_third_party", False)
    if tp_value and isinstance(tp_value, str):
        tp_lower = tp_value.lower()
        for item in included_attachments + included_links:
            firm = item.get("assigned_firm_name", "")
            if firm and (firm.lower() in tp_lower or tp_lower in firm.lower()):
                item["_original_firm_name"] = firm
                item["assigned_firm_name"] = ""

    # --- Firm name recovery pass ---
    # For included artifacts with blank firm names, attempt to recover using context
    if firm_mappings and email_metadata:
        recovery_metadata = dict(email_metadata) if email_metadata else {}
        recovery_metadata["_from_third_party"] = tp_value

        all_included = included_attachments + included_links
        for item in all_included:
            if item.get("assigned_firm_name"):
                continue
            if item.get("confidence", 0.0) < FIRM_RECOVERY_CONFIDENCE_FLOOR:
                continue

            recovered_firm = _recover_firm_name(
                artifact=item,
                all_artifacts=all_included,
                email_metadata=recovery_metadata,
                firm_mappings=firm_mappings,
            )
            if recovered_firm:
                canonical = normalize_firm_name(recovered_firm, firm_mappings)
                item["assigned_firm_name"] = canonical
                item["firm_recovery_method"] = "post_classification_recovery"
            elif item.get("assigned_fund_name"):
                # Fund name known but firm still missing — try web search
                web_firm = _web_search_firm_for_fund(item["assigned_fund_name"])
                if web_firm:
                    canonical = normalize_firm_name(web_firm, firm_mappings)
                    item["assigned_firm_name"] = canonical
                    item["firm_recovery_method"] = "web_search_from_fund_name"

    # --- Condense skipped artifacts for cache efficiency ---
    llm_omitted_count = len(
        [
            x
            for x in skipped_attachments + skipped_links
            if x.get("method") == "llm_omitted"
        ]
    )
    skipped_attachments = [_condense_skipped_artifact(a) for a in skipped_attachments]
    skipped_links = [_condense_skipped_artifact(lnk) for lnk in skipped_links]

    assignments = {
        "included_attachments": included_attachments,
        "included_links": included_links,
        "skipped_attachments": skipped_attachments,
        "skipped_links": skipped_links,
        "summary": {
            "total_attachments": len(candidates.get("attachments", [])),
            "total_links": len(candidates.get("links", [])),
            "included_count": len(included_attachments) + len(included_links),
            "skipped_count": len(skipped_attachments) + len(skipped_links),
            "llm_omitted_count": llm_omitted_count,
        },
    }

    if firm_mappings:
        for item in included_attachments + included_links:
            firm_name = item.get("assigned_firm_name", "")
            fund_name = item.get("assigned_fund_name", "")
            if not firm_name:
                continue
            canonical = normalize_firm_name(firm_name, firm_mappings)
            canonical = apply_folder_reassignment(canonical, firm_mappings)
            add_firm_to_mappings(canonical, [firm_name], firm_mappings)
            item["assigned_firm_name"] = canonical
            if fund_name:
                add_fund_to_firm(canonical, fund_name, [], firm_mappings)

    primary_firm_name = ""
    for item in included_attachments + included_links:
        if item.get("assigned_firm_name"):
            primary_firm_name = item["assigned_firm_name"]
            break

    raw_email_cls = raw_result.get("email_classification", {}) or {}
    included_count = assignments["summary"]["included_count"]
    is_hedge_related = included_count > 0
    confidence = max(
        [
            float(item.get("confidence", 0.0))
            for item in included_attachments + included_links
        ]
        or [0.0]
    )

    email_cls = {
        "is_hedge_fund_related": is_hedge_related,
        "confidence": confidence
        if is_hedge_related
        else float(raw_email_cls.get("confidence", 0.0) or 0.0),
        "email_type": raw_email_cls.get("email_type", "Other"),
        "from_third_party": raw_email_cls.get("from_third_party", False),
        "source_priority": "highest"
        if is_hedge_related
        else raw_email_cls.get("source_priority", "lowest"),
        "reasoning": raw_email_cls.get("reasoning", ""),
    }
    if is_hedge_related:
        email_cls["reasoning"] = (
            email_cls.get("reasoning", "")
            + f" Artifact-based inclusion count={included_count}."
        ).strip()

    return {
        "email_classification": email_cls,
        "firm_name": primary_firm_name,
        "artifact_assignments": assignments,
    }




def classify_email_with_gpt(
    client: OpenAI,
    email_metadata: dict,
    existing_firms: list,
    firm_mappings: dict = None,
) -> dict:
    """
    Artifact-first GPT classification for attachments and links with additive cache v2 output.
    """
    candidates = build_artifact_candidates(
        email_metadata,
        include_filter_log=True,
        email_id=email_metadata.get("id", ""),
    )
    attachments = candidates.get("attachments", [])
    links = candidates.get("links", [])
    filter_log = candidates.get("_link_filter_log", [])

    if filter_log:
        print(
            f"  [pre-filter] {len(filter_log)} link(s) filtered before LLM: "
            + ", ".join(f"{e['reason']}" for e in filter_log)
        )

    total_candidates = len(attachments) + len(links)

    system_prompt = """You are a hedge-fund communications classifier for a fund-of-funds investor.

DEFINITION — What counts as "hedge fund" for this system:
- Hedge funds of any strategy: long/short equity, global macro, event-driven, relative value, CTA/managed futures, multi-strategy, credit, distressed, quantitative, activist, market-neutral, arbitrage
- Fund-of-hedge-funds and multi-manager platforms
- Separately managed accounts (SMAs) run by hedge fund managers
- Capital introduction (cap intro) materials that present specific hedge fund managers
- UCITS funds that are hedge fund wrappers (e.g. Man AHL UCITS, Citadel UCITS)

NOT hedge-fund-related (set is_hedge_fund_related to false):
- Private equity, venture capital, private credit, and infrastructure funds
- Mutual funds, ETFs, index funds, retail UCITS (unless clearly a hedge fund wrapper)
- Insurance products, annuities, structured notes
- Bank research reports, sell-side equity research, general market commentary
- Broker/dealer newsletters, morning notes, weekly market outlooks
- Conference invitations not specifically about hedge fund manager presentations
- Regulatory filings, compliance circulars, industry association bulletins
- Technology vendor pitches (portfolio systems, risk tools, data providers)
- Prime brokerage operational notices (margin calls, trade confirmations, settlement notices)

DECISION FRAMEWORK for each artifact:
1. CONTENT TEST: Does the artifact contain or link to a specific hedge fund's performance data (NAV, returns, AUM), investor letter, factsheet, tear sheet, subscription/redemption document, or DDQ? If YES -> is_hedge_fund_related: true.
2. MANAGER TEST: Is the artifact from or about a specific hedge fund manager presenting their fund(s) to investors? If YES -> is_hedge_fund_related: true.
3. INTERMEDIARY TEST: Is the artifact from a cap intro platform, administrator, prime broker, or distributor forwarding content about a specific hedge fund? If YES -> is_hedge_fund_related: true (leave assigned_firm_name empty if you can only identify the intermediary).
4. GENERIC FINANCIAL TEST: Is this general financial content (market commentary, research, news, broker marketing) not about a specific hedge fund? If YES -> is_hedge_fund_related: false, reason_code: "non_fund_marketing".
5. OPERATIONAL TEST: Is this operational/system content (tracking, unsubscribe, calendar, notification)? If YES -> is_hedge_fund_related: false, reason_code: "tracking_or_system".

KEY PRINCIPLE: The test is whether the artifact concerns a SPECIFIC hedge fund or hedge fund manager, not whether the sender works in finance. An email from Goldman Sachs about their hedge fund IS hedge-fund-related. A Goldman Sachs morning market briefing IS NOT.

WHEN UNCERTAIN about is_hedge_fund_related:
- If the artifact looks like a fund document (factsheet, monthly report, investor update) but you cannot identify the fund, set is_hedge_fund_related: true with confidence reflecting your uncertainty. The downstream system has recovery mechanisms.
- If the artifact is from an unfamiliar or obscure hedge fund, still mark it true. Do not penalize unfamiliar names.
- If genuinely ambiguous whether it is a hedge fund vs. mutual fund/PE fund, set is_hedge_fund_related: true with lower confidence (0.5-0.6). It is better to include a borderline case than to miss a genuine hedge fund document.

FIRM NAME RULES:
- For assigned_firm_name, use the fund's investment management firm, NOT the sender or intermediary.
- If you can only identify the intermediary, leave assigned_firm_name empty.
- Do not guess. If uncertain, leave assigned_firm_name and assigned_fund_name empty.

OUTPUT RULES:
- You MUST return exactly one entry in the "artifacts" array for EVERY artifact candidate provided. Do not omit any.
- Classify each artifact independently.
- For non-hedge-fund artifacts, include them with is_hedge_fund_related: false, a reason_code, and brief evidence.
- Return strict JSON only."""

    email_summary = {
        "subject": email_metadata.get("subject", ""),
        "from": email_metadata.get("from", {})
        .get("emailAddress", {})
        .get("address", ""),
        "body_preview": email_metadata.get("bodyPreview", ""),
        "body_text": _truncate_text(
            _strip_html_tags((email_metadata.get("body", {}) or {}).get("content", "")),
            3000,
        ),
    }

    user_prompt = f"""Classify these email artifacts.

EMAIL_SUMMARY:
{json.dumps(email_summary, indent=2, ensure_ascii=False)}

ATTACHMENT_CANDIDATES:
{json.dumps(attachments, indent=2, ensure_ascii=False)}

LINK_CANDIDATES:
{json.dumps(links, indent=2, ensure_ascii=False)}

EXISTING_FIRMS:
{json.dumps(existing_firms if existing_firms else [], indent=2, ensure_ascii=False)}

IMPORTANT: You received {len(attachments)} attachment candidate(s) and {len(links)} link candidate(s). Your "artifacts" array MUST contain exactly {total_candidates} entries — one per candidate. If an artifact is not hedge-fund related, still include it with is_hedge_fund_related: false and a reason_code explaining why.

Return exactly this structure:
{{
  "email_classification": {{
    "email_type": "Monthly performance update" | "Quarterly performance update" | "Annual report" | "Investor letter / newsletter" | "Factsheet / tear sheet" | "Marketing / fundraising" | "Webinar / event invitation" | "Subscription / redemption" | "Due diligence" | "Cap intro" | "Other",
    "from_third_party": "Name" or false,
    "source_priority": "highest" | "medium" | "medium_low" | "lowest",
    "reasoning": "brief reason"
  }},
  "artifacts": [
    {{
      "artifact_id": "exact artifact id from candidates",
      "artifact_type": "attachment" | "link",
      "is_hedge_fund_related": true or false,
      "reason_code": "fund_document" | "investor_update" | "cap_intro_manager_material" | "tracking_or_system" | "non_fund_marketing" | "other",
      "assigned_firm_name": "string or empty",
      "assigned_fund_name": "string or empty",
      "confidence": 0.0,
      "method": "attachment_content" | "email_context" | "filename_match" | "link_context",
      "evidence": "brief evidence",
      "link_type": "performance_report" | "factsheet" | "investor_portal" | "presentation" | "nav_statement" | "due_diligence" | "webinar" | "other",
      "description": "optional, mainly for links",
      "contains_monthly_net_performance_update": true or false
    }}
  ]
}}
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
        raw_text = response.output[0].content[0].text
        raw_result = json.loads(raw_text)
        result = _finalize_artifact_classification(
            raw_result,
            candidates,
            firm_mappings=firm_mappings,
            email_metadata=email_metadata,
        )
        if filter_log:
            result["artifact_assignments"]["filter_log"] = filter_log
        return result
    except Exception as e:
        print(f"GPT classification error: {e}")
        result = _default_classification(reason=f"Classification error: {str(e)}")
        result["_error"] = True
        result["artifact_assignments"] = _make_empty_artifact_assignments(
            total_attachments=len(attachments), total_links=len(links)
        )
        return result


def load_classification_cache(output_dir: Path) -> dict:
    """Load cached classifications to avoid re-processing."""
    cache_path = output_dir / CLASSIFICATION_CACHE_FILE

    if cache_path.exists():
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)

    return {}


def save_classification_cache(cache: dict, output_dir: Path):
    """Save classification cache."""
    cache_path = output_dir / CLASSIFICATION_CACHE_FILE

    with open(cache_path, "w", encoding="utf-8") as f:
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


# --- Entity-based artifact organization ---


def _resolve_artifact_dest_dir(
    firm_name: str,
    fund_name: str,
    output_dir: Path,
    firm_mappings: dict,
) -> Path:
    """
    Resolve destination directory for an individual artifact.

    - Firm + fund → output_dir/FIRM/FUND/
    - Firm only  → output_dir/FIRM/
    - No firm    → output_dir/_NEEDS_REVIEW/
    """
    if firm_name:
        canonical = normalize_firm_name(firm_name, firm_mappings)
        canonical = apply_folder_reassignment(canonical, firm_mappings)
        safe_firm = sanitize_folder_name(canonical)
        if fund_name:
            safe_fund = sanitize_folder_name(fund_name)
            return output_dir / safe_firm / safe_fund
        else:
            return output_dir / safe_firm
    else:
        return output_dir / NEEDS_REVIEW_FOLDER


def _find_attachment_file(
    email_folder: Path,
    attachment_artifact: dict,
    email_metadata: dict,
) -> Path | None:
    """
    Locate the actual attachment file in the email folder.

    Checks: attachments/ subfolder (primary), then root folder, then fuzzy match.
    """
    filename = attachment_artifact.get("filename", "")

    if filename:
        # Primary: attachments subfolder (standard layout)
        candidate = email_folder / "attachments" / filename
        if candidate.exists():
            return candidate

        # Fallback: root of email folder
        candidate = email_folder / filename
        if candidate.exists():
            return candidate

        # Fuzzy match (case-insensitive)
        attachments_dir = email_folder / "attachments"
        search_dirs = (
            [attachments_dir, email_folder]
            if attachments_dir.is_dir()
            else [email_folder]
        )
        for search_dir in search_dirs:
            for f in search_dir.iterdir():
                if f.is_file() and f.name.lower() == filename.lower():
                    return f

    return None


def _create_link_proxy_file(
    link_artifact: dict,
    email_metadata: dict,
    dest_dir: Path,
    date_prefix: str,
) -> Path:
    """
    Create a .link.json proxy file for a link artifact.

    Contains URL, description, firm/fund assignment, and source email info.
    """
    description = link_artifact.get("description", "") or ""
    url = link_artifact.get("url", "")
    if description:
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", description)[:60].strip("_").lower()
    else:
        parsed = urlparse(url)
        slug = (
            re.sub(r"[^a-zA-Z0-9]+", "_", parsed.path)[:60].strip("_").lower() or "link"
        )

    filename = f"{date_prefix}_link_{slug}.link.json"

    proxy_data = {
        "proxy_version": "1.0",
        "url": url,
        "description": description,
        "link_type": link_artifact.get("link_type", "other"),
        "assigned_firm_name": link_artifact.get("assigned_firm_name", ""),
        "assigned_fund_name": link_artifact.get("assigned_fund_name", ""),
        "confidence": link_artifact.get("confidence", 0.0),
        "method": link_artifact.get("method", ""),
        "evidence": link_artifact.get("evidence", ""),
        "reason_code": link_artifact.get("reason_code", ""),
        "firm_recovery_method": link_artifact.get("firm_recovery_method", ""),
        "source_email": {
            "email_id": email_metadata.get("id", ""),
            "subject": email_metadata.get("subject", ""),
            "from": email_metadata.get("from", {})
            .get("emailAddress", {})
            .get("address", ""),
            "date": email_metadata.get("receivedDateTime", ""),
        },
        "created_at": datetime.now().isoformat(),
    }

    dest_dir.mkdir(parents=True, exist_ok=True)
    filepath = dest_dir / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(proxy_data, f, indent=2, ensure_ascii=False)

    return filepath


def _write_needs_review_context(
    dest_dir: Path,
    artifact: dict,
    email_metadata: dict,
    date_prefix: str,
) -> None:
    """
    Write a .review.json companion file for artifacts routed to _NEEDS_REVIEW.
    """
    identifier = artifact.get("filename", artifact.get("url", "unknown"))
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", identifier)[:40].strip("_").lower()
    review_path = dest_dir / f"{date_prefix}_{slug}.review.json"

    context = {
        "artifact_id": artifact.get("artifact_id", ""),
        "filename": artifact.get("filename", ""),
        "url": artifact.get("url", ""),
        "description": artifact.get("description", ""),
        "assigned_firm_name": artifact.get("assigned_firm_name", ""),
        "assigned_fund_name": artifact.get("assigned_fund_name", ""),
        "confidence": artifact.get("confidence", 0.0),
        "method": artifact.get("method", ""),
        "evidence": artifact.get("evidence", ""),
        "reason_code": artifact.get("reason_code", ""),
        "firm_recovery_method": artifact.get("firm_recovery_method", ""),
        "_original_firm_name": artifact.get("_original_firm_name", ""),
        "_original_fund_name": artifact.get("_original_fund_name", ""),
        "source_email": {
            "email_id": email_metadata.get("id", ""),
            "subject": email_metadata.get("subject", ""),
            "from": email_metadata.get("from", {})
            .get("emailAddress", {})
            .get("address", ""),
            "date": email_metadata.get("receivedDateTime", ""),
        },
    }

    dest_dir.mkdir(parents=True, exist_ok=True)
    with open(review_path, "w", encoding="utf-8") as f:
        json.dump(context, f, indent=2, ensure_ascii=False)


def organize_artifacts_to_folders(
    classification: dict,
    email_folder: Path,
    email_metadata: dict,
    output_dir: Path,
    firm_mappings: dict,
) -> dict:
    """
    Organize individual artifacts into FIRM/FUND/ folders.

    Attachments: copied individually. Links: .link.json proxy files created.
    Unresolvable artifacts routed to _NEEDS_REVIEW/ with .review.json context.

    Returns dict with organized_count, needs_review_count, destinations.
    """
    assignments = classification.get("artifact_assignments", {})
    included_attachments = assignments.get("included_attachments", [])
    included_links = assignments.get("included_links", [])

    # Extract date prefix for filenames
    received_date = email_metadata.get("receivedDateTime", "")
    if received_date:
        try:
            dt = datetime.fromisoformat(received_date.replace("Z", "+00:00"))
            date_prefix = dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            date_prefix = datetime.now().strftime("%Y-%m-%d")
    else:
        date_prefix = datetime.now().strftime("%Y-%m-%d")

    result = {
        "organized_count": 0,
        "needs_review_count": 0,
        "destinations": [],
    }

    # Process attachments
    for att in included_attachments:
        firm_name = att.get("assigned_firm_name", "")
        fund_name = att.get("assigned_fund_name", "")
        filename = att.get("filename", "")

        dest_dir = _resolve_artifact_dest_dir(
            firm_name, fund_name, output_dir, firm_mappings
        )

        source_file = _find_attachment_file(email_folder, att, email_metadata)
        if source_file and source_file.exists():
            safe_filename = (
                sanitize_folder_name(filename) if filename else source_file.name
            )
            dest_filename = f"{date_prefix}_{safe_filename}"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = dest_dir / dest_filename
            shutil.copy2(str(source_file), str(dest_path))
            result["destinations"].append(str(dest_path))
        else:
            dest_dir.mkdir(parents=True, exist_ok=True)

        is_needs_review = NEEDS_REVIEW_FOLDER in str(dest_dir)
        if is_needs_review:
            _write_needs_review_context(dest_dir, att, email_metadata, date_prefix)
            result["needs_review_count"] += 1
        else:
            result["organized_count"] += 1

    # Process links
    for link in included_links:
        firm_name = link.get("assigned_firm_name", "")
        fund_name = link.get("assigned_fund_name", "")

        dest_dir = _resolve_artifact_dest_dir(
            firm_name, fund_name, output_dir, firm_mappings
        )

        proxy_path = _create_link_proxy_file(
            link, email_metadata, dest_dir, date_prefix
        )
        result["destinations"].append(str(proxy_path))

        is_needs_review = NEEDS_REVIEW_FOLDER in str(dest_dir)
        if is_needs_review:
            result["needs_review_count"] += 1
        else:
            result["organized_count"] += 1

    return result


def classify_and_organize_emails(
    email_input_dir: Path = None,
    output_dir: Path = None,
    force_reclassify: bool = False,
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
    classification_cache = (
        {} if force_reclassify else load_classification_cache(output_dir)
    )
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
        "classifications": [],
    }

    # Find all email folders
    email_folders = [
        f
        for f in email_input_dir.iterdir()
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
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)
        except Exception as e:
            print(f"Error loading {email_folder.name}: {e}")
            report["errors"] += 1
            continue

        email_id = metadata.get("id", email_folder.name)
        subject = metadata.get("subject", "No Subject")
        from_address = (
            metadata.get("from", {}).get("emailAddress", {}).get("address", "")
        )

        # Check for email/domain override (priority over GPT classification)
        override_firm = check_email_override(from_address, firm_mappings)
        if override_firm:
            override_source = (
                "email_override"
                if from_address.lower()
                in [e.lower() for e in firm_mappings.get("email_overrides", {}).keys()]
                else "domain_override"
            )
            override_detail = (
                "email: " + from_address
                if override_source == "email_override"
                else (
                    "domain: " + from_address.split("@")[1]
                    if "@" in from_address
                    else "unknown"
                )
            )
            candidates = build_artifact_candidates(metadata, email_id=email_id)
            classification = {
                "email_classification": {
                    "is_hedge_fund_related": True,
                    "confidence": 1.0,
                    "email_type": "unknown",
                    "from_third_party": False,
                    "source_priority": "highest",
                    "reasoning": f"Assigned via override rule for {override_detail}",
                },
                "firm_name": override_firm,
                "artifact_assignments": _make_override_artifact_assignments(
                    candidates,
                    override_firm,
                    override_detail,
                ),
            }
            print(f"[{i + 1}/{len(email_folders)}] (override) {subject[:50]}...")
        # Check cache (skip error'd entries so GPT is re-called)
        elif email_id in classification_cache and not classification_cache[
            email_id
        ].get("_error"):
            classification = classification_cache[email_id]
            print(f"[{i + 1}/{len(email_folders)}] (cached) {subject[:50]}...")
        else:
            # Classify with GPT
            print(f"[{i + 1}/{len(email_folders)}] Classifying: {subject[:50]}...")
            classification = classify_email_with_gpt(
                client, metadata, existing_firms, firm_mappings
            )

            # Don't cache error results — they'll be retried next run
            if not classification.get("_error"):
                classification_cache[email_id] = classification

        # Process classification result
        email_cls = classification.get("email_classification", {})
        included_count = (
            classification.get("artifact_assignments", {})
            .get("summary", {})
            .get("included_count", 0)
        )
        if included_count > 0:
            email_cls["is_hedge_fund_related"] = True
            classification["email_classification"] = email_cls
        classification_entry = {
            "email_id": email_id,
            "email_folder": email_folder.name,
            "subject": subject,
            "from": metadata.get("from", {}).get("emailAddress", {}).get("address", ""),
            **classification,
        }

        if email_cls.get("is_hedge_fund_related"):
            report["hedge_fund_related"] += 1

            # Register firm/fund in mappings from email-level firm_name
            raw_firm_name = classification.get("firm_name", "")
            from_third_party = email_cls.get("from_third_party", False)

            if raw_firm_name:
                canonical_name = normalize_firm_name(raw_firm_name, firm_mappings)
                canonical_name = apply_folder_reassignment(
                    canonical_name, firm_mappings
                )

                if not from_third_party:
                    domain_hints = extract_domain_hints(
                        metadata.get("from", {})
                        .get("emailAddress", {})
                        .get("address", "")
                    )
                    aliases = [raw_firm_name] + domain_hints
                else:
                    aliases = [raw_firm_name]
                add_firm_to_mappings(canonical_name, aliases, firm_mappings)

                # Ensure firm folder exists even if no individual artifacts are organized
                (output_dir / sanitize_folder_name(canonical_name)).mkdir(
                    parents=True, exist_ok=True
                )

                if canonical_name not in existing_firms:
                    existing_firms.append(canonical_name)

                if canonical_name not in report["firms_found"]:
                    report["firms_found"][canonical_name] = {
                        "email_count": 0,
                        "emails": [],
                        "from_third_party": from_third_party,
                    }
                report["firms_found"][canonical_name]["email_count"] += 1
                report["firms_found"][canonical_name]["emails"].append(
                    {"folder": email_folder.name, "subject": subject}
                )
                classification_entry["canonical_firm_name"] = canonical_name

            # Organize individual artifacts to firm/fund folders
            org_result = organize_artifacts_to_folders(
                classification=classification,
                email_folder=email_folder,
                email_metadata=metadata,
                output_dir=output_dir,
                firm_mappings=firm_mappings,
            )
            classification_entry["destinations"] = org_result["destinations"]
            classification_entry["organized_count"] = org_result["organized_count"]
            classification_entry["needs_review_count"] = org_result[
                "needs_review_count"
            ]

            if org_result["organized_count"] > 0:
                print(
                    f"    -> Organized {org_result['organized_count']} artifact(s) to firm folders"
                )
            if org_result["needs_review_count"] > 0:
                print(
                    f"    -> {org_result['needs_review_count']} artifact(s) sent to {NEEDS_REVIEW_FOLDER}/"
                )
            if (
                org_result["organized_count"] == 0
                and org_result["needs_review_count"] == 0
            ):
                if not raw_firm_name:
                    report["hedge_fund_related"] -= 1
                    report["non_hedge_fund"] += 1
                print("    -> Hedge fund related but no artifacts could be organized")
        else:
            report["non_hedge_fund"] += 1
            print(
                f"    -> Not hedge fund related (confidence: {email_cls.get('confidence', 0):.2f})"
            )

        report["classifications"].append(classification_entry)

    # Save updated data
    save_firm_mappings(firm_mappings, output_dir)
    save_classification_cache(classification_cache, output_dir)

    # Save report
    report_path = output_dir / CLASSIFICATION_REPORT_FILE
    with open(report_path, "w", encoding="utf-8") as f:
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
    Reads from classification report and cross-checks the cache to exclude
    error'd entries (so they get retried on the next run).
    """
    report_path = output_dir / CLASSIFICATION_REPORT_FILE
    processed = set()

    if report_path.exists():
        try:
            with open(report_path, "r", encoding="utf-8") as f:
                report = json.load(f)
                for entry in report.get("classifications", []):
                    if "email_folder" in entry:
                        processed.add(entry["email_folder"])
        except Exception:
            pass

    if not processed:
        return processed

    # Exclude emails whose cache entry is flagged as an error
    cache = load_classification_cache(output_dir)
    if cache:
        # Build email_id -> folder_name lookup from report
        id_to_folder = {}
        try:
            with open(report_path, "r", encoding="utf-8") as f:
                report = json.load(f)
                for entry in report.get("classifications", []):
                    eid = entry.get("email_id", "")
                    folder = entry.get("email_folder", "")
                    if eid and folder:
                        id_to_folder[eid] = folder
        except Exception:
            pass

        for email_id, cached_entry in cache.items():
            if cached_entry.get("_error") and email_id in id_to_folder:
                processed.discard(id_to_folder[email_id])

    return processed


def classify_new_emails(email_input_dir: Path = None, output_dir: Path = None) -> dict:
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
        f
        for f in email_input_dir.iterdir()
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
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)
        except Exception as e:
            print(f"Error loading {email_folder.name}: {e}")
            continue

        email_id = metadata.get("id", email_folder.name)
        subject = metadata.get("subject", "No Subject")
        from_address = (
            metadata.get("from", {}).get("emailAddress", {}).get("address", "")
        )

        # Check for email/domain override
        override_firm = check_email_override(from_address, firm_mappings)
        if override_firm:
            override_source = (
                "email_override"
                if from_address.lower()
                in [e.lower() for e in firm_mappings.get("email_overrides", {}).keys()]
                else "domain_override"
            )
            override_detail = (
                "email: " + from_address
                if override_source == "email_override"
                else (
                    "domain: " + from_address.split("@")[1]
                    if "@" in from_address
                    else "unknown"
                )
            )
            candidates = build_artifact_candidates(metadata, email_id=email_id)
            classification = {
                "email_classification": {
                    "is_hedge_fund_related": True,
                    "confidence": 1.0,
                    "email_type": "unknown",
                    "from_third_party": False,
                    "source_priority": "highest",
                    "reasoning": f"Assigned via override rule for {override_detail}",
                },
                "firm_name": override_firm,
                "artifact_assignments": _make_override_artifact_assignments(
                    candidates,
                    override_firm,
                    override_detail,
                ),
            }
            print(f"[{i + 1}/{len(new_folders)}] (override) {subject[:50]}...")
        else:
            # Classify with GPT
            print(f"[{i + 1}/{len(new_folders)}] Classifying: {subject[:50]}...")
            classification = classify_email_with_gpt(client, metadata, existing_firms, firm_mappings)
            # Don't cache or report error results — they'll be retried next run
            if classification.get("_error"):
                print("    -> GPT error, will retry next run")
                continue
            classification_cache[email_id] = classification

        # Process result
        email_cls = classification.get("email_classification", {})
        included_count = (
            classification.get("artifact_assignments", {})
            .get("summary", {})
            .get("included_count", 0)
        )
        if included_count > 0:
            email_cls["is_hedge_fund_related"] = True
            classification["email_classification"] = email_cls
        raw_firm_name = classification.get("firm_name", "")
        from_third_party = email_cls.get("from_third_party", False)

        if email_cls.get("is_hedge_fund_related"):
            # Register firm in mappings if available
            if raw_firm_name:
                canonical_name = normalize_firm_name(raw_firm_name, firm_mappings)
                canonical_name = apply_folder_reassignment(
                    canonical_name, firm_mappings
                )

                if not from_third_party:
                    domain_hints = extract_domain_hints(from_address)
                    aliases = [raw_firm_name] + domain_hints
                else:
                    aliases = [raw_firm_name]
                add_firm_to_mappings(canonical_name, aliases, firm_mappings)

                # Ensure firm folder exists even if no individual artifacts are organized
                (output_dir / sanitize_folder_name(canonical_name)).mkdir(
                    parents=True, exist_ok=True
                )

                if canonical_name not in existing_firms:
                    existing_firms.append(canonical_name)
            else:
                canonical_name = None

            # Organize individual artifacts to firm/fund folders
            org_result = organize_artifacts_to_folders(
                classification=classification,
                email_folder=email_folder,
                email_metadata=metadata,
                output_dir=output_dir,
                firm_mappings=firm_mappings,
            )

            if org_result["organized_count"] > 0:
                print(
                    f"    -> Organized {org_result['organized_count']} artifact(s) to firm folders"
                )
            if org_result["needs_review_count"] > 0:
                print(
                    f"    -> {org_result['needs_review_count']} artifact(s) sent to {NEEDS_REVIEW_FOLDER}/"
                )

            classification_entry = {
                "email_id": email_id,
                "email_folder": email_folder.name,
                "subject": subject,
                "from": from_address,
                **classification,
                "canonical_firm_name": canonical_name,
                "destinations": org_result["destinations"],
                "organized_count": org_result["organized_count"],
                "needs_review_count": org_result["needs_review_count"],
            }
            results.append(classification_entry)
        else:
            print("    -> Skipped: Not hedge fund related")
            classification_entry = {
                "email_id": email_id,
                "email_folder": email_folder.name,
                "subject": subject,
                "from": from_address,
                **classification,
            }
            results.append(classification_entry)

    # Save updated data
    save_firm_mappings(firm_mappings, output_dir)
    save_classification_cache(classification_cache, output_dir)

    # Update the report with new classifications
    report_path = output_dir / CLASSIFICATION_REPORT_FILE
    if report_path.exists():
        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)
    else:
        report = {"classifications": [], "firms_found": {}}

    for result in results:
        report["classifications"].append(result)

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    return {"new_folders_found": len(new_folders), "classifications": results}


def monitor_and_classify(
    email_input_dir: Path = None,
    output_dir: Path = None,
    poll_interval: int = 30,
    run_once: bool = False,
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
                print(
                    f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                    f"Processed {result['new_folders_found']} new email(s)"
                )

                # Summary of what was classified
                for item in result["classifications"]:
                    firm = item.get("firm")
                    if firm:
                        print(f"  + {item['subject'][:40]}... -> {firm}")
                    else:
                        print(
                            f"  - {item['subject'][:40]}... ({item.get('reason', 'skipped')})"
                        )
            else:
                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] No new emails found",
                    end="\r",
                )

            if run_once:
                print("\nSingle check completed.")
                break

            # Wait before next check
            time.sleep(poll_interval)

    except KeyboardInterrupt:
        print("\n\nMonitoring stopped by user.")

    return result if run_once else None


def monitor_and_classify_with_moves(
    email_input_dir: Path = None,
    output_dir: Path = None,
    poll_interval: int = 30,
    run_once: bool = False,
):
    """
    Combined monitor: classify new emails AND sync manually moved artifacts
    in a single polling loop.
    """
    email_input_dir = email_input_dir or DEFAULT_EMAIL_INPUT_DIR
    output_dir = output_dir or DEFAULT_OUTPUT_DIR

    print("=" * 60)
    print("EMAIL + ARTIFACT MOVE MONITOR")
    print("=" * 60)
    print(f"Emails:     {email_input_dir}")
    print(f"Output:     {output_dir}")
    print(f"Poll interval: {poll_interval} seconds")
    if not run_once:
        print("Press Ctrl+C to stop monitoring")
    print("-" * 60)

    classify_result = None
    try:
        while True:
            # 1. Classify new emails
            classify_result = classify_new_emails(email_input_dir, output_dir)

            if classify_result["new_folders_found"] > 0:
                print(
                    f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                    f"Processed {classify_result['new_folders_found']} new email(s)"
                )
                for item in classify_result["classifications"]:
                    firm = item.get("firm")
                    if firm:
                        print(f"  + {item['subject'][:40]}... -> {firm}")
                    else:
                        print(
                            f"  - {item['subject'][:40]}... ({item.get('reason', 'skipped')})"
                        )

            # 2. Sync manually moved artifacts
            move_result = sync_moved_artifacts(output_dir)

            if move_result["moved"]:
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                    f"Synced {len(move_result['moved'])} moved artifact(s)"
                )
                for m in move_result["moved"]:
                    print(
                        f"  ~ {m.get('file', '?')} : {m.get('from', '?')} -> {m.get('firm', '?')}"
                    )

            if move_result.get("new_folders"):
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                    f"Registered {len(move_result['new_folders'])} new folder(s)"
                )
                for nf in move_result["new_folders"]:
                    print(f"  + {nf['folder']} -> {nf['firm']}")

            if move_result["errors"]:
                for err in move_result["errors"]:
                    print(f"  ERROR: {err}")

            if (
                not classify_result["new_folders_found"]
                and not move_result["moved"]
                and not move_result.get("new_folders")
            ):
                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] No new emails or moves",
                    end="\r",
                )

            if run_once:
                print("\nSingle check completed.")
                break

            time.sleep(poll_interval)

    except KeyboardInterrupt:
        print("\n\nMonitoring stopped by user.")

    return classify_result if run_once else None


# =========================
# ARTIFACT MOVE MONITORING
# =========================

# System files at the output_dir root that should never be scanned as artifacts
_SYSTEM_FILES = {
    FIRM_MAPPINGS_FILE,
    CLASSIFICATION_CACHE_FILE,
    "classification_report.json",
}


def _parse_firm_fund_from_path(file_path: Path, output_dir: Path) -> tuple[str, str]:
    """
    Reverse of _resolve_artifact_dest_dir(). Given a file under output_dir,
    extract the firm and optional fund folder names from its location.

    Returns:
        (firm_name, fund_name) where firm_name="" means _NEEDS_REVIEW or root.

    Examples:
        output_dir/FIRM/file.pdf          → ("FIRM", "")
        output_dir/FIRM/FUND/file.pdf     → ("FIRM", "FUND")
        output_dir/_NEEDS_REVIEW/file.pdf → ("", "")
    """
    try:
        rel = file_path.resolve().relative_to(output_dir.resolve())
    except ValueError:
        return ("", "")

    parts = rel.parts  # e.g. ("FIRM", "FUND", "file.pdf") or ("FIRM", "file.pdf")

    if len(parts) < 2:
        # File is directly in output_dir root — not in any firm folder
        return ("", "")

    firm_folder = parts[0]
    if firm_folder == NEEDS_REVIEW_FOLDER:
        return ("", "")

    if len(parts) == 2:
        # output_dir/FIRM/file
        return (firm_folder, "")
    else:
        # output_dir/FIRM/FUND/file (or deeper — treat second level as fund)
        return (firm_folder, parts[1])


def _build_destination_index(
    report: dict,
) -> dict:
    """
    Build reverse index from the classification report:
      destination_path → {email_id, artifact_type, artifact_index, list_key}

    Scans report["classifications"] for entries that have "destinations".
    Also builds a filename-based fallback index for moved-file matching.

    Returns dict with two keys:
        "by_path": {abs_path_str: {email_id, list_key, idx}}
        "by_filename": {filename_str: [{email_id, list_key, idx, old_path}]}
    """
    by_path = {}
    by_filename = {}

    for entry in report.get("classifications", []):
        email_id = entry.get("email_id", "")
        if not email_id:
            continue

        destinations = entry.get("destinations", [])
        assignments = entry.get("artifact_assignments", {})

        # Map each destination to its artifact
        # Destinations are ordered: first all attachments, then all links
        # (matching organize_artifacts_to_folders output order)
        included_atts = assignments.get("included_attachments", [])
        included_links = assignments.get("included_links", [])

        dest_idx = 0
        for i, att in enumerate(included_atts):
            if dest_idx < len(destinations):
                path_str = destinations[dest_idx]
                record = {
                    "email_id": email_id,
                    "list_key": "included_attachments",
                    "idx": i,
                }
                by_path[path_str] = record

                fname = Path(path_str).name
                by_filename.setdefault(fname, []).append(
                    {**record, "old_path": path_str}
                )
                dest_idx += 1

        for i, link in enumerate(included_links):
            if dest_idx < len(destinations):
                path_str = destinations[dest_idx]
                record = {
                    "email_id": email_id,
                    "list_key": "included_links",
                    "idx": i,
                }
                by_path[path_str] = record

                fname = Path(path_str).name
                by_filename.setdefault(fname, []).append(
                    {**record, "old_path": path_str}
                )
                dest_idx += 1

    return {"by_path": by_path, "by_filename": by_filename}


def sync_moved_artifacts(output_dir: Path = None) -> dict:
    """
    Detect artifacts that were manually moved between firm/fund folders
    and new firm folders created by the user, then update the
    classification cache, report, and firm mappings accordingly.

    Returns:
        {"moved": [{"file": str, "from": str, "to": str, "firm": str, "fund": str}],
         "new_folders": [{"folder": str, "firm": str}],
         "errors": [str]}
    """
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    result = {"moved": [], "new_folders": [], "errors": []}

    # Load all persistent state
    report_path = output_dir / CLASSIFICATION_REPORT_FILE
    if not report_path.exists():
        return result

    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    classification_cache = load_classification_cache(output_dir)
    firm_mappings = load_firm_mappings(output_dir)

    # Build index from report
    index = _build_destination_index(report)
    by_path = index["by_path"]

    # Collect all known destination paths (for detecting what's missing)
    known_paths = set(by_path.keys())

    # Scan all artifact files currently on disk
    disk_files = set()
    for dirpath, _dirnames, filenames in os.walk(str(output_dir)):
        dir_p = Path(dirpath)
        # Skip output_dir root-level system files
        for fname in filenames:
            if dir_p == output_dir and fname in _SYSTEM_FILES:
                continue
            # Skip .review.json files — they are companion metadata, not artifacts
            if fname.endswith(".review.json"):
                continue
            disk_files.add(str(dir_p / fname))

    # Find files on disk that are NOT at their known destination
    files_not_at_known = disk_files - known_paths

    # Find known destinations that no longer exist on disk (file was moved away)
    missing_from_known = known_paths - disk_files

    # For each file not at a known path, try to match by filename
    # to a missing known destination
    missing_filenames = {}
    for missing_path in missing_from_known:
        fname = Path(missing_path).name
        missing_filenames.setdefault(fname, []).append(missing_path)

    moves = []  # List of (new_path, old_path, index_record)
    for new_path_str in files_not_at_known:
        fname = Path(new_path_str).name
        if fname not in missing_filenames:
            continue  # File was not moved from a known location

        # Match: this filename disappeared from one place and appeared here
        old_paths = missing_filenames[fname]
        if not old_paths:
            continue

        old_path = old_paths.pop(0)  # Take first match
        if not old_paths:
            del missing_filenames[fname]

        # Find the index record for the old path
        record = by_path.get(old_path)
        if record:
            moves.append((new_path_str, old_path, record))

    # Process each detected move
    modified_email_ids = set()
    for new_path_str, old_path_str, record in moves:
        email_id = record["email_id"]
        list_key = record["list_key"]
        idx = record["idx"]

        new_path = Path(new_path_str)
        firm_folder, fund_folder = _parse_firm_fund_from_path(new_path, output_dir)

        old_firm, old_fund = _parse_firm_fund_from_path(Path(old_path_str), output_dir)

        # Skip if firm/fund hasn't actually changed
        if firm_folder == old_firm and fund_folder == old_fund:
            continue

        # Determine new firm/fund assignment
        is_rejection = firm_folder == ""  # Moved to _NEEDS_REVIEW or root

        if is_rejection:
            new_firm_name = ""
            new_fund_name = ""
            new_method = "manual_rejection"
        else:
            new_firm_name = firm_folder  # Folder name IS the firm name
            new_fund_name = fund_folder
            new_method = "manual_reassignment"

        # --- Update classification cache ---
        cache_entry = classification_cache.get(email_id)
        if cache_entry:
            artifacts = cache_entry.get("artifact_assignments", {}).get(list_key, [])
            if idx < len(artifacts):
                artifact = artifacts[idx]
                artifact["assigned_firm_name"] = new_firm_name
                artifact["assigned_fund_name"] = new_fund_name
                artifact["method"] = new_method
                artifact["firm_recovery_method"] = new_method

                # Update email-level firm_name if this was the primary
                if cache_entry.get("firm_name") == old_firm or not cache_entry.get(
                    "firm_name"
                ):
                    all_included = cache_entry.get("artifact_assignments", {}).get(
                        "included_attachments", []
                    ) + cache_entry.get("artifact_assignments", {}).get(
                        "included_links", []
                    )
                    primary = ""
                    for item in all_included:
                        if item.get("assigned_firm_name"):
                            primary = item["assigned_firm_name"]
                            break
                    cache_entry["firm_name"] = primary

                classification_cache[email_id] = cache_entry
                modified_email_ids.add(email_id)

        # --- Update report entry destinations ---
        for entry in report.get("classifications", []):
            if entry.get("email_id") != email_id:
                continue
            dests = entry.get("destinations", [])
            for i, d in enumerate(dests):
                if d == old_path_str:
                    dests[i] = new_path_str
                    break

            # Also update the artifact in the report's artifact_assignments
            report_artifacts = entry.get("artifact_assignments", {}).get(list_key, [])
            if idx < len(report_artifacts):
                report_artifacts[idx]["assigned_firm_name"] = new_firm_name
                report_artifacts[idx]["assigned_fund_name"] = new_fund_name
                report_artifacts[idx]["method"] = new_method
                report_artifacts[idx]["firm_recovery_method"] = new_method
            break

        # --- Register new firm/fund in mappings ---
        if new_firm_name:
            canonical = normalize_firm_name(new_firm_name, firm_mappings)
            canonical = apply_folder_reassignment(canonical, firm_mappings)
            add_firm_to_mappings(canonical, [new_firm_name], firm_mappings)
            if new_fund_name:
                add_fund_to_firm(canonical, new_fund_name, [], firm_mappings)

        # --- Handle _NEEDS_REVIEW context files ---
        if is_rejection:
            # Write a .review.json for the rejected artifact
            artifact_data = {}
            if cache_entry:
                cache_artifacts = cache_entry.get("artifact_assignments", {}).get(
                    list_key, []
                )
                if idx < len(cache_artifacts):
                    artifact_data = cache_artifacts[idx]

            date_prefix = new_path.name[:10]  # Extract YYYY-MM-DD from filename
            if not re.match(r"\d{4}-\d{2}-\d{2}", date_prefix):
                date_prefix = datetime.now().strftime("%Y-%m-%d")

            review_dir = output_dir / NEEDS_REVIEW_FOLDER
            review_dir.mkdir(parents=True, exist_ok=True)
            identifier = artifact_data.get(
                "filename", artifact_data.get("url", new_path.name)
            )
            slug = re.sub(r"[^a-zA-Z0-9]+", "_", identifier)[:40].strip("_").lower()
            review_path = review_dir / f"{date_prefix}_{slug}.review.json"
            context = {
                "artifact_id": artifact_data.get("artifact_id", ""),
                "filename": artifact_data.get("filename", ""),
                "url": artifact_data.get("url", ""),
                "description": artifact_data.get("description", ""),
                "assigned_firm_name": "",
                "assigned_fund_name": "",
                "confidence": artifact_data.get("confidence", 0.0),
                "method": "manual_rejection",
                "evidence": artifact_data.get("evidence", ""),
                "reason_code": artifact_data.get("reason_code", ""),
                "firm_recovery_method": "manual_rejection",
                "_original_firm_name": old_firm,
                "_original_fund_name": old_fund,
                "moved_from": old_path_str,
                "source_email": {"email_id": email_id},
            }
            with open(review_path, "w", encoding="utf-8") as f:
                json.dump(context, f, indent=2, ensure_ascii=False)

        if not is_rejection and old_firm == "":
            # Moved OUT of _NEEDS_REVIEW — clean up companion .review.json
            old_dir = Path(old_path_str).parent
            for review_file in old_dir.glob("*.review.json"):
                try:
                    with open(review_file, "r", encoding="utf-8") as f:
                        review_data = json.load(f)
                    if review_data.get("source_email", {}).get("email_id") == email_id:
                        review_file.unlink()
                        break
                except (json.JSONDecodeError, OSError):
                    continue

        # --- Update .link.json proxy file if it's a link ---
        if new_path.name.endswith(".link.json"):
            try:
                with open(new_path, "r", encoding="utf-8") as f:
                    proxy_data = json.load(f)
                proxy_data["assigned_firm_name"] = new_firm_name
                proxy_data["assigned_fund_name"] = new_fund_name
                proxy_data["method"] = new_method
                proxy_data["firm_recovery_method"] = new_method
                with open(new_path, "w", encoding="utf-8") as f:
                    json.dump(proxy_data, f, indent=2, ensure_ascii=False)
            except (json.JSONDecodeError, OSError) as e:
                result["errors"].append(f"Failed to update link proxy {new_path}: {e}")

        from_desc = f"{old_firm}/{old_fund}" if old_firm else "_NEEDS_REVIEW"
        to_desc = f"{firm_folder}/{fund_folder}" if firm_folder else "_NEEDS_REVIEW"
        result["moved"].append(
            {
                "file": new_path.name,
                "from": from_desc.rstrip("/"),
                "to": to_desc.rstrip("/"),
                "firm": new_firm_name,
                "fund": new_fund_name,
            }
        )
        print(
            f"  Synced: {new_path.name} | {from_desc.rstrip('/')} -> {to_desc.rstrip('/')}"
        )

    # --- Detect new firm/fund folders created by the user ---
    known_firms = {
        sanitize_folder_name(c) for c in firm_mappings.get("canonical_names", {})
    }
    known_firms.add(NEEDS_REVIEW_FOLDER)
    for child in output_dir.iterdir():
        if not child.is_dir():
            continue
        folder_name = child.name
        if folder_name.startswith(".") or folder_name in _SYSTEM_FILES:
            continue
        if folder_name not in known_firms:
            canonical = normalize_firm_name(folder_name, firm_mappings)
            add_firm_to_mappings(canonical, [folder_name], firm_mappings)
            known_firms.add(folder_name)
            result["new_folders"].append({"folder": folder_name, "firm": canonical})
            print(
                f"  New firm folder detected: {folder_name} -> registered as {canonical}"
            )

            # Also detect fund sub-folders inside the new firm folder
            for subfolder in child.iterdir():
                if subfolder.is_dir() and not subfolder.name.startswith("."):
                    add_fund_to_firm(canonical, subfolder.name, [], firm_mappings)
                    result["new_folders"].append(
                        {"folder": f"{folder_name}/{subfolder.name}", "firm": canonical}
                    )
                    print(f"  New fund folder detected: {folder_name}/{subfolder.name}")

    # Save all modified state
    if result["moved"] or result["new_folders"]:
        save_classification_cache(classification_cache, output_dir)
        save_firm_mappings(firm_mappings, output_dir)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

    return result


def add_email_override(email_address: str, firm_name: str, output_dir: Path = None):
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


def add_domain_override(domain: str, firm_name: str, output_dir: Path = None):
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
    domain = domain.lstrip("@").lower()

    mappings = load_firm_mappings(output_dir)
    mappings["domain_overrides"][domain] = firm_name
    save_firm_mappings(mappings, output_dir)

    print(f"Added domain override: @{domain} -> {firm_name}")
    print("All future emails from this domain will be assigned to {firm_name}.")
    print("Run classify_and_organize_emails() again to apply the change.")


def reassign_firm(old_firm_name: str, new_firm_name: str, output_dir: Path = None):
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
            "auto_added": datetime.now().isoformat(),
        }

        if old_firm_key:
            # Copy description if it existed
            if canonical_names[old_firm_key].get("description"):
                canonical_names[new_canonical]["description"] = canonical_names[
                    old_firm_key
                ]["description"]
            del canonical_names[old_firm_key]
            print(f"Renamed '{old_firm_key}' to '{new_canonical}'")
        else:
            print(f"Created new firm '{new_canonical}' with alias '{old_firm_name}'")

        if old_aliases:
            print(f"Aliases: {list(old_aliases)}")

        new_firm_key = new_canonical

    # Add folder reassignment so future classifications redirect properly
    mappings["folder_reassignments"][old_firm_name.upper()] = (
        new_firm_key or new_firm_name.upper()
    )

    save_firm_mappings(mappings, output_dir)

    print(
        f"\nFolder reassignment added: {old_firm_name.upper()} -> {new_firm_key or new_firm_name.upper()}"
    )

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
                print("\nFolder reorganization complete:")
                print(f"  - Moved {emails_moved} email(s) to '{new_folder_name}/'")
                print(
                    f"  - Deleted old folder: {old_folder_path.relative_to(output_dir)}/"
                )
            except Exception as e:
                print(
                    f"\nWarning: Could not delete old folder '{old_folder_path}': {e}"
                )
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
        "folder_reassignments": folder_reassignments,
    }


def remove_override(override_type: str, key: str, output_dir: Path = None):
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
        "folder": "folder_reassignments",
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
        print(f"\n{canonical}")
        if aliases:
            print(f"  Aliases: {', '.join(aliases)}")
        if info.get("description"):
            print(f"  Description: {info['description']}")
        if funds:
            print(f"  Funds ({len(funds)}):")
            for fund_id, fund_info in funds.items():
                fund_aliases = fund_info.get("aliases", [])
                alias_str = (
                    f" (aliases: {', '.join(fund_aliases)})" if fund_aliases else ""
                )
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
                print("    (No fund aliases)")

    return aliases


def delete_firm_alias(
    firm_name: str, alias_to_delete: str, output_dir: Path = None
) -> bool:
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

    sorted_firms = sorted(canonical_names.keys())
    print("\nAvailable firms:")
    for i, firm in enumerate(sorted_firms, 1):
        alias_count = len(canonical_names[firm].get("aliases", []))
        print(f"  {i}. {firm} ({alias_count} aliases)")

    raw_input = input("\nEnter firm name or number: ").strip()
    if not raw_input:
        print("No firm name provided.")
        return

    firm_name = _resolve_from_numbered_list(raw_input, sorted_firms)
    if firm_name is None:
        print(f"Invalid selection: {raw_input}")
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
        alias_to_delete = input("Enter alias name or number to delete: ").strip()
        if alias_to_delete:
            resolved = _resolve_from_numbered_list(alias_to_delete, aliases)
            if resolved is None:
                print(f"Invalid selection: {alias_to_delete}")
                return
            delete_firm_alias(firm_name, resolved, output_dir)
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
        for i, (fund_id, fund_info) in enumerate(funds.items(), 1):
            print(f"\n  {i}. {fund_info['display_name']}")
            print(f"     ID: {fund_id}")
            fund_aliases = fund_info.get("aliases", [])
            if fund_aliases:
                print(f"     Aliases: {', '.join(fund_aliases)}")
            else:
                print("     Aliases: (none)")
    else:
        print("  (No funds registered)")

    return funds


def add_fund_alias_to_firm(
    firm_name: str, fund_id: str, alias: str, output_dir: Path = None
) -> bool:
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
        print(
            f"Alias '{alias}' already exists for fund '{funds[fund_id]['display_name']}'."
        )
        return False

    fund_aliases.append(alias)
    funds[fund_id]["aliases"] = fund_aliases
    save_firm_mappings(mappings, output_dir)
    print(
        f"Added alias '{alias}' to fund '{funds[fund_id]['display_name']}' under '{firm_key}'."
    )
    return True


def delete_fund_alias_from_firm(
    firm_name: str, fund_id: str, alias: str, output_dir: Path = None
) -> bool:
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
        print(
            f"Deleted alias '{alias_found}' from fund '{funds[fund_id]['display_name']}'."
        )
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
    sorted_firms = sorted(canonical_names.keys())
    print("\nAvailable firms:")
    for i, firm in enumerate(sorted_firms, 1):
        fund_count = len(canonical_names[firm].get("funds", {}))
        print(f"  {i}. {firm} ({fund_count} funds)")

    raw_input = input("\nEnter firm name or number: ").strip()
    if not raw_input:
        print("No firm name provided.")
        return

    firm_name = _resolve_from_numbered_list(raw_input, sorted_firms)
    if firm_name is None:
        print(f"Invalid selection: {raw_input}")
        return

    funds = list_firm_funds(firm_name, output_dir)
    if not funds:
        return

    fund_ids = list(funds.keys())
    raw_fund = input("\nEnter fund ID or number: ").strip()
    if not raw_fund:
        print("No fund provided.")
        return

    fund_id = _resolve_from_numbered_list(raw_fund, fund_ids)
    if fund_id is None or fund_id not in funds:
        print(f"Invalid fund selection: {raw_fund}")
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
        fund_aliases = funds[fund_id].get("aliases", [])
        if not fund_aliases:
            print("No aliases to delete for this fund.")
            return
        print("\nFund aliases:")
        for i, alias in enumerate(fund_aliases, 1):
            print(f"  {i}. {alias}")
        alias_to_delete = input("Enter alias name or number to delete: ").strip()
        if alias_to_delete:
            resolved = _resolve_from_numbered_list(alias_to_delete, fund_aliases)
            if resolved is None:
                print(f"Invalid selection: {alias_to_delete}")
                return
            delete_fund_alias_from_firm(firm_name, fund_id, resolved, output_dir)
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
DEFAULT_DOWNLOAD_DIR = _PROJECT_ROOT / "output" / "testing" / "downloads"


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
    previous_actions: list = None,
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
    page_source_truncated = (
        page_source[:15000] if len(page_source) > 15000 else page_source
    )

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
                                "detail": "high",
                            },
                        },
                    ],
                },
            ],
            max_tokens=1000,
            temperature=0.1,
        )

        result_text = response.choices[0].message.content
        # Extract JSON from response
        json_match = re.search(r"\{[\s\S]*\}", result_text)
        if json_match:
            return json.loads(json_match.group())
        else:
            return {
                "action": "give_up",
                "target": "",
                "value": "",
                "reasoning": f"Could not parse response: {result_text[:200]}",
            }

    except Exception as e:
        return {
            "action": "give_up",
            "target": "",
            "value": "",
            "reasoning": f"Vision analysis error: {str(e)}",
        }


def execute_action(
    driver,
    action: dict,
    investor_email: str = "",
    investor_name: str = "",
    investor_company: str = "",
) -> bool:
    """
    Execute the action determined by the vision model.

    Returns:
        True if action was successful, False otherwise
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException, NoSuchElementException

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
                selectors_to_try.extend(
                    [
                        "a[href*='download']",
                        "button:contains('Download')",
                        "[class*='download']",
                        "a[href$='.pdf']",
                    ]
                )

            for selector in selectors_to_try:
                try:
                    # Handle :contains pseudo-selector (not standard CSS)
                    if ":contains(" in selector:
                        text = re.search(
                            r":contains\(['\"](.+?)['\"]\)", selector
                        ).group(1)
                        tag = selector.split(":")[0] or "*"
                        element = driver.find_element(
                            By.XPATH, f"//{tag}[contains(text(), '{text}')]"
                        )
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
                driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center'});", element
                )
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
                elif (
                    "company" in target_lower
                    or "firm" in target_lower
                    or "organization" in target_lower
                ):
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


def check_for_new_downloads(
    download_dir: Path, known_files: set, timeout: int = 30
) -> list:
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
            download_dir / f
            for f in new_file_names
            if not f.endswith((".crdownload", ".tmp", ".part", ".partial"))
        ]

        if complete_new_files:
            return complete_new_files

        # Check if there's an in-progress download
        in_progress = [
            f for f in new_file_names if f.endswith((".crdownload", ".tmp", ".part"))
        ]
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
    max_actions: int = 10,
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
        "actions_taken": [],
    }

    try:
        print(f"    Navigating to: {url[:80]}...")
        driver.get(url)
        time.sleep(3)

        # Check if this is a direct file download (PDF, Excel, etc.)
        current_url = driver.current_url
        if any(
            current_url.lower().endswith(ext)
            for ext in [".pdf", ".xlsx", ".xls", ".doc", ".docx", ".ppt", ".pptx"]
        ):
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
                client, screenshot, current_url, page_source, goal, previous_actions
            )

            action_desc = f"{action['action']}: {action.get('target', '')[:50]} - {action['reasoning'][:50]}"
            previous_actions.append(action_desc)
            result["actions_taken"].append(action_desc)
            print(
                f"      Action {i + 1}: {action['action']} - {action['reasoning'][:60]}"
            )

            # Check for completion or give up
            if action["action"] == "download_complete":
                new_files = check_for_new_downloads(firm_download_dir, existing_files)
                if new_files:
                    result["status"] = "success"
                    result["downloaded_files"] = [str(f) for f in new_files]
                else:
                    result["status"] = "uncertain"
                    result["message"] = (
                        "Download may have completed but no new files detected"
                    )
                return result

            elif action["action"] == "give_up":
                result["status"] = "failed"
                result["message"] = action["reasoning"]
                return result

            # Execute action
            success = execute_action(
                driver, action, investor_email, investor_name, investor_company
            )

            if not success:
                print("      Action failed, continuing...")

            # Check for new downloads after each action
            new_files = check_for_new_downloads(
                firm_download_dir, existing_files, timeout=5
            )
            if new_files:
                result["status"] = "success"
                result["downloaded_files"] = [str(f) for f in new_files]
                return result

            time.sleep(1)

        # Max actions reached
        result["status"] = "max_actions_reached"
        result["message"] = (
            f"Reached maximum of {max_actions} actions without completing download"
        )

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
    skip_downloaded: bool = True,
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
        with open(download_history_path, "r", encoding="utf-8") as f:
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

        fund_links = classification.get("artifact_assignments", {}).get(
            "included_links", []
        )

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

            links_to_process.append(
                {
                    "email_id": email_id,
                    "firm_name": firm_name,
                    "link": link,
                    "subject": classification.get("subject", ""),
                }
            )

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
        "results": [],
    }

    try:
        driver = setup_selenium_driver(download_dir, headless)

        for i, item in enumerate(links_to_process):
            link_info = item["link"]
            firm_name = item["firm_name"]
            url = link_info.get("url", "")

            print(f"\n[{i + 1}/{len(links_to_process)}] {firm_name}")
            print(f"    URL: {url[:70]}...")
            print(f"    Type: {link_info.get('link_type', 'unknown')}")

            result = download_link_agentic(
                driver, client, link_info, download_dir, firm_name
            )

            result["email_id"] = item["email_id"]
            result["firm_name"] = firm_name
            result["subject"] = item["subject"]
            report["results"].append(result)

            if result["status"] == "success":
                report["successful"] += 1
                download_history["downloaded_urls"].append(url)
                print(
                    f"    ??Downloaded: {len(result.get('downloaded_files', []))} file(s)"
                )
            elif result["status"] == "failed":
                report["failed"] += 1
                print(f"    ??Failed: {result.get('message', 'Unknown error')}")
            else:
                report["errors"] += 1
                print(f"    ? {result['status']}: {result.get('message', '')}")

            # Save attempt to history
            download_history["attempts"].append(
                {
                    "url": url,
                    "firm": firm_name,
                    "status": result["status"],
                    "timestamp": datetime.now().isoformat(),
                }
            )

            # Brief pause between downloads
            time.sleep(2)

    except Exception as e:
        print(f"\nFatal error: {e}")
        report["fatal_error"] = str(e)

    finally:
        if driver:
            driver.quit()

    # Save download history
    with open(download_history_path, "w", encoding="utf-8") as f:
        json.dump(download_history, f, indent=2)

    # Save report
    report_path = (
        download_dir
        / f"download_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    with open(report_path, "w", encoding="utf-8") as f:
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
        with open(download_history_path, "r", encoding="utf-8") as f:
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
        fund_links = classification.get("artifact_assignments", {}).get(
            "included_links", []
        )

        for link in fund_links:
            url = link.get("url", "")
            if url and url not in downloaded_urls:
                pending.append(
                    {
                        "firm": firm_name,
                        "url": url,
                        "type": link.get("link_type", "unknown"),
                        "description": link.get("description", ""),
                    }
                )

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
                if link["description"]:
                    print(f"    {link['description'][:80]}")
    else:
        print("\nNo pending links found.")

    print(f"\nTotal pending links: {len(pending)}")
    return pending


def get_all_undownloaded_links(
    output_dir: Path = None, download_dir: Path = None
) -> list:
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
        with open(download_history_path, "r", encoding="utf-8") as f:
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
                        "firm": attempt.get("firm", ""),
                    }

    undownloaded = []

    for email_id, classification in cache.items():
        email_cls = classification.get("email_classification", {})
        if not email_cls.get("is_hedge_fund_related"):
            continue

        firm_name = classification.get("firm_name", "UNKNOWN")
        fund_links = classification.get("artifact_assignments", {}).get(
            "included_links", []
        )
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

            undownloaded.append(
                {
                    "email_id": email_id,
                    "firm": firm_name,
                    "subject": subject,
                    "url": url,
                    "link_type": link.get("link_type", "unknown"),
                    "description": link.get("description", ""),
                    "status": status,
                    "last_attempt": last_attempt,
                }
            )

    return undownloaded


def interactive_download_manager(
    output_dir: Path = None,
    download_dir: Path = None,
    firm_filter: str = None,
    status_filter: str = None,
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
        with open(download_history_path, "r", encoding="utf-8") as f:
            download_history = json.load(f)
    else:
        download_history = {
            "downloaded_urls": [],
            "attempts": [],
            "manual_downloads": [],
        }

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

        print(f"\n[{i + 1}/{len(organized_links)}] {'=' * 50}")
        print(f"Firm:        {link['firm']}")
        print(
            f"Subject:     {link['subject'][:60]}..."
            if len(link["subject"]) > 60
            else f"Subject:     {link['subject']}"
        )
        print(f"Type:        {link['link_type']}")
        print(
            f"Description: {link['description'][:70]}..."
            if len(link["description"]) > 70
            else f"Description: {link['description']}"
        )
        print(f"Status:      {link['status']}")
        if link["last_attempt"]:
            print(f"Last attempt: {link['last_attempt']}")
        print(f"\nURL: {link['url']}")

        # Open in browser
        print("\nOpening link in browser...")
        try:
            webbrowser.open(link["url"])
        except Exception as e:
            print(f"Could not open browser: {e}")
            print("Please copy the URL above and open it manually.")

        # Wait for user input
        while True:
            action = input("\nAction [d/s/f/i/o/n/q/l]: ").strip().lower()

            if action == "d":
                # Mark as downloaded
                if link["url"] not in download_history["downloaded_urls"]:
                    download_history["downloaded_urls"].append(link["url"])
                download_history["manual_downloads"].append(
                    {
                        "url": link["url"],
                        "firm": link["firm"],
                        "timestamp": datetime.now().isoformat(),
                        "method": "manual",
                    }
                )
                # Save immediately
                with open(download_history_path, "w", encoding="utf-8") as f:
                    json.dump(download_history, f, indent=2)
                print("??Marked as downloaded")
                downloaded += 1
                processed += 1
                i += 1
                break

            elif action == "s":
                # Skip - don't update anything
                print("??Skipped (will show again next time)")
                skipped += 1
                i += 1
                break

            elif action == "f":
                # Mark as permanently failed
                download_history["attempts"].append(
                    {
                        "url": link["url"],
                        "firm": link["firm"],
                        "status": "manual_failed",
                        "timestamp": datetime.now().isoformat(),
                        "method": "manual",
                    }
                )
                with open(download_history_path, "w", encoding="utf-8") as f:
                    json.dump(download_history, f, indent=2)
                print("??Marked as failed")
                failed += 1
                processed += 1
                i += 1
                break

            elif action == "i":
                # Mark as ignored (add to downloaded to prevent future processing)
                if link["url"] not in download_history["downloaded_urls"]:
                    download_history["downloaded_urls"].append(link["url"])
                download_history["manual_downloads"].append(
                    {
                        "url": link["url"],
                        "firm": link["firm"],
                        "timestamp": datetime.now().isoformat(),
                        "method": "ignored",
                        "reason": "Marked as not relevant by user",
                    }
                )
                with open(download_history_path, "w", encoding="utf-8") as f:
                    json.dump(download_history, f, indent=2)
                print("??Marked as ignored (won't appear again)")
                ignored += 1
                processed += 1
                i += 1
                break

            elif action == "o":
                # Re-open link
                print("Re-opening link in browser...")
                try:
                    webbrowser.open(link["url"])
                except Exception as e:
                    print(f"Could not open browser: {e}")

            elif action == "n":
                # Next without marking
                print("??Moving to next link")
                i += 1
                break

            elif action == "q":
                # Quit
                print("\nExiting interactive manager...")
                print("\nSession Summary:")
                print(f"  Downloaded: {downloaded}")
                print(f"  Skipped:    {skipped}")
                print(f"  Failed:     {failed}")
                print(f"  Ignored:    {ignored}")
                print(f"  Remaining:  {len(organized_links) - i}")
                return

            elif action == "l":
                # List remaining links
                remaining = organized_links[i:]
                print(f"\n--- Remaining {len(remaining)} links ---")
                current_firm = None
                for j, rem_link in enumerate(remaining):
                    if rem_link["firm"] != current_firm:
                        current_firm = rem_link["firm"]
                        print(f"\n{current_firm}:")
                    print(
                        f"  {i + j + 1}. [{rem_link['link_type']}] {rem_link['url'][:50]}..."
                    )

            else:
                print(
                    "Invalid action. Use: d=downloaded, s=skip, f=failed, i=ignore, o=open, n=next, q=quit, l=list"
                )

    # End of list
    print("\n" + "=" * 70)
    print("INTERACTIVE DOWNLOAD MANAGER - COMPLETE")
    print("=" * 70)
    print("\nFinal Summary:")
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
    download_dir: Path = None,
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
        with open(download_history_path, "r", encoding="utf-8") as f:
            download_history = json.load(f)
    else:
        download_history = {
            "downloaded_urls": [],
            "attempts": [],
            "manual_downloads": [],
        }

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
            download_history["manual_downloads"].append(
                {
                    "url": url,
                    "timestamp": datetime.now().isoformat(),
                    "method": "batch_manual",
                }
            )
            marked_count += 1
            print(f"??{url[:60]}...")

    with open(download_history_path, "w", encoding="utf-8") as f:
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
        with open(download_history_path, "r", encoding="utf-8") as f:
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
        fund_links = classification.get("artifact_assignments", {}).get(
            "included_links", []
        )

        if firm_name not in by_firm:
            by_firm[firm_name] = {
                "total": 0,
                "downloaded": 0,
                "pending": 0,
                "failed": 0,
            }

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
                failed_statuses = [
                    "failed",
                    "error",
                    "manual_failed",
                    "max_actions_reached",
                ]
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

    print("\nOverall:")
    print(f"  Total links:      {total_links}")
    print(
        f"  Downloaded:       {total_downloaded} ({100 * total_downloaded / total_links:.1f}%)"
        if total_links > 0
        else "  Downloaded:       0"
    )
    print(f"  Pending:          {total_pending}")
    print(f"  Failed:           {total_failed}")

    # Manual vs automated
    manual_count = len([m for m in manual_downloads if m.get("method") != "ignored"])
    ignored_count = len([m for m in manual_downloads if m.get("method") == "ignored"])
    automated_count = total_downloaded - manual_count - ignored_count

    print("\nDownload Methods:")
    print(f"  Automated:        {automated_count}")
    print(f"  Manual:           {manual_count}")
    print(f"  Ignored:          {ignored_count}")

    print("\nBy Firm:")
    print("-" * 70)
    print(f"{'Firm':<35} {'Total':>8} {'Done':>8} {'Pending':>8} {'Failed':>8}")
    print("-" * 70)

    for firm in sorted(by_firm.keys()):
        stats = by_firm[firm]
        if stats["total"] > 0:
            print(
                f"{firm[:35]:<35} {stats['total']:>8} {stats['downloaded']:>8} {stats['pending']:>8} {stats['failed']:>8}"
            )

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
    print("  8. Monitor for new emails + artifact moves (continuous)")
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
        old_firm = _interactive_firm_picker("Select OLD firm to reassign/remove")
        if not old_firm:
            print("No firm selected.")
        else:
            new_firm = _interactive_firm_picker(
                "Select NEW firm (target)",
                allow_new=True,
            )
            if not new_firm:
                print("No target firm provided.")
            else:
                reassign_firm(old_firm, new_firm)
    elif mode == "8":
        interval = input("Poll interval in seconds (default 30): ").strip()
        interval = int(interval) if interval.isdigit() else 30
        monitor_and_classify_with_moves(poll_interval=interval, run_once=False)
    elif mode == "9":
        monitor_and_classify(run_once=True)
    elif mode == "10":
        manage_firm_aliases()
    elif mode == "11":
        manage_fund_aliases()
    elif mode == "12":
        print("\n--- FUND LINK DOWNLOADER ---")
        firm_filter = (
            input("Filter by firm name (or press Enter for all): ").strip() or None
        )
        link_type = (
            input("Filter by link type (or press Enter for all): ").strip() or None
        )
        max_links_input = input(
            "Max links to process (or press Enter for all): "
        ).strip()
        max_links = int(max_links_input) if max_links_input.isdigit() else None
        headless_input = (
            input("Run in headless mode? (y/n, default n): ").strip().lower()
        )
        headless = headless_input == "y"

        download_fund_links(
            firm_filter=firm_filter,
            link_type_filter=link_type,
            max_links=max_links,
            headless=headless,
        )
    elif mode == "13":
        list_pending_links()
    elif mode == "14":
        print("\n--- INTERACTIVE DOWNLOAD MANAGER ---")
        print("This will open each undownloaded link in your browser")
        print("and let you manually mark each as downloaded.\n")
        firm_filter = (
            input("Filter by firm name (or press Enter for all): ").strip() or None
        )
        print("\nStatus filters: not_attempted, failed, error, max_actions_reached")
        status_filter = (
            input("Filter by status (or press Enter for all): ").strip() or None
        )
        interactive_download_manager(
            firm_filter=firm_filter, status_filter=status_filter
        )
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
                confirm = (
                    input(
                        f"Mark ALL undownloaded links from '{firm_name}' as downloaded? (y/n): "
                    )
                    .strip()
                    .lower()
                )
                if confirm == "y":
                    batch_mark_downloaded(firm_name=firm_name)
                else:
                    print("Cancelled.")
            else:
                print("Firm name required.")
        elif batch_choice == "2":
            print(
                "Enter URLs to mark as downloaded (one per line, empty line to finish):"
            )
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
