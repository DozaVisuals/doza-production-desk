"""Fetch Gmail threads into the raw_threads staging table.

Filtering happens in three layers:
1. The Gmail query skips chat and the Promotions/Social/Forums/Updates
   category tabs (newsletters, receipts, notifications).
2. Obvious robot senders (noreply@, mailer-daemon, …) are dropped.
3. The sender_blocklist table drops senders the owner has flagged over time.

Everything that survives is stored as compact JSON for the classifier.
Sent mail is included on purpose: "waiting on them" needs threads where
the owner spoke last.
"""
import base64
import json
import re
from datetime import datetime, timedelta

from app import db

GMAIL_QUERY = ("-in:chat -category:promotions -category:social "
               "-category:forums -category:updates")
ROBOT_SENDER = re.compile(
    r"(no-?reply|do-?not-?reply|mailer-daemon|postmaster|notifications?@|"
    r"alerts?@|newsletter|marketing@)", re.I)
QUOTE_MARKERS = [
    re.compile(r"^On .{5,120} wrote:\s*$"),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}$", re.I),
    re.compile(r"^From: .+@.+$"),
    re.compile(r"^Sent from my iPhone", re.I),
]
MAX_BODY_CHARS = 1500
MESSAGES_PER_THREAD = 4          # most recent N messages go to the classifier


def _header(msg: dict, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _decode(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data.encode()).decode("utf-8", "replace")
    except Exception:
        return ""


def _body_text(payload: dict) -> str:
    """Prefer text/plain; fall back to stripped text/html."""
    mime = payload.get("mimeType", "")
    data = payload.get("body", {}).get("data")
    if mime == "text/plain" and data:
        return _decode(data)
    if mime.startswith("multipart/"):
        parts = payload.get("parts", [])
        for p in parts:                       # plain first
            t = _body_text(p)
            if t and p.get("mimeType") != "text/html":
                return t
        for p in parts:                       # then anything
            t = _body_text(p)
            if t:
                return t
    if mime == "text/html" and data:
        html = _decode(data)
        text = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", html,
                      flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"&nbsp;?", " ", text)
        return re.sub(r"\s+", " ", text)
    return ""


def _strip_quotes(text: str) -> str:
    """Drop quoted history so the classifier only sees the new words."""
    out = []
    for line in text.splitlines():
        if any(m.match(line.strip()) for m in QUOTE_MARKERS):
            break
        if line.lstrip().startswith(">"):
            continue
        out.append(line)
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return cleaned[:MAX_BODY_CHARS]


def _addr(raw: str) -> str:
    m = re.search(r"<([^>]+)>", raw)
    return (m.group(1) if m else raw).strip().lower()


def fetch(service, conn, since: datetime, account_email: str,
          max_threads: int | None = None) -> dict:
    """Pull threads with activity since `since` into raw_threads."""
    blocklist = {r["email"].lower() for r in
                 conn.execute("SELECT email FROM sender_blocklist")}
    q = f"after:{int(since.timestamp())} {GMAIL_QUERY}"

    thread_ids: list[str] = []
    page_token = None
    while True:
        resp = service.users().threads().list(
            userId="me", q=q, maxResults=100, pageToken=page_token).execute()
        thread_ids += [t["id"] for t in resp.get("threads", [])]
        page_token = resp.get("nextPageToken")
        if not page_token or (max_threads and len(thread_ids) >= max_threads):
            break
    if max_threads:
        thread_ids = thread_ids[:max_threads]

    stats = {"listed": len(thread_ids), "stored": 0, "updated": 0,
             "skipped_noise": 0, "errors": 0}
    now_iso = datetime.now().isoformat(timespec="seconds")

    for tid in thread_ids:
        try:
            t = service.users().threads().get(
                userId="me", id=tid, format="full").execute()
        except Exception:
            stats["errors"] += 1
            continue

        msgs = t.get("messages", [])
        if not msgs:
            continue
        last = msgs[-1]
        last_from = _addr(_header(last, "From"))
        last_at = datetime.fromtimestamp(
            int(last["internalDate"]) / 1000).isoformat(timespec="seconds")
        direction = ("outbound" if account_email and
                     account_email in last_from else "inbound")

        if direction == "inbound" and (
                last_from in blocklist or ROBOT_SENDER.search(last_from)):
            stats["skipped_noise"] += 1
            continue

        compact = {
            "gmail_thread_id": tid,
            "subject": _header(msgs[0], "Subject") or "(no subject)",
            "message_count": len(msgs),
            "last_message_at": last_at,
            "last_direction": direction,
            "messages": [{
                "from": _header(m, "From"),
                "to": _header(m, "To"),
                "at": datetime.fromtimestamp(
                    int(m["internalDate"]) / 1000).isoformat(timespec="seconds"),
                "direction": ("outbound" if account_email and
                              account_email in _addr(_header(m, "From"))
                              else "inbound"),
                "body": _strip_quotes(_body_text(m.get("payload", {}))) or
                        m.get("snippet", ""),
            } for m in msgs[-MESSAGES_PER_THREAD:]],
        }

        prev = conn.execute(
            "SELECT last_message_at FROM raw_threads WHERE gmail_thread_id=?",
            (tid,)).fetchone()
        if prev and prev["last_message_at"] == last_at:
            continue                      # nothing new — keep classification
        conn.execute(
            "INSERT INTO raw_threads (gmail_thread_id, subject,"
            " last_message_at, last_direction, payload, fetched_at,"
            " classified_at) VALUES (?,?,?,?,?,?,NULL)"
            " ON CONFLICT(gmail_thread_id) DO UPDATE SET"
            "  subject=excluded.subject,"
            "  last_message_at=excluded.last_message_at,"
            "  last_direction=excluded.last_direction,"
            "  payload=excluded.payload,"
            "  fetched_at=excluded.fetched_at,"
            "  classified_at=NULL",
            (tid, compact["subject"], last_at, direction,
             json.dumps(compact), now_iso))
        stats["updated" if prev else "stored"] += 1

    conn.commit()
    return stats
