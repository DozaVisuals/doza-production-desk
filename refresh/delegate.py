"""Suggest and execute delegations — work Claude can take off the owner's plate.

suggest(conn): whole-board pass after each refresh; replaces prior
'suggested' rows with a fresh top-3/4. execute(delegation_id): runs one
delegation headless and stores the deliverable in `result`. Both produce
drafts only — there is no path from here to sending anything.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

from app import db
from .classify import _run_claude, log

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = PROJECT_ROOT / "refresh" / "delegate_prompt.md"
INPUT_PATH = PROJECT_ROOT / "data" / "delegate_input.json"

def _voice() -> str:
    from app import config as appconfig
    cfg = appconfig.load()
    sig = cfg["signature"] or cfg["owner_name"].split()[0]
    return (f"You draft on behalf of {cfg['owner_name']}, who runs "
            f"{cfg['studio_name']} — {cfg['owner_bio']}. Voice: warm, direct, "
            f"concise, professional — no filler, no exclamation marks, signs "
            f"emails simply \"{sig}\". Output ONLY the deliverable (markdown "
            f"for documents, plain email body for replies — no subject line "
            f"unless asked, no preamble, no commentary). You cannot send "
            f"anything; this is a draft the owner will review and paste "
            f"themselves. Ground every fact in the provided context — never "
            f"invent dates, amounts, or commitments.")

KINDS = {"draft_reply", "draft_doc", "summarize", "prep", "cowork", "chat"}
SESSION_KINDS = {"cowork", "chat"}   # handed to a real Claude session, not run headless


def _thread_payload(conn, ref: str | None) -> dict | None:
    if not ref:
        return None
    row = conn.execute("SELECT payload FROM raw_threads WHERE gmail_thread_id=?",
                       (ref,)).fetchone()
    return json.loads(row["payload"]) if row else None


def suggest(conn) -> int:
    threads_me, threads_them = [], []
    for r in conn.execute(
            "SELECT t.*, p.name AS project_name FROM threads t"
            " LEFT JOIN projects p ON p.id=t.project_id"
            " WHERE t.waiting_on IN ('me','them') AND t.is_noise=0"
            " ORDER BY t.last_message_at DESC LIMIT 40"):
        payload = _thread_payload(conn, r["gmail_thread_id"]) or {}
        entry = {
            "gmail_thread_id": r["gmail_thread_id"],
            "project_id": r["project_id"],
            "project": r["project_name"],
            "counterpart": r["counterpart"],
            "subject": r["subject"],
            "last_message_at": r["last_message_at"],
            "messages": payload.get("messages", [])[-2:],
        }
        (threads_me if r["waiting_on"] == "me" else threads_them).append(entry)

    payload = {
        "today": datetime.now().strftime("%Y-%m-%d"),
        "projects": [dict(r) for r in conn.execute(
            "SELECT id, name, client, status, notes FROM projects"
            " WHERE archived_at IS NULL")],
        "waiting_on_me": threads_me,
        "waiting_on_them": threads_them[:15],
        "open_actions": [dict(r) for r in conn.execute(
            "SELECT project_id, title, due_at FROM actions WHERE state='open'")],
        "dismissed_titles": [r["title"] for r in conn.execute(
            "SELECT title FROM delegations WHERE state='dismissed'"
            " ORDER BY created_at DESC LIMIT 30")],
        "recently_done": [r["title"] for r in conn.execute(
            "SELECT title FROM delegations WHERE state='done'"
            " AND completed_at > datetime('now','-7 day')")],
    }
    INPUT_PATH.parent.mkdir(exist_ok=True)
    INPUT_PATH.write_text(json.dumps(payload, indent=1))

    from app import config as appconfig
    cfg = appconfig.load()
    prompt = PROMPT_PATH.read_text().replace(
        "[[OWNER_CONTEXT]]", appconfig.owner_context(cfg))
    result = _run_claude(prompt + f"\n{INPUT_PATH}\n")
    try:
        suggestions = json.loads(result).get("suggestions", [])
    except json.JSONDecodeError:
        import re
        m = re.search(r"\{.*\}", result, re.S)
        suggestions = json.loads(m.group(0)).get("suggestions", []) if m else []

    valid_projects = {r["id"] for r in conn.execute(
        "SELECT id FROM projects WHERE archived_at IS NULL")}
    conn.execute("DELETE FROM delegations WHERE state='suggested'")
    n = 0
    for s in suggestions[:5]:
        title = (s.get("title") or "").strip()
        prompt = (s.get("prompt") or "").strip()
        if not title or not prompt:
            continue
        pid = s.get("project_id")
        conn.execute(
            "INSERT INTO delegations (project_id, title, why, kind, ref,"
            " prompt) VALUES (?,?,?,?,?,?)",
            (pid if pid in valid_projects else None, title[:90],
             (s.get("why") or "")[:80],
             s.get("kind") if s.get("kind") in KINDS else "draft_doc",
             s.get("ref"), prompt))
        n += 1
    conn.commit()
    return n


def execute(delegation_id: int) -> bool:
    conn = db.connect()
    db.init(conn)
    row = conn.execute("SELECT * FROM delegations WHERE id=?",
                       (delegation_id,)).fetchone()
    if not row:
        return False
    thread = _thread_payload(conn, row["ref"])
    proj = conn.execute("SELECT name, client, notes FROM projects WHERE id=?",
                        (row["project_id"],)).fetchone() if row["project_id"] else None
    parts = [_voice(), "\n## Task\n", row["prompt"]]
    if proj:
        parts.append(f"\n## Project\n{proj['name']} — {proj['client']}."
                     f" {proj['notes'] or ''}")
    if thread:
        parts.append("\n## Source thread\n" + json.dumps(thread, indent=1))
    try:
        result = _run_claude("\n".join(parts), timeout=300)
        if not result.strip():
            raise RuntimeError("empty deliverable")
        conn.execute(
            "UPDATE delegations SET state='done', result=?, completed_at=?"
            " WHERE id=?",
            (result.strip(), datetime.now().isoformat(timespec="seconds"),
             delegation_id))
        log(f"delegation {delegation_id} done: {row['title']}")
        ok = True
    except Exception as e:
        conn.execute(
            "UPDATE delegations SET state='failed', result=? WHERE id=?",
            (f"{type(e).__name__}: {e}", delegation_id))
        log(f"delegation {delegation_id} FAILED: {e}")
        ok = False
    conn.commit()
    conn.close()
    return ok


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "suggest":
        c = db.connect(); db.init(c)
        print(f"{suggest(c)} suggestions")
    else:
        sys.exit(0 if execute(int(sys.argv[1])) else 1)
