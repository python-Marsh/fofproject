from dotenv import load_dotenv
load_dotenv()
import re
import os
import webbrowser
import requests
import msal

# =========================
# CONFIG (fill in from .env)
# =========================
TENANT_ID = os.getenv("TENANT_ID")        
CLIENT_ID = os.getenv("CLIENT_ID")           

# If you ONLY need read + categorize, Mail.ReadWrite is enough.
SCOPES = ["Mail.ReadWrite"]

GRAPH = "https://graph.microsoft.com/v1.0"


def get_access_token_interactive():
    """
    Delegated auth (user signs in). Great for running locally.
    Requires a "Mobile and desktop applications" / public client app setup in Entra.
    """
    authority = f"https://login.microsoftonline.com/{TENANT_ID}"

    app = msal.PublicClientApplication(
        client_id=CLIENT_ID,
        authority=authority,
    )

    # Try cached token first
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            return result["access_token"]

    # Interactive device flow (easy + reliable)
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"Failed to create device flow. Error: {flow}")

    print(flow["message"])  # shows code + login URL
    # You can also open the login URL automatically:
    try:
        webbrowser.open(flow["verification_uri"])
    except Exception:
        pass

    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(f"Token error: {result}")

    return result["access_token"]


def list_inbox_messages(token, top=25):
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "$top": top,
        # Keep payload small; add fields as needed
        "$select": "id,subject,from,receivedDateTime,bodyPreview",
        "$orderby": "receivedDateTime desc",
    }
    r = requests.get(f"{GRAPH}/me/mailFolders/Inbox/messages", headers=headers, params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("value", [])


def decide_category(msg):
    """
    Replace this with your real logic (keywords, ML model, etc.)
    Return a category string or None.
    """
    subject = (msg.get("subject") or "").lower()
    preview = (msg.get("bodyPreview") or "").lower()
    sender = ((msg.get("from") or {}).get("emailAddress") or {}).get("address", "").lower()

    text = f"{subject} {preview} {sender}"

    if re.search(r"\burgent\b|\basap\b|\bimmediately\b", text):
        return "Urgent"
    if "invoice" in text or "payment" in text:
        return "Finance"
    if "meeting" in text or "calendar" in text:
        return "Meetings"
    if sender.endswith("@yourcompany.com"):
        return "Internal"

    return "General"


def set_message_category(token, message_id, category):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # IMPORTANT:
    # categories is a LIST of strings. This will overwrite existing categories.
    # If you want to append instead, you must first read existing categories and merge.
    payload = {"categories": [category]}

    r = requests.patch(f"{GRAPH}/me/messages/{message_id}", headers=headers, json=payload, timeout=30)
    r.raise_for_status()


def main():
    token = get_access_token_interactive()

    msgs = list_inbox_messages(token, top=20)
    print(f"Fetched {len(msgs)} messages.")

    for m in msgs:
        cat = decide_category(m)
        if not cat:
            continue

        msg_id = m["id"]
        subject = m.get("subject", "")
        sender = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "")

        set_message_category(token, msg_id, cat)
        print(f"Categorized: [{cat}] From: {sender} | Subject: {subject}")


if __name__ == "__main__":
    main()
