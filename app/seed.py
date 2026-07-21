"""Starter sample data — fictional, so day one isn't an empty screen.

Runs automatically on first server start (empty projects table only). The
first real refresh replaces the sample threads/events with your actual mail
and calendar; edit or archive the sample projects from the dashboard.
For a fuller fictional board (screenshots, demos) see tools/demo_seed.py.
"""
import json
from datetime import datetime, timedelta

from . import db


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def seed(conn) -> None:
    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    ahead = lambda d, h=17: _iso(today + timedelta(days=d, hours=h))
    ago = lambda d: _iso(today - timedelta(days=d, hours=-10))

    projects = [
        ("Sample — Bluefin Hotels brand film", "Bluefin Hospitality",
         "Negotiating", 4_800_000, ahead(4), 1,
         "Example project: a quote in negotiation. Archive me from the board."),
        ("Sample — Harborline Museum film", "Harborline Museum",
         "Production", None, None, 0,
         "Example project: mid-production with feedback flowing."),
        ("Sample — Meridian retainer", "Meridian Conservatory",
         "Booked", 3_600_000, None, 2,
         "Example project: a recurring retainer client."),
    ]
    for name, client, status, money, deadline, touch, notes in projects:
        conn.execute(
            "INSERT INTO projects (name, client, status, money_cents,"
            " deadline_at, last_touch_at, notes, contacts, keywords)"
            " VALUES (?,?,?,?,?,?,?,'[]','[]')",
            (name, client, status, money, deadline, ago(touch), notes))
    pid = {r["name"]: r["id"] for r in
           conn.execute("SELECT id, name FROM projects")}

    conn.execute(
        "INSERT INTO actions (project_id, title, due_at, source)"
        " VALUES (?,?,?, 'seed')",
        (pid["Sample — Bluefin Hotels brand film"],
         "Send the revised quote to Dana", ahead(1)))
    conn.execute(
        "INSERT INTO actions (project_id, title, source) VALUES (?,?, 'seed')",
        (pid["Sample — Harborline Museum film"],
         "Cut V2 from the curator's notes"))

    conn.execute(
        "INSERT INTO threads (gmail_thread_id, project_id, subject, snippet,"
        " counterpart, counterpart_email, last_direction, last_message_at,"
        " waiting_on, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("sample-1", pid["Sample — Bluefin Hotels brand film"],
         "Brand film — quote timing",
         "“Board reviews budgets Thursday, would love the revised number.”",
         "Dana Whitfield", "dana@example.com", "inbound", ago(1), "me",
         _iso(now)))

    db.set_state(conn, "seeded_at", _iso(now))
    db.set_state(conn, "last_refresh_at", _iso(now))
    db.set_state(conn, "last_refresh_ok", "1")
    conn.commit()


def seed_if_empty(conn) -> bool:
    if conn.execute("SELECT COUNT(*) c FROM projects").fetchone()["c"] == 0:
        seed(conn)
        return True
    return False


if __name__ == "__main__":
    c = db.connect()
    db.init(c)
    print("seeded" if seed_if_empty(c) else "already populated, nothing done")
