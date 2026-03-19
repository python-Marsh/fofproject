"""
Notion Integration for Firm & Fund Database Sync

Monitors a local folder for new firm/fund directories and syncs them
to Notion databases, uploading files and linking related pages.

Folder structure expected:
    {WATCH_FOLDER}/
    ├── {FIRM_NAME}/
    │   ├── file1.pdf                     (firm-level file)
    │   ├── {FUND_NAME} - {IDENTIFIER}/   (fund subfolder)
    │   │   ├── factsheet.pdf
    │   │   ├── report.xlsx
    │   │   ├── graph/                    (uploaded to Quantitative Screening)
    │   │   │   └── graph1.png
    │   │   ├── json/                     (extracted data, not uploaded)
    │   │   │   └── {IDENTIFIER}.json
    │   │   ├── meetings/                 (downloaded from Notion, not uploaded)
    │   │   │   └── Meeting Title.pdf
    │   │   └── researches/               (downloaded from Notion, not uploaded)
    │   │       └── Research Title.pdf
    │   └── {FUND_NAME2} - {ID2}/
    │       └── ...

Usage:
    poetry run python -m fofproject.notion
    poetry run python -m fofproject.notion --folder "C:/path/to/watch"
"""

from dotenv import load_dotenv
from pathlib import Path
import os
import re
import time
import mimetypes
import logging
import threading

import requests
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from fofproject.classify import (
    SKIP_SUBFOLDERS,
    FIRM_MAPPINGS_FILE,
    _parse_artifact_id_from_filename,
)

# Load .env from the same directory as this script
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

# =========================
# CONFIGURATION
# =========================
NOTION_SECRET = os.getenv("NOTION_SECRET")
NOTION_VERSION = "2025-09-03"
BASE_URL = "https://api.notion.com/v1"

# Default watch folder — same as classify.py's output directory
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_WATCH_FOLDER = _PROJECT_ROOT / "output" / "testing" / "fund firm identifier"

# Fund subfolder pattern: "{FUND_NAME} - {IDENTIFIER}"
FUND_FOLDER_PATTERN = re.compile(r"^(.+?)\s*-\s*(\d+)$")

# Prefix used by performance.py to flag identifier conflicts
CONFLICT_IDENTIFIER_PREFIX = "404 multiple identifier"


# File size limit for single-part upload (20 MB)
MAX_SINGLE_UPLOAD_BYTES = 20 * 1024 * 1024

# Fund page Data Packs section names (H2 headings in the template)
SECTION_PRESENTATION = "Presentation"
SECTION_MONTHLY_LETTERS = "Monthly Letters"
SECTION_OTHERS = "Others"

# H3 heading for graph/ subfolder uploads (under Past Performance)
H3_QUANTITATIVE_SCREENINGS = "Quantitative Screenings"
# Callout text that marks where python-generated graphs should be inserted after
GRAPH_CALLOUT_TEXT = "Please attach python-generated graphs here."

# Keywords used to classify files into sections (case-insensitive)
PRESENTATION_KEYWORDS = [
    "deck",
    "presentation",
    "overview",
    "pitch",
    "ppt",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# =========================
# LOW-LEVEL API HELPERS
# =========================


def _headers(content_type: str = "application/json") -> dict:
    """Return standard Notion API headers."""
    if not NOTION_SECRET:
        raise ValueError(
            "NOTION_SECRET not found in environment variables. "
            "Add it to src/fofproject/.env"
        )
    h = {
        "Authorization": f"Bearer {NOTION_SECRET}",
        "Notion-Version": NOTION_VERSION,
    }
    if content_type:
        h["Content-Type"] = content_type
    return h


def _notion_request(method: str, endpoint: str, **kwargs) -> dict:
    """Make a Notion API request with retry logic for rate limits and gateway timeouts."""
    url = f"{BASE_URL}{endpoint}" if endpoint.startswith("/") else endpoint
    kwargs.setdefault("headers", _headers())
    kwargs.setdefault("timeout", 30)

    retryable_statuses = {429, 502, 503, 504}
    max_retries = 5

    for attempt in range(max_retries):
        try:
            resp = getattr(requests, method)(url, **kwargs)
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                wait = 2**attempt
                log.warning(
                    "Request timed out, retrying in %ds... (attempt %d/%d)",
                    wait,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(wait)
                continue
            raise

        if resp.status_code in retryable_statuses:
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 2))
            else:
                wait = 2**attempt  # exponential backoff: 1, 2, 4, 8, 16s
            log.warning(
                "Notion API %d on %s %s, retrying in %ds... (attempt %d/%d)",
                resp.status_code,
                method.upper(),
                endpoint,
                wait,
                attempt + 1,
                max_retries,
            )
            time.sleep(wait)
            continue

        if resp.status_code >= 400:
            log.error("Notion API error %d: %s", resp.status_code, resp.text)
            resp.raise_for_status()
        return resp.json()

    resp.raise_for_status()
    return {}


def _search_databases() -> list[dict]:
    """Search for all databases the integration can access."""
    results = []
    start_cursor = None

    while True:
        body: dict = {
            "filter": {"property": "object", "value": "data_source"},
        }
        if start_cursor:
            body["start_cursor"] = start_cursor

        data = _notion_request("post", "/search", json=body)
        for item in data.get("results", []):
            title_parts = item.get("title", [])
            title = "".join(t.get("plain_text", "") for t in title_parts)
            results.append({"id": item["id"], "title": title})

        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")

    return results


def discover_databases() -> tuple[str, str]:
    """
    Auto-discover the Firm and Fund database IDs.

    Returns:
        (firm_db_id, fund_db_id)
    """
    databases = _search_databases()
    log.info("Found %d databases: %s", len(databases), [d["title"] for d in databases])

    firm_db_id = None
    fund_db_id = None

    for db in databases:
        title_lower = db["title"].lower()
        if "firm" in title_lower and firm_db_id is None:
            firm_db_id = db["id"]
            log.info("Firm database: %s (%s)", db["title"], db["id"])
        if "fund" in title_lower and fund_db_id is None:
            fund_db_id = db["id"]
            log.info("Fund database: %s (%s)", db["title"], db["id"])

    if not firm_db_id:
        raise RuntimeError(
            f"Could not find a database with 'Firm' in its title. "
            f"Available databases: {[d['title'] for d in databases]}"
        )
    if not fund_db_id:
        raise RuntimeError(
            f"Could not find a database with 'Fund' in its title. "
            f"Available databases: {[d['title'] for d in databases]}"
        )

    return firm_db_id, fund_db_id


def _query_database_by_title(
    db_id: str,
    title: str,
    title_property: str = "Name",
) -> dict | None:
    """Query a database for a page with the given title. Returns page or None."""
    body = {
        "filter": {
            "property": title_property,
            "title": {"equals": title},
        },
        "page_size": 1,
    }
    data = _notion_request("post", f"/data_sources/{db_id}/query", json=body)
    results = data.get("results", [])
    return results[0] if results else None


# Title property names per database (discovered from schema)
FIRM_TITLE_PROP = "Doc name"
FUND_TITLE_PROP = "Fund Name"


def _create_page(
    db_id: str,
    title: str,
    title_property: str = "Name",
    extra_properties: dict | None = None,
    use_template: bool = True,
) -> str:
    """
    Create a new page in a Notion database.

    Args:
        db_id: Database ID
        title: Page title
        title_property: Name of the title property in the database schema
        extra_properties: Additional properties to set (e.g. Identifier, relations)
        use_template: Whether to apply the database's default template

    Returns:
        The created page ID
    """
    properties = {
        title_property: {"title": [{"type": "text", "text": {"content": title}}]}
    }
    if extra_properties:
        properties.update(extra_properties)

    body: dict = {
        "parent": {"data_source_id": db_id},
        "properties": properties,
    }
    if use_template:
        body["template"] = {"type": "default"}

    data = _notion_request("post", "/pages", json=body)
    page_id = data["id"]
    log.info("Created page '%s' -> %s", title, page_id)
    return page_id


def _update_page_properties(page_id: str, properties: dict) -> dict:
    """Update properties on an existing page."""
    body = {"properties": properties}
    return _notion_request("patch", f"/pages/{page_id}", json=body)


def _archive_page(page_id: str):
    """Archive (soft-delete) a Notion page.

    Archived pages can be recovered from the Notion trash:
      - In Notion UI: Settings & members → Trash → find and restore the page
      - Via API: PATCH /pages/{page_id} with {"archived": false}
    """
    _notion_request("patch", f"/pages/{page_id}", json={"archived": True})
    log.info("Archived page %s", page_id)


# =========================
# FILE UPLOAD
# =========================


IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".svg",
    ".webp",
    ".tiff",
    ".ico",
}


def _get_block_type(file_path: Path, as_image: bool = False) -> str:
    """Determine the Notion block type for a file.

    By default returns 'file' so documents appear as clickable attachments
    rather than inline embeds (e.g. PDFs won't auto-expand on the page).

    When *as_image* is True, image files are returned as 'image' so they
    display inline on the page.
    """
    if as_image and file_path.suffix.lower() in IMAGE_EXTENSIONS:
        return "image"
    return "file"


def _get_mime_type(file_path: Path) -> str:
    """Get MIME type for a file."""
    mime, _ = mimetypes.guess_type(str(file_path))
    return mime or "application/octet-stream"


def _upload_file(file_path: Path) -> str | None:
    """
    Upload a file to Notion and return the file_upload_id.

    Returns None if the file is too large or upload fails.
    """
    file_size = file_path.stat().st_size
    if file_size > MAX_SINGLE_UPLOAD_BYTES:
        log.warning(
            "Skipping '%s' — file is %.1f MB (max %d MB for single upload)",
            file_path.name,
            file_size / (1024 * 1024),
            MAX_SINGLE_UPLOAD_BYTES // (1024 * 1024),
        )
        return None

    try:
        # Step 1: Create file upload object
        create_body = {
            "filename": file_path.name,
            "content_type": _get_mime_type(file_path),
        }
        create_resp = _notion_request("post", "/file_uploads", json=create_body)
        upload_id = create_resp["id"]
    except requests.HTTPError as e:
        log.warning(
            "Skipping '%s' — unsupported by Notion File Upload API: %s",
            file_path.name,
            e,
        )
        return None

    # Step 2: Send file content (with retry for gateway timeouts)
    send_url = f"{BASE_URL}/file_uploads/{upload_id}/send"
    send_headers = {
        "Authorization": f"Bearer {NOTION_SECRET}",
        "Notion-Version": NOTION_VERSION,
    }
    retryable = {429, 502, 503, 504}
    send_resp = None

    for attempt in range(5):
        with open(file_path, "rb") as f:
            try:
                send_resp = requests.post(
                    send_url,
                    headers=send_headers,
                    files={"file": (file_path.name, f, _get_mime_type(file_path))},
                    timeout=120,
                )
            except requests.exceptions.Timeout:
                if attempt < 4:
                    wait = 2**attempt
                    log.warning(
                        "File send timed out for '%s', retrying in %ds...",
                        file_path.name,
                        wait,
                    )
                    time.sleep(wait)
                    continue
                log.error("File send timed out for '%s' after retries", file_path.name)
                return None

        if send_resp.status_code in retryable:
            wait = int(send_resp.headers.get("Retry-After", 2**attempt))
            log.warning(
                "File send got %d for '%s', retrying in %ds...",
                send_resp.status_code,
                file_path.name,
                wait,
            )
            time.sleep(wait)
            continue
        break

    if send_resp is None or send_resp.status_code >= 400:
        log.error(
            "Failed to send file '%s': %d %s",
            file_path.name,
            send_resp.status_code if send_resp else 0,
            send_resp.text if send_resp else "no response",
        )
        return None

    log.info("Uploaded file '%s' -> %s", file_path.name, upload_id)
    return upload_id


def _extract_filename_from_url(url: str) -> str | None:
    """Extract the filename from a Notion S3 URL (keeps underscores as-is).

    Handles double-encoded URLs (e.g. %253D → %3D → =) by unquoting
    repeatedly until the string stabilizes.
    """
    from urllib.parse import urlparse, unquote

    try:
        path = urlparse(url).path
        name = path.rsplit("/", 1)[-1]
        # Unquote repeatedly to handle double (or triple) encoding
        prev = None
        while name != prev:
            prev = name
            name = unquote(name)
        return name
    except Exception:
        return None


def _normalize_filename(name: str) -> str:
    """Normalize a filename for comparison (spaces <-> underscores)."""
    return name.replace(" ", "_").lower()


def _delete_block(block_id: str):
    """Delete a Notion block by ID."""
    _notion_request("delete", f"/blocks/{block_id}")
    log.info("Deleted block %s", block_id)


FILE_BLOCK_TYPES = {"file", "pdf", "image", "audio", "video"}


def _find_file_block_ids_in_children(
    parent_id: str,
    normalized_name: str,
) -> list[str]:
    """Find all file block IDs under a parent that match the given normalized filename."""
    matching = []
    start_cursor = None

    while True:
        params = {"page_size": 100}
        if start_cursor:
            params["start_cursor"] = start_cursor
        data = _notion_request("get", f"/blocks/{parent_id}/children", params=params)
        for block in data.get("results", []):
            btype = block.get("type", "")
            if btype in FILE_BLOCK_TYPES:
                block_data = block.get(btype, {})
                name = block_data.get("name")
                if not name:
                    file_info = block_data.get("file", {})
                    url = file_info.get("url", "")
                    if url:
                        name = _extract_filename_from_url(url)
                if name and _normalize_filename(name) == normalized_name:
                    matching.append(block["id"])
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")

    return matching


def _rename_file_block(block_id: str, new_name: str):
    """Rename a file block by updating its name via the Block PATCH endpoint."""
    # Fetch the block to determine its type
    block = _notion_request("get", f"/blocks/{block_id}")
    btype = block.get("type", "")
    if btype not in FILE_BLOCK_TYPES:
        log.warning("Block %s is type '%s', not a file block — skipping rename", block_id, btype)
        return
    _notion_request("patch", f"/blocks/{block_id}", json={btype: {"name": new_name}})
    log.info("Renamed block %s to '%s'", block_id, new_name)


def _rename_file_on_page(page_id: str, old_name: str, new_name: str) -> bool:
    """Find file blocks matching old_name on a page and rename them to new_name.

    Searches page-level children and section callout children.
    Returns True if at least one block was renamed.
    """
    norm = _normalize_filename(old_name)
    block_ids = _find_file_block_ids_in_children(page_id, norm)

    # Also check inside section callout blocks (fund pages)
    section_callouts = _find_section_callout_ids(page_id)
    for callout_id in section_callouts.values():
        block_ids.extend(_find_file_block_ids_in_children(callout_id, norm))

    if block_ids:
        for bid in block_ids:
            _rename_file_block(bid, new_name)
        log.info(
            "Renamed %d block(s) from '%s' to '%s' on page %s",
            len(block_ids), old_name, new_name, page_id,
        )
        return True
    return False


def _remove_file_from_page(page_id: str, filename: str):
    """Remove all file blocks matching filename from a page and its section callouts."""
    norm = _normalize_filename(filename)

    # Check page-level children
    block_ids = _find_file_block_ids_in_children(page_id, norm)

    # Also check inside section callout blocks (fund pages)
    section_callouts = _find_section_callout_ids(page_id)
    for callout_id in section_callouts.values():
        block_ids.extend(_find_file_block_ids_in_children(callout_id, norm))

    if block_ids:
        for bid in block_ids:
            _delete_block(bid)
        log.info(
            "Removed %d block(s) for file '%s' from page %s",
            len(block_ids),
            filename,
            page_id,
        )
    else:
        log.warning("File '%s' not found on page %s", filename, page_id)


def _rename_page_title(page_id: str, new_title: str, title_property: str):
    """Rename a page by updating its title property."""
    _update_page_properties(
        page_id,
        {title_property: {"title": [{"type": "text", "text": {"content": new_title}}]}},
    )
    log.info("Renamed page %s to '%s'", page_id, new_title)


def _remove_file_from_firm_property(page_id: str, filename: str):
    """Remove a file from the 'Firm Documents' files property."""
    norm = _normalize_filename(filename)
    page_data = _notion_request("get", f"/pages/{page_id}")
    existing_files = (
        page_data.get("properties", {}).get("Firm Documents", {}).get("files", [])
    )

    updated = [
        f for f in existing_files if _normalize_filename(f.get("name", "")) != norm
    ]

    if len(updated) == len(existing_files):
        log.warning("File '%s' not found in Firm Documents property", filename)
        return

    _update_page_properties(page_id, {"Firm Documents": {"files": updated}})
    log.info("Removed '%s' from Firm Documents property on page %s", filename, page_id)


def _get_existing_filenames_normalized(page_id: str) -> set[str]:
    """Get normalized filenames of files already attached to a page."""
    filenames = set()
    start_cursor = None
    file_block_types = {"file", "pdf", "image", "audio", "video"}

    while True:
        params = {"page_size": 100}
        if start_cursor:
            params["start_cursor"] = start_cursor
        data = _notion_request("get", f"/blocks/{page_id}/children", params=params)
        for block in data.get("results", []):
            btype = block.get("type", "")
            if btype in file_block_types:
                block_data = block.get(btype, {})
                name = block_data.get("name")
                if not name:
                    file_info = block_data.get("file", {})
                    url = file_info.get("url", "")
                    if url:
                        name = _extract_filename_from_url(url)
                if name:
                    filenames.add(_normalize_filename(name))
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")

    return filenames


def _attach_files_to_page(page_id: str, file_infos: list[tuple[Path, str]]):
    """
    Attach uploaded files to a page as child blocks.

    Args:
        page_id: The Notion page to attach files to
        file_infos: List of (file_path, file_upload_id) tuples
    """
    if not file_infos:
        return

    children = []
    for file_path, upload_id in file_infos:
        block_type = _get_block_type(file_path)
        children.append(
            {
                "type": block_type,
                block_type: {
                    "type": "file_upload",
                    "file_upload": {"id": upload_id},
                },
            }
        )

    # Notion allows max 100 blocks per request
    for i in range(0, len(children), 100):
        batch = children[i : i + 100]
        _notion_request(
            "patch",
            f"/blocks/{page_id}/children",
            json={"children": batch},
        )
        log.info("Attached %d file(s) to page %s", len(batch), page_id)


# =========================
# FUND PAGE SECTION HANDLING
# =========================


def _load_mappings(watch_folder: Path) -> dict:
    """Load firm_fund_mappings.json from the watch folder, or return empty dict."""
    mappings_path = watch_folder / FIRM_MAPPINGS_FILE
    if mappings_path.exists():
        import json as _json

        with open(mappings_path, "r", encoding="utf-8") as f:
            return _json.load(f)
    return {}


def _file_contains_performance(file_path: Path, watch_folder: Path) -> bool:
    """Check if a file is flagged as containing performance in firm_fund_mappings.

    Looks up the artifact by its embedded artifact_id in the filename.
    """
    artifact_id = _parse_artifact_id_from_filename(file_path.name)
    if not artifact_id:
        return False

    mappings = _load_mappings(watch_folder)
    canonical_names = mappings.get("canonical_names", {})

    for firm_entry in canonical_names.values():
        # Check firm-level artifacts
        for art_id, record in firm_entry.get("artifacts", {}).items():
            if art_id == artifact_id:
                return bool(record.get("contains_monthly_net_performance_update"))
        # Check fund-level artifacts
        for fund_entry in firm_entry.get("funds", {}).values():
            for art_id, record in fund_entry.get("artifacts", {}).items():
                if art_id == artifact_id:
                    return bool(record.get("contains_monthly_net_performance_update"))
    return False


def _classify_file(file_path: Path, watch_folder: Path | None = None) -> str:
    """
    Classify a file into a Data Packs section.

    If the file is flagged as containing performance data (via mappings),
    it goes to Presentation (if filename matches presentation keywords)
    or Monthly Letters (default). Non-performance files go to Others.

    Returns one of: SECTION_PRESENTATION, SECTION_MONTHLY_LETTERS, SECTION_OTHERS
    """
    if watch_folder is None:
        # Infer watch_folder: file is at {watch}/{firm}/{fund}/file
        watch_folder = file_path.parent.parent.parent

    has_perf = _file_contains_performance(file_path, watch_folder)

    if has_perf:
        name_lower = file_path.name.lower()
        for kw in PRESENTATION_KEYWORDS:
            if kw in name_lower:
                return SECTION_PRESENTATION
        return SECTION_MONTHLY_LETTERS

    return SECTION_OTHERS


def _find_section_callout_ids(page_id: str) -> dict[str, str]:
    """
    Find the callout block IDs for each Data Packs section on a fund page.

    The template structure is:
        H1: Data Packs
          H2: Presentation
          callout (children go here)
          H2: Monthly Letters
          callout (children go here)
          H2: Others
          callout (children go here)

    Returns: {"Presentation": callout_id, "Monthly Letters": callout_id, "Others": callout_id}
    """
    start_cursor = None
    all_blocks = []
    while True:
        params = {"page_size": 100}
        if start_cursor:
            params["start_cursor"] = start_cursor
        data = _notion_request("get", f"/blocks/{page_id}/children", params=params)
        all_blocks.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")

    section_callouts = {}
    current_h2 = None
    in_data_packs = False

    for block in all_blocks:
        btype = block.get("type", "")

        if btype == "heading_1":
            text_parts = block.get(btype, {}).get("rich_text", [])
            text = "".join(t.get("plain_text", "") for t in text_parts)
            in_data_packs = "data packs" in text.lower()
            current_h2 = None

        elif btype == "heading_2" and in_data_packs:
            text_parts = block.get(btype, {}).get("rich_text", [])
            current_h2 = "".join(t.get("plain_text", "") for t in text_parts).strip()

        elif btype == "callout" and in_data_packs and current_h2:
            section_callouts[current_h2] = block["id"]
            current_h2 = None  # Only take the first callout after each H2

    return section_callouts


def _find_graph_callout(page_id: str) -> str | None:
    """
    Find the callout block containing GRAPH_CALLOUT_TEXT on a fund page.

    This callout sits under the 'Quantitative Screenings' H3 heading and
    marks the insertion point for python-generated graph images.
    Graph files are appended as sibling blocks right after this callout.

    Returns: block ID, or None if not found.
    """
    start_cursor = None
    while True:
        params = {"page_size": 100}
        if start_cursor:
            params["start_cursor"] = start_cursor
        data = _notion_request("get", f"/blocks/{page_id}/children", params=params)
        for block in data.get("results", []):
            if block.get("type") == "callout":
                text_parts = block["callout"].get("rich_text", [])
                text = "".join(t.get("plain_text", "") for t in text_parts).strip()
                if text == GRAPH_CALLOUT_TEXT:
                    return block["id"]
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
    return None


def _upload_files_into_callout(
    callout_id: str,
    files: list[Path],
    label: str = "graph",
    as_image: bool = False,
):
    """
    Upload files and append them as children inside a callout block.

    Skips files whose normalized name already appears inside the callout.

    When *as_image* is True, image files are inserted as ``image`` blocks
    so they render inline on the Notion page.
    """
    existing = _get_existing_filenames_in_block(callout_id)
    new_files = [f for f in files if _normalize_filename(f.name) not in existing]
    if len(new_files) < len(files):
        log.info(
            "Quantitative Screenings: skipping %d already-uploaded file(s)",
            len(files) - len(new_files),
        )
    if not new_files:
        return

    file_infos = []
    for f in new_files:
        uid = _upload_file(f)
        if uid:
            file_infos.append((f, uid))

    if not file_infos:
        return

    children = []
    for file_path, upload_id in file_infos:
        block_type = _get_block_type(file_path, as_image=as_image)
        children.append(
            {
                "type": block_type,
                block_type: {
                    "type": "file_upload",
                    "file_upload": {"id": upload_id},
                },
            }
        )

    for i in range(0, len(children), 100):
        batch = children[i : i + 100]
        _notion_request(
            "patch",
            f"/blocks/{callout_id}/children",
            json={"children": batch},
        )
    log.info(
        "Attached %d %s file(s) inside '%s' callout",
        len(file_infos),
        label,
        GRAPH_CALLOUT_TEXT,
    )


def _get_existing_filenames_in_block(block_id: str) -> set[str]:
    """Get normalized filenames of files that are children of a specific block."""
    filenames = set()
    file_block_types = {"file", "pdf", "image", "audio", "video"}
    start_cursor = None

    while True:
        params = {"page_size": 100}
        if start_cursor:
            params["start_cursor"] = start_cursor
        data = _notion_request("get", f"/blocks/{block_id}/children", params=params)
        for child in data.get("results", []):
            btype = child.get("type", "")
            if btype in file_block_types:
                block_data = child.get(btype, {})
                name = block_data.get("name")
                if not name:
                    file_info = block_data.get("file", {})
                    url = file_info.get("url", "")
                    if url:
                        name = _extract_filename_from_url(url)
                if name:
                    filenames.add(_normalize_filename(name))
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")

    return filenames


def _upload_files_to_sections(
    page_id: str,
    files: list[Path],
    section_callouts: dict[str, str],
):
    """
    Upload files into the correct Data Packs section callout on a fund page.

    Files are classified by name and uploaded as children of the matching
    callout block (Presentation, Monthly Letters, or Others).
    """
    # Group files by section
    by_section: dict[str, list[Path]] = {
        SECTION_PRESENTATION: [],
        SECTION_MONTHLY_LETTERS: [],
        SECTION_OTHERS: [],
    }
    for f in files:
        section = _classify_file(f)
        by_section[section].append(f)

    for section_name, section_files in by_section.items():
        if not section_files:
            continue

        callout_id = section_callouts.get(section_name)
        if not callout_id:
            log.warning(
                "Section '%s' callout not found on page, "
                "falling back to page-level attachment",
                section_name,
            )
            # Fallback: attach directly to page
            file_infos = []
            for f in section_files:
                uid = _upload_file(f)
                if uid:
                    file_infos.append((f, uid))
            _attach_files_to_page(page_id, file_infos)
            continue

        # Check existing files in this callout to skip duplicates
        existing = _get_existing_filenames_in_block(callout_id)
        new_files = [
            f for f in section_files if _normalize_filename(f.name) not in existing
        ]
        if len(new_files) < len(section_files):
            log.info(
                "Section '%s': skipping %d already-uploaded file(s)",
                section_name,
                len(section_files) - len(new_files),
            )
        if not new_files:
            continue

        # Upload and attach to the callout block
        file_infos = []
        for f in new_files:
            uid = _upload_file(f)
            if uid:
                file_infos.append((f, uid))

        if file_infos:
            children = []
            for file_path, upload_id in file_infos:
                block_type = _get_block_type(file_path)
                children.append(
                    {
                        "type": block_type,
                        block_type: {
                            "type": "file_upload",
                            "file_upload": {"id": upload_id},
                        },
                    }
                )

            for i in range(0, len(children), 100):
                batch = children[i : i + 100]
                _notion_request(
                    "patch",
                    f"/blocks/{callout_id}/children",
                    json={"children": batch},
                )
            log.info(
                "Attached %d file(s) to '%s' section",
                len(file_infos),
                section_name,
            )


def _wait_for_template(page_id: str, max_wait: int = 30) -> bool:
    """
    Wait for the async template to populate on a newly created page.

    Returns True if template blocks appeared, False if timed out.
    """
    for attempt in range(max_wait // 2):
        data = _notion_request(
            "get", f"/blocks/{page_id}/children", params={"page_size": 5}
        )
        if data.get("results"):
            return True
        log.info("Waiting for template to apply... (%ds)", (attempt + 1) * 2)
        time.sleep(2)
    log.warning("Template did not populate within %ds", max_wait)
    return False


def _upload_firm_files_to_property(page_id: str, files: list[Path]):
    """
    Upload firm-level files and set them on the 'Firm Documents' files property.

    Notion's files property requires file_upload references.
    """
    if not files:
        return

    # Get existing files in the property to avoid duplicates
    page_data = _notion_request("get", f"/pages/{page_id}")
    existing_files = (
        page_data.get("properties", {}).get("Firm Documents", {}).get("files", [])
    )
    existing_names = {_normalize_filename(f.get("name", "")) for f in existing_files}

    new_files = [f for f in files if _normalize_filename(f.name) not in existing_names]
    if len(new_files) < len(files):
        log.info(
            "Skipping %d already-uploaded firm file(s)",
            len(files) - len(new_files),
        )
    if not new_files:
        return

    # Upload each file
    upload_entries = []
    # Keep existing file references
    for ef in existing_files:
        if ef.get("type") == "file":
            upload_entries.append(ef)
        elif ef.get("type") == "file_upload":
            upload_entries.append(ef)

    for f in new_files:
        uid = _upload_file(f)
        if uid:
            upload_entries.append(
                {
                    "type": "file_upload",
                    "file_upload": {"id": uid},
                    "name": f.name,
                }
            )

    # Update the property
    _update_page_properties(
        page_id,
        {
            "Firm Documents": {
                "files": upload_entries,
            }
        },
    )
    log.info("Updated Firm Documents with %d file(s)", len(upload_entries))


# =========================
# FOLDER PARSING
# =========================


def parse_fund_folder_name(folder_name: str) -> tuple[str, str] | None:
    """
    Parse a fund folder name into (fund_name, identifier).

    Expected format: "{FUND_NAME} - {NUMERIC_ID}"
    Returns None if the folder name doesn't match the pattern.
    """
    match = FUND_FOLDER_PATTERN.match(folder_name.strip())
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip()


def _resolve_fund_name(folder_name: str) -> str:
    """
    Extract the fund name from a folder name.

    If the folder matches the pattern "{NAME} - {ID}", returns NAME.
    Otherwise returns the full folder name (for folders renamed without an identifier).
    """
    parsed = parse_fund_folder_name(folder_name)
    return parsed[0] if parsed else folder_name


def get_firm_contents(firm_path: Path) -> tuple[list[Path], list[Path]]:
    """
    Analyze a firm folder and separate firm-level files from fund subfolders.

    Returns:
        (firm_files, fund_folders)
    """
    firm_files = []
    fund_folders = []

    for item in firm_path.iterdir():
        if item.is_dir():
            if item.name.startswith(CONFLICT_IDENTIFIER_PREFIX):
                log.warning(
                    "Skipping fund folder '%s' — flagged as identifier conflict",
                    item.name,
                )
            elif parse_fund_folder_name(item.name):
                fund_folders.append(item)
            else:
                log.warning(
                    "Skipping subdirectory '%s' — doesn't match fund pattern "
                    "'{FUND_NAME} - {ID}'",
                    item.name,
                )
        elif item.is_file():
            firm_files.append(item)

    return firm_files, fund_folders


# =========================
# SYNC LOGIC
# =========================


def _upload_and_attach_files(page_id: str, files: list[Path]):
    """Upload a list of files and attach them to a Notion page, skipping duplicates."""
    existing = _get_existing_filenames_normalized(page_id)
    new_files = [f for f in files if _normalize_filename(f.name) not in existing]
    if len(new_files) < len(files):
        log.info("Skipping %d already-uploaded file(s)", len(files) - len(new_files))
    file_infos = []
    for f in new_files:
        upload_id = _upload_file(f)
        if upload_id:
            file_infos.append((f, upload_id))
    _attach_files_to_page(page_id, file_infos)


def sync_fund(
    fund_path: Path,
    fund_db_id: str,
    firm_page_id: str,
) -> str | None:
    """
    Sync a single fund folder to the Notion Fund database.

    Args:
        fund_path: Path to the fund folder
        fund_db_id: Notion Fund database ID
        firm_page_id: Notion page ID of the parent firm

    Returns:
        The fund page ID, or None if skipped
    """
    parsed = parse_fund_folder_name(fund_path.name)
    if not parsed:
        log.warning("Cannot parse fund folder name: '%s'", fund_path.name)
        return None

    fund_name, identifier = parsed
    log.info("Syncing fund: %s (identifier: %s)", fund_name, identifier)

    # Check if fund already exists
    existing = _query_database_by_title(fund_db_id, fund_name, FUND_TITLE_PROP)
    if existing:
        fund_page_id = existing["id"]
        log.info("Fund '%s' already exists -> %s, skipping uploads", fund_name, fund_page_id)
        return fund_page_id

    # Create fund page with Identifier and AM Firm relation
    extra_properties = {
        "Identifier": {
            "rich_text": [{"type": "text", "text": {"content": identifier}}]
        },
        "AM Firm": {"relation": [{"id": firm_page_id}]},
    }
    fund_page_id = _create_page(
        fund_db_id,
        fund_name,
        title_property=FUND_TITLE_PROP,
        extra_properties=extra_properties,
        use_template=True,
    )

    # Wait for template to be applied to the new page
    _wait_for_template(fund_page_id)

    # Find section callout blocks from the template
    section_callouts = _find_section_callout_ids(fund_page_id)

    # Upload fund files into the correct sections
    fund_files = [f for f in fund_path.iterdir() if f.is_file()]
    if fund_files:
        log.info("Uploading %d file(s) for fund '%s'", len(fund_files), fund_name)
        if section_callouts:
            _upload_files_to_sections(fund_page_id, fund_files, section_callouts)
        else:
            log.warning(
                "No Data Packs sections found on page, "
                "falling back to page-level upload"
            )
            _upload_and_attach_files(fund_page_id, fund_files)

    # Upload graph/ subfolder files as inline images after the graph callout
    graph_dir = fund_path / "graph"
    if graph_dir.is_dir():
        graph_files = [f for f in graph_dir.iterdir() if f.is_file()]
        if graph_files:
            callout_id = _find_graph_callout(fund_page_id)
            if callout_id:
                log.info(
                    "Uploading %d graph file(s) for fund '%s'",
                    len(graph_files),
                    fund_name,
                )
                _upload_files_into_callout(
                    callout_id,
                    graph_files,
                    label="graph",
                    as_image=True,
                )
            else:
                log.warning(
                    "Graph callout '%s' not found on page for fund '%s', "
                    "skipping graph uploads",
                    GRAPH_CALLOUT_TEXT,
                    fund_name,
                )

    # Upload JSON properties to the fund page
    upload_json_folder_to_fund(fund_page_id, fund_path)

    return fund_page_id


def sync_firm(firm_path: Path, firm_db_id: str, fund_db_id: str):
    """
    Sync a firm folder (and its fund subfolders) to Notion.

    Args:
        firm_path: Path to the firm folder
        firm_db_id: Notion Firm database ID
        fund_db_id: Notion Fund database ID
    """
    firm_name = firm_path.name
    log.info("=== Syncing firm: %s ===", firm_name)

    # Check if firm already exists
    existing = _query_database_by_title(firm_db_id, firm_name, FIRM_TITLE_PROP)
    if existing:
        firm_page_id = existing["id"]
        log.info("Firm '%s' already exists -> %s", firm_name, firm_page_id)
    else:
        firm_page_id = _create_page(
            firm_db_id,
            firm_name,
            title_property=FIRM_TITLE_PROP,
            use_template=True,
        )

    # Parse folder contents
    firm_files, fund_folders = get_firm_contents(firm_path)

    # Only upload firm-level files if the firm was just created
    if not existing and firm_files:
        log.info("Uploading %d firm-level file(s)", len(firm_files))
        _upload_firm_files_to_property(firm_page_id, firm_files)

    # Sync each fund subfolder (sync_fund skips existing funds)
    fund_page_ids = []
    for fund_folder in fund_folders:
        fund_page_id = sync_fund(fund_folder, fund_db_id, firm_page_id)
        if fund_page_id:
            fund_page_ids.append(fund_page_id)

    # Update firm's "Managing Funds" relation to include all fund pages
    if fund_page_ids:
        _update_page_properties(
            firm_page_id,
            {"Managing Funds": {"relation": [{"id": pid} for pid in fund_page_ids]}},
        )
        log.info("Linked firm '%s' to %d fund(s)", firm_name, len(fund_page_ids))

    log.info("=== Done syncing firm: %s ===", firm_name)


# =========================
# JSON → NOTION PROPERTY UPLOAD
# =========================

# Hardcoded geocoding map for common fund-manager cities.
# Keys are lowercase city/location fragments matched against the GPT-extracted base string.
CITY_COORDINATES: dict[str, tuple[float, float]] = {
    "hong kong": (22.3193, 114.1694),
    "singapore": (1.3521, 103.8198),
    "new york": (40.7128, -74.0060),
    "london": (51.5074, -0.1278),
    "tokyo": (35.6762, 139.6503),
    "seoul": (37.5665, 126.9780),
    "shanghai": (31.2304, 121.4737),
    "beijing": (39.9042, 116.4074),
    "shenzhen": (22.5431, 114.0579),
    "taipei": (25.0330, 121.5654),
    "mumbai": (19.0760, 72.8777),
    "zurich": (47.3769, 8.5417),
    "geneva": (46.2044, 6.1432),
    "sydney": (33.8688, 151.2093),
    "melbourne": (-37.8136, 144.9631),
    "san francisco": (37.7749, -122.4194),
    "chicago": (41.8781, -87.6298),
    "boston": (42.3601, -71.0589),
    "los angeles": (34.0522, -118.2437),
    "dallas": (32.7767, -96.7970),
    "greenwich": (41.0262, -73.6282),
    "stamford": (41.0534, -73.5387),
    "dubai": (25.2048, 55.2708),
    "abu dhabi": (24.4539, 54.3773),
    "cayman": (19.3133, -81.2546),
    "luxembourg": (49.6117, 6.1319),
    "dublin": (53.3498, -6.2603),
    "paris": (48.8566, 2.3522),
    "amsterdam": (52.3676, 4.9041),
    "frankfurt": (50.1109, 8.6821),
    "edinburgh": (55.9533, -3.1883),
    "toronto": (43.6532, -79.3832),
    "vancouver": (49.2827, -123.1207),
    "jakarta": (6.2088, 106.8456),
    "kuala lumpur": (3.1390, 101.6869),
    "bangkok": (13.7563, 100.5018),
    "manila": (14.5995, 120.9842),
    "sao paulo": (-23.5505, -46.6333),
    "mexico city": (19.4326, -99.1332),
}

# Country name → capital city key in CITY_COORDINATES.
# Used as a fallback when only a country name is provided.
COUNTRY_CAPITALS: dict[str, str] = {
    "china": "beijing",
    "japan": "tokyo",
    "south korea": "seoul",
    "korea": "seoul",
    "india": "mumbai",
    "indonesia": "jakarta",
    "malaysia": "kuala lumpur",
    "thailand": "bangkok",
    "philippines": "manila",
    "taiwan": "taipei",
    "united states": "new york",
    "usa": "new york",
    "us": "new york",
    "united kingdom": "london",
    "uk": "london",
    "england": "london",
    "scotland": "edinburgh",
    "switzerland": "zurich",
    "australia": "sydney",
    "canada": "toronto",
    "france": "paris",
    "germany": "frankfurt",
    "netherlands": "amsterdam",
    "ireland": "dublin",
    "uae": "dubai",
    "united arab emirates": "dubai",
    "brazil": "sao paulo",
    "mexico": "mexico city",
    "luxembourg": "luxembourg",
    "cayman islands": "cayman",
    "singapore": "singapore",
    "hong kong": "hong kong",
}


def _geocode_base(base_str: str) -> dict | None:
    """Convert a base location string to a Notion place property value.

    Matches against CITY_COORDINATES using case-insensitive substring search.
    Falls back to COUNTRY_CAPITALS if no city match is found.
    Returns a dict ready to be used as a Notion ``place`` property value,
    or None if no match is found.
    """
    if not base_str:
        return None
    lower = base_str.lower()
    # Try matching a city first
    for city, (lat, lon) in CITY_COORDINATES.items():
        if city in lower:
            return {"place": {"name": base_str, "lat": lat, "lon": lon}}
    # Fall back to country → capital
    for country, capital_key in COUNTRY_CAPITALS.items():
        if country in lower:
            lat, lon = CITY_COORDINATES[capital_key]
            return {"place": {"name": base_str, "lat": lat, "lon": lon}}
    return None


def _build_fund_properties(data: dict) -> dict:
    """Build a Notion properties payload from a JSON result dict.

    Maps JSON fields extracted by load.py to their corresponding
    Notion fund page property names and formats.
    """
    props: dict = {}

    # One Liner (rich_text)
    if data.get("one_liner"):
        props["One Liner"] = {
            "rich_text": [{"type": "text", "text": {"content": data["one_liner"]}}]
        }

    # AI summary — same as one_liner for now (rich_text)
    # Skipped — no JSON source

    # Geo Focus (select)
    if data.get("geo_focus"):
        props["Geo Focus"] = {"select": {"name": data["geo_focus"]}}

    # Strategy (multi_select)
    if data.get("strategy"):
        strategies = [{"name": s} for s in data["strategy"] if s]
        if strategies:
            props["Strategy"] = {"multi_select": strategies}

    # Asset Class (multi_select)
    if data.get("asset_class"):
        asset_classes = [{"name": a} for a in data["asset_class"] if a]
        if asset_classes:
            props["Asset Class"] = {"multi_select": asset_classes}

    # IR name (rich_text)
    if data.get("ir_name"):
        props["IR name"] = {
            "rich_text": [{"type": "text", "text": {"content": data["ir_name"]}}]
        }

    # Email (email)
    if data.get("email"):
        props["Email"] = {"email": data["email"]}

    # Phone (phone_number)
    if data.get("phone"):
        props["Phone"] = {"phone_number": data["phone"]}

    # Base (place) — requires geocoding
    if data.get("base"):
        place_val = _geocode_base(data["base"])
        if place_val:
            props["Base"] = place_val
        else:
            log.warning(
                "Could not geocode base '%s' — skipping Base property",
                data["base"],
            )

    # Fund Inception (date)
    if data.get("fund_inception"):
        props["Fund Inception"] = {"date": {"start": data["fund_inception"]}}

    # AUM (mn) (number)
    if data.get("aum_size") is not None:
        props["AUM (mn)"] = {"number": data["aum_size"]}

    # Return (p.a.) (number)
    if data.get("return_pa") is not None:
        props["Return (p.a.)"] = {"number": data["return_pa"]}

    # Volatility (p.a.) (number)
    if data.get("volatility_pa") is not None:
        props["Volatility (p.a.)"] = {"number": data["volatility_pa"]}

    # Min Ticket ('000 USD) (number)
    if data.get("min_ticket") is not None:
        props["Min Ticket ('000 USD)"] = {"number": data["min_ticket"]}

    # Identifier (rich_text)
    if data.get("identifier"):
        props["Identifier"] = {
            "rich_text": [{"type": "text", "text": {"content": data["identifier"]}}]
        }

    return props


def upload_json_to_fund_page(fund_page_id: str, json_path: Path):
    """Read a JSON file and update the corresponding Notion fund page properties.

    Args:
        fund_page_id: The Notion page ID for the fund.
        json_path: Path to the JSON file produced by load.py.
    """
    import json as _json

    with open(json_path, "r", encoding="utf-8") as f:
        data = _json.load(f)

    props = _build_fund_properties(data)
    if not props:
        log.info("No properties to update from %s", json_path.name)
        return

    _update_page_properties(fund_page_id, props)
    log.info(
        "Updated %d properties on fund page %s from %s",
        len(props),
        fund_page_id,
        json_path.name,
    )


def upload_json_folder_to_fund(fund_page_id: str, fund_path: Path):
    """Upload all JSON files in a fund's json/ subfolder to its Notion page.

    If multiple JSON files exist, the most recently modified one is used.

    Args:
        fund_page_id: The Notion page ID for the fund.
        fund_path: Path to the fund folder (parent of json/).
    """
    json_dir = fund_path / "json"
    if not json_dir.is_dir():
        return

    json_files = sorted(
        json_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not json_files:
        return

    # Use the most recent JSON file
    upload_json_to_fund_page(fund_page_id, json_files[0])
    if len(json_files) > 1:
        log.info(
            "Multiple JSON files found in %s — used most recent: %s",
            json_dir,
            json_files[0].name,
        )


# =========================
# WATCHDOG HANDLER
# =========================


class NotionFolderHandler(FileSystemEventHandler):
    """Watches for new firm/fund folders and files, syncs to Notion."""

    def __init__(self, watch_folder: Path, firm_db_id: str, fund_db_id: str):
        super().__init__()
        self.watch_folder = watch_folder
        self.firm_db_id = firm_db_id
        self.fund_db_id = fund_db_id

    def on_created(self, event):
        if not event.is_directory:
            # A new file was added — figure out where it belongs
            self._handle_new_file(Path(event.src_path))
            return

        new_path = Path(event.src_path)
        parent = new_path.parent

        if parent == self.watch_folder:
            # New firm folder created
            log.info("New firm folder detected: %s", new_path.name)
            threading.Thread(
                target=sync_firm,
                args=(new_path, self.firm_db_id, self.fund_db_id),
                daemon=True,
            ).start()
        elif parent.parent == self.watch_folder:
            # New fund folder inside an existing firm
            if new_path.name.startswith(CONFLICT_IDENTIFIER_PREFIX):
                log.warning(
                    "Skipping new fund folder '%s' — identifier conflict",
                    new_path.name,
                )
                return
            parsed = parse_fund_folder_name(new_path.name)
            if parsed:
                log.info("New fund folder detected: %s", new_path.name)
                firm_name = parent.name
                existing_firm = _query_database_by_title(
                    self.firm_db_id, firm_name, FIRM_TITLE_PROP
                )
                if existing_firm:
                    threading.Thread(
                        target=sync_fund,
                        args=(
                            new_path,
                            self.fund_db_id,
                            existing_firm["id"],
                        ),
                        daemon=True,
                    ).start()
                else:
                    log.warning(
                        "Fund folder '%s' appeared but firm '%s' not found "
                        "in Notion. Syncing entire firm.",
                        new_path.name,
                        firm_name,
                    )
                    threading.Thread(
                        target=sync_firm,
                        args=(parent, self.firm_db_id, self.fund_db_id),
                        daemon=True,
                    ).start()

    def _handle_new_file(self, file_path: Path):
        """Handle a new file added to an existing firm or fund folder."""
        parent = file_path.parent

        # Handle files in graph/ subfolder — upload to Quantitative Screening
        if parent.name == "graph":
            fund_folder = parent.parent
            firm_folder = fund_folder.parent
            if firm_folder.parent == self.watch_folder:
                fund_name = _resolve_fund_name(fund_folder.name)
                existing_fund = _query_database_by_title(
                    self.fund_db_id, fund_name, FUND_TITLE_PROP
                )
                if existing_fund:
                    log.info(
                        "New graph file in fund '%s': %s",
                        fund_name,
                        file_path.name,
                    )
                    threading.Thread(
                        target=self._upload_graph_file_to_section,
                        args=(existing_fund["id"], file_path),
                        daemon=True,
                    ).start()
            return

        # Handle files in json/ subfolder — upload properties to fund page
        if parent.name == "json" and file_path.suffix.lower() == ".json":
            fund_folder = parent.parent
            firm_folder = fund_folder.parent
            if firm_folder.parent == self.watch_folder:
                fund_name = _resolve_fund_name(fund_folder.name)
                existing_fund = _query_database_by_title(
                    self.fund_db_id, fund_name, FUND_TITLE_PROP
                )
                if existing_fund:
                    log.info(
                        "New JSON file in fund '%s': %s",
                        fund_name,
                        file_path.name,
                    )
                    threading.Thread(
                        target=upload_json_to_fund_page,
                        args=(existing_fund["id"], file_path),
                        daemon=True,
                    ).start()
            return

        # Skip files inside other managed subfolders (meetings/, researches/)
        if parent.name in SKIP_SUBFOLDERS:
            log.debug(
                "Skipping file in managed subfolder '%s': %s",
                parent.name,
                file_path.name,
            )
            return

        grandparent = parent.parent

        # File in a fund folder
        if grandparent.parent == self.watch_folder:
            fund_name = _resolve_fund_name(parent.name)
            existing_fund = _query_database_by_title(
                self.fund_db_id, fund_name, FUND_TITLE_PROP
            )
            if existing_fund:
                log.info(
                    "New file in fund '%s': %s",
                    fund_name,
                    file_path.name,
                )
                threading.Thread(
                    target=self._upload_fund_file_to_section,
                    args=(existing_fund["id"], file_path),
                    daemon=True,
                ).start()
                return

        # File in a firm folder (direct child)
        if parent.parent == self.watch_folder:
            firm_name = parent.name
            existing_firm = _query_database_by_title(
                self.firm_db_id, firm_name, FIRM_TITLE_PROP
            )
            if existing_firm:
                log.info(
                    "New file in firm '%s': %s",
                    firm_name,
                    file_path.name,
                )
                threading.Thread(
                    target=_upload_firm_files_to_property,
                    args=(existing_firm["id"], [file_path]),
                    daemon=True,
                ).start()

    def _upload_fund_file_to_section(self, fund_page_id: str, file_path: Path):
        """Upload a single fund file into the correct Data Packs section."""
        section_callouts = _find_section_callout_ids(fund_page_id)
        if section_callouts:
            _upload_files_to_sections(fund_page_id, [file_path], section_callouts)
        else:
            log.warning(
                "No Data Packs sections found on page %s, "
                "falling back to page-level upload",
                fund_page_id,
            )
            _upload_and_attach_files(fund_page_id, [file_path])

    def _upload_graph_file_to_section(self, fund_page_id: str, file_path: Path):
        """Upload a single graph file as an inline image after the graph callout."""
        callout_id = _find_graph_callout(fund_page_id)
        if not callout_id:
            log.warning(
                "Graph callout '%s' not found on page %s, skipping graph upload",
                GRAPH_CALLOUT_TEXT,
                fund_page_id,
            )
            return
        _upload_files_into_callout(
            callout_id,
            [file_path],
            label="graph",
            as_image=True,
        )

    def on_modified(self, event):
        """Handle file modification — re-upload JSON properties to Notion."""
        if event.is_directory:
            return

        file_path = Path(event.src_path)
        parent = file_path.parent

        # Only handle JSON files in json/ subfolders
        if parent.name != "json" or file_path.suffix.lower() != ".json":
            return

        fund_folder = parent.parent
        firm_folder = fund_folder.parent
        if firm_folder.parent != self.watch_folder:
            return

        fund_name = _resolve_fund_name(fund_folder.name)
        existing_fund = _query_database_by_title(
            self.fund_db_id, fund_name, FUND_TITLE_PROP
        )
        if existing_fund:
            log.info(
                "JSON file modified in fund '%s': %s",
                fund_name, file_path.name,
            )
            threading.Thread(
                target=upload_json_to_fund_page,
                args=(existing_fund["id"], file_path),
                daemon=True,
            ).start()

    def on_deleted(self, event):
        """Handle file or folder deletion — remove corresponding content from Notion."""
        deleted_path = Path(event.src_path)

        if event.is_directory:
            self._handle_folder_deleted(deleted_path)
            return

        parent = deleted_path.parent
        filename = deleted_path.name

        # Skip files in managed subfolders (graph/, json/, meetings/, researches/)
        if parent.name in SKIP_SUBFOLDERS:
            log.debug(
                "Skipping deletion in managed subfolder '%s': %s", parent.name, filename
            )
            return

        grandparent = parent.parent

        # File deleted from a fund folder
        if grandparent.parent == self.watch_folder:
            fund_name = _resolve_fund_name(parent.name)
            existing_fund = _query_database_by_title(
                self.fund_db_id, fund_name, FUND_TITLE_PROP
            )
            if existing_fund:
                log.info(
                    "File deleted from fund '%s': %s",
                    fund_name,
                    filename,
                )
                threading.Thread(
                    target=_remove_file_from_page,
                    args=(existing_fund["id"], filename),
                    daemon=True,
                ).start()
            return

        # File deleted from a firm folder
        if parent.parent == self.watch_folder:
            firm_name = parent.name
            existing_firm = _query_database_by_title(
                self.firm_db_id, firm_name, FIRM_TITLE_PROP
            )
            if existing_firm:
                log.info(
                    "File deleted from firm '%s': %s",
                    firm_name,
                    filename,
                )
                threading.Thread(
                    target=_remove_file_from_firm_property,
                    args=(existing_firm["id"], filename),
                    daemon=True,
                ).start()

    def _handle_folder_deleted(self, deleted_path: Path):
        """Handle a folder being deleted — archive the corresponding Notion page."""
        parent = deleted_path.parent

        # Fund folder deleted (child of a firm folder)
        if parent.parent == self.watch_folder:
            fund_name = _resolve_fund_name(deleted_path.name)
            existing_fund = _query_database_by_title(
                self.fund_db_id, fund_name, FUND_TITLE_PROP
            )
            if existing_fund:
                log.info("Fund folder deleted: '%s' — archiving Notion page", deleted_path.name)
                threading.Thread(
                    target=_archive_page,
                    args=(existing_fund["id"],),
                    daemon=True,
                ).start()
            return

        # Firm folder deleted (direct child of watch folder)
        if parent == self.watch_folder:
            firm_name = deleted_path.name
            existing_firm = _query_database_by_title(
                self.firm_db_id, firm_name, FIRM_TITLE_PROP
            )
            if existing_firm:
                log.info("Firm folder deleted: '%s' — archiving Notion page", firm_name)
                threading.Thread(
                    target=_archive_page,
                    args=(existing_firm["id"],),
                    daemon=True,
                ).start()

    def on_moved(self, event):
        """Handle file or folder rename/move — update Notion accordingly."""
        old_path = Path(event.src_path)
        new_path = Path(event.dest_path)

        if event.is_directory:
            self._handle_folder_rename(old_path, new_path)
        else:
            self._handle_file_rename(old_path, new_path)

    def _handle_folder_rename(self, old_path: Path, new_path: Path):
        """Handle a folder being renamed."""
        parent = old_path.parent

        # Firm folder renamed (direct child of watch folder)
        if parent == self.watch_folder:
            old_name = old_path.name
            new_name = new_path.name
            existing_firm = _query_database_by_title(
                self.firm_db_id, old_name, FIRM_TITLE_PROP
            )
            if existing_firm:
                log.info("Firm folder renamed: '%s' -> '%s'", old_name, new_name)
                threading.Thread(
                    target=_rename_page_title,
                    args=(existing_firm["id"], new_name, FIRM_TITLE_PROP),
                    daemon=True,
                ).start()
            return

        # Fund folder renamed (child of a firm folder)
        if parent.parent == self.watch_folder:
            old_parsed = parse_fund_folder_name(old_path.name)
            new_parsed = parse_fund_folder_name(new_path.name)

            # Determine old fund name to look up in Notion
            old_fund_name = old_parsed[0] if old_parsed else old_path.name

            # Determine new fund name and identifier
            if new_parsed:
                new_fund_name, new_identifier = new_parsed
            else:
                # No identifier in new name — use full folder name, clear identifier
                new_fund_name = new_path.name
                new_identifier = ""

            existing_fund = _query_database_by_title(
                self.fund_db_id, old_fund_name, FUND_TITLE_PROP
            )
            if existing_fund:
                log.info(
                    "Fund folder renamed: '%s' -> '%s'",
                    old_path.name,
                    new_path.name,
                )

                def _rename_fund(page_id, name, identifier):
                    _rename_page_title(page_id, name, FUND_TITLE_PROP)
                    _update_page_properties(
                        page_id,
                        {
                            "Identifier": {
                                "rich_text": [
                                    {"type": "text", "text": {"content": identifier}}
                                ]
                                if identifier
                                else []
                            },
                        },
                    )

                threading.Thread(
                    target=_rename_fund,
                    args=(existing_fund["id"], new_fund_name, new_identifier),
                    daemon=True,
                ).start()

    def _handle_file_rename(self, old_path: Path, new_path: Path):
        """Handle a file being renamed or moved — rename block or move between pages."""
        old_parent = old_path.parent
        new_parent = new_path.parent

        # Same-folder rename: just rename the block on the same page
        if old_parent == new_parent:
            self._handle_same_folder_rename(old_path, new_path)
            return

        # Cross-folder move: remove from source page, upload to destination page
        self._handle_cross_folder_move(old_path, new_path)

    def _handle_same_folder_rename(self, old_path: Path, new_path: Path):
        """Handle a file renamed within the same folder."""
        parent = old_path.parent

        # File renamed within a fund folder
        if parent.parent.parent == self.watch_folder:
            fund_name = _resolve_fund_name(parent.name)
            existing_fund = _query_database_by_title(
                self.fund_db_id, fund_name, FUND_TITLE_PROP
            )
            if existing_fund:
                log.info(
                    "File renamed in fund '%s': '%s' -> '%s'",
                    fund_name,
                    old_path.name,
                    new_path.name,
                )
                threading.Thread(
                    target=_rename_file_on_page,
                    args=(existing_fund["id"], old_path.name, new_path.name),
                    daemon=True,
                ).start()
            return

        # File renamed within a firm folder
        if parent.parent == self.watch_folder:
            firm_name = parent.name
            existing_firm = _query_database_by_title(
                self.firm_db_id, firm_name, FIRM_TITLE_PROP
            )
            if existing_firm:
                log.info(
                    "File renamed in firm '%s': '%s' -> '%s'",
                    firm_name,
                    old_path.name,
                    new_path.name,
                )
                threading.Thread(
                    target=_rename_file_on_page,
                    args=(existing_firm["id"], old_path.name, new_path.name),
                    daemon=True,
                ).start()

    def _resolve_page_for_path(self, file_path: Path) -> tuple[str | None, str]:
        """Resolve a file path to its Notion page ID and page type ('fund' or 'firm').

        Returns (page_id, page_type) or (None, '') if no matching page is found.
        """
        parent = file_path.parent

        # File in a fund folder (watch/firm/fund/file)
        if parent.parent.parent == self.watch_folder:
            fund_name = _resolve_fund_name(parent.name)
            existing = _query_database_by_title(
                self.fund_db_id, fund_name, FUND_TITLE_PROP
            )
            if existing:
                return existing["id"], "fund"
            return None, ""

        # File in a firm folder (watch/firm/file)
        if parent.parent == self.watch_folder:
            firm_name = parent.name
            existing = _query_database_by_title(
                self.firm_db_id, firm_name, FIRM_TITLE_PROP
            )
            if existing:
                return existing["id"], "firm"

        return None, ""

    def _handle_cross_folder_move(self, old_path: Path, new_path: Path):
        """Handle a file moved between different folders (fund→fund, fund→firm, etc.).

        Removes the file from the source Notion page and uploads it to the destination page.
        """
        source_page_id, source_type = self._resolve_page_for_path(old_path)
        dest_page_id, dest_type = self._resolve_page_for_path(new_path)

        def _do_cross_folder_move():
            # Remove from source
            if source_page_id:
                if source_type == "fund":
                    _remove_file_from_page(source_page_id, old_path.name)
                else:
                    _remove_file_from_firm_property(source_page_id, old_path.name)
                log.info(
                    "Removed file '%s' from %s page %s (cross-folder move)",
                    old_path.name, source_type, source_page_id,
                )

            # Upload to destination
            if dest_page_id and new_path.exists():
                if dest_type == "fund":
                    section_callouts = _find_section_callout_ids(dest_page_id)
                    if section_callouts:
                        _upload_files_to_sections(
                            dest_page_id, [new_path], section_callouts
                        )
                    else:
                        _upload_and_attach_files(dest_page_id, [new_path])
                else:
                    _upload_firm_files_to_property(dest_page_id, [new_path])
                log.info(
                    "Uploaded file '%s' to %s page %s (cross-folder move)",
                    new_path.name, dest_type, dest_page_id,
                )

        log.info(
            "File moved across folders: '%s' -> '%s'",
            old_path, new_path,
        )
        threading.Thread(target=_do_cross_folder_move, daemon=True).start()


# =========================
# RELATION SYNC (Notion → Local)
# =========================

# Poll intervals (seconds)
POLL_INTERVAL_FAST = 30  # For newly created pages
POLL_INTERVAL_SLOW = 600  # For routine modification checks
FAST_WINDOW_SECONDS = 600  # Pages created within this window get fast polling


def _sanitize_for_pdf(text: str) -> str:
    """Sanitize text for PDF rendering with Helvetica (latin-1 compatible).

    Replaces common Unicode characters with ASCII equivalents and strips
    characters outside the font's supported range.
    """
    # Common substitutions
    replacements = {
        "\u2022": "-",  # bullet •
        "\u2013": "-",  # en dash –
        "\u2014": "--",  # em dash —
        "\u2018": "'",  # left single quote '
        "\u2019": "'",  # right single quote '
        "\u201c": '"',  # left double quote "
        "\u201d": '"',  # right double quote "
        "\u2026": "...",  # ellipsis …
        "\u00a0": " ",  # non-breaking space
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    # Strip any remaining non-latin-1 characters (above U+00FF)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _setup_pdf() -> "FPDF":
    """Create an FPDF instance with a Unicode-capable font if available."""
    try:
        from fpdf import FPDF
    except ImportError:
        raise ImportError(
            "fpdf2 is required for PDF export. Install it with: poetry add fpdf2"
        )

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Try to use Arial TTF for better Unicode support
    if os.name == "nt":
        fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    else:
        fonts_dir = Path("/usr/share/fonts/truetype/msttcorefonts")
    arial = fonts_dir / "arial.ttf"
    arial_bold = fonts_dir / "arialbd.ttf"
    arial_italic = fonts_dir / "ariali.ttf"
    arial_bi = fonts_dir / "arialbi.ttf"

    if arial.exists():
        try:
            pdf.add_font("Arial", "", str(arial))
            if arial_bold.exists():
                pdf.add_font("Arial", "B", str(arial_bold))
            if arial_italic.exists():
                pdf.add_font("Arial", "I", str(arial_italic))
            if arial_bi.exists():
                pdf.add_font("Arial", "BI", str(arial_bi))
            pdf._font_family = "Arial"
            log.debug("Using Arial TTF for PDF rendering")
            return pdf
        except Exception:
            log.debug("Failed to load Arial TTF, falling back to Helvetica")

    return pdf


def _pdf_font(pdf) -> str:
    """Return the font family name to use for this PDF instance."""
    # Check if Arial TTF was loaded
    if "arial" in pdf.fonts:
        return "Arial"
    return "Helvetica"


def _pdf_write(pdf, width, height, text: str):
    """Write text to PDF, sanitizing if using a non-Unicode font."""
    font_family = _pdf_font(pdf)
    if font_family == "Helvetica":
        text = _sanitize_for_pdf(text)
    pdf.multi_cell(width, height, text)


def _export_subpage_as_pdf(page_id: str, title: str, dest: Path):
    """
    Export a Notion sub-page as a PDF file.

    Reads all block content from the page and renders it directly to PDF.
    """
    pdf = _setup_pdf()
    font = _pdf_font(pdf)
    pdf.add_page()

    # Title
    pdf.set_font(font, "B", 16)
    _pdf_write(pdf, 0, 10, title)
    pdf.ln(4)

    _render_blocks_to_pdf(pdf, page_id)

    dest.parent.mkdir(parents=True, exist_ok=True)
    # Suppress verbose font subsetting logs from fontTools during PDF output
    fonttools_logger = logging.getLogger("fontTools")
    prev_level = fonttools_logger.level
    fonttools_logger.setLevel(logging.WARNING)
    try:
        pdf.output(str(dest))
    finally:
        fonttools_logger.setLevel(prev_level)
    log.info("Exported sub-page '%s' as PDF -> %s", title, dest.name)


def _render_blocks_to_pdf(pdf, block_id: str, indent: int = 0):
    """Render Notion blocks to a FPDF document."""
    font = _pdf_font(pdf)
    start_cursor = None
    numbered_idx = 0

    # Cap indent so left_margin never exceeds usable page width
    # Page width is 210mm, right margin ~10mm → max left_margin ~170mm
    MAX_INDENT = 20
    indent = min(indent, MAX_INDENT)

    while True:
        params = {"page_size": 100}
        if start_cursor:
            params["start_cursor"] = start_cursor
        data = _notion_request("get", f"/blocks/{block_id}/children", params=params)
        for block in data.get("results", []):
            btype = block.get("type", "")
            block_data = block.get(btype, {})
            rich_text = block_data.get("rich_text", [])
            text = "".join(t.get("plain_text", "") for t in rich_text)

            left_margin = 10 + indent * 8

            if btype == "heading_1":
                pdf.ln(4)
                pdf.set_font(font, "B", 14)
                pdf.set_x(left_margin)
                _pdf_write(pdf, 0, 7, text)
                pdf.ln(2)
                numbered_idx = 0
            elif btype == "heading_2":
                pdf.ln(3)
                pdf.set_font(font, "B", 12)
                pdf.set_x(left_margin)
                _pdf_write(pdf, 0, 6, text)
                pdf.ln(1)
                numbered_idx = 0
            elif btype == "heading_3":
                pdf.ln(2)
                pdf.set_font(font, "BI", 11)
                pdf.set_x(left_margin)
                _pdf_write(pdf, 0, 6, text)
                numbered_idx = 0
            elif btype in ("paragraph", "quote"):
                pdf.set_font(font, "", 10)
                pdf.set_x(left_margin)
                if text:
                    _pdf_write(pdf, 0, 5, text)
                else:
                    pdf.ln(3)
                numbered_idx = 0
            elif btype == "bulleted_list_item":
                pdf.set_font(font, "", 10)
                pdf.set_x(left_margin + 4)
                _pdf_write(pdf, 0, 5, f"-  {text}")
                numbered_idx = 0
            elif btype == "numbered_list_item":
                numbered_idx += 1
                pdf.set_font(font, "", 10)
                pdf.set_x(left_margin + 4)
                _pdf_write(pdf, 0, 5, f"{numbered_idx}.  {text}")
            elif btype == "to_do":
                checked = block_data.get("checked", False)
                mark = "[x]" if checked else "[ ]"
                pdf.set_font(font, "", 10)
                pdf.set_x(left_margin + 4)
                _pdf_write(pdf, 0, 5, f"{mark}  {text}")
                numbered_idx = 0
            elif btype == "callout":
                pdf.set_font(font, "I", 10)
                pdf.set_x(left_margin + 4)
                if text:
                    _pdf_write(pdf, 0, 5, f"| {text}")
                numbered_idx = 0
            elif btype == "divider":
                y = pdf.get_y()
                pdf.line(left_margin, y, 200, y)
                pdf.ln(3)
                numbered_idx = 0
            elif btype == "table":
                pdf.set_font(font, "", 9)
                table_data = _notion_request(
                    "get",
                    f"/blocks/{block['id']}/children",
                    params={"page_size": 100},
                )
                for row in table_data.get("results", []):
                    cells = row.get("table_row", {}).get("cells", [])
                    cell_texts = [
                        "".join(t.get("plain_text", "") for t in cell) for cell in cells
                    ]
                    pdf.set_x(left_margin)
                    _pdf_write(pdf, 0, 5, " | ".join(cell_texts))
                pdf.ln(2)
                numbered_idx = 0
            else:
                # Unknown block type with text
                if text:
                    pdf.set_font(font, "", 10)
                    pdf.set_x(left_margin)
                    _pdf_write(pdf, 0, 5, text)
                numbered_idx = 0

            # Recurse into children (except tables, handled above)
            if block.get("has_children") and btype not in ("table",):
                _render_blocks_to_pdf(pdf, block["id"], indent + 1)

        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")


def _get_relation_page_items(page_id: str, relation_property: str) -> list[dict]:
    """
    Fetch pages linked via a relation property on a fund page.

    Args:
        page_id: The fund page ID
        relation_property: Name of the relation property (e.g. "Meeting Notes", "Researches")

    Returns a list of dicts: {"name": "Title.pdf", "page_id": id, "title": title}
    """
    items = []
    try:
        page_data = _notion_request("get", f"/pages/{page_id}")
    except Exception as e:
        log.error("Failed to fetch page %s for %s: %s", page_id, relation_property, e)
        return items

    relations = (
        page_data.get("properties", {}).get(relation_property, {}).get("relation", [])
    )
    for rel in relations:
        try:
            related_page = _notion_request("get", f"/pages/{rel['id']}")
        except Exception as e:
            log.error("Failed to fetch %s page %s: %s", relation_property, rel["id"], e)
            continue

        props = related_page.get("properties", {})
        title = ""
        for prop_name, prop_val in props.items():
            if prop_val.get("type") == "title":
                title_parts = prop_val.get("title", [])
                title = "".join(t.get("plain_text", "") for t in title_parts)
                break

        if title:
            items.append(
                {
                    "name": f"{title}.pdf",
                    "page_id": rel["id"],
                    "title": title,
                }
            )
        else:
            log.warning(
                "%s page %s has no title, skipping", relation_property, rel["id"]
            )

    return items


def _get_meeting_notes_items(page_id: str) -> list[dict]:
    """Fetch pages linked via the 'Meeting Notes' relation property."""
    return _get_relation_page_items(page_id, "Meeting Notes")


def _get_research_items(page_id: str) -> list[dict]:
    """Fetch pages linked via the 'Researches' relation property."""
    return _get_relation_page_items(page_id, "Researches")


def _sync_relation_items_to_subfolder(
    items: list[dict],
    subfolder: Path,
    label: str,
):
    """
    Sync relation page items (meetings, researches) to a dedicated subfolder.

    All files in the subfolder are Notion-origin, so stale files (on disk but
    no longer linked in Notion) are deleted, and new items are exported as PDF.
    """
    subfolder.mkdir(exist_ok=True)

    # Build set of expected normalized filenames from Notion
    remote_names = {_normalize_filename(item["name"]) for item in items}

    # Delete stale files (on disk but no longer linked in Notion)
    for f in subfolder.iterdir():
        if not f.is_file():
            continue
        if _normalize_filename(f.name) not in remote_names:
            f.unlink()
            log.info(
                "Deleted stale %s file '%s' (unlinked from Notion)",
                label,
                f.name,
            )

    # Download/export new items
    existing_local = {
        _normalize_filename(f.name) for f in subfolder.iterdir() if f.is_file()
    }

    for item in items:
        norm = _normalize_filename(item["name"])
        if norm in existing_local:
            continue

        dest = subfolder / item["name"]
        try:
            _export_subpage_as_pdf(item["page_id"], item["title"], dest)
            log.info("Exported %s '%s' -> %s", label, item["title"], dest.name)
        except Exception as e:
            log.error("Failed to export %s '%s': %s", label, item["title"], e)


def _sync_files_from_notion_page(page_id: str, local_folder: Path):
    """
    Sync meeting notes and researches from a Notion fund page to local subfolders.

    The local folder is the source of truth for regular files (uploaded via watchdog).
    Only relation-linked pages (meetings, researches) are downloaded from Notion
    into dedicated subfolders.
    """
    if not local_folder.exists():
        log.warning("Local folder does not exist, skipping sync: %s", local_folder)
        return

    # Sync meeting notes to meetings/ subfolder
    meeting_items = _get_meeting_notes_items(page_id)
    _sync_relation_items_to_subfolder(
        meeting_items, local_folder / "meetings", "meeting note"
    )

    # Sync researches to researches/ subfolder
    research_items = _get_research_items(page_id)
    _sync_relation_items_to_subfolder(
        research_items, local_folder / "researches", "research"
    )


def _get_fund_page_info(page: dict) -> tuple[str, str, str]:
    """
    Extract fund name, identifier, and firm name from a fund page dict.

    Returns:
        (fund_name, identifier, firm_page_id)
    """
    props = page.get("properties", {})

    # Fund name
    title_parts = props.get(FUND_TITLE_PROP, {}).get("title", [])
    fund_name = "".join(t.get("plain_text", "") for t in title_parts)

    # Identifier
    id_parts = props.get("Identifier", {}).get("rich_text", [])
    identifier = "".join(t.get("plain_text", "") for t in id_parts)

    # AM Firm relation
    relation = props.get("AM Firm", {}).get("relation", [])
    firm_page_id = relation[0]["id"] if relation else ""

    return fund_name, identifier, firm_page_id


def _resolve_local_fund_folder(
    watch_folder: Path,
    firm_page_id: str,
    firm_db_id: str,
    fund_name: str,
) -> Path | None:
    """
    Find the local folder path for a fund page.

    Only returns a path if a matching folder already exists on disk.
    Returns None if no corresponding local folder is found (no download
    should happen for fund pages without a local folder).
    """
    # Get firm name from Notion
    if not firm_page_id:
        return None
    try:
        firm_data = _notion_request("get", f"/pages/{firm_page_id}")
    except Exception:
        return None

    firm_props = firm_data.get("properties", {})
    firm_title_parts = firm_props.get(FIRM_TITLE_PROP, {}).get("title", [])
    firm_name = "".join(t.get("plain_text", "") for t in firm_title_parts)
    if not firm_name:
        return None

    firm_folder = watch_folder / firm_name
    if not firm_folder.exists():
        return None

    # Try to find existing fund folder (with or without identifier)
    for sub in firm_folder.iterdir():
        if not sub.is_dir():
            continue
        parsed = parse_fund_folder_name(sub.name)
        if parsed and parsed[0] == fund_name:
            return sub
        if sub.name == fund_name:
            return sub

    # No matching folder found — do not create one
    return None


# =========================
# NOTION DATABASE POLLER
# =========================


class NotionPoller:
    """
    Polls fund pages that have local folders and syncs relation-linked
    content (meeting notes, researches) to dedicated subfolders.

    Regular files are NOT downloaded — the local folder is the source
    of truth and uploads are handled by the watchdog.

    Uses two polling speeds:
      - Fast (30s) for pages created within the last 10 minutes
      - Slow (5min) for routine checks on all other pages
    """

    def __init__(
        self,
        watch_folder: Path,
        firm_db_id: str,
        fund_db_id: str,
    ):
        self.watch_folder = watch_folder
        self.firm_db_id = firm_db_id
        self.fund_db_id = fund_db_id
        # page_id -> creation time (ISO string) for fast-poll window
        self._creation_times: dict[str, str] = {}
        # Resolved mappings: page_id -> local_folder (cached, only for pages with folders)
        self._page_folders: dict[str, Path] = {}
        self._stop_event = threading.Event()

    def _query_all_fund_pages(self) -> list[dict]:
        """Query all pages in the fund database."""
        pages = []
        start_cursor = None
        while True:
            body: dict = {"page_size": 100}
            if start_cursor:
                body["start_cursor"] = start_cursor
            data = _notion_request(
                "post", f"/data_sources/{self.fund_db_id}/query", json=body
            )
            pages.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            start_cursor = data.get("next_cursor")
        return pages

    def _is_recently_created(self, created_time: str) -> bool:
        """Check if a page was created within the fast-poll window."""
        from datetime import datetime, timezone

        try:
            created = datetime.fromisoformat(created_time.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            return (now - created).total_seconds() < FAST_WINDOW_SECONDS
        except Exception:
            return False

    def _resolve_pages_with_folders(self):
        """
        Build/refresh the mapping from fund page IDs to local folders.

        Only pages that have a corresponding local folder are tracked.
        """
        try:
            pages = self._query_all_fund_pages()
        except Exception as e:
            log.error("Failed to query fund pages: %s", e)
            return

        self._page_folders.clear()
        for page in pages:
            page_id = page["id"]
            created_time = page.get("created_time", "")
            if page_id not in self._creation_times:
                self._creation_times[page_id] = created_time

            fund_name, _, firm_page_id = _get_fund_page_info(page)
            if not fund_name:
                continue

            local_folder = _resolve_local_fund_folder(
                self.watch_folder,
                firm_page_id,
                self.firm_db_id,
                fund_name,
            )
            if local_folder and local_folder.exists():
                self._page_folders[page_id] = local_folder

            # Small delay between pages to avoid API call bursts
            time.sleep(0.3)

        log.info(
            "Poller: tracking %d fund page(s) with local folders",
            len(self._page_folders),
        )

    def _poll_once(self):
        """
        Run one poll cycle: for each tracked page, sync meeting notes
        and researches from Notion relation properties to local subfolders.
        """
        for page_id, local_folder in self._page_folders.items():
            if self._stop_event.is_set():
                break
            if not local_folder.exists():
                continue
            try:
                _sync_files_from_notion_page(page_id, local_folder)
            except Exception as e:
                log.error(
                    "Error syncing page %s to %s: %s",
                    page_id,
                    local_folder,
                    e,
                )
            # Pace API calls between pages
            time.sleep(0.5)

    def _get_sleep_interval(self) -> int:
        """
        Return the appropriate poll interval.

        Uses fast interval if any tracked page was recently created.
        """
        for created_time in self._creation_times.values():
            if self._is_recently_created(created_time):
                return POLL_INTERVAL_FAST
        return POLL_INTERVAL_SLOW

    def run(self):
        """Run the poller loop (blocking). Call stop() from another thread."""
        log.info(
            "Notion poller started (fast=%ds, slow=%ds)",
            POLL_INTERVAL_FAST,
            POLL_INTERVAL_SLOW,
        )

        # Build initial page-to-folder mapping and poll immediately
        self._resolve_pages_with_folders()
        self._poll_once()

        while not self._stop_event.is_set():
            interval = self._get_sleep_interval()
            if self._stop_event.wait(timeout=interval):
                break
            # Periodically refresh the folder mapping (picks up new local folders)
            self._resolve_pages_with_folders()
            self._poll_once()

        log.info("Notion poller stopped.")

    def stop(self):
        """Signal the poller to stop."""
        self._stop_event.set()


# =========================
# ENTRY POINTS
# =========================


def initial_scan(watch_folder: Path, firm_db_id: str, fund_db_id: str):
    """Scan existing folders and sync any that aren't yet in Notion."""
    if not watch_folder.exists():
        log.warning("Watch folder does not exist: %s", watch_folder)
        return

    for firm_folder in watch_folder.iterdir():
        if firm_folder.is_dir():
            sync_firm(firm_folder, firm_db_id, fund_db_id)


def watch_folder(folder_path: Path | str | None = None):
    """
    Start monitoring a folder for new firm/fund directories.

    Args:
        folder_path: Path to monitor. Defaults to DEFAULT_WATCH_FOLDER.
    """
    watch_path = Path(folder_path) if folder_path else DEFAULT_WATCH_FOLDER
    log.info("Watch folder: %s", watch_path)

    if not watch_path.exists():
        watch_path.mkdir(parents=True, exist_ok=True)
        log.info("Created watch folder: %s", watch_path)

    # Auto-discover databases
    log.info("Discovering Notion databases...")
    firm_db_id, fund_db_id = discover_databases()

    # Initial scan of existing folders
    log.info("Running initial scan...")
    initial_scan(watch_path, firm_db_id, fund_db_id)

    # Set up watchdog (local → Notion)
    handler = NotionFolderHandler(watch_path, firm_db_id, fund_db_id)
    observer = Observer()
    observer.schedule(handler, str(watch_path), recursive=True)
    observer.start()

    # Set up Notion poller (Notion → local)
    poller = NotionPoller(watch_path, firm_db_id, fund_db_id)
    poller_thread = threading.Thread(target=poller.run, daemon=True)
    poller_thread.start()

    log.info("Watching for changes... (Ctrl+C to stop)")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Stopping watcher...")
        poller.stop()
        observer.stop()
    observer.join()
    log.info("Watcher stopped.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Sync local firm/fund folders to Notion databases."
    )
    parser.add_argument(
        "--folder",
        type=str,
        default=None,
        help="Path to the folder to monitor (default: built-in path)",
    )
    args = parser.parse_args()

    watch_folder(args.folder)
