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
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from datetime import datetime
from typing import Optional  # noqa: F401 - kept for potential future use
from openai import OpenAI
from pydantic import BaseModel
from agents import Agent, Runner
from agents.tool import WebSearchTool
from urllib.parse import urlparse, urljoin, parse_qsl, urlencode
from fofproject.log import log, CLASSIFY
from fofproject.paths import DEFAULT_EMAIL_INPUT_DIR, DEFAULT_OUTPUT_DIR


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

# File names for persistent data
FIRM_MAPPINGS_FILE = "firm_fund_mappings.json"  # Human-editable mappings
CLASSIFICATION_REPORT_FILE = "classification_report.json"  # Full report of all classifications (single source of truth)

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

# Our own firm names / domains — must never be added as aliases to external firms.
_OWN_FIRM_NAMES = frozenset(
    {
        "river delta wealth management",
        "river delta",
        "riverdeltawm",
        "river delta global frontier fund",
        "rdgff",
    }
)


def _is_own_firm(name: str) -> bool:
    """Return True if *name* matches one of our own firm identifiers."""
    lowered = name.strip().lower()
    if lowered in _OWN_FIRM_NAMES:
        return True
    # Also catch partial domain hits like "riverdeltawm"
    return any(own in lowered or lowered in own for own in _OWN_FIRM_NAMES)


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
                "funds": {
                    "Springs China Alpha Fund": {
                        "aliases": ["China Alpha"],
                        "artifacts": {
                            "<artifact_id>": {
                                "file_name": "2025-01-15_factsheet.pdf",
                                "identifier": "0011223344",  // 10-digit text or null
                                "contains_monthly_net_performance_update": true,
                                "processed": false
                            }
                        }
                    }
                },
                "artifacts": {
                    // artifacts directly under firm, not belonging to a specific fund
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
    mappings.setdefault("_metadata", {})["last_updated"] = datetime.now().isoformat()

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
    # Strip trailing commas and other punctuation left over from LLM output
    cleaned = re.sub(r"[,;]+$", "", cleaned)
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
            "funds": {},
            "artifacts": {},
        }

    # Add new aliases, rejecting purely generic terms like "capital"
    existing_aliases = set(
        a.lower() for a in mappings["canonical_names"][canonical]["aliases"]
    )
    for alias in aliases:
        alias_words = alias.strip().split()
        all_generic = alias_words and all(
            w.lower() in _GENERIC_FIRM_WORDS for w in alias_words
        )
        if (
            not all_generic
            and not _is_own_firm(alias)
            and alias.lower() not in existing_aliases
        ):
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
        if (
            len(part) > 2
            and part
            not in [
                "mail",
                "email",
                "info",
                "contact",
                "admin",
                "www",
            ]
            and part not in _GENERIC_FIRM_WORDS
            and not _is_own_firm(part)
        ):
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
        "identifier": None,
        "artifacts": {},
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


def _lookup_firm_for_fund(fund_name: str, firm_mappings: dict) -> str:
    """Check if any firm in mappings already has this fund registered."""
    if not fund_name or not firm_mappings:
        return ""
    for canonical, info in firm_mappings.get("canonical_names", {}).items():
        if normalize_fund_name(fund_name, info):
            return canonical
    return ""


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
CONFLICT_IDENTIFIER_PREFIX = "404 multiple identifier"
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
        prefix = f"{email_id[-8:]}_" if email_id else ""
        links.append(
            {
                "artifact_id": f"{prefix}link_{link_idx}",
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

    prefix = f"{email_id[-8:]}_" if email_id else ""
    candidates = []
    for idx, att in enumerate(attachments, 1):
        filename = att.get("name", "")
        mime_type = att.get("contentType", "")
        is_inline = bool(att.get("isInline", False))

        candidates.append(
            {
                "artifact_id": f"{prefix}att_{idx}",
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

    # Strategy 2: Domain hints matched against canonical names (skip if email is from a third party)
    if not from_tp:
        sender = (
            email_metadata.get("from", {}).get("emailAddress", {}).get("address", "")
            or ""
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
    canonical_names = firm_mappings.get("canonical_names", {})
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
        log.detail(
            f"Web search for '{fund_name}': "
            f"firm='{ws_result.firm_name}', confidence={ws_result.confidence}",
            phase=CLASSIFY,
        )

        if not ws_result.firm_name or ws_result.firm_name.upper() == "UNKNOWN":
            return empty

        # Clean up the result — remove trailing punctuation, quotes, periods, commas
        clean_name = ws_result.firm_name.strip(".\"',")
        clean_name = clean_name.split("\n")[0].strip()

        if len(clean_name) > 100 or len(clean_name) < 2:
            return empty

        return WebSearchFirmResult(
            firm_name=clean_name.upper(),
            confidence=ws_result.confidence,
        )
    except Exception as e:
        log.warn(f"Web search for firm failed (fund='{fund_name}'): {e}", phase=CLASSIFY)
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
            if payload.get("artifact_type") in (
                "performance_report",
                "factsheet",
                "presentation",
            ):
                payload["contains_monthly_net_performance_update"] = True
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
            if payload.get("artifact_type") in (
                "performance_report",
                "factsheet",
                "presentation",
            ):
                payload["contains_monthly_net_performance_update"] = True
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

            WEB_SEARCH_CONFIDENCE_THRESHOLD = 50
            MAX_WEB_SEARCH_ATTEMPTS = 2

            if item.get("assigned_fund_name"):
                fund_name = item["assigned_fund_name"]

                # Check mappings first — skip web search if fund→firm already known
                known_firm = _lookup_firm_for_fund(fund_name, firm_mappings)
                if known_firm:
                    item["assigned_firm_name"] = known_firm
                    recovery["mappings_lookup"] = {
                        "result": known_firm,
                        "success": True,
                    }
                    recovery["final_method"] = "mappings_lookup"
                    continue

                # Fund not in mappings — do web search with retry
                web_result = None
                for attempt in range(MAX_WEB_SEARCH_ATTEMPTS):
                    web_result = _web_search_firm_for_fund(fund_name)
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
                    canonical = apply_folder_reassignment(canonical, firm_mappings)
                    item["assigned_firm_name"] = canonical

                    # Immediately register in mappings so subsequent artifacts skip search
                    add_firm_to_mappings(
                        canonical, [web_result.firm_name], firm_mappings
                    )
                    add_fund_to_firm(canonical, fund_name, [], firm_mappings)

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
                # If recovery returned the intermediary itself, label it as
                # third-party so downstream routing can distinguish it.
                if recovery.get("reason") == "third party intermediary":
                    tp_name = recovery_metadata.get("_from_third_party", "")
                    if (
                        isinstance(tp_name, str)
                        and tp_name
                        and (
                            canonical.lower() in tp_name.lower()
                            or tp_name.lower() in canonical.lower()
                        )
                    ):
                        item["assigned_firm_name"] = f"third_party {canonical}"
                        recovery["post_classification"] = {
                            "attempted": True,
                            "result": recovered_firm,
                            "success": False,
                        }
                        recovery["final_method"] = "post_classification"
                        continue
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
                # If the artifact was flagged as third-party intermediary and
                # all recovery attempts failed, clear the firm name so the
                # artifact routes to NEEDS_REVIEW instead of the intermediary's folder.
                if recovery.get("reason") == "third party intermediary" and item.get(
                    "assigned_fund_name"
                ):
                    item["assigned_firm_name"] = ""
                # Third-party intermediary with no fund name — preserve the
                # intermediary identity with a prefix so it can be routed.
                elif recovery.get(
                    "reason"
                ) == "third party intermediary" and not item.get("assigned_fund_name"):
                    original_firm = item.get("assigned_firm_name", "")
                    if original_firm:
                        item["assigned_firm_name"] = f"third_party {original_firm}"

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
            + f" Number of artifacts that are hedge fund related is {included_count}."
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
        log.detail(
            f"  [pre-filter] {len(filter_log)} link(s) filtered before LLM: "
            + ", ".join(f"{e['reason']}" for e in filter_log),
            phase=CLASSIFY,
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

• Long/Short Equity (LS equity, EQ L/S, equity L-S, long-short equities)
• Global Macro (macro, discretionary macro, macro trading)
• Sector Specialists (biotech, healthcare, energy, financials, consumer, technology, industrials, TMT etc.)
• Event-Driven (special sits, special situations, corporate events, merger arb, activist)
• Relative Value (RV, RV arb, relative value arb, basis trading)
• CTA / Managed Futures (CTA, managed futures, trend, trend-following, systematic futures)
• Multi-Strategy (multi-strat, multi strategy, platform, pod shop, diversified alpha)
• Credit (credit L/S, credit opportunities, opportunistic credit, structured credit, performing credit)
• Distressed (distressed debt, distressed credit, stressed credit, restructuring, turnaround)
• Quantitative (quant, systematic, model-driven, systematic equities)
• Market Neutral (EMN, equity market neutral, equity neutral)
• Arbitrage (arb, stat arb, index arb, statistical arbitrage, risk arb)

Also include:

• fund-of-hedge-funds
• multi-manager hedge platforms
• hedge fund UCITS wrappers
• SMAs run by hedge fund managers

------------------------------------------------
NOT hedge fund related
------------------------------------------------

Do NOT classify as hedge-fund-related if the artifact is:

• broker newsletters
• regulatory notices
• technology vendor marketing
• operational messages
• generic corporate links
• email signatures
• homepages

------------------------------------------------
Third party intermediary
------------------------------------------------

A third party intermediary is a firm that distributes or forwards hedge fund materials on behalf of a manager, rather than the manager sending them directly.

Third party categories:
• fund administrators (e.g., CITCO, APEX FUND SERVICES)
• prime brokers / securities services (e.g., GOLDMAN SACHS, MORGAN STANLEY, BNP PARIBAS)
• cap intro desks (e.g., MAREX)
• sell-side distribution
• hedge fund marketing agents / placement agents (e.g., AGECROFT)
• distributors and IR consultants
• derivatives brokers 

Rules:
• If the email is from a known third party, 
    - set from_third_party to the intermediary firm name
    - assign the asset manager firm name at assigned_firm_name at the artifact level 
• A forwarded email is NOT automatically third party — only classify as third party if the sender is an intermediary firm
• When unsure, default to false — do not invent a third party relationship

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
MONTHLY NET PERFORMANCE DETECTION
------------------------------------------------

Set contains_monthly_net_performance_update = true if the artifact appears to be:

• a monthly factsheet
• a monthly performance report
• a monthly presentation that is related and could potentiall contain performance
• a monthly newsletter that does not appear to be an actual letter from the manager 
• a quartely or annually report can sometimes contain monthly performance updates as well

Even if "net" and "performance" are not explicitly visible.

Set false if clearly:

• webinar 
• DDQ or operational materials
• subscription/redemption docs
• a commentary or an actual letter regarding a matter other than performance.

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
                            "description": "Name of third-party intermediary, or false if not sent via third-party",
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
        log.error(f"GPT classification error: {e}", phase=CLASSIFY)
        result = _default_classification(reason=f"Classification error: {str(e)}")
        result["_error"] = True
        result["artifact_assignments"] = _make_empty_artifact_assignments(
            total_attachments=len(attachments), total_links=len(links)
        )
        return result


def _load_classification_lookup(output_dir: Path) -> dict:
    """Build {email_id: classification} lookup from the classification report.

    Used to skip GPT calls for already-classified emails. Returns only the
    classification-relevant fields (email_classification, artifact_assignments)
    so the caller gets the same shape as a fresh GPT result.
    """
    report_path = output_dir / CLASSIFICATION_REPORT_FILE
    if not report_path.exists():
        return {}

    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    lookup = {}
    for entry in report.get("classifications", []):
        email_id = entry.get("email_id", "")
        if email_id:
            lookup[email_id] = {
                "email_classification": entry.get("email_classification", {}),
                "artifact_assignments": entry.get("artifact_assignments", {}),
            }
    return lookup


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
            # Include identifier in folder name if stored in mappings
            firm_entry = firm_mappings.get("canonical_names", {}).get(canonical, {})
            matched_fund = normalize_fund_name(fund_name, firm_entry)
            if matched_fund:
                fund_data = firm_entry.get("funds", {}).get(matched_fund, {})
                fund_identifier = fund_data.get("identifier")
                if fund_identifier:
                    safe_fund = f"{safe_fund} - {fund_identifier}"
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

        # Fuzzy match (case-insensitive, then normalized separators)
        attachments_dir = email_folder / "attachments"
        search_dirs = (
            [attachments_dir, email_folder]
            if attachments_dir.is_dir()
            else [email_folder]
        )

        def _normalize_name(n: str) -> str:
            """Collapse spaces, hyphens, underscores for comparison."""
            return re.sub(r"[\s\-_]+", "", n).lower()

        target_lower = filename.lower()
        target_normalized = _normalize_name(filename)

        for search_dir in search_dirs:
            for f in search_dir.iterdir():
                if not f.is_file():
                    continue
                # Exact case-insensitive
                if f.name.lower() == target_lower:
                    return f
                # Normalized (ignore space/hyphen/underscore differences)
                if _normalize_name(f.name) == target_normalized:
                    return f

    return None


def _embed_artifact_id_in_filename(filename: str, artifact_id: str) -> str:
    """Embed artifact_id into a filename before the extension.

    Example: 'factsheet.pdf' + 'EHI5AAA=_att_1' -> 'factsheet - EHI5AAA=_att_1.pdf'
    For compound extensions like '.link.json': 'slug.link.json' -> 'slug - id.link.json'
    """
    if not artifact_id:
        return filename

    # Handle .link.json compound extension
    if filename.endswith(".link.json"):
        base = filename[: -len(".link.json")]
        return f"{base} - {artifact_id}.link.json"

    path = Path(filename)
    stem = path.stem
    ext = path.suffix
    return f"{stem} - {artifact_id}{ext}"


def _parse_artifact_id_from_filename(filename: str) -> str:
    """Extract artifact_id from a filename that follows the ' - {artifact_id}.ext' convention.

    Returns empty string if no artifact_id is found.
    """
    # Handle .link.json compound extension
    if filename.endswith(".link.json"):
        base = filename[: -len(".link.json")]
    else:
        base = Path(filename).stem

    match = re.search(r" - ([a-zA-Z0-9=_]+)$", base)
    return match.group(1) if match else ""


def _strip_artifact_id_from_filename(filename: str) -> str:
    """Strip the artifact_id suffix from a filename, returning the base name.

    Example: 'factsheet - art002.pdf' -> 'factsheet.pdf'
             'link_report - link001.link.json' -> 'link_report.link.json'
    Returns the filename unchanged if no artifact_id pattern is found.
    """
    art_id = _parse_artifact_id_from_filename(filename)
    if not art_id:
        return filename
    # Remove the ' - {artifact_id}' portion
    suffix_pattern = f" - {re.escape(art_id)}"
    if filename.endswith(".link.json"):
        base = filename[: -len(".link.json")]
        base = re.sub(re.escape(suffix_pattern) + r"$", "", base)
        return f"{base}.link.json"
    else:
        path = Path(filename)
        stem = path.stem
        ext = path.suffix
        stem = re.sub(re.escape(suffix_pattern) + r"$", "", stem)
        return f"{stem}{ext}"


def _create_link_proxy_file(
    link_artifact: dict,
    email_metadata: dict,
    dest_dir: Path,
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

    artifact_id = link_artifact.get("artifact_id", "")
    filename = f"link_{slug}.link.json"
    filename = _embed_artifact_id_in_filename(filename, artifact_id)

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


def _register_artifact_in_mappings(
    artifact: dict,
    firm_name: str,
    fund_name: str,
    dest_filename: str,
    firm_mappings: dict,
):
    """Register an artifact in the firm_fund_mappings under the appropriate firm/fund.

    Adds the artifact entry keyed by artifact_id with file_name, identifier,
    contains_monthly_net_performance_update, and processed fields.
    """
    artifact_id = artifact.get("artifact_id", "")
    if not artifact_id or not firm_name:
        return

    canonical = normalize_firm_name(firm_name, firm_mappings)
    canonical_names = firm_mappings.get("canonical_names", {})
    if canonical not in canonical_names:
        return

    firm_entry = canonical_names[canonical]
    artifact_record = {
        "file_name": _strip_artifact_id_from_filename(dest_filename),
        "identifier": None,
        "contains_monthly_net_performance_update": bool(
            artifact.get("contains_monthly_net_performance_update", False)
        ),
        "processed": False,
    }

    if fund_name:
        # Try to find the fund in the firm's funds
        matched_fund = normalize_fund_name(fund_name, firm_entry)
        if matched_fund and matched_fund in firm_entry.get("funds", {}):
            firm_entry["funds"][matched_fund].setdefault("artifacts", {})
            firm_entry["funds"][matched_fund]["artifacts"][artifact_id] = (
                artifact_record
            )
            return

    # No fund match or no fund_name — store at firm level
    firm_entry.setdefault("artifacts", {})
    firm_entry["artifacts"][artifact_id] = artifact_record


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
            artifact_id = att.get("artifact_id", "")
            dest_filename = _embed_artifact_id_in_filename(safe_filename, artifact_id)
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
            _register_artifact_in_mappings(
                att,
                firm_name,
                fund_name,
                dest_filename if source_file and source_file.exists() else filename,
                firm_mappings,
            )
            result["organized_count"] += 1

    # Process links
    for link in included_links:
        firm_name = link.get("assigned_firm_name", "")
        fund_name = link.get("assigned_fund_name", "")

        dest_dir = _resolve_artifact_dest_dir(
            firm_name, fund_name, output_dir, firm_mappings
        )

        proxy_path = _create_link_proxy_file(link, email_metadata, dest_dir)
        result["destinations"].append(str(proxy_path))

        is_needs_review = NEEDS_REVIEW_FOLDER in str(dest_dir)
        if is_needs_review:
            result["needs_review_count"] += 1
        else:
            _register_artifact_in_mappings(
                link,
                firm_name,
                fund_name,
                proxy_path.name if proxy_path else "",
                firm_mappings,
            )

    return result


def _classify_single_email(
    client,
    metadata: dict,
    email_id: str,
    from_address: str,
    existing_firms: list,
    firm_mappings: dict,
    classification_lookup: dict,
    lookup_lock: threading.Lock = None,
    use_lookup: bool = True,
    progress_label: str = "",
):
    """
    Classify a single email: override check -> report lookup -> GPT call.

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
        log.detail(f"{progress_label} (override) {subject[:50]}...", phase=CLASSIFY)
        return classification

    # Check report lookup (skip GPT for already-classified emails)
    if use_lookup:
        if lookup_lock:
            with lookup_lock:
                cached = classification_lookup.get(email_id)
        else:
            cached = classification_lookup.get(email_id)
        if cached is not None:
            log.detail(f"{progress_label} (cached) {subject[:50]}...", phase=CLASSIFY)
            return cached

    # Classify with GPT
    log.detail(f"{progress_label} Classifying: {subject[:50]}...", phase=CLASSIFY)
    classification = classify_email_with_gpt(
        client, metadata, existing_firms, firm_mappings
    )

    if classification.get("_error"):
        log.error(f"  GPT error for {subject[:40]}..., will retry next run", phase=CLASSIFY)
        return None

    if lookup_lock:
        with lookup_lock:
            classification_lookup[email_id] = classification
    else:
        classification_lookup[email_id] = classification
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
        classification_entry["destinations"] = list(
            dict.fromkeys(org_result["destinations"])
        )
        classification_entry["organized_count"] = org_result["organized_count"]
        classification_entry["needs_review_count"] = org_result["needs_review_count"]

        if org_result["organized_count"] > 0:
            log.detail(
                f"  Organized {org_result['organized_count']} artifact(s) to firm folders",
                phase=CLASSIFY,
            )
        if org_result["needs_review_count"] > 0:
            log.detail(
                f"  {org_result['needs_review_count']} artifact(s) sent to {NEEDS_REVIEW_FOLDER}/",
                phase=CLASSIFY,
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
    classification_lookup = (
        {} if force_reclassify else _load_classification_lookup(output_dir)
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

    log.info(f"Found {len(email_folders)} email folders to process", phase=CLASSIFY)
    log.info(f"Output directory: {output_dir}", phase=CLASSIFY)

    # --- Phase 1: Load metadata and classify emails (GPT calls in parallel) ---
    email_data = []  # list of (email_folder, metadata, email_id, from_address, subject)
    for email_folder in email_folders:
        metadata_path = email_folder / "metadata.json"
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)
        except Exception as e:
            log.error(f"Error loading {email_folder.name}: {e}", phase=CLASSIFY)
            report["errors"] += 1
            continue
        email_id = metadata.get("id", email_folder.name)
        subject = metadata.get("subject", "No Subject")
        from_address = (
            metadata.get("from", {}).get("emailAddress", {}).get("address", "")
        )
        email_data.append((email_folder, metadata, email_id, from_address, subject))

    # Classify emails concurrently (GPT I/O bound); overrides and cache hits
    # are fast but still safe to run in the pool.
    max_workers = min(8, len(email_data)) or 1
    classifications = {}  # email_id -> classification dict
    _lookup_lock = threading.Lock()

    def _classify_task(idx, email_folder, metadata, email_id, from_address, subject):
        return email_id, _classify_single_email(
            client,
            metadata,
            email_id,
            from_address,
            existing_firms,
            firm_mappings,
            classification_lookup,
            lookup_lock=_lookup_lock,
            use_lookup=True,
            progress_label=f"[{idx + 1}/{len(email_data)}]",
        )

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                _classify_task, i, ef, md, eid, fa, subj
            ): eid
            for i, (ef, md, eid, fa, subj) in enumerate(email_data)
        }
        for future in as_completed(futures):
            email_id, classification = future.result()
            if classification is not None:
                classifications[email_id] = classification

    # --- Phase 2: Post-process serially (mutates firm_mappings) ---
    for email_folder, metadata, email_id, from_address, subject in email_data:
        report["total_emails"] += 1

        classification = classifications.get(email_id)
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
                log.detail("  Hedge fund related but no artifacts could be organized", phase=CLASSIFY)
        else:
            report["non_hedge_fund"] += 1
            log.detail("  Not hedge fund related", phase=CLASSIFY)

        report["classifications"].append(entry)

    # Save updated data
    save_firm_mappings(firm_mappings, output_dir)

    # Save report (single source of truth — no separate cache file)
    report_path = output_dir / CLASSIFICATION_REPORT_FILE
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # Summary
    log.info("CLASSIFICATION SUMMARY", phase=CLASSIFY)
    log.info(f"Total emails processed: {report['total_emails']}", phase=CLASSIFY)
    log.info(f"Hedge fund related: {report['hedge_fund_related']}", phase=CLASSIFY)
    log.info(f"Non-hedge fund: {report['non_hedge_fund']}", phase=CLASSIFY)
    log.info(f"Errors: {report['errors']}", phase=CLASSIFY)
    log.info(f"Firms identified: {len(report['firms_found'])}", phase=CLASSIFY)

    for firm, info in sorted(report["firms_found"].items()):
        log.detail(f"  - {firm}: {info['email_count']} email(s)", phase=CLASSIFY)

    log.info(f"Report saved to: {report_path}", phase=CLASSIFY)
    log.info(f"Firm mappings saved to: {output_dir / FIRM_MAPPINGS_FILE}", phase=CLASSIFY)

    return report


def get_processed_folders(output_dir: Path) -> set:
    """
    Get set of email folder names that have already been processed.
    Reads from classification report. Error'd emails never reach the report
    (they are skipped during classification), so they are automatically retried.
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

    return processed


def classify_new_emails(
    email_input_dir: Path = None, output_dir: Path = None, *, mappings: dict = None
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
        f
        for f in email_input_dir.iterdir()
        if f.is_dir() and (f / "metadata.json").exists()
    ]

    # Filter to only new folders
    new_folders = [f for f in all_folders if f.name not in processed_folders]

    if not new_folders:
        return {"new_folders_found": 0, "classifications": []}

    log.info(f"Found {len(new_folders)} new email(s) to classify.", phase=CLASSIFY)

    # Process only the new folders
    # We'll do this by temporarily filtering what classify_and_organize_emails processes
    output_dir.mkdir(parents=True, exist_ok=True)

    _owns_mappings = mappings is None
    firm_mappings = mappings if mappings is not None else load_firm_mappings(output_dir)
    classification_lookup = _load_classification_lookup(output_dir)
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
            log.error(f"Error loading {email_folder.name}: {e}", phase=CLASSIFY)
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
            classification_lookup,
            use_lookup=False,
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
            log.detail(f"  Skipped: {subject[:40]}... (not hedge fund related)", phase=CLASSIFY)

        results.append(entry)

    # Save updated data (skip if caller owns the mappings object)
    if _owns_mappings:
        save_firm_mappings(firm_mappings, output_dir)

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


def _generate_artifact_id() -> str:
    """Generate a random artifact_id for newly discovered artifacts."""
    return uuid.uuid4().hex[:12]


def _parse_folder_identifier(folder_name: str) -> tuple[str, str]:
    """Parse a folder name like 'FundName - identifier' into (name, identifier).

    Returns (folder_name, "") if no identifier separator is found.
    """
    match = re.match(r"^(.+)\s+-\s+([^\s-]+)$", folder_name)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return folder_name, ""


def _match_by_contents(disk_contents: set, registry_contents_map: dict) -> str | None:
    """Match a folder to a registry entry by comparing its contents.

    Compares the set of items found on disk (subfolder names, artifact filenames)
    against the contents lists stored in the registry for each candidate entry.

    Args:
        disk_contents: Set of names found on disk (fund folder names, artifact filenames).
        registry_contents_map: {key: [content_item, ...]} from the registry index.

    Returns:
        The registry key with the highest content overlap, or None if no overlap.
    """
    if not disk_contents:
        return None

    best_key = None
    best_overlap = 0
    for key, reg_contents in registry_contents_map.items():
        overlap = len(disk_contents & set(reg_contents))
        if overlap > best_overlap:
            best_overlap = overlap
            best_key = key
    return best_key


def _build_registry_index(mappings: dict) -> dict:
    """Build lookup indexes from the current firm_fund_mappings.

    Returns:
        {
            "artifact_ids": {artifact_id: (canonical_firm, fund_name_or_None)},
            "firm_folders": {canonical_firm: [subfolder_or_filename, ...]},
            "fund_contents": {(canonical_firm, fund_name): [artifact_filename, ...]},
            "fund_identifiers": {identifier: (canonical_firm, fund_name)},
            "artifact_filenames": {(canonical_firm, fund_name_or_None, base_filename): artifact_id},
        }
    """
    canonical_names = mappings.get("canonical_names", {})
    artifact_ids = {}
    firm_folders = {}
    fund_contents = {}
    fund_identifiers = {}
    artifact_filenames = {}

    for canonical, firm_data in canonical_names.items():
        # Skip soft-deleted firms for content-based matching indexes,
        # but still index their artifact_ids so moves from deleted entries
        # can be tracked.
        is_firm_deleted = bool(firm_data.get("_deleted_at"))

        # Collect contents: fund subfolder names + firm-level artifact filenames
        contents = []

        # Firm-level artifacts
        for art_id, art_data in firm_data.get("artifacts", {}).items():
            if isinstance(art_data, dict) and art_data.get("_deleted_at"):
                continue
            artifact_ids[art_id] = (canonical, None)
            fn = art_data.get("file_name", "") if isinstance(art_data, dict) else ""
            if fn:
                artifact_filenames[(canonical, None, fn)] = art_id
                if not is_firm_deleted:
                    contents.append(fn)

        # Funds
        for fund_name, fund_data in firm_data.get("funds", {}).items():
            is_fund_deleted = bool(fund_data.get("_deleted_at"))
            if not is_firm_deleted and not is_fund_deleted:
                contents.append(fund_name)
            fund_file_contents = []
            for art_id, art_data in fund_data.get("artifacts", {}).items():
                if isinstance(art_data, dict) and art_data.get("_deleted_at"):
                    continue
                artifact_ids[art_id] = (canonical, fund_name)
                fn = art_data.get("file_name", "") if isinstance(art_data, dict) else ""
                if fn:
                    artifact_filenames[(canonical, fund_name, fn)] = art_id
                    if not is_fund_deleted:
                        fund_file_contents.append(fn)
            if not is_fund_deleted:
                fund_contents[(canonical, fund_name)] = fund_file_contents
            # Index fund identifiers for identifier-based matching
            fund_id = fund_data.get("identifier")
            if fund_id and not is_fund_deleted:
                fund_identifiers[str(fund_id)] = (canonical, fund_name)

        if not is_firm_deleted:
            firm_folders[canonical] = contents

    return {
        "artifact_ids": artifact_ids,
        "firm_folders": firm_folders,
        "fund_contents": fund_contents,
        "fund_identifiers": fund_identifiers,
        "artifact_filenames": artifact_filenames,
    }


def _scan_disk_state(output_dir: Path) -> dict:
    """Scan the output directory and return the current disk state.

    Returns:
        {
            "firms": {
                folder_name: {
                    "path": Path,
                    "funds": {
                        folder_name: {
                            "path": Path,
                            "name": str,  # parsed name (without identifier)
                            "identifier": str,
                            "artifacts": {artifact_id: filename, ...},
                            "untagged_files": [filename, ...],
                        }
                    },
                    "artifacts": {artifact_id: filename, ...},
                    "untagged_files": [filename, ...],
                }
            }
        }
    """
    state = {"firms": {}}

    for child in sorted(output_dir.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith(".") or child.name in SYSTEM_FILES:
            continue
        if child.name == NEEDS_REVIEW_FOLDER:
            continue

        firm_entry = {
            "path": child,
            "funds": {},
            "artifacts": {},
            "untagged_files": [],
        }

        # Scan direct files under firm
        for f in sorted(child.iterdir()):
            if (
                f.is_file()
                and not f.name.startswith(".")
                and f.name not in SYSTEM_FILES
                and not _is_zone_identifier(f.name)
            ):
                if f.suffix.lower() == ".json" and ".link.json" not in f.name.lower():
                    continue
                art_id = _parse_artifact_id_from_filename(f.name)
                if art_id:
                    firm_entry["artifacts"][art_id] = f.name
                else:
                    firm_entry["untagged_files"].append(f.name)

        # Scan fund subfolders
        for subfolder in sorted(child.iterdir()):
            if not subfolder.is_dir() or subfolder.name.startswith("."):
                continue
            if subfolder.name in SKIP_SUBFOLDERS:
                continue
            if subfolder.name.startswith(CONFLICT_IDENTIFIER_PREFIX):
                continue

            fund_name, identifier = _parse_folder_identifier(subfolder.name)
            fund_entry = {
                "path": subfolder,
                "name": fund_name,
                "identifier": identifier,
                "artifacts": {},
                "untagged_files": [],
            }

            for f in sorted(subfolder.iterdir()):
                if (
                    f.is_file()
                    and not f.name.startswith(".")
                    and f.name not in SYSTEM_FILES
                    and not _is_zone_identifier(f.name)
                ):
                    if (
                        f.suffix.lower() == ".json"
                        and ".link.json" not in f.name.lower()
                    ):
                        continue
                    art_id = _parse_artifact_id_from_filename(f.name)
                    if art_id:
                        fund_entry["artifacts"][art_id] = f.name
                    else:
                        fund_entry["untagged_files"].append(f.name)

            firm_entry["funds"][subfolder.name] = fund_entry

        state["firms"][child.name] = firm_entry

    return state


def sync_moved_artifacts(output_dir: Path = None, *, mappings: dict = None) -> dict:
    """Sync the firm_fund_mappings registry with the actual disk state.

    Detects:
    - New firms/funds/artifacts on disk not in registry -> registers them
    - Firms/funds/artifacts in registry but missing from disk -> soft-deletes
    - Artifacts moved between firms/funds -> updates registry location
    - Fund identifier changes from folder renames -> updates registry

    Matching strategy:
    - Artifacts matched by artifact_id embedded in filename suffix
    - Fund folders matched by identifier in folder name (fund_name - identifier)
    - Firm folders matched by folder name against aliases

    Returns dict with: moved, new_folders, removed_folders, new_artifacts,
    deleted_artifacts, errors.
    """
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    if not output_dir.exists():
        return {
            "moved": [],
            "new_folders": [],
            "removed_folders": [],
            "new_artifacts": [],
            "deleted_artifacts": [],
            "errors": [],
        }

    _owns_mappings = mappings is None
    mappings = mappings if mappings is not None else load_firm_mappings(output_dir)
    canonical_names = mappings.get("canonical_names", {})
    registry_index = _build_registry_index(mappings)
    disk_state = _scan_disk_state(output_dir)

    result = {
        "moved": [],
        "new_folders": [],
        "removed_folders": [],
        "new_artifacts": [],
        "deleted_artifacts": [],
        "errors": [],
    }

    _registry_dirty = False  # Track metadata-only changes (e.g. identifier updates)

    # Track which registry firms/funds/artifacts are seen on disk
    seen_firms = set()
    seen_funds = {}  # (canonical_firm, fund_name) -> True
    seen_artifacts = set()  # artifact_ids found on disk

    # Build a reverse lookup: upper-cased name/alias -> canonical name (O(1) matching)
    _alias_to_canonical: dict[str, str] = {}
    for canon, info in canonical_names.items():
        if info.get("_deleted_at"):
            continue
        _alias_to_canonical[canon.upper()] = canon
        for alias in info.get("aliases", []):
            _alias_to_canonical[alias.upper()] = canon

    # --- Pass 1: Walk disk state and reconcile with registry ---
    for firm_folder_name, firm_disk in disk_state["firms"].items():
        # Primary: O(1) match folder name against canonical names and aliases
        folder_upper = firm_folder_name.upper()
        matched_canonical = _alias_to_canonical.get(folder_upper)

        if not matched_canonical:
            # Fallback: match by contents (fund subfolders + artifact filenames)
            disk_contents = set()
            for fund_folder_name_inner in firm_disk["funds"]:
                fund_name_inner, _ = _parse_folder_identifier(fund_folder_name_inner)
                disk_contents.add(fund_name_inner)
            for art_id_inner, filename_inner in firm_disk["artifacts"].items():
                disk_contents.add(_strip_artifact_id_from_filename(filename_inner))
            for filename_inner in firm_disk["untagged_files"]:
                disk_contents.add(filename_inner)

            # Exclude firms already matched to other disk folders
            eligible_firms = {
                k: v
                for k, v in registry_index["firm_folders"].items()
                if k not in seen_firms
            }
            matched_canonical = _match_by_contents(disk_contents, eligible_firms)

        if not matched_canonical:
            # New firm — register it
            new_canonical = firm_folder_name.upper()
            canonical_names[new_canonical] = {
                "aliases": [firm_folder_name],
                "funds": {},
                "artifacts": {},
            }
            registry_index["firm_folders"][new_canonical] = []
            _alias_to_canonical[new_canonical] = new_canonical
            _alias_to_canonical[firm_folder_name.upper()] = new_canonical
            matched_canonical = new_canonical
            result["new_folders"].append(
                {
                    "folder": firm_folder_name,
                    "firm": new_canonical,
                    "type": "firm",
                }
            )

        # Detect firm folder rename: if the disk folder name differs from
        # the matched canonical, re-key the firm under the new name.
        # Skip if the target name already exists as a different firm (would overwrite).
        new_canonical_candidate = firm_folder_name.upper()
        if (
            matched_canonical
            and new_canonical_candidate != matched_canonical
            and new_canonical_candidate not in canonical_names
        ):
            # Add old canonical as alias
            firm_data = canonical_names[matched_canonical]
            existing_aliases = [a.lower() for a in firm_data.get("aliases", [])]
            if matched_canonical.lower() not in existing_aliases:
                firm_data.setdefault("aliases", []).append(matched_canonical)
            # Re-key under new name
            canonical_names[new_canonical_candidate] = canonical_names.pop(
                matched_canonical
            )
            # Update reverse alias lookup: point all old aliases to new canonical
            for alias_key, canon_val in list(_alias_to_canonical.items()):
                if canon_val == matched_canonical:
                    _alias_to_canonical[alias_key] = new_canonical_candidate
            _alias_to_canonical[new_canonical_candidate] = new_canonical_candidate
            # Update registry index
            registry_index["firm_folders"][new_canonical_candidate] = registry_index[
                "firm_folders"
            ].pop(matched_canonical, [])
            # Update artifact_ids index
            for art_id, loc in list(registry_index["artifact_ids"].items()):
                if loc[0] == matched_canonical:
                    registry_index["artifact_ids"][art_id] = (
                        new_canonical_candidate,
                        loc[1],
                    )
            # Update artifact_filenames index
            for key, art_id in list(registry_index["artifact_filenames"].items()):
                if key[0] == matched_canonical:
                    new_key = (new_canonical_candidate, key[1], key[2])
                    registry_index["artifact_filenames"][new_key] = registry_index[
                        "artifact_filenames"
                    ].pop(key)
            # Update fund_contents index
            for key, contents in list(registry_index["fund_contents"].items()):
                if key[0] == matched_canonical:
                    new_key = (new_canonical_candidate, key[1])
                    registry_index["fund_contents"][new_key] = registry_index[
                        "fund_contents"
                    ].pop(key)
            # Update fund_identifiers index
            for ident, loc in list(registry_index["fund_identifiers"].items()):
                if loc[0] == matched_canonical:
                    registry_index["fund_identifiers"][ident] = (
                        new_canonical_candidate,
                        loc[1],
                    )
            matched_canonical = new_canonical_candidate
            _registry_dirty = True

        seen_firms.add(matched_canonical)
        firm_entry = canonical_names[matched_canonical]

        # --- Firm-level artifacts ---
        firm_entry.setdefault("artifacts", {})

        # Check tagged artifacts at firm level
        for art_id, filename in firm_disk["artifacts"].items():
            file_name = _strip_artifact_id_from_filename(filename)
            seen_artifacts.add(art_id)
            prev_location = registry_index["artifact_ids"].get(art_id)

            if prev_location is None:
                # Check if base filename matches an existing artifact here
                # (file was renamed to embed a new/different artifact_id)
                old_art_id = registry_index["artifact_filenames"].get(
                    (matched_canonical, None, file_name)
                )
                if old_art_id and old_art_id in firm_entry.get("artifacts", {}):
                    # Transfer existing metadata to new artifact_id
                    old_data = firm_entry["artifacts"].pop(old_art_id)
                    old_data["file_name"] = file_name
                    firm_entry["artifacts"][art_id] = old_data
                    # Update index
                    registry_index["artifact_ids"].pop(old_art_id, None)
                    registry_index["artifact_ids"][art_id] = (matched_canonical, None)
                    registry_index["artifact_filenames"][
                        (matched_canonical, None, file_name)
                    ] = art_id
                    seen_artifacts.discard(old_art_id)
                    _registry_dirty = True
                else:
                    # New artifact (tagged but not in registry)
                    firm_entry["artifacts"][art_id] = {
                        "file_name": file_name,
                        "identifier": None,
                        "contains_monthly_net_performance_update": False,
                        "processed": False,
                    }
                    registry_index["artifact_ids"][art_id] = (matched_canonical, None)
                    if file_name:
                        registry_index["artifact_filenames"][
                            (matched_canonical, None, file_name)
                        ] = art_id
                    result["new_artifacts"].append(
                        {
                            "artifact_id": art_id,
                            "firm": matched_canonical,
                            "fund": None,
                            "file_name": file_name,
                        }
                    )
            elif prev_location != (matched_canonical, None):
                # Moved from another location — carry over existing metadata
                old_firm, old_fund = prev_location
                old_data = _remove_artifact_from_registry(
                    art_id, old_firm, old_fund, canonical_names
                )
                old_data["file_name"] = file_name
                firm_entry["artifacts"][art_id] = old_data
                registry_index["artifact_ids"][art_id] = (matched_canonical, None)
                if file_name:
                    registry_index["artifact_filenames"][
                        (matched_canonical, None, file_name)
                    ] = art_id
                result["moved"].append(
                    {
                        "artifact_id": art_id,
                        "old_file": file_name,
                        "from": old_firm + (f"/{old_fund}" if old_fund else ""),
                        "to": matched_canonical,
                        "firm": matched_canonical,
                        "fund": None,
                        "new_path": str(firm_disk["path"] / filename),
                    }
                )
            else:
                # Same location — update file_name if changed
                if art_id in firm_entry["artifacts"]:
                    old_stored = firm_entry["artifacts"][art_id].get("file_name", "")
                    if old_stored != file_name:
                        if old_stored:
                            registry_index["artifact_filenames"].pop(
                                (matched_canonical, None, old_stored), None
                            )
                        if file_name:
                            registry_index["artifact_filenames"][
                                (matched_canonical, None, file_name)
                            ] = art_id
                        firm_entry["artifacts"][art_id]["file_name"] = file_name
                        _registry_dirty = True

        # Register untagged firm-level files
        for filename in firm_disk["untagged_files"]:
            # Fallback: check if this filename matches an existing artifact
            # whose artifact_id was changed/lost
            existing_art_id = registry_index["artifact_filenames"].get(
                (matched_canonical, None, filename)
            )
            if existing_art_id and existing_art_id not in seen_artifacts:
                new_id = existing_art_id
            else:
                new_id = _generate_artifact_id()
            firm_entry["artifacts"][new_id] = {
                "file_name": filename,
                "identifier": None,
                "contains_monthly_net_performance_update": False,
                "processed": False,
            }
            registry_index["artifact_ids"][new_id] = (matched_canonical, None)
            if filename:
                registry_index["artifact_filenames"][
                    (matched_canonical, None, filename)
                ] = new_id
            # Rename file on disk to include artifact_id
            old_path = firm_disk["path"] / filename
            new_filename = _embed_artifact_id_in_filename(filename, new_id)
            new_path = firm_disk["path"] / new_filename
            try:
                old_path.rename(new_path)
            except OSError as e:
                result["errors"].append(f"Failed to tag artifact {filename}: {e}")
            seen_artifacts.add(new_id)
            result["new_artifacts"].append(
                {
                    "artifact_id": new_id,
                    "firm": matched_canonical,
                    "fund": None,
                    "file_name": filename,
                }
            )

        # --- Fund subfolders ---
        for fund_folder_name, fund_disk in firm_disk["funds"].items():
            fund_name = fund_disk["name"]
            identifier = fund_disk["identifier"]

            # Match fund to registry — prefer identifier, fall back to name
            matched_fund = None

            if identifier:
                # Primary: match by identifier (stable across renames)
                id_lookup = registry_index["fund_identifiers"].get(str(identifier))
                if id_lookup and id_lookup[0] == matched_canonical:
                    matched_fund = id_lookup[1]
                    # Fund name may have changed (folder rename) — update if needed
                    if (
                        fund_name
                        and fund_name != matched_fund
                        and matched_fund in firm_entry.get("funds", {})
                    ):
                        fund_data = firm_entry["funds"][matched_fund]
                        existing_aliases = [
                            a.lower() for a in fund_data.get("aliases", [])
                        ]
                        if matched_fund.lower() not in existing_aliases:
                            fund_data.setdefault("aliases", []).append(matched_fund)
                        # Re-key the fund under the new name
                        firm_entry["funds"][fund_name] = firm_entry["funds"].pop(
                            matched_fund
                        )
                        # Update fund_identifiers index to point to new name
                        registry_index["fund_identifiers"][str(identifier)] = (
                            matched_canonical,
                            fund_name,
                        )
                        # Update artifact_ids index to reflect new fund name
                        for art_id_upd, loc_upd in list(
                            registry_index["artifact_ids"].items()
                        ):
                            if loc_upd == (matched_canonical, matched_fund):
                                registry_index["artifact_ids"][art_id_upd] = (
                                    matched_canonical,
                                    fund_name,
                                )
                        # Update artifact_filenames index
                        for key_upd in list(
                            registry_index["artifact_filenames"].keys()
                        ):
                            if (
                                key_upd[0] == matched_canonical
                                and key_upd[1] == matched_fund
                            ):
                                new_key_upd = (matched_canonical, fund_name, key_upd[2])
                                registry_index["artifact_filenames"][new_key_upd] = (
                                    registry_index["artifact_filenames"].pop(key_upd)
                                )
                        # Update fund_contents index
                        old_fc_key = (matched_canonical, matched_fund)
                        if old_fc_key in registry_index["fund_contents"]:
                            registry_index["fund_contents"][
                                (matched_canonical, fund_name)
                            ] = registry_index["fund_contents"].pop(old_fc_key)
                        matched_fund = fund_name
                        _registry_dirty = True

            if not matched_fund:
                # Fallback 1: match by fund name directly (takes priority over
                # content matching to prevent a folder named "FUND_B" from being
                # matched to registry "FUND_A" just because of shared artifacts)
                for reg_fund_name, reg_fund_data in firm_entry.get("funds", {}).items():
                    if reg_fund_data.get("_deleted_at"):
                        continue
                    if fund_name and fund_name.upper() == reg_fund_name.upper():
                        matched_fund = reg_fund_name
                        break
                    for alias in reg_fund_data.get("aliases", []):
                        if fund_name and fund_name.upper() == alias.upper():
                            matched_fund = reg_fund_name
                            break
                    if matched_fund:
                        break

            if not matched_fund:
                # Fallback 2: match by contents (artifact filenames on disk vs registry)
                disk_fund_contents = set()
                for art_id_inner, filename_inner in fund_disk["artifacts"].items():
                    disk_fund_contents.add(
                        _strip_artifact_id_from_filename(filename_inner)
                    )
                for filename_inner in fund_disk["untagged_files"]:
                    disk_fund_contents.add(filename_inner)
                # Build candidates scoped to this firm
                firm_fund_contents = {
                    fn: contents
                    for (firm_key, fn), contents in registry_index[
                        "fund_contents"
                    ].items()
                    if firm_key == matched_canonical
                }
                matched_fund = _match_by_contents(
                    disk_fund_contents, firm_fund_contents
                )

            if not matched_fund:
                # New fund — register it
                firm_entry.setdefault("funds", {})
                firm_entry["funds"][fund_name] = {
                    "aliases": [],
                    "identifier": identifier or None,
                    "artifacts": {},
                }
                matched_fund = fund_name
                if identifier:
                    registry_index["fund_identifiers"][str(identifier)] = (
                        matched_canonical,
                        fund_name,
                    )
                registry_index["fund_contents"][(matched_canonical, fund_name)] = []
                result["new_folders"].append(
                    {
                        "folder": fund_folder_name,
                        "firm": matched_canonical,
                        "type": "fund",
                    }
                )
            else:
                # Update identifier if newly provided from folder name
                fund_entry_ref = firm_entry.get("funds", {}).get(matched_fund, {})
                if identifier and fund_entry_ref.get("identifier") != identifier:
                    old_identifier = fund_entry_ref.get("identifier")
                    if old_identifier:
                        registry_index["fund_identifiers"].pop(
                            str(old_identifier), None
                        )
                    fund_entry_ref["identifier"] = identifier
                    registry_index["fund_identifiers"][str(identifier)] = (
                        matched_canonical,
                        matched_fund,
                    )
                    _registry_dirty = True
                elif not identifier and fund_entry_ref.get("identifier"):
                    # Identifier removed from folder name — clear from registry
                    old_identifier = fund_entry_ref["identifier"]
                    registry_index["fund_identifiers"].pop(str(old_identifier), None)
                    fund_entry_ref["identifier"] = None
                    _registry_dirty = True

            seen_funds[(matched_canonical, matched_fund)] = True
            fund_entry = firm_entry["funds"][matched_fund]
            fund_entry.setdefault("artifacts", {})

            # Check tagged artifacts under fund
            for art_id, filename in fund_disk["artifacts"].items():
                file_name = _strip_artifact_id_from_filename(filename)
                seen_artifacts.add(art_id)
                prev_location = registry_index["artifact_ids"].get(art_id)

                if prev_location is None:
                    # Check if base filename matches an existing artifact here
                    # (file was renamed to embed a new/different artifact_id)
                    old_art_id = registry_index["artifact_filenames"].get(
                        (matched_canonical, matched_fund, file_name)
                    )
                    if old_art_id and old_art_id in fund_entry.get("artifacts", {}):
                        # Transfer existing metadata to new artifact_id
                        old_data = fund_entry["artifacts"].pop(old_art_id)
                        old_data["file_name"] = file_name
                        fund_entry["artifacts"][art_id] = old_data
                        # Update index
                        registry_index["artifact_ids"].pop(old_art_id, None)
                        registry_index["artifact_ids"][art_id] = (
                            matched_canonical,
                            matched_fund,
                        )
                        registry_index["artifact_filenames"][
                            (matched_canonical, matched_fund, file_name)
                        ] = art_id
                        seen_artifacts.discard(old_art_id)
                        _registry_dirty = True
                    else:
                        fund_entry["artifacts"][art_id] = {
                            "file_name": file_name,
                            "identifier": None,
                            "contains_monthly_net_performance_update": False,
                            "processed": False,
                        }
                        registry_index["artifact_ids"][art_id] = (
                            matched_canonical,
                            matched_fund,
                        )
                        if file_name:
                            registry_index["artifact_filenames"][
                                (matched_canonical, matched_fund, file_name)
                            ] = art_id
                        result["new_artifacts"].append(
                            {
                                "artifact_id": art_id,
                                "firm": matched_canonical,
                                "fund": matched_fund,
                                "file_name": file_name,
                            }
                        )
                elif prev_location != (matched_canonical, matched_fund):
                    # Moved from another location — carry over existing metadata
                    old_firm, old_fund = prev_location
                    old_data = _remove_artifact_from_registry(
                        art_id, old_firm, old_fund, canonical_names
                    )
                    old_data["file_name"] = file_name
                    fund_entry["artifacts"][art_id] = old_data
                    registry_index["artifact_ids"][art_id] = (
                        matched_canonical,
                        matched_fund,
                    )
                    if file_name:
                        registry_index["artifact_filenames"][
                            (matched_canonical, matched_fund, file_name)
                        ] = art_id
                    result["moved"].append(
                        {
                            "artifact_id": art_id,
                            "old_file": file_name,
                            "from": old_firm + (f"/{old_fund}" if old_fund else ""),
                            "to": f"{matched_canonical}/{matched_fund}",
                            "firm": matched_canonical,
                            "fund": matched_fund,
                            "new_path": str(fund_disk["path"] / filename),
                        }
                    )
                else:
                    # Same location — update file_name if changed
                    if art_id in fund_entry["artifacts"]:
                        old_stored = fund_entry["artifacts"][art_id].get(
                            "file_name", ""
                        )
                        if old_stored != file_name:
                            if old_stored:
                                registry_index["artifact_filenames"].pop(
                                    (matched_canonical, matched_fund, old_stored), None
                                )
                            if file_name:
                                registry_index["artifact_filenames"][
                                    (matched_canonical, matched_fund, file_name)
                                ] = art_id
                            fund_entry["artifacts"][art_id]["file_name"] = file_name
                            _registry_dirty = True

            # Register untagged fund-level files
            for filename in fund_disk["untagged_files"]:
                # Fallback: check if this filename matches an existing artifact
                # whose artifact_id was changed/lost
                existing_art_id = registry_index["artifact_filenames"].get(
                    (matched_canonical, matched_fund, filename)
                )
                if existing_art_id and existing_art_id not in seen_artifacts:
                    new_id = existing_art_id
                else:
                    new_id = _generate_artifact_id()
                fund_entry["artifacts"][new_id] = {
                    "file_name": filename,
                    "identifier": None,
                    "contains_monthly_net_performance_update": False,
                    "processed": False,
                }
                registry_index["artifact_ids"][new_id] = (
                    matched_canonical,
                    matched_fund,
                )
                if filename:
                    registry_index["artifact_filenames"][
                        (matched_canonical, matched_fund, filename)
                    ] = new_id
                old_path = fund_disk["path"] / filename
                new_filename = _embed_artifact_id_in_filename(filename, new_id)
                new_path = fund_disk["path"] / new_filename
                try:
                    old_path.rename(new_path)
                except OSError as e:
                    result["errors"].append(f"Failed to tag artifact {filename}: {e}")
                seen_artifacts.add(new_id)
                result["new_artifacts"].append(
                    {
                        "artifact_id": new_id,
                        "firm": matched_canonical,
                        "fund": matched_fund,
                        "file_name": filename,
                    }
                )

    # --- Pass 2: Soft-delete registry entries not found on disk ---
    now = datetime.now().isoformat()

    for canonical, firm_data in canonical_names.items():
        # Check if firm folder exists on disk
        if canonical not in seen_firms and not firm_data.get("_deleted_at"):
            # Check if firm has any non-deleted content
            has_content = any(
                not a.get("_deleted_at")
                for a in firm_data.get("artifacts", {}).values()
            ) or any(
                not f.get("_deleted_at") for f in firm_data.get("funds", {}).values()
            )
            if has_content:
                firm_data["_deleted_at"] = now
                result["removed_folders"].append(
                    {
                        "folder": sanitize_folder_name(canonical),
                        "firm": canonical,
                        "type": "firm",
                    }
                )

        # Check funds
        for fund_name, fund_data in firm_data.get("funds", {}).items():
            if (
                (canonical, fund_name) not in seen_funds
                and not fund_data.get("_deleted_at")
                and canonical in seen_firms
            ):
                fund_data["_deleted_at"] = now
                result["removed_folders"].append(
                    {
                        "folder": fund_name,
                        "firm": canonical,
                        "type": "fund",
                    }
                )

            # Check artifacts in this fund
            for art_id, art_data in fund_data.get("artifacts", {}).items():
                if art_id not in seen_artifacts and not art_data.get("_deleted_at"):
                    art_data["_deleted_at"] = now
                    result["deleted_artifacts"].append(
                        {
                            "artifact_id": art_id,
                            "firm": canonical,
                            "fund": fund_name,
                            "file_name": art_data.get("file_name", ""),
                        }
                    )

        # Check firm-level artifacts
        for art_id, art_data in firm_data.get("artifacts", {}).items():
            if art_id not in seen_artifacts and not art_data.get("_deleted_at"):
                art_data["_deleted_at"] = now
                result["deleted_artifacts"].append(
                    {
                        "artifact_id": art_id,
                        "firm": canonical,
                        "fund": None,
                        "file_name": art_data.get("file_name", ""),
                    }
                )

    # Save updated mappings (skip if caller owns the mappings object)
    if _owns_mappings and (
        result["moved"]
        or result["new_folders"]
        or result["removed_folders"]
        or result["new_artifacts"]
        or result["deleted_artifacts"]
        or _registry_dirty
    ):
        save_firm_mappings(mappings, output_dir)

    return result


def reconcile_misplaced_artifacts(
    output_dir: Path = None, *, mappings: dict = None
) -> dict:
    """Find artifacts whose identifier matches a different fund folder and move them.

    Scans all artifacts in the registry that have a non-null ``identifier``.
    If that identifier matches a fund folder's identifier (from the folder
    name pattern ``{FUND_NAME} - {IDENTIFIER}``), and the artifact is NOT
    already inside that fund folder, the file is moved on disk and the
    registry is updated.

    This covers:
    - Artifacts sitting at firm level that belong to a specific fund
    - Artifacts in the wrong fund folder
    - ``.link.json`` proxy files with identifiers matching another fund

    Companion ``json/{identifier}.json`` files are moved alongside the artifact.

    Returns:
        {
            "relocated": [{"artifact_id", "file_name", "from", "to"}, ...],
            "errors": [str, ...],
        }
    """
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    _owns_mappings = mappings is None
    mappings = mappings if mappings is not None else load_firm_mappings(output_dir)
    canonical_names = mappings.get("canonical_names", {})

    result: dict = {"relocated": [], "errors": []}

    # --- Step 1: Build identifier → (firm, fund_name) index from fund-level identifiers ---
    fund_id_index: dict[
        str, tuple[str, str]
    ] = {}  # identifier → (canonical_firm, fund_name)
    for canonical, firm_data in canonical_names.items():
        for fund_name, fund_data in firm_data.get("funds", {}).items():
            if fund_data.get("_deleted_at"):
                continue
            fund_id = fund_data.get("identifier")
            if fund_id:
                fund_id_index[str(fund_id)] = (canonical, fund_name)

    if not fund_id_index:
        return result

    # --- Step 2: Scan disk state to resolve actual file paths ---
    disk_state = _scan_disk_state(output_dir)

    # Map (firm_folder, fund_folder_or_None, artifact_id) → file Path on disk
    disk_file_paths: dict[tuple[str, str | None, str], Path] = {}
    for firm_folder, firm_disk in disk_state["firms"].items():
        for art_id, filename in firm_disk["artifacts"].items():
            disk_file_paths[(firm_folder, None, art_id)] = firm_disk["path"] / filename
        for fund_folder_name, fund_disk in firm_disk["funds"].items():
            for art_id, filename in fund_disk["artifacts"].items():
                file_path = _find_artifact_file(fund_disk["path"], filename)
                if file_path:
                    disk_file_paths[(firm_folder, fund_folder_name, art_id)] = file_path

    # Map (canonical_firm, fund_name) → fund folder Path on disk
    fund_folder_paths: dict[tuple[str, str], Path] = {}
    for firm_folder, firm_disk in disk_state["firms"].items():
        for fund_folder_name, fund_disk in firm_disk["funds"].items():
            parsed_name = fund_disk["name"]
            parsed_id = fund_disk["identifier"]
            for canonical, firm_data in canonical_names.items():
                if sanitize_folder_name(canonical) != firm_folder:
                    continue
                for fund_name, fund_data in firm_data.get("funds", {}).items():
                    reg_id = fund_data.get("identifier")
                    if reg_id and str(reg_id) == parsed_id:
                        fund_folder_paths[(canonical, fund_name)] = fund_disk["path"]
                        break
                    if fund_name == parsed_name:
                        fund_folder_paths[(canonical, fund_name)] = fund_disk["path"]
                        break

    # --- Step 3: Collect relocations (artifacts whose identifier points elsewhere) ---
    relocations: list[dict] = []

    for canonical, firm_data in canonical_names.items():
        firm_folder = sanitize_folder_name(canonical)

        # Firm-level artifacts
        for art_id, art_data in list(firm_data.get("artifacts", {}).items()):
            if art_data.get("_deleted_at"):
                continue
            art_identifier = art_data.get("identifier")
            if not art_identifier or str(art_identifier) not in fund_id_index:
                continue
            target_firm, target_fund = fund_id_index[str(art_identifier)]
            relocations.append(
                {
                    "artifact_id": art_id,
                    "art_data": art_data,
                    "from_firm": canonical,
                    "from_fund": None,
                    "to_firm": target_firm,
                    "to_fund": target_fund,
                    "disk_key": (firm_folder, None, art_id),
                }
            )

        # Fund-level artifacts
        for fund_name, fund_data in firm_data.get("funds", {}).items():
            if fund_data.get("_deleted_at"):
                continue
            for art_id, art_data in list(fund_data.get("artifacts", {}).items()):
                if art_data.get("_deleted_at"):
                    continue
                art_identifier = art_data.get("identifier")
                if not art_identifier or str(art_identifier) not in fund_id_index:
                    continue
                target_firm, target_fund = fund_id_index[str(art_identifier)]
                # Already in the right place
                if target_firm == canonical and target_fund == fund_name:
                    continue
                # Resolve disk folder name for this fund
                fund_disk_folder = None
                for fdn, fd in (
                    disk_state["firms"].get(firm_folder, {}).get("funds", {}).items()
                ):
                    if fd["name"] == fund_name or fdn == fund_name:
                        fund_disk_folder = fdn
                        break
                relocations.append(
                    {
                        "artifact_id": art_id,
                        "art_data": art_data,
                        "from_firm": canonical,
                        "from_fund": fund_name,
                        "to_firm": target_firm,
                        "to_fund": target_fund,
                        "disk_key": (firm_folder, fund_disk_folder, art_id),
                    }
                )

    # --- Step 4: Execute relocations ---
    for reloc in relocations:
        art_id = reloc["artifact_id"]
        art_data = reloc["art_data"]
        from_firm = reloc["from_firm"]
        from_fund = reloc["from_fund"]
        to_firm = reloc["to_firm"]
        to_fund = reloc["to_fund"]

        # Resolve target folder on disk
        target_path = fund_folder_paths.get((to_firm, to_fund))
        if not target_path:
            target_firm_data = canonical_names.get(to_firm, {})
            target_fund_data = target_firm_data.get("funds", {}).get(to_fund, {})
            target_id = target_fund_data.get("identifier", "")
            target_folder_name = f"{to_fund} - {target_id}" if target_id else to_fund
            target_path = (
                output_dir / sanitize_folder_name(to_firm) / target_folder_name
            )
            if not target_path.exists():
                result["errors"].append(
                    f"Target folder not found for {art_id}: {to_firm}/{to_fund}"
                )
                continue

        # Locate source file on disk
        source_file = disk_file_paths.get(reloc["disk_key"])
        if not source_file or not source_file.exists():
            base_filename = art_data.get("file_name", "")
            tagged_filename = _embed_artifact_id_in_filename(base_filename, art_id)
            from_firm_path = output_dir / sanitize_folder_name(from_firm)
            if from_fund:
                for candidate in from_firm_path.iterdir():
                    if candidate.is_dir():
                        source_file = _find_artifact_file(candidate, tagged_filename)
                        if source_file:
                            break
            else:
                source_file = from_firm_path / tagged_filename
                if not source_file.exists():
                    source_file = None

        if not source_file or not source_file.exists():
            result["errors"].append(
                f"Source file not found on disk for artifact {art_id}"
            )
            continue

        dest_file = target_path / source_file.name

        # Move the file (collision check + move under a single try/except)
        try:
            # Handle name collisions
            if dest_file.exists() and dest_file != source_file:
                stem = dest_file.stem
                ext = dest_file.suffix
                counter = 1
                while dest_file.exists():
                    dest_file = target_path / f"{stem}_{counter}{ext}"
                    counter += 1

            target_path.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source_file), str(dest_file))
        except OSError as e:
            result["errors"].append(
                f"Failed to move {source_file.name} to {target_path}: {e}"
            )
            continue

        # Move companion json/{identifier}.json if it exists
        art_identifier = str(art_data.get("identifier", ""))
        if art_identifier:
            _move_companion_json(
                art_identifier, source_file.parent, target_path, result["errors"]
            )

        # Update registry: remove from old location, add to new
        _remove_artifact_from_registry(art_id, from_firm, from_fund, canonical_names)
        target_fund_entry = (
            canonical_names.get(to_firm, {}).get("funds", {}).get(to_fund, {})
        )
        target_fund_entry.setdefault("artifacts", {})[art_id] = art_data

        from_label = f"{from_firm}/{from_fund}" if from_fund else from_firm
        to_label = f"{to_firm}/{to_fund}"
        result["relocated"].append(
            {
                "artifact_id": art_id,
                "file_name": art_data.get("file_name", source_file.name),
                "from": from_label,
                "to": to_label,
            }
        )

    # Save if any changes were made (skip if caller owns the mappings object)
    if _owns_mappings and result["relocated"]:
        save_firm_mappings(mappings, output_dir)

    return result


def _move_companion_json(
    identifier: str,
    source_parent: Path,
    target_path: Path,
    errors: list,
):
    """Move json/{identifier}.json from source to target folder if it exists.

    Checks for a ``json/`` subfolder relative to ``source_parent`` (the
    folder the artifact was in). If ``{identifier}.json`` exists there, moves
    it to ``target_path/json/{identifier}.json``.

    When a file with the same name already exists at the destination, keeps
    the one with the longer performance track record (more months of data),
    matching the behavior in ``load._save_json_result()``.
    """
    json_filename = f"{identifier}.json"

    # Look for json/ at the same level as the artifact
    source_json_dir = source_parent / "json"
    source_json_file = source_json_dir / json_filename
    if not source_json_file.exists():
        # Also check one level up (artifact may have been in a subdirectory)
        source_json_dir = source_parent.parent / "json"
        source_json_file = source_json_dir / json_filename
        if not source_json_file.exists():
            return

    target_json_dir = target_path / "json"
    target_json_file = target_json_dir / json_filename

    try:
        target_json_dir.mkdir(parents=True, exist_ok=True)
        if target_json_file.exists():
            # Keep the one with longer performance track record
            with open(source_json_file, "r", encoding="utf-8") as f:
                source_data = json.load(f)
            with open(target_json_file, "r", encoding="utf-8") as f:
                target_data = json.load(f)
            source_len = len(source_data.get("performance", []))
            target_len = len(target_data.get("performance", []))
            if source_len > target_len:
                shutil.move(str(source_json_file), str(target_json_file))
            else:
                source_json_file.unlink()
        else:
            shutil.move(str(source_json_file), str(target_json_file))
    except OSError as e:
        errors.append(f"Failed to move companion {json_filename}: {e}")


def _find_artifact_file(folder: Path, filename: str) -> Path | None:
    """Find an artifact file in a folder or its subdirectories."""
    direct = folder / filename
    if direct.exists():
        return direct
    for f in folder.rglob(filename):
        if f.is_file():
            return f
    return None


def _remove_artifact_from_registry(
    artifact_id: str, firm: str, fund: str | None, canonical_names: dict
) -> dict:
    """Remove an artifact entry from its old location and return it."""
    if firm not in canonical_names:
        return {}
    firm_data = canonical_names[firm]
    if fund:
        fund_data = firm_data.get("funds", {}).get(fund, {})
        return fund_data.get("artifacts", {}).pop(artifact_id, None) or {}
    else:
        return firm_data.get("artifacts", {}).pop(artifact_id, None) or {}




# =========================
# ARTIFACT MOVE MONITORING
# =========================

# System files at the output_dir root that should never be scanned as artifacts
SYSTEM_FILES = {
    FIRM_MAPPINGS_FILE,
    CLASSIFICATION_REPORT_FILE,
}

SKIP_SUBFOLDERS = {"json", "graph", "meetings", "researches"}

# Windows Zone.Identifier alternate data streams (exposed as files on WSL2)
_ZONE_ID_SUFFIX = ".identifier"


def _is_zone_identifier(filename: str) -> bool:
    """Return True if the file is a Windows Zone.Identifier ADS artifact."""
    return filename.lower().endswith(_ZONE_ID_SUFFIX)


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

    # Self-merge guard: if old and new resolve to the same canonical entry, no-op
    if old_firm_key and new_firm_key and old_firm_key == new_firm_key:
        print(
            f"'{old_firm_name}' and '{new_firm_name}' resolve to the same firm "
            f"'{old_firm_key}'. Nothing to do."
        )
        return

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
            old_entry = canonical_names[old_firm_key]

            # Merge funds: carry over funds from old firm that don't exist in new
            old_funds = old_entry.get("funds", {})
            new_funds = canonical_names[new_firm_key].setdefault("funds", {})
            for fund_name, fund_data in old_funds.items():
                if fund_name not in new_funds:
                    new_funds[fund_name] = fund_data
                else:
                    # Fund exists in both — merge artifacts
                    existing_arts = new_funds[fund_name].setdefault("artifacts", {})
                    for art_id, art_data in fund_data.get("artifacts", {}).items():
                        if art_id not in existing_arts:
                            existing_arts[art_id] = art_data

            # Merge firm-level artifacts
            old_artifacts = old_entry.get("artifacts", {})
            new_artifacts = canonical_names[new_firm_key].setdefault("artifacts", {})
            for art_id, art_data in old_artifacts.items():
                if art_id not in new_artifacts:
                    new_artifacts[art_id] = art_data

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
            "funds": {},
            "artifacts": {},
        }

        if old_firm_key:
            # Carry over funds and artifacts from old firm
            old_entry = canonical_names[old_firm_key]
            canonical_names[new_canonical]["funds"] = old_entry.get("funds", {})
            canonical_names[new_canonical]["artifacts"] = old_entry.get("artifacts", {})
            del canonical_names[old_firm_key]
            print(f"Renamed '{old_firm_key}' to '{new_canonical}'")
        else:
            print(f"Created new firm '{new_canonical}' with alias '{old_firm_name}'")

        if old_aliases:
            print(f"Aliases: {list(old_aliases)}")

        new_firm_key = new_canonical

    # Add folder reassignment so future classifications redirect properly
    target = new_firm_key or new_firm_name.upper()
    mappings["folder_reassignments"][old_firm_name.upper()] = target

    # Flatten existing reassignment chains: if any entry pointed to old_firm,
    # update it to point directly to the new target
    for src, dst in list(mappings["folder_reassignments"].items()):
        if dst == old_firm_name.upper() and src != old_firm_name.upper():
            mappings["folder_reassignments"][src] = target

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


