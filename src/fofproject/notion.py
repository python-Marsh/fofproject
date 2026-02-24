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
    │   │   └── report.xlsx
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

# Load .env from the same directory as this script
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

# =========================
# CONFIGURATION
# =========================
NOTION_SECRET = os.getenv("NOTION_SECRET")
NOTION_VERSION = "2025-09-03"
BASE_URL = "https://api.notion.com/v1"

# Default watch folder — change this to monitor a different folder
DEFAULT_WATCH_FOLDER = Path(
    r"C:\Users\FOF Analyst\Desktop\fofproject\output\testing\notion\firm"
)

# Fund subfolder pattern: "{FUND_NAME} - {IDENTIFIER}"
FUND_FOLDER_PATTERN = re.compile(r"^(.+?)\s*-\s*(\d+)$")

# File size limit for single-part upload (20 MB)
MAX_SINGLE_UPLOAD_BYTES = 20 * 1024 * 1024

# Fund page Data Packs section names (H2 headings in the template)
SECTION_PRESENTATION = "Presentation"
SECTION_MONTHLY_LETTERS = "Monthly Letters"
SECTION_OTHERS = "Others"

# Keywords used to classify files into sections (case-insensitive)
PRESENTATION_KEYWORDS = [
    "deck", "presentation", "overview", "pitch", "ppt",
]
MONTHLY_LETTERS_KEYWORDS = [
    "factsheet", "tearsheet", "tear sheet", "fact sheet",
    "investor letter", "monthly letter", "newsletter",
    "monthly report", "monthly update",
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
    """Make a Notion API request with retry logic."""
    url = f"{BASE_URL}{endpoint}" if endpoint.startswith("/") else endpoint
    kwargs.setdefault("headers", _headers())

    for attempt in range(3):
        resp = getattr(requests, method)(url, **kwargs)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 2))
            log.warning("Rate limited, retrying in %ds...", retry_after)
            time.sleep(retry_after)
            continue
        if resp.status_code >= 400:
            log.error(
                "Notion API error %d: %s", resp.status_code, resp.text
            )
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
    log.info("Found %d databases: %s", len(databases),
             [d["title"] for d in databases])

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
    db_id: str, title: str, title_property: str = "Name",
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
        title_property: {
            "title": [{"type": "text", "text": {"content": title}}]
        }
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


# =========================
# FILE UPLOAD
# =========================

def _get_block_type(file_path: Path) -> str:
    """Determine the Notion block type for a file based on its extension."""
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"):
        return "image"
    if suffix in (".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a"):
        return "audio"
    if suffix in (".mp4", ".mov", ".avi", ".mkv", ".webm"):
        return "video"
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
        create_resp = _notion_request(
            "post", "/file_uploads", json=create_body
        )
        upload_id = create_resp["id"]
    except requests.HTTPError as e:
        log.warning(
            "Skipping '%s' — unsupported by Notion File Upload API: %s",
            file_path.name, e,
        )
        return None

    # Step 2: Send file content
    send_url = f"{BASE_URL}/file_uploads/{upload_id}/send"
    with open(file_path, "rb") as f:
        send_resp = requests.post(
            send_url,
            headers={
                "Authorization": f"Bearer {NOTION_SECRET}",
                "Notion-Version": NOTION_VERSION,
            },
            files={"file": (file_path.name, f, _get_mime_type(file_path))},
        )
    if send_resp.status_code >= 400:
        log.error(
            "Failed to send file '%s': %d %s",
            file_path.name, send_resp.status_code, send_resp.text,
        )
        return None

    log.info("Uploaded file '%s' -> %s", file_path.name, upload_id)
    return upload_id


def _extract_filename_from_url(url: str) -> str | None:
    """Extract the filename from a Notion S3 URL (keeps underscores as-is)."""
    from urllib.parse import urlparse, unquote
    try:
        path = urlparse(url).path
        encoded_name = path.rsplit("/", 1)[-1]
        return unquote(encoded_name)
    except Exception:
        return None


def _normalize_filename(name: str) -> str:
    """Normalize a filename for comparison (spaces <-> underscores)."""
    return name.replace(" ", "_").lower()


def _get_existing_filenames_normalized(page_id: str) -> set[str]:
    """Get normalized filenames of files already attached to a page."""
    filenames = set()
    start_cursor = None
    file_block_types = {"file", "pdf", "image", "audio", "video"}

    while True:
        params = {"page_size": 100}
        if start_cursor:
            params["start_cursor"] = start_cursor
        data = _notion_request(
            "get", f"/blocks/{page_id}/children", params=params
        )
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
        children.append({
            "type": block_type,
            block_type: {
                "type": "file_upload",
                "file_upload": {"id": upload_id},
            },
        })

    # Notion allows max 100 blocks per request
    for i in range(0, len(children), 100):
        batch = children[i:i + 100]
        _notion_request(
            "patch",
            f"/blocks/{page_id}/children",
            json={"children": batch},
        )
        log.info(
            "Attached %d file(s) to page %s", len(batch), page_id
        )


# =========================
# FUND PAGE SECTION HANDLING
# =========================

def _classify_file(file_path: Path) -> str:
    """
    Classify a file into a Data Packs section based on its filename.

    Returns one of: SECTION_PRESENTATION, SECTION_MONTHLY_LETTERS, SECTION_OTHERS
    """
    name_lower = file_path.name.lower()
    for kw in PRESENTATION_KEYWORDS:
        if kw in name_lower:
            return SECTION_PRESENTATION
    for kw in MONTHLY_LETTERS_KEYWORDS:
        if kw in name_lower:
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
        data = _notion_request(
            "get", f"/blocks/{page_id}/children", params=params
        )
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


def _get_existing_filenames_in_block(block_id: str) -> set[str]:
    """Get normalized filenames of files that are children of a specific block."""
    filenames = set()
    file_block_types = {"file", "pdf", "image", "audio", "video"}
    start_cursor = None

    while True:
        params = {"page_size": 100}
        if start_cursor:
            params["start_cursor"] = start_cursor
        data = _notion_request(
            "get", f"/blocks/{block_id}/children", params=params
        )
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
            f for f in section_files
            if _normalize_filename(f.name) not in existing
        ]
        if len(new_files) < len(section_files):
            log.info(
                "Section '%s': skipping %d already-uploaded file(s)",
                section_name, len(section_files) - len(new_files),
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
                children.append({
                    "type": block_type,
                    block_type: {
                        "type": "file_upload",
                        "file_upload": {"id": upload_id},
                    },
                })

            for i in range(0, len(children), 100):
                batch = children[i:i + 100]
                _notion_request(
                    "patch",
                    f"/blocks/{callout_id}/children",
                    json={"children": batch},
                )
            log.info(
                "Attached %d file(s) to '%s' section",
                len(file_infos), section_name,
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
    existing_files = page_data.get("properties", {}).get("Firm Documents", {}).get("files", [])
    existing_names = {
        _normalize_filename(f.get("name", "")) for f in existing_files
    }

    new_files = [
        f for f in files
        if _normalize_filename(f.name) not in existing_names
    ]
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
            upload_entries.append({
                "type": "file_upload",
                "file_upload": {"id": uid},
                "name": f.name,
            })

    # Update the property
    _update_page_properties(page_id, {
        "Firm Documents": {
            "files": upload_entries,
        }
    })
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
    match = FUND_FOLDER_PATTERN.match(folder_name)
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip()


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
            if parse_fund_folder_name(item.name):
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
        log.info(
            "Skipping %d already-uploaded file(s)", len(files) - len(new_files)
        )
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
        log.info("Fund '%s' already exists -> %s", fund_name, fund_page_id)
    else:
        # Create fund page with Identifier and AM Firm relation
        extra_properties = {
            "Identifier": {
                "rich_text": [
                    {"type": "text", "text": {"content": identifier}}
                ]
            },
            "AM Firm": {
                "relation": [{"id": firm_page_id}]
            },
        }
        fund_page_id = _create_page(
            fund_db_id, fund_name,
            title_property=FUND_TITLE_PROP,
            extra_properties=extra_properties,
            use_template=True,
        )

    # Wait for template if we just created the page
    if not existing:
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
            firm_db_id, firm_name,
            title_property=FIRM_TITLE_PROP,
            use_template=True,
        )

    # Parse folder contents
    firm_files, fund_folders = get_firm_contents(firm_path)

    # Upload firm-level files to "Firm Documents" property
    if firm_files:
        log.info("Uploading %d firm-level file(s)", len(firm_files))
        _upload_firm_files_to_property(firm_page_id, firm_files)

    # Sync each fund subfolder
    fund_page_ids = []
    for fund_folder in fund_folders:
        fund_page_id = sync_fund(fund_folder, fund_db_id, firm_page_id)
        if fund_page_id:
            fund_page_ids.append(fund_page_id)

    # Update firm's "Managing Funds" relation to include all fund pages
    if fund_page_ids:
        _update_page_properties(firm_page_id, {
            "Managing Funds": {
                "relation": [{"id": pid} for pid in fund_page_ids]
            }
        })
        log.info(
            "Linked firm '%s' to %d fund(s)", firm_name, len(fund_page_ids)
        )

    log.info("=== Done syncing firm: %s ===", firm_name)


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
                        new_path.name, firm_name,
                    )
                    threading.Thread(
                        target=sync_firm,
                        args=(parent, self.firm_db_id, self.fund_db_id),
                        daemon=True,
                    ).start()

    def _handle_new_file(self, file_path: Path):
        """Handle a new file added to an existing firm or fund folder."""
        parent = file_path.parent
        grandparent = parent.parent

        # File in a fund folder
        if grandparent.parent == self.watch_folder:
            parsed = parse_fund_folder_name(parent.name)
            if parsed:
                fund_name, _ = parsed
                existing_fund = _query_database_by_title(
                    self.fund_db_id, fund_name, FUND_TITLE_PROP
                )
                if existing_fund:
                    log.info(
                        "New file in fund '%s': %s",
                        fund_name, file_path.name,
                    )
                    threading.Thread(
                        target=_upload_and_attach_files,
                        args=(existing_fund["id"], [file_path]),
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
                    firm_name, file_path.name,
                )
                threading.Thread(
                    target=_upload_and_attach_files,
                    args=(existing_firm["id"], [file_path]),
                    daemon=True,
                ).start()


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

    # Set up watchdog
    handler = NotionFolderHandler(watch_path, firm_db_id, fund_db_id)
    observer = Observer()
    observer.schedule(handler, str(watch_path), recursive=True)
    observer.start()
    log.info("Watching for changes... (Ctrl+C to stop)")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Stopping watcher...")
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
