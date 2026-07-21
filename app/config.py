"""User configuration.

`config.json` at the project root (gitignored) overrides DEFAULTS — see
config.example.json. The desk works with pure defaults; config just makes
the AI passes speak about (and sign as) the actual owner.
"""
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "owner_name": "the studio owner",
    "studio_name": "the studio",
    "owner_bio": "a small creative production studio",
    "signature": "",                 # how drafts sign off, e.g. "Alex"
    "home_lat": 42.3601,             # weather fallback location (Boston)
    "home_lon": -71.0589,
    "api_user_agent": "production-desk (github.com/dozavisuals/doza-production-desk)",
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    path = PROJECT_ROOT / "config.json"
    if path.exists():
        try:
            cfg.update(json.loads(path.read_text()))
        except ValueError:
            pass
    return cfg


def owner_context(cfg: dict, account_email: str = "") -> str:
    email = f" ({account_email})" if account_email else ""
    return (f"The user is {cfg['owner_name']}{email}, who runs "
            f"{cfg['studio_name']} — {cfg['owner_bio']}.")
