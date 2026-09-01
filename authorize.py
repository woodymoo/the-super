"""One-time OAuth bootstrap — produces token.json.

Usage:
    source .venv/bin/activate
    python authorize.py

A browser opens for you to sign in and authorize. Afterwards token.json lands in
the project root, and both main.py and adk web reuse it without a browser.

⚠️ Be sure to sign in with **the account that has Google Voice**. Picking the
   wrong one breaks the entire SMS channel. The script prints the account it
   actually authorized and warns if it doesn't match LANDLORD_EMAIL in .env.

To re-authorize (for example after the 7-day expiry): delete token.json and run
it again.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

load_dotenv(Path(__file__).parent / "the_super" / ".env")

# The single gmail.modify scope is enough: reading mail, reading attachments,
# creating drafts, and sending are all covered.
# Not mail.google.com — that also grants permanent deletion, which we don't need.
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

CREDENTIALS_FILE = os.environ.get("GMAIL_CREDENTIALS_FILE", "credentials.json")
TOKEN_FILE = os.environ.get("GMAIL_TOKEN_FILE", "token.json")


def get_credentials() -> Credentials:
    """Obtain usable credentials: reuse an existing token, refresh an expired one,
    and only fall back to browser authorization when neither works.

    tools/gmail.py reuses this function directly.
    """
    creds = None
    if Path(TOKEN_FILE).exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        # Silent refresh — this is the path a scheduled, unattended job takes
        creds.refresh(Request())
    else:
        if not Path(CREDENTIALS_FILE).exists():
            sys.exit(
                f"{CREDENTIALS_FILE} not found.\n"
                "Create a Desktop app OAuth client ID in the Google Cloud Console,\n"
                "then download the JSON, rename it to credentials.json, and put it "
                "in the project root."
            )
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)

    Path(TOKEN_FILE).write_text(creds.to_json())
    return creds


if __name__ == "__main__":
    creds = get_credentials()

    # Confirm which account was authorized — with two Gmail accounts it is easy to misclick
    profile = build("gmail", "v1", credentials=creds).users().getProfile(userId="me").execute()
    actual = profile["emailAddress"]

    print(f"\n✅ Authorized: {actual}")
    print(f"   Token written to {TOKEN_FILE}")
    print(f"   {profile['messagesTotal']} messages in the mailbox")

    expected = os.environ.get("LANDLORD_EMAIL", "")
    if expected and expected.lower() != actual.lower():
        print(
            f"\n⚠️  This does not match LANDLORD_EMAIL ({expected}) in .env!\n"
            f"   If {actual} is not the account with Google Voice,\n"
            f"   delete {TOKEN_FILE}, run this again, and pick the right account."
        )
    elif not expected:
        print(f"\nTip: set LANDLORD_EMAIL={actual} in the_super/.env")
