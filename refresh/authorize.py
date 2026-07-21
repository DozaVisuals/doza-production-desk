"""One-time interactive Google authorization.

Opens the browser, asks for read-only Gmail + Calendar consent, stores the
token at credentials/token.json, and records the account email (used to tell
inbound from outbound mail).
"""
import sys

from app import db
from . import google_client


def main() -> int:
    try:
        creds = google_client.load_credentials(interactive=True)
    except google_client.NotAuthorized as e:
        print(f"✗ {e}")
        return 1

    profile = google_client.gmail_service(creds).users().getProfile(
        userId="me").execute()
    email = profile["emailAddress"]

    conn = db.connect()
    db.init(conn)
    db.set_state(conn, "account_email", email)
    conn.close()

    print(f"✓ Authorized {email} (read-only Gmail + Calendar).")
    print(f"✓ Token saved to {google_client.TOKEN_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
