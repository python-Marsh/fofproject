from dotenv import load_dotenv
from pathlib import Path
# Load .env from the same directory as this script
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)
import re
import os
import json
import time
import threading
from datetime import datetime, timezone
from typing import Optional
import requests
import msal

from fofproject.log import log, EMAIL
from fofproject.paths import EMAIL_STORAGE_DIR
from fofproject.utils import GracefulCycle

# =========================
# CONFIG (fill in from .env)
# =========================
TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
GRAPH_EMAIL = os.getenv("GRAPH_EMAIL")
GRAPH_PASSWORD = os.getenv("GRAPH_PASSWORD")

# If you ONLY need read + categorize, Mail.ReadWrite is enough.
SCOPES = ["Mail.ReadWrite"]

GRAPH = "https://graph.microsoft.com/v1.0"


def automate_device_login(user_code: str, email: str, password: str):
    """
    Use Selenium to automatically enter the device code and login credentials.
    """
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.options import Options
    from selenium.common.exceptions import StaleElementReferenceException

    log.detail("Selenium: Starting browser...", phase=EMAIL)

    options = Options()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(options=options)
    wait = WebDriverWait(driver, 30)

    try:
        # Navigate to the device login page
        log.detail("Selenium: Navigating to device login page...", phase=EMAIL)
        driver.get("https://microsoft.com/devicelogin")

        # Wait for and enter the device code
        log.detail("Selenium: Entering device code...", phase=EMAIL)
        code_input = wait.until(EC.presence_of_element_located((By.ID, "otc")))
        code_input.clear()
        code_input.send_keys(user_code)

        # Click Next button
        log.detail("Selenium: Clicking Next after code entry...", phase=EMAIL)
        next_btn = wait.until(EC.element_to_be_clickable((By.ID, "idSIButton9")))
        next_btn.click()

        # Wait for email input field and enter email
        log.detail("Selenium: Waiting for email field...", phase=EMAIL)
        email_input = wait.until(EC.presence_of_element_located((By.NAME, "loginfmt")))
        log.detail(f"Selenium: Entering email: {email}", phase=EMAIL)
        email_input.clear()
        email_input.send_keys(email)

        # Click Next
        log.detail("Selenium: Clicking Next after email...", phase=EMAIL)
        time.sleep(1)
        next_btn = wait.until(EC.element_to_be_clickable((By.ID, "idSIButton9")))
        next_btn.click()

        # Wait for password field and enter password - robust approach
        log.detail("Selenium: Waiting for password field...", phase=EMAIL)
        time.sleep(2)
        for _retry in range(3):
            try:
                password_input = wait.until(EC.element_to_be_clickable((By.NAME, "passwd")))
                password_input.is_displayed()  # verify element is not stale
                break
            except StaleElementReferenceException:
                time.sleep(1)
        else:
            password_input = wait.until(EC.element_to_be_clickable((By.NAME, "passwd")))
        time.sleep(0.5)

        # Click to ensure focus
        password_input.click()
        time.sleep(0.3)

        # Clear using JavaScript for reliability
        driver.execute_script("arguments[0].value = '';", password_input)
        time.sleep(0.2)

        # Enter password using JavaScript to avoid input issues
        log.detail("Selenium: Entering password...", phase=EMAIL)
        driver.execute_script("arguments[0].value = arguments[1];", password_input, password)

        # Trigger input event so the page recognizes the change
        driver.execute_script("""
            var event = new Event('input', { bubbles: true });
            arguments[0].dispatchEvent(event);
        """, password_input)
        time.sleep(0.5)

        # Click Sign in
        log.detail("Selenium: Clicking Sign In...", phase=EMAIL)
        time.sleep(1)
        sign_in_btn = wait.until(EC.element_to_be_clickable((By.ID, "idSIButton9")))
        sign_in_btn.click()

        # Handle "Stay signed in?" prompt if it appears
        log.detail("Selenium: Checking for 'Stay signed in' prompt...", phase=EMAIL)
        try:
            time.sleep(2)
            stay_signed_in_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.ID, "idSIButton9"))
            )
            log.detail("Selenium: Clicking 'Yes' on Stay signed in...", phase=EMAIL)
            stay_signed_in_btn.click()
        except:
            log.detail("Selenium: No 'Stay signed in' prompt found.", phase=EMAIL)

        # Handle "Are you trying to sign in to..." confirmation if it appears
        log.detail("Selenium: Checking for confirmation prompt...", phase=EMAIL)
        try:
            time.sleep(3)
            # Look for the continue button on the "Are you trying to sign in" page
            continue_btn = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.ID, "idSIButton9"))
            )
            log.detail("Selenium: Clicking Continue...", phase=EMAIL)
            continue_btn.click()
            time.sleep(2)
        except:
            log.detail("Selenium: No confirmation prompt found.", phase=EMAIL)

        # Wait for the success page - keep checking until we see success or timeout
        log.detail("Selenium: Waiting for success confirmation...", phase=EMAIL)
        max_wait = 30
        start = time.time()
        while time.time() - start < max_wait:
            page_source = driver.page_source.lower()

            # Check for success indicators
            if "you have signed in" in page_source or "you're all set" in page_source:
                log.info("Selenium: Successfully authenticated!", phase=EMAIL)
                break

            # If we're still on the device auth page with a button, click it
            try:
                btn = driver.find_element(By.ID, "idSIButton9")
                if btn.is_displayed() and btn.is_enabled():
                    log.detail("Selenium: Found additional button, clicking...", phase=EMAIL)
                    btn.click()
                    time.sleep(2)
            except:
                pass

            time.sleep(1)
        else:
            log.warn(f"Selenium: Final URL: {driver.current_url}", phase=EMAIL)
            log.warn("Selenium: Authentication flow completed (timeout waiting for success page).", phase=EMAIL)

    except Exception as e:
        log.error(f"Selenium automation error: {e}", phase=EMAIL)
        try:
            log.error(f"Selenium: Current URL at error: {driver.current_url}", phase=EMAIL)
            log.error(f"Selenium: Page title: {driver.title}", phase=EMAIL)
        except:
            pass
        raise
    finally:
        time.sleep(2)
        driver.quit()
        log.detail("Selenium: Browser closed.", phase=EMAIL)


def get_msal_app():
    """Get or create MSAL PublicClientApplication."""
    authority = f"https://login.microsoftonline.com/{TENANT_ID}"
    return msal.PublicClientApplication(
        client_id=CLIENT_ID,
        authority=authority,
    )


def get_access_token_interactive(app=None):
    """
    Delegated auth using device flow with Selenium automation.
    """
    log.info("Starting authentication...", phase=EMAIL)

    if app is None:
        app = get_msal_app()

    # Try cached token first
    accounts = app.get_accounts()
    if accounts:
        log.detail("Found cached accounts, attempting silent token acquisition...", phase=EMAIL)
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            log.info("Using cached token.", phase=EMAIL)
            return result["access_token"]

    # Interactive device flow with Selenium automation
    log.info("Initiating device flow...", phase=EMAIL)
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"Failed to create device flow. Error: {flow}")

    user_code = flow["user_code"]
    log.info(f"Device code: {user_code}", phase=EMAIL)
    log.detail("Starting Selenium to automate login...", phase=EMAIL)

    # Run Selenium in a separate thread so we can wait for the token
    selenium_thread = threading.Thread(
        target=automate_device_login,
        args=(user_code, GRAPH_EMAIL, GRAPH_PASSWORD)
    )
    selenium_thread.start()

    # Wait for the device flow to complete
    log.detail("Waiting for device flow to complete...", phase=EMAIL)
    result = app.acquire_token_by_device_flow(flow)
    selenium_thread.join()

    if "access_token" not in result:
        raise RuntimeError(f"Token error: {result}")

    log.info("Token acquired successfully!", phase=EMAIL)
    return result["access_token"]


def create_token_provider():
    """
    Create a token provider function that handles token refresh.
    Returns a callable that always returns a valid token.
    """
    app = get_msal_app()
    # Initial authentication
    get_access_token_interactive(app)

    def get_token():
        """Get a valid token, refreshing if necessary."""
        accounts = app.get_accounts()
        if accounts:
            result = app.acquire_token_silent(SCOPES, account=accounts[0])
            if result and "access_token" in result:
                return result["access_token"]
        # If silent fails, need interactive auth again
        return get_access_token_interactive(app)

    return get_token


# =============================================================================
# EMAIL STORAGE SYSTEM
# =============================================================================
# Folder Structure:
#   "Z:\Research Team\Artificial Intelligence\emails"
#   ├── index.json                          # Master index of all downloaded emails
#   ├── 2026-02-02_Subject-Here_abc12345/   # One folder per email
#   │   ├── metadata.json                   # Full email metadata
#   │   └── attachments/                    # Attachments subfolder
#   │       ├── document.pdf
#   │       └── image.png
#   └── ...
# =============================================================================


def sanitize_folder_name(name: str, max_length: int = 50) -> str:
    """Sanitize a string to be used as a folder name."""
    # Remove or replace invalid characters
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name)
    sanitized = re.sub(r'\s+', '-', sanitized)  # Replace whitespace with dash
    sanitized = re.sub(r'-+', '-', sanitized)   # Collapse multiple dashes
    sanitized = sanitized.strip('-.')           # Remove leading/trailing dashes and dots
    # Truncate if too long
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rstrip('-')
    return sanitized or "no-subject"


def get_email_folder_name(msg: dict) -> str:
    """Generate a unique folder name for an email."""
    # Parse date
    received = msg.get("receivedDateTime", "")
    try:
        dt = datetime.fromisoformat(received.replace("Z", "+00:00"))
        date_str = dt.strftime("%Y-%m-%d_%H%M%S")
    except:
        date_str = "unknown-date"

    # Sanitize subject
    subject = msg.get("subject", "no-subject") or "no-subject"
    subject_clean = sanitize_folder_name(subject)

    # Use first 8 chars of message ID for uniqueness
    msg_id = msg.get("id", "")[:8]

    return f"{date_str}_{subject_clean}_{msg_id}"


def get_message_full(token: str, message_id: str) -> dict:
    """Fetch full message details including body."""
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "$select": "id,subject,from,toRecipients,ccRecipients,bccRecipients,"
                   "receivedDateTime,sentDateTime,bodyPreview,body,importance,"
                   "hasAttachments,internetMessageId,conversationId,categories,"
                   "isRead,isDraft,flag"
    }
    r = requests.get(f"{GRAPH}/me/messages/{message_id}", headers=headers, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def get_attachments(token: str, message_id: str) -> list:
    """Get list of attachments for a message."""
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{GRAPH}/me/messages/{message_id}/attachments", headers=headers, timeout=30)
    r.raise_for_status()
    return r.json().get("value", [])


def download_attachment(attachment: dict, save_path: Path) -> Optional[Path]:
    """Download a single attachment and save it to disk."""
    att_name = attachment.get("name", "unnamed")
    att_type = attachment.get("@odata.type", "")

    # Handle different attachment types
    if "#microsoft.graph.fileAttachment" in att_type:
        # File attachment - content is base64 encoded in the response
        content_bytes = attachment.get("contentBytes")
        if content_bytes:
            import base64
            file_path = save_path / sanitize_folder_name(att_name, max_length=100)
            # Preserve extension
            original_ext = Path(att_name).suffix
            if original_ext and not str(file_path).endswith(original_ext):
                file_path = Path(str(file_path) + original_ext)
            file_path.write_bytes(base64.b64decode(content_bytes))
            return file_path
    elif "#microsoft.graph.itemAttachment" in att_type:
        # Item attachment (embedded email) - save as JSON
        file_path = save_path / f"{sanitize_folder_name(att_name)}.json"
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(attachment, f, indent=2, ensure_ascii=False)
        return file_path

    return None


def save_email(token: str, msg: dict, base_dir: Path) -> Path:
    """
    Save an email with its metadata and attachments to a folder.
    Returns the path to the email folder.
    """
    # Get full message details
    full_msg = get_message_full(token, msg["id"])

    # Create folder for this email
    folder_name = get_email_folder_name(full_msg)
    email_folder = base_dir / folder_name
    email_folder.mkdir(parents=True, exist_ok=True)

    # Prepare metadata
    metadata = {
        "id": full_msg.get("id"),
        "internetMessageId": full_msg.get("internetMessageId"),
        "conversationId": full_msg.get("conversationId"),
        "subject": full_msg.get("subject"),
        "from": full_msg.get("from"),
        "toRecipients": full_msg.get("toRecipients", []),
        "ccRecipients": full_msg.get("ccRecipients", []),
        "bccRecipients": full_msg.get("bccRecipients", []),
        "receivedDateTime": full_msg.get("receivedDateTime"),
        "sentDateTime": full_msg.get("sentDateTime"),
        "importance": full_msg.get("importance"),
        "categories": full_msg.get("categories", []),
        "isRead": full_msg.get("isRead"),
        "isDraft": full_msg.get("isDraft"),
        "flag": full_msg.get("flag"),
        "hasAttachments": full_msg.get("hasAttachments"),
        "bodyPreview": full_msg.get("bodyPreview"),
        "body": full_msg.get("body"),  # Contains contentType and content (HTML/text)
        "attachments": [],  # Will be populated below
        "_downloadedAt": datetime.now().isoformat(),
        "_folderName": folder_name,
    }

    # Handle attachments
    if full_msg.get("hasAttachments"):
        attachments_dir = email_folder / "attachments"
        attachments_dir.mkdir(exist_ok=True)

        attachments = get_attachments(token, msg["id"])
        for att in attachments:
            att_info = {
                "id": att.get("id"),
                "name": att.get("name"),
                "contentType": att.get("contentType"),
                "size": att.get("size"),
                "isInline": att.get("isInline", False),
                "type": att.get("@odata.type"),
            }

            # Download the attachment
            saved_path = download_attachment(att, attachments_dir)
            if saved_path:
                att_info["localPath"] = str(saved_path.relative_to(email_folder))

            metadata["attachments"].append(att_info)

    # Save metadata
    metadata_path = email_folder / "metadata.json"
    with open(metadata_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    return email_folder


def update_index(base_dir: Path, email_folder: Path, metadata: dict):
    """Update the master index file with a new email entry."""
    index_path = base_dir / "index.json"

    # Load existing index or create new
    if index_path.exists():
        with open(index_path, 'r', encoding='utf-8') as f:
            index = json.load(f)
    else:
        index = {
            "created": datetime.now().isoformat(),
            "lastUpdated": None,
            "totalEmails": 0,
            "emails": {}
        }

    # Add/update email entry - INSERT AT BEGINNING to keep newest at top
    msg_id = metadata.get("id")
    new_entry = {
        "folder": email_folder.name,
        "subject": metadata.get("subject"),
        "from": metadata.get("from", {}).get("emailAddress", {}).get("address"),
        "receivedDateTime": metadata.get("receivedDateTime"),
        "hasAttachments": metadata.get("hasAttachments"),
        "attachmentCount": len(metadata.get("attachments", [])),
    }

    # Prepend new entry so newest emails stay at top
    index["emails"] = {msg_id: new_entry, **index["emails"]}
    index["totalEmails"] = len(index["emails"])
    index["lastUpdated"] = datetime.now().isoformat()

    # Save index
    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(index, f, indent=2, ensure_ascii=False)


def get_downloaded_ids(base_dir: Path) -> set:
    """Get set of already downloaded message IDs from index."""
    index_path = base_dir / "index.json"
    if not index_path.exists():
        return set()

    try:
        with open(index_path, 'r', encoding='utf-8') as f:
            index = json.load(f)
        return set(index.get("emails", {}).keys())
    except (json.JSONDecodeError, KeyError):
        log.warn(f"Corrupted index.json at {index_path}, treating as empty.", phase=EMAIL)
        return set()


def _sender(msg: dict) -> str:
    """Extract sender address from a Graph API message dict."""
    return msg.get("from", {}).get("emailAddress", {}).get("address", "unknown")


# =============================================================================
# MAIN DOWNLOAD FUNCTIONS
# =============================================================================


def download_all_emails(token: str, base_dir: Path = None, skip_existing: bool = True) -> int:
    """
    Download ALL emails from inbox with pagination.

    Args:
        token: Access token for Graph API
        base_dir: Directory to save emails (default: EMAIL_STORAGE_DIR)
        skip_existing: If True, skip emails already downloaded

    Returns:
        Number of emails downloaded
    """
    base_dir = base_dir or EMAIL_STORAGE_DIR
    base_dir.mkdir(parents=True, exist_ok=True)

    downloaded_ids = get_downloaded_ids(base_dir) if skip_existing else set()
    downloaded_count = 0

    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "$top": 50,  # Batch size
        "$select": "id,subject,from,receivedDateTime,hasAttachments",
        "$orderby": "receivedDateTime desc",
    }

    url = f"{GRAPH}/me/mailFolders/Inbox/messages"

    log.info(f"Starting download of all emails to: {base_dir}", phase=EMAIL)

    while url:
        r = requests.get(url, headers=headers, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()

        messages = data.get("value", [])
        for msg in messages:
            msg_id = msg.get("id")

            if skip_existing and msg_id in downloaded_ids:
                log.detail(f"Skipping (already exists): {msg.get('subject', '')[:50]}", phase=EMAIL)
                continue

            try:
                email_folder = save_email(token, msg, base_dir)

                # Load metadata for index update
                with open(email_folder / "metadata.json", 'r', encoding='utf-8') as f:
                    metadata = json.load(f)
                update_index(base_dir, email_folder, metadata)

                downloaded_count += 1
                att_count = len(metadata.get("attachments", []))
                log.info(f"Downloaded ({downloaded_count}): {msg.get('subject', '')[:50]} "
                         f"| from: {_sender(msg)} [{att_count} attachments]", phase=EMAIL)

            except Exception as e:
                log.error(f"ERROR downloading {msg.get('subject', '')[:30]}: {e}", phase=EMAIL)

        # Get next page URL
        url = data.get("@odata.nextLink")
        params = {}  # Clear params for next link (they're included in the URL)

    log.info(f"Download complete. Total emails downloaded: {downloaded_count}", phase=EMAIL)
    return downloaded_count


def download_top_emails(token: str, count: int = 100, base_dir: Path = None,
                        skip_existing: bool = True) -> int:
    """
    Download the top N most recent emails.

    Args:
        token: Access token for Graph API
        count: Number of emails to download (default: 100)
        base_dir: Directory to save emails (default: EMAIL_STORAGE_DIR)
        skip_existing: If True, skip emails already downloaded

    Returns:
        Number of emails downloaded
    """
    base_dir = base_dir or EMAIL_STORAGE_DIR
    base_dir.mkdir(parents=True, exist_ok=True)

    downloaded_ids = get_downloaded_ids(base_dir) if skip_existing else set()
    downloaded_count = 0

    headers = {"Authorization": f"Bearer {token}"}

    log.info(f"Starting download of top {count} emails to: {base_dir}", phase=EMAIL)

    # Fetch in batches
    remaining = count
    skip = 0

    while remaining > 0:
        batch_size = min(50, remaining)
        params = {
            "$top": batch_size,
            "$skip": skip,
            "$select": "id,subject,from,receivedDateTime,hasAttachments",
            "$orderby": "receivedDateTime desc",
        }

        r = requests.get(f"{GRAPH}/me/mailFolders/Inbox/messages",
                        headers=headers, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()

        messages = data.get("value", [])
        if not messages:
            break

        for msg in messages:
            if remaining <= 0:
                break

            msg_id = msg.get("id")

            if skip_existing and msg_id in downloaded_ids:
                log.detail(f"Skipping (already exists): {msg.get('subject', '')[:50]}", phase=EMAIL)
                remaining -= 1
                continue

            try:
                email_folder = save_email(token, msg, base_dir)

                # Load metadata for index update
                with open(email_folder / "metadata.json", 'r', encoding='utf-8') as f:
                    metadata = json.load(f)
                update_index(base_dir, email_folder, metadata)

                downloaded_count += 1
                remaining -= 1
                att_count = len(metadata.get("attachments", []))
                log.info(f"Downloaded ({downloaded_count}/{count}): {msg.get('subject', '')[:50]} "
                         f"| from: {_sender(msg)} [{att_count} attachments]", phase=EMAIL)

            except Exception as e:
                log.error(f"ERROR downloading {msg.get('subject', '')[:30]}: {e}", phase=EMAIL)
                remaining -= 1

        skip += batch_size

    log.info(f"Download complete. Total emails downloaded: {downloaded_count}", phase=EMAIL)
    return downloaded_count


def monitor_emails(token_func, base_dir: Path = None, poll_interval: int = 600,
                   max_runtime: int = None) -> None:
    """
    Continuously monitor for new emails and download them.

    Uses a receivedDateTime filter so that email bursts larger than a single
    page are never missed, and keeps downloaded IDs in memory to avoid
    re-reading the full index.json every cycle.

    Args:
        token_func: Callable that returns a valid access token (handles refresh)
        base_dir: Directory to save emails (default: EMAIL_STORAGE_DIR)
        poll_interval: Seconds between checks (default: 600)
        max_runtime: Maximum seconds to run (None = run forever)
    """
    base_dir = base_dir or EMAIL_STORAGE_DIR
    base_dir.mkdir(parents=True, exist_ok=True)

    start_time = time.time()
    total_downloaded = 0

    # Load downloaded IDs once, then maintain in memory
    downloaded_ids = get_downloaded_ids(base_dir)
    # Track the high-water mark so we only ask for emails newer than this
    last_check_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    log.info(f"Starting email monitor. Checking every {poll_interval} seconds.", phase=EMAIL)
    log.info(f"Saving to: {base_dir}", phase=EMAIL)
    log.info("Press Ctrl+C to stop.", phase=EMAIL)

    with GracefulCycle(logger=log, phase=EMAIL) as gc:
        while not gc.should_stop:
            # Check runtime limit
            if max_runtime and (time.time() - start_time) > max_runtime:
                log.info(f"Max runtime reached ({max_runtime}s). Stopping.", phase=EMAIL)
                break

            try:
                # Get fresh token for each poll cycle to handle expiration
                token = token_func()
            except Exception as e:
                log.error(f"Token refresh failed: {e}", phase=EMAIL)
                time.sleep(poll_interval)
                continue

            headers = {"Authorization": f"Bearer {token}"}
            cycle_check_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            # Use receivedDateTime filter so bursts of >1 page are fully captured
            url = f"{GRAPH}/me/mailFolders/Inbox/messages"
            params = {
                "$top": 50,
                "$select": "id,subject,from,receivedDateTime,hasAttachments",
                "$orderby": "receivedDateTime desc",
                "$filter": f"receivedDateTime ge {last_check_time}",
            }

            try:
                new_emails = []
                cycle_failed = False
                # Paginate through all emails received since last_check_time
                while url:
                    r = requests.get(url, headers=headers, params=params, timeout=30)

                    # Handle token expiration specifically
                    if r.status_code == 401:
                        log.warn("Token expired, will refresh on next cycle.", phase=EMAIL)
                        cycle_failed = True
                        break

                    r.raise_for_status()
                    data = r.json()
                    messages = data.get("value", [])

                    for m in messages:
                        if m["id"] not in downloaded_ids:
                            new_emails.append(m)

                    url = data.get("@odata.nextLink")
                    params = {}  # nextLink already contains query params

                if cycle_failed:
                    time.sleep(poll_interval)
                    continue

                if new_emails:
                    log.info(f"Found {len(new_emails)} new email(s)", phase=EMAIL)

                    for msg in new_emails:
                        try:
                            email_folder = save_email(token, msg, base_dir)

                            with open(email_folder / "metadata.json", 'r', encoding='utf-8') as f:
                                metadata = json.load(f)
                            update_index(base_dir, email_folder, metadata)

                            # Keep in-memory set current
                            downloaded_ids.add(msg["id"])
                            total_downloaded += 1
                            att_count = len(metadata.get("attachments", []))
                            log.info(f"New email: {msg.get('subject', '')[:50]} | from: {_sender(msg)} [{att_count} att]", phase=EMAIL)

                        except Exception as e:
                            log.error(f"  {e}", phase=EMAIL)
                else:
                    log.detail("No new emails.", phase=EMAIL)

                # Advance the high-water mark after a successful cycle
                last_check_time = cycle_check_time

            except requests.exceptions.RequestException as e:
                log.error(f"API error: {e}", phase=EMAIL)

            # Wait before next check (use short sleeps so we can check should_stop)
            for _ in range(poll_interval):
                if gc.should_stop:
                    break
                time.sleep(1)

    log.info(f"Total emails downloaded during session: {total_downloaded}", phase=EMAIL)


def main():
    """Main entry point with mode selection."""
    import sys

    print("=" * 60)
    print("EMAIL DOWNLOAD SYSTEM")
    print("=" * 60)
    print("\nSelect mode:")
    print("  1. Download top 100 emails")
    print("  2. Download ALL emails")
    print("  3. Monitor for new emails (continuous)")
    print()

    # Check for command line argument
    if len(sys.argv) > 1:
        mode = sys.argv[1]
    else:
        mode = input("Enter mode (1-3): ").strip()

    if mode == "1":
        token = get_access_token_interactive()
        download_top_emails(token, count=100)
    elif mode == "2":
        token = get_access_token_interactive()
        download_all_emails(token)
    elif mode == "3":
        # Monitor continuously with automatic token refresh
        token_provider = create_token_provider()
        monitor_emails(token_provider, poll_interval=600)
    else:
        print("Invalid mode. Please enter 1, 2, or 3.")


# Convenience functions for direct imports
def run_download_top_100():
    """Download top 100 emails."""
    token = get_access_token_interactive()
    return download_top_emails(token, count=100)


def run_download_all():
    """Download all emails."""
    token = get_access_token_interactive()
    return download_all_emails(token)


def run_monitor(poll_interval: int = 600, max_runtime: int = None):
    """Start email monitor with automatic token refresh."""
    token_provider = create_token_provider()
    return monitor_emails(token_provider, poll_interval=poll_interval, max_runtime=max_runtime)


if __name__ == "__main__":
    main()
