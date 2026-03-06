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
from pydantic import BaseModel
from agents import Agent, Runner
from agents.tool import WebSearchTool
from urllib.parse import urlparse, urljoin, parse_qsl, urlencode


class WebSearchFirmResult(BaseModel):
    firm_name: str
    confidence: int  # 0-100


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
                    "Springs China Alpha Fund": {
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


def add_fund_to_firm(
    firm_name: str, fund_display_name: str, aliases: list, mappings: dict
) -> str:
    """
    Register a fund under a firm in the mappings.
    Returns the fund display name used as key.
    """
    canonical = normalize_firm_name(firm_name, mappings)

    if canonical not in mappings.get("canonical_names", {}):
        return ""

    firm_entry = mappings["canonical_names"][canonical]
    if "funds" not in firm_entry:
        firm_entry["funds"] = {}

    # Check if fund already exists (by key or by alias match)
    if fund_display_name in firm_entry["funds"]:
        # Add new aliases if provided
        existing_aliases = set(
            a.lower() for a in firm_entry["funds"][fund_display_name].get("aliases", [])
        )
        for alias in aliases:
            if (
                alias.lower() not in existing_aliases
                and alias.lower() != fund_display_name.lower()
            ):
                firm_entry["funds"][fund_display_name]["aliases"].append(alias)
        return fund_display_name

    # Check if it matches an existing fund by case-insensitive key or alias
    for existing_name, fund_info in firm_entry["funds"].items():
        name_lower = fund_display_name.lower()
        if name_lower == existing_name.lower():
            return existing_name
        for alias in fund_info.get("aliases", []):
            if name_lower == alias.lower():
                return existing_name

    # Create new fund entry
    firm_entry["funds"][fund_display_name] = {
        "aliases": [a for a in aliases if a.lower() != fund_display_name.lower()],
        "auto_added": datetime.now().isoformat(),
    }

    return fund_display_name


def normalize_fund_name(name: str, firm_entry: dict):
    """
    Match a fund name against known funds in a firm entry.
    Returns the fund display name (key) if matched, None otherwise.
    """
    if not name or not firm_entry:
        return None

    name_lower = name.lower().strip()
    funds = firm_entry.get("funds", {})

    for fund_name, fund_info in funds.items():
        if name_lower == fund_name.lower():
            return fund_name
        for alias in fund_info.get("aliases", []):
            if name_lower == alias.lower():
                return fund_name

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


ASSIGNMENT_CONFIDENCE_THRESHOLD = 0.65
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
        "description": "",
        "artifact_type": "other",
        "assigned_firm_name": "",
        "assigned_fund_name": "",
        "confidence": 0.0,
        "method": "",
        "evidence": "",
        "reason_code": REASON_CODE_OTHER,
        "contains_monthly_net_performance_update": False,
        "_recovery": {
            "needed": False,
            "reason": "",
            "original_firm_name": "",
            "original_fund_name": "",
        },
    }


def _blank_link_artifact() -> dict:
    """Canonical field set for a link artifact. Single source of truth."""
    return {
        "artifact_id": "",
        "url": "",
        "description": "",
        "artifact_type": "other",
        "assigned_firm_name": "",
        "assigned_fund_name": "",
        "confidence": 0.0,
        "method": "",
        "evidence": "",
        "reason_code": REASON_CODE_OTHER,
        "contains_monthly_net_performance_update": False,
        "_recovery": {
            "needed": False,
            "reason": "",
            "original_firm_name": "",
            "original_fund_name": "",
        },
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
                "contains_monthly_net_performance_update": _contains_monthly_net_update(
                    att.get("filename", "")
                ),
                "_recovery": {
                    "needed": False,
                    "reason": "",
                    "original_firm_name": override_firm,
                    "original_fund_name": "",
                },
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
                "contains_monthly_net_performance_update": _contains_monthly_net_update(
                    link.get("url", "")
                ),
                "_recovery": {
                    "needed": False,
                    "reason": "",
                    "original_firm_name": override_firm,
                    "original_fund_name": "",
                },
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
    2. Sender domain hints matched against canonical names
    3. Artifact evidence/description scanned for known firm names
    """
    if not firm_mappings:
        return ""

    # Strategy 1: Sibling artifact with firm name (skip if email is from a third party)
    from_tp = email_metadata.get("_from_third_party", False)
    if not from_tp:
        for sibling in all_artifacts:
            if sibling is artifact:
                continue
            sibling_firm = sibling.get("assigned_firm_name", "")
            if sibling_firm:
                return sibling_firm

    # Strategy 2: Domain hints matched against canonical names
    sender = (
        email_metadata.get("from", {}).get("emailAddress", {}).get("address", "") or ""
    ).lower()

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

    # Strategy 3: Scan evidence/description for known firm names
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


def _web_search_firm_for_fund(
    fund_name: str, client: OpenAI = None
) -> WebSearchFirmResult:
    """
    Use OpenAI Agents SDK WebSearchTool to discover which firm manages a given fund.

    Called when a fund name is known but the firm name could not be resolved
    through local recovery strategies (sibling artifacts, domain hints, etc.).

    Returns a WebSearchFirmResult with firm_name (uppercase) and confidence (0-100).
    """
    empty = WebSearchFirmResult(firm_name="", confidence=0)
    if not fund_name or not fund_name.strip():
        return empty

    prompt = (
        f"What is the name of the investment management firm or asset manager "
        f'that manages the fund called "{fund_name}"? '
        f"Search the internet to find the answer. "
        f"Return the firm name and your confidence level (0-100) in the result. "
        f"If you cannot determine the firm, return an empty firm_name with confidence 0."
    )

    try:
        agent = Agent(
            name="FirmSearcher",
            instructions=(
                "You are a financial research assistant. Use the web search tool "
                "to find which firm manages the specified investment fund. "
                "A firm name is the name of the investment management company or "
                "asset manager, e.g. 'BlackRock', 'Bridgewater Associates', "
                "'Two Sigma', 'Citadel', 'Point72 Asset Management'. "
                "It is NOT the fund name itself, a ticker symbol, or an index name. "
                "Return the firm name and your confidence level (0-100)."
            ),
            tools=[WebSearchTool()],
            output_type=WebSearchFirmResult,
            model="gpt-5.2",
        )
        result = Runner.run_sync(agent, prompt)
        ws_result: WebSearchFirmResult = result.final_output
        print(
            f"Web search response for fund '{fund_name}': "
            f"firm='{ws_result.firm_name}', confidence={ws_result.confidence}"
        )

        if not ws_result.firm_name or ws_result.firm_name.upper() == "UNKNOWN":
            return empty

        # Clean up the result — remove trailing punctuation, quotes, periods
        clean_name = ws_result.firm_name.strip(".\"'")
        clean_name = clean_name.split("\n")[0].strip()

        if len(clean_name) > 100 or len(clean_name) < 2:
            return empty

        return WebSearchFirmResult(
            firm_name=clean_name.upper(),
            confidence=ws_result.confidence,
        )
    except Exception as e:
        print(f"Web search for firm failed (fund='{fund_name}'): {e}")
        return empty


def _default_classification(reason: str = "") -> dict:
    return {
        "email_classification": {
            "is_hedge_fund_related": False,
            "from_third_party": False,
            "reasoning": reason,
        },
        "artifact_assignments": _make_empty_artifact_assignments(),
    }


def _finalize_artifact_classification(
    raw_result: dict,
    candidates: dict,
    firm_mappings: dict = None,
    email_metadata: dict = None,
) -> dict:
    """
    Post-process raw GPT classification output into a structured artifact result.

    Takes the raw LLM response and candidate artifacts (attachments/links), then:
    1. Sorts artifacts into included/skipped buckets based on hedge-fund relevance.
    2. Flags low-confidence assignments with a name prefix.
    3. Catches any candidates the LLM omitted and marks them for review.
    4. Tags firm names matching third-party intermediaries with a
       "was third party - {firm}" prefix so they can be identified and
       recovered to the actual fund manager.
    5. Attempts firm name recovery via email context or web search for
       included artifacts with missing, low-confidence, or third-party-tagged
       firm names.
    6. Normalizes and registers firm/fund names in firm_mappings.

    Args:
        raw_result: Raw dict returned by the GPT classification call, containing
            'artifacts' and 'email_classification' keys.
        candidates: Dict with 'attachments' and 'links' lists of candidate artifacts,
            each identified by 'artifact_id'.
        firm_mappings: Optional firm/fund mappings dict for name normalization,
            folder reassignment, and registering new firms/funds.
        email_metadata: Optional dict with sender info (from_address, from_name, etc.)
            used during firm name recovery.

    Returns:
        Dict with keys: 'included_attachments', 'included_links',
        'skipped_attachments', 'skipped_links', and 'summary' (counts).
    """
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
        _recovery_needed = _is_assignment_uncertain(confidence, evidence)

        _recovery_info = {
            "needed": _recovery_needed,
            "reason": "low confidence" if _recovery_needed else "",
            "original_firm_name": original_firm_name,
            "original_fund_name": original_fund_name,
        }

        if is_attachment:
            payload = _blank_attachment_artifact()
            payload.update(
                {
                    "artifact_id": artifact_id,
                    "filename": source.get("filename", ""),
                    "mime_type": source.get("mime_type", ""),
                    "description": record.get("description", ""),
                    "assigned_firm_name": firm_name,
                    "assigned_fund_name": fund_name,
                    "confidence": confidence,
                    "method": method,
                    "evidence": evidence,
                    "reason_code": reason_code,
                    "artifact_type": record.get("artifact_type", "other"),
                    "contains_monthly_net_performance_update": bool(
                        record.get(
                            "contains_monthly_net_performance_update",
                            _contains_monthly_net_update(source.get("filename", "")),
                        )
                    ),
                    "_recovery": _recovery_info,
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
                    "artifact_type": record.get("artifact_type", "other"),
                    "assigned_firm_name": firm_name,
                    "assigned_fund_name": fund_name,
                    "confidence": confidence,
                    "method": method,
                    "evidence": evidence,
                    "reason_code": reason_code,
                    "contains_monthly_net_performance_update": bool(
                        record.get(
                            "contains_monthly_net_performance_update",
                            _contains_monthly_net_update(source.get("url", "")),
                        )
                    ),
                    "_recovery": _recovery_info,
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
                    "artifact_type": "other",
                    "confidence": 0.0,
                    "method": "llm_omitted",
                    "evidence": "LLM did not return this artifact; flagged for review",
                    "reason_code": REASON_CODE_OTHER,
                }
            )
            skipped_links.append(payload)

    # --- Third-party firm name correction ---
    # If GPT identified a third-party sender, mark any artifact firm names that
    # match the intermediary so they get recovered to the actual fund manager.
    raw_email_cls_for_recovery = raw_result.get("email_classification", {}) or {}
    tp_value = raw_email_cls_for_recovery.get("from_third_party", False)
    if tp_value and isinstance(tp_value, str):
        tp_lower = tp_value.lower()
        for item in included_attachments + included_links:
            firm = item.get("assigned_firm_name", "")
            if firm and (firm.lower() in tp_lower or tp_lower in firm.lower()):
                item["_recovery"]["needed"] = True
                item["_recovery"]["reason"] = "third party intermediary"

    # --- Firm name recovery pass ---
    # For included artifacts with blank firm names, attempt to recover using context
    if firm_mappings and email_metadata:
        recovery_metadata = dict(email_metadata) if email_metadata else {}
        recovery_metadata["_from_third_party"] = tp_value

        all_included = included_attachments + included_links
        for item in all_included:
            firm_val = item.get("assigned_firm_name", "")
            if firm_val and not item.get("_recovery", {}).get("needed"):
                continue

            recovery = item.get("_recovery", {})
            recovery["needed"] = True
            if not recovery.get("reason"):
                recovery["reason"] = "empty firm name"
            item["_recovery"] = recovery

            recovery_reason = recovery["reason"]

            WEB_SEARCH_CONFIDENCE_THRESHOLD = 50
            MAX_WEB_SEARCH_ATTEMPTS = 2

            if item.get("assigned_fund_name"):
                # Fund name known — try web search with retry on low confidence
                web_result = None
                for attempt in range(MAX_WEB_SEARCH_ATTEMPTS):
                    web_result = _web_search_firm_for_fund(item["assigned_fund_name"])
                    if (
                        web_result.firm_name
                        and web_result.confidence >= WEB_SEARCH_CONFIDENCE_THRESHOLD
                    ):
                        break

                if (
                    web_result.firm_name
                    and web_result.confidence >= WEB_SEARCH_CONFIDENCE_THRESHOLD
                ):
                    canonical = normalize_firm_name(web_result.firm_name, firm_mappings)
                    item["assigned_firm_name"] = canonical
                    recovery["web_search"] = {
                        "attempted": True,
                        "result": web_result.firm_name,
                        "confidence": web_result.confidence,
                        "attempts": attempt + 1,
                        "success": True,
                    }
                    recovery["final_method"] = "web_search"
                    continue
                else:
                    recovery["web_search"] = {
                        "attempted": True,
                        "result": web_result.firm_name if web_result else "",
                        "confidence": web_result.confidence if web_result else 0,
                        "attempts": MAX_WEB_SEARCH_ATTEMPTS,
                        "success": False,
                    }

            # Fund name unknown — fall back to recovery
            recovered_firm = _recover_firm_name(
                artifact=item,
                all_artifacts=all_included,
                email_metadata=recovery_metadata,
                firm_mappings=firm_mappings,
            )
            if recovered_firm:
                canonical = normalize_firm_name(recovered_firm, firm_mappings)
                item["assigned_firm_name"] = canonical
                recovery["post_classification"] = {
                    "attempted": True,
                    "result": recovered_firm,
                    "success": True,
                }
                recovery["final_method"] = "post_classification"
            else:
                recovery["post_classification"] = {
                    "attempted": True,
                    "result": "",
                    "success": False,
                }
                recovery["final_method"] = "post_classification"

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

    raw_email_cls = raw_result.get("email_classification", {}) or {}
    included_count = assignments["summary"]["included_count"]
    is_hedge_related = included_count > 0

    email_cls = {
        "is_hedge_fund_related": is_hedge_related,
        "from_third_party": raw_email_cls.get("from_third_party", False),
        "reasoning": raw_email_cls.get("reasoning", ""),
    }
    if is_hedge_related:
        email_cls["reasoning"] = (
            email_cls.get("reasoning", "")
            + f"Number of artifacts that are hedge fund related is {included_count}."
        ).strip()

    return {
        "email_classification": email_cls,
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

Your task is to classify each artifact candidate independently and return structured JSON.

------------------------------------------------
DEFINITION — Hedge fund related
------------------------------------------------

Hedge fund related artifacts include materials concerning a specific hedge fund or hedge fund manager, such as:

• performance reports
• monthly factsheets
• investor letters
• CIO updates
• webinars or presentations about a fund
• due diligence materials
• subscription/redemption documents
• cap intro materials presenting specific managers

Hedge fund strategies include (but are not limited to):

long/short equity  
global macro  
event-driven  
relative value  
CTA / managed futures  
multi-strategy  
credit  
distressed  
quantitative  
market-neutral  
arbitrage  

Also include:

• fund-of-hedge-funds
• multi-manager hedge platforms
• hedge fund UCITS wrappers
• SMAs run by hedge fund managers

------------------------------------------------
NOT hedge fund related
------------------------------------------------

Do NOT classify as hedge-fund-related if the artifact is:

• private equity / venture capital / private credit
• mutual funds / ETFs / retail funds
• bank research or market commentary
• broker newsletters
• regulatory notices
• technology vendor marketing
• operational messages
• generic corporate links
• email signatures
• homepages

------------------------------------------------
EVIDENCE HIERARCHY
------------------------------------------------

Use signals in this priority order:

1. email body text
2. email subject
3. attachment filename
4. link context or surrounding text
5. sender domain

Attachment content is NOT available.

------------------------------------------------
ARTIFACT INDEPENDENCE RULE
------------------------------------------------

Each artifact must be evaluated independently.

Do NOT assume artifacts belong to the same fund or firm unless explicitly stated.

------------------------------------------------
FUND AND FIRM ASSIGNMENT
------------------------------------------------

Fund name:
• May be identified from email context or filename.

Firm name:
• Must be explicitly stated in the email context.
• Do NOT infer the firm from the fund name.

If fund is identifiable but firm is not:

assigned_firm_name = ""
assigned_fund_name = detected fund

Never guess firm names.

------------------------------------------------
INTERMEDIARY RULE
------------------------------------------------

Emails may come from:

• administrators
• cap intro desks
• placement agents
• distributors
• IR consultants
• bank platforms

These intermediaries may distribute hedge fund materials.

If the manager firm is explicitly identified, assign it.

Otherwise leave assigned_firm_name empty.

------------------------------------------------
LINK CLASSIFICATION RULE
------------------------------------------------

Generic links such as:

• company homepages
• signature links
• social media
• tracking links
• unsubscribe links

are NOT hedge-fund-related unless the email clearly states the link points to hedge fund materials.

------------------------------------------------
MONTHLY PERFORMANCE DETECTION
------------------------------------------------

Set contains_monthly_net_performance_update = true if the artifact appears to be:

• a monthly factsheet
• a monthly performance report
• a document labeled "monthly", "MTD", or "factsheet"

Even if "net" is not explicitly visible.

Set false if clearly:

• quarterly
• annual
• presentation/webinar without performance tables.

------------------------------------------------
CONFIDENCE
------------------------------------------------

Confidence reflects evidence strength.

High confidence:
• explicit fund name
• clear filename indicators
• direct email references

Medium confidence:
• contextual inference

Low confidence:
• weak signals or ambiguity.

Return exactly one artifact entry for every candidate provided.
Do not omit any artifact. """

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
"""

    _classification_response_format = {
        "type": "json_schema",
        "name": "email_classification_response",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "email_classification": {
                    "type": "object",
                    "properties": {
                        "from_third_party": {
                            "description": "Name of third-party intermediary, or false if direct",
                            "anyOf": [{"type": "string"}, {"type": "boolean"}],
                        },
                        "reasoning": {
                            "type": "string",
                            "description": "Brief explanation of key signals for why it is a third party email",
                        },
                    },
                    "required": ["from_third_party", "reasoning"],
                    "additionalProperties": False,
                },
                "artifacts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "artifact_id": {"type": "string"},
                            "is_hedge_fund_related": {"type": "boolean"},
                            "reason_code": {
                                "type": "string",
                                "enum": [
                                    "fund_document",
                                    "investor_update",
                                    "cap_intro_manager_material",
                                    "tracking_or_system",
                                    "non_fund_marketing",
                                    "other",
                                ],
                            },
                            "assigned_firm_name": {"type": "string"},
                            "assigned_fund_name": {"type": "string"},
                            "confidence": {"type": "number"},
                            "method": {
                                "type": "string",
                                "enum": [
                                    "attachment_content",
                                    "email_context",
                                    "filename_match",
                                    "link_context",
                                ],
                            },
                            "evidence": {
                                "type": "string",
                                "description": "Brief explanation of key signals that led to the classification decision",
                            },
                            "artifact_type": {
                                "type": "string",
                                "enum": [
                                    "performance_report",
                                    "factsheet",
                                    "investor_portal",
                                    "presentation",
                                    "nav_statement",
                                    "due_diligence",
                                    "webinar",
                                    "other",
                                ],
                            },
                            "description": {
                                "type": "string",
                                "description": "What is likely within this link or attachments based on the email context? For example, if the email says 'Please see the attached factsheet for XYZ Fund', then the description could be 'Monthly factsheet for XYZ Fund'. This helps provide more context for the artifact when the filename or URL is not descriptive.",
                            },
                            "contains_monthly_net_performance_update": {
                                "type": "boolean"
                            },
                        },
                        "required": [
                            "artifact_id",
                            "is_hedge_fund_related",
                            "reason_code",
                            "assigned_firm_name",
                            "assigned_fund_name",
                            "confidence",
                            "method",
                            "evidence",
                            "artifact_type",
                            "description",
                            "contains_monthly_net_performance_update",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["email_classification", "artifacts"],
            "additionalProperties": False,
        },
    }

    try:
        response = client.responses.create(
            model="gpt-5.2",
            tools=[{"type": "web_search"}],
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            text={"format": _classification_response_format},
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
        "artifact_type": link_artifact.get("artifact_type", "other"),
        "assigned_firm_name": link_artifact.get("assigned_firm_name", ""),
        "assigned_fund_name": link_artifact.get("assigned_fund_name", ""),
        "confidence": link_artifact.get("confidence", 0.0),
        "method": link_artifact.get("method", ""),
        "evidence": link_artifact.get("evidence", ""),
        "reason_code": link_artifact.get("reason_code", ""),
        "_recovery": link_artifact.get("_recovery", {}),
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
        "_recovery": artifact.get("_recovery", {}),
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


def _classify_single_email(
    client,
    metadata: dict,
    email_id: str,
    from_address: str,
    existing_firms: list,
    firm_mappings: dict,
    classification_cache: dict,
    use_cache: bool = True,
    progress_label: str = "",
):
    """
    Classify a single email: override check -> cache check -> GPT call.

    Returns classification dict, or None on GPT error.
    """
    subject = metadata.get("subject", "No Subject")

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
                "from_third_party": False,
                "reasoning": f"Assigned via override rule for {override_detail}",
            },
            "artifact_assignments": _make_override_artifact_assignments(
                candidates,
                override_firm,
                override_detail,
            ),
        }
        print(f"{progress_label} (override) {subject[:50]}...")
        return classification

    # Check cache (skip error'd entries so GPT is re-called)
    if (
        use_cache
        and email_id in classification_cache
        and not classification_cache[email_id].get("_error")
    ):
        print(f"{progress_label} (cached) {subject[:50]}...")
        return classification_cache[email_id]

    # Classify with GPT
    print(f"{progress_label} Classifying: {subject[:50]}...")
    classification = classify_email_with_gpt(
        client, metadata, existing_firms, firm_mappings
    )

    if classification.get("_error"):
        print("    -> GPT error, will retry next run")
        return None

    classification_cache[email_id] = classification
    return classification


def _process_classified_email(
    classification: dict,
    email_id: str,
    email_folder: Path,
    metadata: dict,
    from_address: str,
    subject: str,
    output_dir: Path,
    firm_mappings: dict,
    existing_firms: list,
) -> dict:
    """
    Post-process a classified email: hedge fund override, firm registration,
    artifact organization. Returns a classification_entry dict.
    """
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
        "from": from_address,
        **classification,
    }

    canonical_name = None
    if email_cls.get("is_hedge_fund_related"):
        # Derive firm name from first included artifact
        all_included = classification.get("artifact_assignments", {}).get(
            "included_attachments", []
        ) + classification.get("artifact_assignments", {}).get("included_links", [])
        raw_firm_name = ""
        for item in all_included:
            if item.get("assigned_firm_name"):
                raw_firm_name = item["assigned_firm_name"]
                break
        from_third_party = email_cls.get("from_third_party", False)

        if raw_firm_name:
            canonical_name = normalize_firm_name(raw_firm_name, firm_mappings)
            canonical_name = apply_folder_reassignment(canonical_name, firm_mappings)

            if not from_third_party:
                domain_hints = extract_domain_hints(from_address)
                aliases = [raw_firm_name] + domain_hints
            else:
                aliases = [raw_firm_name]
            add_firm_to_mappings(canonical_name, aliases, firm_mappings)

            (output_dir / sanitize_folder_name(canonical_name)).mkdir(
                parents=True, exist_ok=True
            )

            if canonical_name not in existing_firms:
                existing_firms.append(canonical_name)

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
        classification_entry["needs_review_count"] = org_result["needs_review_count"]

        if org_result["organized_count"] > 0:
            print(
                f"    -> Organized {org_result['organized_count']} artifact(s) to firm folders"
            )
        if org_result["needs_review_count"] > 0:
            print(
                f"    -> {org_result['needs_review_count']} artifact(s) sent to {NEEDS_REVIEW_FOLDER}/"
            )

    classification_entry["_canonical_name"] = canonical_name
    return classification_entry


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

        classification = _classify_single_email(
            client,
            metadata,
            email_id,
            from_address,
            existing_firms,
            firm_mappings,
            classification_cache,
            use_cache=True,
            progress_label=f"[{i + 1}/{len(email_folders)}]",
        )
        if classification is None:
            continue

        entry = _process_classified_email(
            classification,
            email_id,
            email_folder,
            metadata,
            from_address,
            subject,
            output_dir,
            firm_mappings,
            existing_firms,
        )

        email_cls = classification.get("email_classification", {})
        canonical_name = entry.pop("_canonical_name", None)

        if email_cls.get("is_hedge_fund_related"):
            report["hedge_fund_related"] += 1

            if canonical_name:
                from_third_party = email_cls.get("from_third_party", False)
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

            if (
                entry.get("organized_count", 0) == 0
                and entry.get("needs_review_count", 0) == 0
            ):
                if not canonical_name:
                    report["hedge_fund_related"] -= 1
                    report["non_hedge_fund"] += 1
                print("    -> Hedge fund related but no artifacts could be organized")
        else:
            report["non_hedge_fund"] += 1
            print("    -> Not hedge fund related")

        report["classifications"].append(entry)

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

        classification = _classify_single_email(
            client,
            metadata,
            email_id,
            from_address,
            existing_firms,
            firm_mappings,
            classification_cache,
            use_cache=False,
            progress_label=f"[{i + 1}/{len(new_folders)}]",
        )
        if classification is None:
            continue

        entry = _process_classified_email(
            classification,
            email_id,
            email_folder,
            metadata,
            from_address,
            subject,
            output_dir,
            firm_mappings,
            existing_firms,
        )
        entry.pop("_canonical_name", None)

        email_cls = classification.get("email_classification", {})
        if not email_cls.get("is_hedge_fund_related"):
            print("    -> Skipped: Not hedge fund related")

        results.append(entry)

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
      destination_path → {email_id, artifact_index, list_key}

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
                artifact.setdefault("_recovery", {})["final_method"] = new_method

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
                report_artifacts[idx].setdefault("_recovery", {})["final_method"] = (
                    new_method
                )
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
                "_recovery": {
                    "needed": True,
                    "reason": "manual_rejection",
                    "original_firm_name": old_firm,
                    "original_fund_name": old_fund,
                    "final_method": "manual_rejection",
                },
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
                proxy_data.setdefault("_recovery", {})["final_method"] = new_method
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
            for fund_name, fund_info in funds.items():
                fund_aliases = fund_info.get("aliases", [])
                alias_str = (
                    f" (aliases: {', '.join(fund_aliases)})" if fund_aliases else ""
                )
                print(f"    - {fund_name}{alias_str}")

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
        for fund_name, fund_info in funds.items():
            print(f"\n  {fund_name}")
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


def manage_aliases(output_dir: Path = None):
    """Interactive menu to manage firm and fund aliases."""
    output_dir = output_dir or DEFAULT_OUTPUT_DIR

    print("\n" + "=" * 50)
    print("MANAGE ALIASES")
    print("=" * 50)

    mappings = load_firm_mappings(output_dir)
    canonical_names = mappings.get("canonical_names", {})

    if not canonical_names:
        print("No firms found in mappings.")
        return

    sorted_firms = sorted(canonical_names.keys())
    print("\nAvailable firms:")
    for i, firm in enumerate(sorted_firms, 1):
        alias_count = len(canonical_names[firm].get("aliases", []))
        fund_count = len(canonical_names[firm].get("funds", {}))
        print(f"  {i}. {firm} ({alias_count} aliases, {fund_count} funds)")

    raw_input = input("\nEnter firm name or number: ").strip()
    if not raw_input:
        print("No firm name provided.")
        return

    firm_name = _resolve_from_numbered_list(raw_input, sorted_firms)
    if firm_name is None:
        print(f"Invalid selection: {raw_input}")
        return

    # Show firm aliases
    aliases = list_firm_aliases(firm_name, output_dir)

    print("\nWhat would you like to manage?")
    print("  1. Firm aliases")
    print("  2. Fund aliases")
    print("  3. Exit")

    level = input("\nEnter choice (1-3): ").strip()

    if level == "1":
        print("\nOptions:")
        print("  1. Add a new firm alias")
        print("  2. Delete a firm alias")

        choice = input("\nEnter choice (1-2): ").strip()
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
        else:
            print("Invalid choice.")

    elif level == "2":
        funds = list_firm_funds(firm_name, output_dir)
        if not funds:
            return

        fund_names = list(funds.keys())
        raw_fund = input("\nEnter fund name or number: ").strip()
        if not raw_fund:
            print("No fund provided.")
            return

        fund_name = _resolve_from_numbered_list(raw_fund, fund_names)
        if fund_name is None or fund_name not in funds:
            print(f"Invalid fund selection: {raw_fund}")
            return

        print("\nOptions:")
        print("  1. Add a fund alias")
        print("  2. Delete a fund alias")

        choice = input("\nEnter choice (1-2): ").strip()
        if choice == "1":
            new_alias = input("Enter new alias: ").strip()
            if new_alias:
                add_fund_alias_to_firm(firm_name, fund_name, new_alias, output_dir)
            else:
                print("No alias provided.")
        elif choice == "2":
            fund_aliases = funds[fund_name].get("aliases", [])
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
                delete_fund_alias_from_firm(firm_name, fund_name, resolved, output_dir)
            else:
                print("No alias provided.")
        else:
            print("Invalid choice.")

    elif level == "3":
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
        for i, (fund_name, fund_info) in enumerate(funds.items(), 1):
            print(f"\n  {i}. {fund_name}")
            fund_aliases = fund_info.get("aliases", [])
            if fund_aliases:
                print(f"     Aliases: {', '.join(fund_aliases)}")
            else:
                print("     Aliases: (none)")
    else:
        print("  (No funds registered)")

    return funds


def add_fund_alias_to_firm(
    firm_name: str, fund_name: str, alias: str, output_dir: Path = None
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
    if fund_name not in funds:
        print(f"Fund '{fund_name}' not found under firm '{firm_key}'.")
        return False

    fund_aliases = funds[fund_name].get("aliases", [])
    if alias.lower() in [a.lower() for a in fund_aliases]:
        print(f"Alias '{alias}' already exists for fund '{fund_name}'.")
        return False

    fund_aliases.append(alias)
    funds[fund_name]["aliases"] = fund_aliases
    save_firm_mappings(mappings, output_dir)
    print(f"Added alias '{alias}' to fund '{fund_name}' under '{firm_key}'.")
    return True


def delete_fund_alias_from_firm(
    firm_name: str, fund_name: str, alias: str, output_dir: Path = None
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
    if fund_name not in funds:
        print(f"Fund '{fund_name}' not found under firm '{firm_key}'.")
        return False

    fund_aliases = funds[fund_name].get("aliases", [])
    alias_found = None
    for a in fund_aliases:
        if a.lower() == alias.lower():
            alias_found = a
            break

    if alias_found:
        fund_aliases.remove(alias_found)
        funds[fund_name]["aliases"] = fund_aliases
        save_firm_mappings(mappings, output_dir)
        print(f"Deleted alias '{alias_found}' from fund '{fund_name}'.")
        return True
    else:
        print(f"Alias '{alias}' not found for fund '{fund_name}'.")
        return False


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
    print(" 10. Manage aliases (firm/fund)")
    print()

    if len(sys.argv) > 1:
        mode = sys.argv[1]
    else:
        mode = input("Enter mode (1-10): ").strip()

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
        manage_aliases()
    else:
        print("Invalid mode.")


if __name__ == "__main__":
    main()
