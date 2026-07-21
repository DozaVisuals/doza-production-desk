"""Classify staged Gmail threads with headless Claude Code.

Batches unclassified raw_threads into one JSON file per chunk, runs
`claude -p` with the classify_prompt, validates the JSON reply, and writes
the conclusions to the live tables. Guardrails enforced HERE, not just in
the prompt: the classifier can never mark actions done, never touches
existing action state, and unknown project ids are treated as null.
"""
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from app import db

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = PROJECT_ROOT / "refresh" / "classify_prompt.md"
INPUT_PATH = PROJECT_ROOT / "data" / "classify_input.json"
LOG_PATH = PROJECT_ROOT / "data" / "refresh.log"

CHUNK = 50
SIGNAL_KINDS = {"quote_sent", "contract_signed", "deposit_received",
                "feedback_received", "delivery_confirmed", "invoice_sent",
                "payment_received", "status_suggestion"}


def log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def _projects_for_prompt(conn) -> list[dict]:
    out = []
    for p in conn.execute(
            "SELECT id, name, client, status, notes, contacts, keywords"
            " FROM projects WHERE archived_at IS NULL"):
        out.append({
            "id": p["id"], "name": p["name"], "client": p["client"],
            "status": p["status"], "notes": p["notes"],
            "contacts": json.loads(p["contacts"] or "[]"),
            "keywords": json.loads(p["keywords"] or "[]"),
        })
    return out


CLAUDE_BIN = Path.home() / ".local" / "bin" / "claude"


def _run_claude(prompt: str, timeout: int = 600) -> str:
    # Strip inherited CLAUDE*/ANTHROPIC* vars so the CLI always resolves its
    # own stored login, even when invoked from inside another Claude session.
    import os
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("CLAUDE", "ANTHROPIC"))}
    proc = subprocess.run(
        [str(CLAUDE_BIN), "-p", prompt, "--output-format", "json",
         "--allowedTools", "Read"],
        capture_output=True, text=True, timeout=timeout, cwd=PROJECT_ROOT,
        env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: "
                           f"{proc.stderr.strip()[:400]}")
    reply = json.loads(proc.stdout)
    result = reply.get("result", "")
    result = re.sub(r"^```(?:json)?\s*|\s*```$", "", result.strip())
    return result


def _norm_money(v) -> int | None:
    return int(v) if isinstance(v, (int, float)) and v > 0 else None


def _apply(conn, verdicts: list[dict], batch_ids: set[str],
           payload_by_id: dict[str, dict]) -> dict:
    now_iso = datetime.now().isoformat(timespec="seconds")
    valid_projects = {r["id"] for r in conn.execute(
        "SELECT id FROM projects WHERE archived_at IS NULL")}
    stats = {"relevant": 0, "irrelevant": 0, "actions": 0, "signals": 0,
             "blocklisted": 0, "unknown_ids": 0}

    for v in verdicts:
        tid = v.get("gmail_thread_id")
        if tid not in batch_ids:
            stats["unknown_ids"] += 1
            continue
        payload = payload_by_id[tid]
        last_at = payload["last_message_at"]

        if v.get("blocklist_sender"):
            sender = payload["messages"][-1]["from"]
            m = re.search(r"<([^>]+)>", sender)
            addr = (m.group(1) if m else sender).strip().lower()
            if addr and "@" in addr:
                conn.execute("INSERT OR IGNORE INTO sender_blocklist"
                             " (email, reason) VALUES (?, 'classifier')",
                             (addr,))
                stats["blocklisted"] += 1

        if not v.get("relevant"):
            stats["irrelevant"] += 1
            conn.execute("UPDATE raw_threads SET classified_at=?"
                         " WHERE gmail_thread_id=?", (now_iso, tid))
            continue

        stats["relevant"] += 1
        project_id = v.get("project_id")
        if project_id not in valid_projects:
            project_id = None
        waiting_on = v.get("waiting_on")
        if waiting_on not in ("them", "me", "none"):
            waiting_on = "none"

        conn.execute(
            "INSERT INTO threads (gmail_thread_id, project_id, subject,"
            " snippet, counterpart, counterpart_email, last_direction,"
            " last_message_at, waiting_on, is_noise, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,0,?)"
            " ON CONFLICT(gmail_thread_id) DO UPDATE SET"
            "  project_id=excluded.project_id, subject=excluded.subject,"
            "  snippet=excluded.snippet, counterpart=excluded.counterpart,"
            "  counterpart_email=excluded.counterpart_email,"
            "  last_direction=excluded.last_direction,"
            "  last_message_at=excluded.last_message_at,"
            "  waiting_on=excluded.waiting_on, is_noise=0,"
            "  updated_at=excluded.updated_at",
            (tid, project_id, payload["subject"],
             (v.get("snippet") or "")[:200], v.get("counterpart"),
             v.get("counterpart_email"), payload["last_direction"],
             last_at, waiting_on, now_iso))

        summary = (v.get("summary") or "").strip()
        if summary and project_id:
            dup = conn.execute(
                "SELECT 1 FROM activity WHERE project_id=? AND ref=?"
                " AND occurred_at=?", (project_id, tid, last_at)).fetchone()
            if not dup:
                conn.execute(
                    "INSERT INTO activity (project_id, kind, summary,"
                    " occurred_at, ref) VALUES (?,'email',?,?,?)",
                    (project_id, summary[:200], last_at, tid))

        action = (v.get("next_action") or "").strip()
        if action:
            exists = conn.execute(
                "SELECT 1 FROM actions WHERE state IN ('open','snoozed')"
                " AND lower(title)=lower(?)"
                " AND (project_id IS ? OR project_id=?)",
                (action, project_id, project_id)).fetchone()
            if not exists:
                due = v.get("next_action_due")
                due_iso = f"{due}T17:00:00" if due else None
                conn.execute(
                    "INSERT INTO actions (project_id, title, due_at,"
                    " money_cents, source, ref) VALUES (?,?,?,?,'ai',?)",
                    (project_id, action[:120], due_iso,
                     _norm_money(v.get("money_cents")), tid))
                stats["actions"] += 1

        for s in v.get("signals") or []:
            if s.get("kind") in SIGNAL_KINDS:
                conn.execute(
                    "INSERT INTO signals (project_id, kind, detail,"
                    " money_cents, occurred_at, ref) VALUES (?,?,?,?,?,?)",
                    (project_id, s["kind"], (s.get("detail") or "")[:200],
                     _norm_money(s.get("money_cents")), last_at, tid))
                stats["signals"] += 1

        if project_id:
            conn.execute(
                "UPDATE projects SET last_touch_at=?"
                " WHERE id=? AND (last_touch_at IS NULL OR last_touch_at<?)",
                (last_at, project_id, last_at))

        conn.execute("UPDATE raw_threads SET classified_at=?"
                     " WHERE gmail_thread_id=?", (now_iso, tid))

    conn.commit()
    return stats


DECAY_DAYS = 21


def decay(conn) -> int:
    """A thread stops counting as waiting (either direction) after
    DECAY_DAYS — by then it was handled outside email or the exchange simply
    ended. It stays in the project's activity; only the lists let go."""
    n = conn.execute(
        "UPDATE threads SET waiting_on='none' WHERE waiting_on IN ('me','them')"
        f" AND last_message_at < datetime('now','-{DECAY_DAYS} day')").rowcount
    conn.commit()
    return n


def classify_pending(conn, limit: int | None = None,
                     chunk: int = CHUNK) -> dict:
    total = {"chunks": 0, "relevant": 0, "irrelevant": 0, "actions": 0,
             "signals": 0, "blocklisted": 0, "unknown_ids": 0, "missing": 0}
    from app import config as appconfig
    cfg = appconfig.load()
    prompt_base = PROMPT_PATH.read_text().replace(
        "[[OWNER_CONTEXT]]",
        appconfig.owner_context(cfg, db.get_state(conn, "account_email", "")))
    done = 0
    while True:
        if limit is not None and done >= limit:
            break
        take = chunk if limit is None else min(chunk, limit - done)
        rows = conn.execute(
            "SELECT gmail_thread_id, payload FROM raw_threads"
            " WHERE classified_at IS NULL ORDER BY last_message_at DESC"
            " LIMIT ?", (take,)).fetchall()
        if not rows:
            break
        payload_by_id = {r["gmail_thread_id"]: json.loads(r["payload"])
                         for r in rows}
        INPUT_PATH.parent.mkdir(exist_ok=True)
        INPUT_PATH.write_text(json.dumps({
            "account_email": db.get_state(conn, "account_email", ""),
            "today": datetime.now().strftime("%Y-%m-%d"),
            "projects": _projects_for_prompt(conn),
            "threads": list(payload_by_id.values()),
        }, indent=1))

        log(f"classifying chunk of {len(rows)} threads …")
        result = _run_claude(prompt_base + f"\n{INPUT_PATH}\n")
        (PROJECT_ROOT / "data" / "last_classify_reply.txt").write_text(result)
        try:
            verdicts = json.loads(result).get("threads", [])
        except json.JSONDecodeError:
            # Salvage a JSON object embedded in prose before giving up.
            m = re.search(r"\{.*\}", result, re.S)
            if not m:
                log(f"  unparseable reply ({len(result)} chars): "
                    f"{result[:300]!r}")
                raise
            verdicts = json.loads(m.group(0)).get("threads", [])
        stats = _apply(conn, verdicts, set(payload_by_id), payload_by_id)

        seen = {v.get("gmail_thread_id") for v in verdicts}
        missing = set(payload_by_id) - seen
        total["missing"] += len(missing)
        # Unanswered threads stay unclassified and retry next run — but if
        # the same chunk head never shrinks we would loop forever, so stop
        # this run when nothing was consumed.
        for k, n in stats.items():
            total[k] += n
        total["chunks"] += 1
        done += len(rows)
        log(f"  chunk done: {stats}")
        if len(missing) == len(rows):
            log("  classifier answered none of the batch — stopping run")
            break
    total["decayed"] = decay(conn)
    return total


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="max threads this run")
    ap.add_argument("--chunk", type=int, default=CHUNK)
    args = ap.parse_args()
    conn = db.connect()
    db.init(conn)
    try:
        totals = classify_pending(conn, args.limit, args.chunk)
    except Exception as e:
        log(f"CLASSIFY FAILED: {e}")
        return 1
    pending = conn.execute("SELECT COUNT(*) c FROM raw_threads"
                           " WHERE classified_at IS NULL").fetchone()["c"]
    log(f"classification totals: {totals} · {pending} still pending")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
