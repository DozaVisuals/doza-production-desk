"""Friday digest: one headless pass over the week, stored in sync_state."""
import json
from datetime import datetime
from pathlib import Path

from app import db
from .classify import _run_claude, log

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = PROJECT_ROOT / "refresh" / "digest_prompt.md"
INPUT_PATH = PROJECT_ROOT / "data" / "digest_input.json"


def generate(conn) -> str | None:
    payload = {
        "week_ending": datetime.now().strftime("%Y-%m-%d"),
        "projects": [dict(r) for r in conn.execute(
            "SELECT id, name, client, status, money_cents, last_touch_at"
            " FROM projects WHERE archived_at IS NULL")],
        "activity_7d": [dict(r) for r in conn.execute(
            "SELECT a.project_id, a.kind, a.summary, a.occurred_at"
            " FROM activity a WHERE a.occurred_at > datetime('now','-7 day')"
            " ORDER BY a.occurred_at")],
        "signals_7d": [dict(r) for r in conn.execute(
            "SELECT project_id, kind, detail, money_cents, occurred_at"
            " FROM signals WHERE occurred_at > datetime('now','-7 day')")],
        "done_7d": [dict(r) for r in conn.execute(
            "SELECT project_id, title, completed_at FROM actions"
            " WHERE state='done' AND completed_at > datetime('now','-7 day')")],
        "open_actions": [dict(r) for r in conn.execute(
            "SELECT project_id, title, due_at FROM actions WHERE state='open'")],
        "waiting_on_me": [dict(r) for r in conn.execute(
            "SELECT counterpart, subject, last_message_at FROM threads"
            " WHERE waiting_on='me'")],
        "waiting_on_them": [dict(r) for r in conn.execute(
            "SELECT counterpart, subject, last_message_at FROM threads"
            " WHERE waiting_on='them'")],
    }
    INPUT_PATH.parent.mkdir(exist_ok=True)
    INPUT_PATH.write_text(json.dumps(payload, indent=1))
    from app import config as appconfig
    cfg = appconfig.load()
    prompt = PROMPT_PATH.read_text().replace(
        "[[OWNER_CONTEXT]]", appconfig.owner_context(cfg))
    try:
        text = _run_claude(prompt + f"\n{INPUT_PATH}\n", timeout=300).strip()
        return text or None
    except Exception as e:
        log(f"digest failed: {e}")
        return None
