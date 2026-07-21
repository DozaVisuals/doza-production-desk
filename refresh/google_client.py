"""Google API auth and service builders.

Read-only by design: the ONLY scopes this app ever requests are
gmail.readonly and calendar.readonly. If a token with broader scopes somehow
appears, we refuse it. Client secret and token live in credentials/
(gitignored); nothing leaves this machine.
"""
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CRED_DIR = PROJECT_ROOT / "credentials"
CLIENT_SECRET = CRED_DIR / "client_secret.json"
TOKEN_PATH = CRED_DIR / "token.json"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]


class NotAuthorized(RuntimeError):
    pass


def load_credentials(interactive: bool = False) -> Credentials:
    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        extra = set(creds.scopes or []) - set(SCOPES)
        if extra:
            raise NotAuthorized(
                f"token.json has unexpected scopes {extra} — delete "
                f"{TOKEN_PATH} and re-run authorization.")

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save(creds)

    if not creds or not creds.valid:
        if not interactive:
            raise NotAuthorized(
                "No valid Google token. Run:\n"
                f'  cd "{PROJECT_ROOT}" && ./venv/bin/python -m refresh.authorize')
        if not CLIENT_SECRET.exists():
            raise NotAuthorized(
                f"Missing {CLIENT_SECRET}.\nDownload the OAuth desktop-app "
                "client JSON from the Google Cloud console and save it there.")
        import os
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
        creds = flow.run_local_server(
            port=0, prompt="consent",
            open_browser=not os.environ.get("DOZA_AUTH_NO_BROWSER"))
        _save(creds)
    return creds


def _save(creds: Credentials) -> None:
    CRED_DIR.mkdir(exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json())
    TOKEN_PATH.chmod(0o600)


def gmail_service(creds: Credentials):
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def calendar_service(creds: Credentials):
    return build("calendar", "v3", credentials=creds, cache_discovery=False)
