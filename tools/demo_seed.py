"""Build data/demo.db — a fully fictional board for shareable screenshots.

Every client, person, thread, and dollar amount here is invented. Run with:
  DOZA_DB=data/demo.db ./venv/bin/python tools/demo_seed.py
then serve it:
  DOZA_DB=data/demo.db ./venv/bin/python -m uvicorn app.server:app --port 5199
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db  # noqa: E402

assert "demo" in str(db.DB_PATH), "refusing to run against a non-demo DB"
db.DB_PATH.unlink(missing_ok=True)

conn = db.connect()
db.init(conn)
now = datetime.now()
today = now.replace(hour=0, minute=0, second=0, microsecond=0)
iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S")
ago = lambda days, h=10: iso(today - timedelta(days=days) + timedelta(hours=h))
ahead = lambda days, h=10, m=0: iso(today + timedelta(days=days) + timedelta(hours=h, minutes=m))

# ---- projects (name, client, kind, status, money, deadline, touch_ago) ----
P = [
    ("Bluefin Hotels brand film", "Bluefin Hospitality", "client", "Negotiating", 4_800_000, ahead(3, 17), 0),
    ("Harborline Museum — opening film", "Harborline Museum", "client", "Production", None, ahead(1, 17), 0),
    ("Summit & Co. leadership headshots", "Summit & Co.", "client", "Booked", None, ahead(4, 17), 1),
    ("Meridian Conservatory retainer", "Meridian Conservatory", "client", "Booked", 3_600_000, None, 1),
    ("Cutline", "Studio product", "product", "Booked", None, None, 0),
    ("Atlas Theater 50th anniversary", "Atlas Theater", "client", "Post", None, None, 5),
    ("Northgate Athletics campaign", "Northgate Athletics", "client", "Review", None, None, 4),
    ("Riverbend Farms documentary", "Riverbend Farms", "client", "Lead", 2_200_000, None, 11),
]
for name, client, kind, status, money, deadline, touch in P:
    conn.execute(
        "INSERT INTO projects (name, client, kind, status, money_cents,"
        " deadline_at, last_touch_at, contacts, keywords)"
        " VALUES (?,?,?,?,?,?,?,'[]','[]')",
        (name, client, kind, status, money, deadline, ago(touch)))
pid = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM projects")}

# ---- actions ----
A = [
    ("Confirm call time with Jamie — brand film", "Bluefin Hotels brand film", ahead(0, 17), None),
    ("Send Priya the Harborline V2 for curator review", "Harborline Museum — opening film", ahead(1, 12), None),
    ("Send revised Bluefin quote to Dana", "Bluefin Hotels brand film", ahead(3, 17), 4_800_000),
    ("Countersign the Summit & Co. agreement", "Summit & Co. leadership headshots", ahead(4, 17), None),
    ("Deliver August content calendar", "Meridian Conservatory retainer", ahead(6, 17), None),
    ("Start assembly edit from the interview selects", "Atlas Theater 50th anniversary", None, None),
    ("Chase Marcus on the Atlas invoice", "Atlas Theater 50th anniversary", None, 640_000),
]
for title, proj, due, money in A:
    conn.execute("INSERT INTO actions (project_id, title, due_at, money_cents,"
                 " source, ref) VALUES (?,?,?,?,'ai','demo1')",
                 (pid[proj], title, due, money))

# ---- events ----
E = [
    ("Color pass — Harborline film", "Suite A", ahead(0, 0), 1, 0, "Harborline Museum — opening film"),
    ("Location scout — waterfront", "Pier 4", ahead(0, 10), 0, 0, "Bluefin Hotels brand film"),
    ("Meridian check-in call", "Zoom", ahead(0, 15, 30), 0, 0, "Meridian Conservatory retainer"),
    ("SHOOT — Bluefin Hotels, Harborview rooftop", "Harborview Hotel, Boston", ahead(1, 0), 1, 1, "Bluefin Hotels brand film"),
    ("Gear prep + batteries", "Studio", ahead(1, 16), 0, 0, "Bluefin Hotels brand film"),
    ("SHOOT — Riverbend Farms golden hour", "Riverbend Farms", ahead(7, 0), 1, 1, "Riverbend Farms documentary"),
]
for title, loc, start, all_day, shoot, proj in E:
    conn.execute("INSERT INTO events (gcal_event_id, title, location, start_at,"
                 " end_at, all_day, is_shoot, project_id, updated_at)"
                 " VALUES (?,?,?,?,?,?,?,?,?)",
                 (f"demo-{title[:12]}", title, loc, start, start, all_day,
                  shoot, pid[proj], iso(now)))

# ---- threads ----
T = [
    ("Priya Shah", "Harborline V2 — curator notes", "“The middle section lands now — two small trims and we're there.”", "Harborline Museum — opening film", "me", 0),
    ("Dana Whitfield", "Bluefin brand film — quote timing", "“Board reviews budgets Thursday, would love the revised number before.”", "Bluefin Hotels brand film", "me", 1),
    ("Alex Kim", "Cutline license question", "“Does the studio license cover our second edit bay?”", "Cutline", "me", 3),
    ("Sam Torres", "Documentary idea — harbor pilots", "“Would you be open to a call about a short doc on the harbor pilots?”", None, "me", 2),
    ("Marcus Lee", "Atlas Theater — final invoice", "Sent the final invoice for the anniversary film.", "Atlas Theater 50th anniversary", "them", 21),
    ("Jordan Ellis", "Northgate finals — sign-off", "Delivered finals via review link, awaiting brand team sign-off.", "Northgate Athletics campaign", "them", 4),
    ("Meridian events team", "September series planning", "Proposed three dates for the September series.", "Meridian Conservatory retainer", "them", 6),
]
for i, (who, subj, snip, proj, waiting, days) in enumerate(T):
    conn.execute(
        "INSERT INTO threads (gmail_thread_id, project_id, subject, snippet,"
        " counterpart, counterpart_email, last_direction, last_message_at,"
        " waiting_on, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (f"demo{i}", pid.get(proj), subj, snip, who,
         "hello@example.com", "outbound" if waiting == "them" else "inbound",
         ago(days, 9 + i % 7), waiting, iso(now)))

# ---- signals (money zone) ----
S = [
    ("Atlas Theater 50th anniversary", "invoice_sent", "Final invoice — anniversary film", 640_000, 21),
    ("Meridian Conservatory retainer", "invoice_sent", "July retainer invoice", 300_000, 12),
    ("Bluefin Hotels brand film", "quote_sent", "Brand film proposal — full package", 4_800_000, 2),
]
for proj, kind, detail, money, days in S:
    conn.execute("INSERT INTO signals (project_id, kind, detail, money_cents,"
                 " occurred_at, ref) VALUES (?,?,?,?,?,'demo1')",
                 (pid[proj], kind, detail, money, ago(days)))

# ---- delegations ----
conn.execute(
    "INSERT INTO delegations (project_id, title, why, kind, ref, prompt, state)"
    " VALUES (?,?,?,?,?,?,'suggested')",
    (pid["Bluefin Hotels brand film"], "Draft reply to Dana on quote timing",
     "her board reviews budgets Thursday", "draft_reply", "demo1", "demo"))
conn.execute(
    "INSERT INTO delegations (project_id, title, why, kind, ref, prompt, state)"
    " VALUES (?,?,?,?,?,?,'suggested')",
    (pid["Bluefin Hotels brand film"], "Assemble the Bluefin proposal skeleton",
     "quote due this week; drafts the structure", "cowork", None, "demo"))
conn.execute(
    "INSERT INTO delegations (project_id, title, why, kind, ref, prompt, state,"
    " result, completed_at) VALUES (?,?,?,?,?,?,'done',?,?)",
    (pid["Harborline Museum — opening film"],
     "Draft reply to Priya confirming the V2 timeline",
     "keeps the opening-night edit on schedule", "draft_reply", "demo0", "demo",
     "Hi Priya,\n\nGlad the middle section is landing. I'll make the two trims "
     "tomorrow morning and have V2 to you by end of day — that keeps us "
     "comfortably ahead of the opening.\n\nBest,\nSam", iso(now)))

# ---- sync state ----
db.set_state(conn, "account_email", "studio@example.com")
db.set_state(conn, "last_refresh_at", iso(now))
db.set_state(conn, "last_refresh_ok", "1")
db.set_state(conn, "weather", json.dumps({
    "date": (today + timedelta(days=1)).strftime("%Y-%m-%d"),
    "location": "Harborview Hotel, Boston",
    "summary": "Sunny, 76°F · wind 8 mph"}))
db.set_state(conn, "digest", json.dumps({
    "week": "demo-week", "created_at": iso(now),
    "text": "**What moved**\nHarborline — V2 approved direction from the curator.\n"
            "Bluefin — proposal updated to $48,000, shoot scheduled.\n\n"
            "**What stalled**\nAtlas Theater — final invoice unanswered for 3 weeks.\n\n"
            "**What's owed**\nSend Dana the revised quote before Thursday's board review."}))
conn.commit()
print(f"demo board written → {db.DB_PATH}")
