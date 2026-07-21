"""Doza Dashboard server.

GET  /                       the dashboard
GET  /api/dashboard          everything the page needs in one payload
POST /api/actions/{id}/done
POST /api/actions/{id}/snooze   {"preset": "1d" | "3d" | "next_week"}
POST /api/threads/{id}/snooze   {"preset": ...}
POST /api/projects/{id}/archive
POST /api/quickadd              {"text": "send the quote by Friday"}

Only the owner marks things done — the classifier has no path to these endpoints.
"""
import json
import math
import re
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import config as appconfig
from . import db, seed

app = FastAPI(title="Doza Production Desk")
CFG = appconfig.load()
STATIC = Path(__file__).resolve().parent.parent / "static"


@app.on_event("startup")
def startup() -> None:
    conn = db.connect()
    db.init(conn)
    seed.seed_if_empty(conn)
    conn.close()


def _parse(dt: str | None) -> datetime | None:
    if not dt:
        return None
    try:
        return datetime.fromisoformat(dt)
    except ValueError:
        return None


def _days_until(dt: str | None, now: datetime) -> float | None:
    d = _parse(dt)
    if d is None:
        return None
    return (d - now).total_seconds() / 86400


def _days_since(dt: str | None, now: datetime) -> int | None:
    d = _parse(dt)
    if d is None:
        return None
    return max(0, (now.date() - d.date()).days)


def _active(conn) -> list:
    return conn.execute(
        "SELECT * FROM projects WHERE archived_at IS NULL"
    ).fetchall()


# ---- money -----------------------------------------------------------------
# Built from the signals ledger. The classifier sometimes files the same
# invoice/quote from two threads, so rows dedupe on (project, kind, amount,
# day). Payments cancel a project's oldest outstanding invoice (FIFO).

def _money_view(conn, now: datetime) -> dict:
    rows = conn.execute(
        "SELECT s.*, p.name AS project_name, p.status AS project_status"
        " FROM signals s LEFT JOIN projects p ON p.id = s.project_id"
        " WHERE s.kind IN ('invoice_sent','payment_received','quote_sent',"
        "'contract_signed') ORDER BY s.occurred_at").fetchall()
    seen = set()
    invoices, payments, quotes, contracts = [], [], [], []
    for r in rows:
        key = (r["project_id"], r["kind"], r["money_cents"] or 0,
               (r["occurred_at"] or "")[:10])
        if key in seen:
            continue
        seen.add(key)
        d = dict(r)
        d["days"] = _days_since(r["occurred_at"], now) or 0
        {"invoice_sent": invoices, "payment_received": payments,
         "quote_sent": quotes, "contract_signed": contracts}[r["kind"]].append(d)

    for pay in payments:                      # FIFO cancel per project
        for inv in invoices:
            if (inv["project_id"] == pay["project_id"]
                    and not inv.get("paid")
                    and inv["occurred_at"] <= pay["occurred_at"]):
                inv["paid"] = True
                break
    outstanding = [i for i in invoices if not i.get("paid") and i["days"] <= 120]

    signed = {c["project_id"] for c in contracts}
    in_flight = [q for q in quotes if q["days"] <= 45
                 and not any(c["project_id"] == q["project_id"]
                             and c["occurred_at"] > q["occurred_at"]
                             for c in contracts)]

    pipeline = {"negotiating": 0, "booked": 0}
    for p in conn.execute("SELECT status, money_cents FROM projects"
                          " WHERE archived_at IS NULL AND money_cents"):
        bucket = ("negotiating" if p["status"] in ("Lead", "Negotiating")
                  else "booked" if p["status"] not in ("Paid",) else None)
        if bucket:
            pipeline[bucket] += p["money_cents"]

    slim = lambda x: {k: x.get(k) for k in
                      ("project_name", "detail", "money_cents", "days", "ref")}
    return {
        "invoices_outstanding": [slim(i) for i in outstanding],
        "quotes_in_flight": [slim(q) for q in in_flight],
        "pipeline": pipeline,
    }


# ---- urgency ---------------------------------------------------------------
# Deadline proximity beats everything, then money at stake, then staleness.
# An unanswered inbound thread is a standing boost: someone is waiting on the owner.

def _urgency(deadline_days, money_cents, stale_days, inbound_waiting) -> float:
    score = 0.0
    if deadline_days is not None:
        if deadline_days < 0:
            score += 900 + min(300, -deadline_days * 50)
        elif deadline_days <= 1:
            score += 800
        elif deadline_days <= 3:
            score += 650
        elif deadline_days <= 7:
            score += 450
        elif deadline_days <= 14:
            score += 250
        else:
            score += 80
    if money_cents:
        score += min(120, math.log10(max(1, money_cents / 100)) * 25)
    if stale_days:
        score += min(150, stale_days * 10)
    if inbound_waiting:
        score += 200
    return round(score, 1)


@app.get("/api/dashboard")
def dashboard():
    conn = db.connect()
    db.init(conn)
    now = datetime.now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    two_days = day_start + timedelta(days=2)

    # --- events: today + tomorrow, plus the next shoot within 14 days -------
    events = [dict(r) for r in conn.execute(
        "SELECT * FROM events WHERE start_at >= ? AND start_at < ? ORDER BY start_at",
        (day_start.isoformat(), two_days.isoformat()))]
    for e in events:
        e["is_today"] = _parse(e["start_at"]).date() == now.date()

    next_shoot = conn.execute(
        "SELECT * FROM events WHERE is_shoot=1 AND start_at >= ? ORDER BY start_at LIMIT 1",
        (two_days.isoformat(),)).fetchone()
    next_shoot = dict(next_shoot) if next_shoot and _days_until(
        next_shoot["start_at"], now) <= 14 else None

    # Shoot-day card: today's shoot with stored forecast + recent prep threads
    weather = None
    w_raw = db.get_state(conn, "weather")
    if w_raw:
        try:
            weather = json.loads(w_raw)
        except ValueError:
            weather = None
    shoot_row = conn.execute(
        "SELECT * FROM events WHERE is_shoot=1 AND date(start_at)=date(?)"
        " ORDER BY start_at LIMIT 1", (now.isoformat(),)).fetchone()
    shoot_card = None
    if shoot_row:
        shoot_card = dict(shoot_row)
        shoot_card["weather"] = (weather["summary"] if weather and
                                 weather["date"] == shoot_row["start_at"][:10]
                                 else None)
        shoot_card["prep_threads"] = [dict(r) for r in conn.execute(
            "SELECT gmail_thread_id, subject, counterpart FROM threads"
            " WHERE project_id=? AND last_message_at > datetime('now','-7 day')"
            " ORDER BY last_message_at DESC LIMIT 3",
            (shoot_row["project_id"],))] if shoot_row["project_id"] else []
    next_shoot_weather = (weather["summary"] if weather and next_shoot and
                          weather["date"] == next_shoot["start_at"][:10]
                          else None)

    # --- projects with computed urgency -------------------------------------
    projects = []
    proj_rows = _active(conn)
    inbound_by_project = {
        r["project_id"]: r["id"] for r in conn.execute(
            "SELECT project_id, id FROM threads WHERE waiting_on='me'"
            " AND (snooze_until IS NULL OR snooze_until < ?)", (now.isoformat(),))
        if r["project_id"] is not None
    }
    open_actions = [dict(r) for r in conn.execute(
        "SELECT * FROM actions WHERE state='open'"
        " OR (state='snoozed' AND snooze_until < ?)", (now.isoformat(),))]
    actions_by_project: dict[int, list] = {}
    for a in open_actions:
        actions_by_project.setdefault(a["project_id"], []).append(a)

    for row in proj_rows:
        p = dict(row)
        acts = sorted(actions_by_project.get(p["id"], []),
                      key=lambda a: (a["due_at"] is None, a["due_at"] or ""))
        due_candidates = [d for d in
                          [_days_until(a["due_at"], now) for a in acts] +
                          [_days_until(p["deadline_at"], now)] if d is not None]
        deadline_days = min(due_candidates) if due_candidates else None
        stale_days = _days_since(p["last_touch_at"], now) or 0

        p["days_since_touch"] = stale_days
        p["stale"] = stale_days >= 10
        p["deadline_days"] = deadline_days
        p["inbound_waiting"] = p["id"] in inbound_by_project
        p["urgency"] = _urgency(deadline_days, p["money_cents"], stale_days,
                                p["inbound_waiting"])
        p["next_action"] = acts[0] if acts else None
        p["open_actions"] = acts
        p["activity"] = [dict(r) for r in conn.execute(
            "SELECT kind, summary, occurred_at, ref FROM activity"
            " WHERE project_id=? ORDER BY occurred_at DESC LIMIT 5",
            (p["id"],))]
        p["upcoming_events"] = [dict(r) for r in conn.execute(
            "SELECT title, location, start_at, is_shoot FROM events"
            " WHERE project_id=? AND start_at >= ? ORDER BY start_at LIMIT 3",
            (p["id"], now.isoformat()))]
        projects.append(p)

    projects.sort(key=lambda p: -p["urgency"])

    # --- top 3 actions -------------------------------------------------------
    proj_by_id = {p["id"]: p for p in projects}

    def action_score(a):
        d = _days_until(a["due_at"], now)
        proj = proj_by_id.get(a["project_id"])
        stale = proj["days_since_touch"] if proj else 0
        money = a["money_cents"] or (proj["money_cents"] if proj else None)
        return _urgency(d, money, stale, proj["inbound_waiting"] if proj else False)

    ranked = sorted(open_actions, key=action_score, reverse=True)
    top_actions = []
    for a in ranked[:12]:
        proj = proj_by_id.get(a["project_id"])
        top_actions.append({**a,
                            "project_name": proj["name"] if proj else None,
                            "due_days": _days_until(a["due_at"], now)})

    # --- waiting lists -------------------------------------------------------
    def thread_list(waiting_on):
        rows = conn.execute(
            "SELECT t.*, p.name AS project_name FROM threads t"
            " LEFT JOIN projects p ON p.id = t.project_id"
            " WHERE t.waiting_on=? AND t.is_noise=0"
            " AND (t.snooze_until IS NULL OR t.snooze_until < ?)"
            " ORDER BY t.last_message_at ASC", (waiting_on, now.isoformat()))
        out = []
        for r in rows:
            t = dict(r)
            t["days"] = _days_since(t["last_message_at"], now)
            out.append(t)
        return out

    waiting_on_them = thread_list("them")
    waiting_on_me = sorted(thread_list("me"), key=lambda t: -t["days"])

    delegations = [dict(r) for r in conn.execute(
        "SELECT d.*, p.name AS project_name FROM delegations d"
        " LEFT JOIN projects p ON p.id=d.project_id"
        " WHERE d.state IN ('suggested','running')"
        "    OR (d.state='done' AND d.completed_at > datetime('now','-3 day'))"
        " ORDER BY CASE d.state WHEN 'running' THEN 0 WHEN 'suggested' THEN 1"
        "          ELSE 2 END, d.id")]

    last_refresh = db.get_state(conn, "last_refresh_at")
    refresh_ok = db.get_state(conn, "last_refresh_ok", "1") == "1"
    account_email = db.get_state(conn, "account_email", "")
    money = _money_view(conn, now)
    digest = json.loads(db.get_state(conn, "digest") or "null")
    conn.close()

    return {
        "generated_at": now.isoformat(),
        "last_refresh_at": last_refresh,
        "refresh_ok": refresh_ok,
        "account_email": account_email,
        "events": events,
        "next_shoot": next_shoot,
        "next_shoot_weather": next_shoot_weather,
        "shoot_card": shoot_card,
        "weather": weather,
        "money": money,
        "digest": digest,
        "config": {"signature": CFG["signature"],
                   "studio_name": CFG["studio_name"]},
        "top_actions": top_actions,
        "projects": projects,
        "waiting_on_them": waiting_on_them,
        "waiting_on_me": waiting_on_me,
        "delegations": delegations,
    }


# ---- interactions ----------------------------------------------------------

class SnoozeBody(BaseModel):
    preset: str = "1d"


class QuickAddBody(BaseModel):
    text: str


def _snooze_until(preset: str, now: datetime) -> str:
    if preset == "3d":
        target = now + timedelta(days=3)
    elif preset == "next_week":
        target = now + timedelta(days=(7 - now.weekday()) or 7)  # next Monday
    else:
        target = now + timedelta(days=1)
    return target.replace(hour=7, minute=0, second=0, microsecond=0).isoformat()


@app.post("/api/actions/{action_id}/done")
def action_done(action_id: int):
    conn = db.connect()
    cur = conn.execute(
        "UPDATE actions SET state='done', completed_at=? WHERE id=?",
        (datetime.now().isoformat(), action_id))
    conn.commit(); conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404)
    return {"ok": True}


@app.post("/api/actions/{action_id}/snooze")
def action_snooze(action_id: int, body: SnoozeBody):
    until = _snooze_until(body.preset, datetime.now())
    conn = db.connect()
    cur = conn.execute(
        "UPDATE actions SET state='snoozed', snooze_until=? WHERE id=?",
        (until, action_id))
    conn.commit(); conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404)
    return {"ok": True, "until": until}


@app.post("/api/threads/{thread_id}/snooze")
def thread_snooze(thread_id: int, body: SnoozeBody):
    until = _snooze_until(body.preset, datetime.now())
    conn = db.connect()
    cur = conn.execute(
        "UPDATE threads SET snooze_until=? WHERE id=?", (until, thread_id))
    conn.commit(); conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404)
    return {"ok": True, "until": until}


@app.post("/api/projects/{project_id}/archive")
def project_archive(project_id: int):
    conn = db.connect()
    cur = conn.execute(
        "UPDATE projects SET archived_at=? WHERE id=?",
        (datetime.now().isoformat(), project_id))
    conn.commit(); conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404)
    return {"ok": True}


class PromoteBody(BaseModel):
    name: str


@app.post("/api/threads/{thread_id}/promote")
def thread_promote(thread_id: int, body: PromoteBody):
    """Turn a 'possible new project' thread into a real Lead on the board."""
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "empty name")
    conn = db.connect()
    t = conn.execute("SELECT * FROM threads WHERE id=?",
                     (thread_id,)).fetchone()
    if not t:
        conn.close()
        raise HTTPException(404)
    if conn.execute("SELECT 1 FROM projects WHERE name=?", (name,)).fetchone():
        conn.close()
        raise HTTPException(409, "a project with that name already exists")
    contacts = [{"name": t["counterpart"], "email": t["counterpart_email"]}]
    cur = conn.execute(
        "INSERT INTO projects (name, client, status, contacts, keywords,"
        " last_touch_at) VALUES (?,?, 'Lead', ?, '[]', ?)",
        (name, t["counterpart"], json.dumps(contacts), t["last_message_at"]))
    pid = cur.lastrowid
    conn.execute("UPDATE threads SET project_id=? WHERE id=?",
                 (pid, thread_id))
    conn.execute(
        "INSERT INTO activity (project_id, kind, summary, occurred_at, ref)"
        " VALUES (?, 'note', ?, ?, ?)",
        (pid, f"Tracked as project from thread: {t['subject']}"[:200],
         datetime.now().isoformat(timespec="seconds"), t["gmail_thread_id"]))
    conn.commit(); conn.close()
    return {"ok": True, "project_id": pid, "name": name}


# ---- delegations: "Go" hands a suggestion to headless Claude --------------

@app.post("/api/delegations/{delegation_id}/go")
def delegation_go(delegation_id: int):
    import subprocess
    import sys
    conn = db.connect()
    row = conn.execute("SELECT state, kind FROM delegations WHERE id=?",
                       (delegation_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404)
    if row["kind"] in ("cowork", "chat"):
        conn.close()
        raise HTTPException(400, "session-lane suggestions open in Claude,"
                                 " they don't run headless")
    if row["state"] == "running":
        conn.close()
        return {"ok": True, "state": "running"}
    conn.execute("UPDATE delegations SET state='running', result=NULL"
                 " WHERE id=?", (delegation_id,))
    conn.commit(); conn.close()
    root = Path(__file__).resolve().parent.parent
    subprocess.Popen(
        [sys.executable, "-m", "refresh.delegate", str(delegation_id)],
        cwd=root, start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True, "state": "running"}


@app.get("/api/delegations/{delegation_id}")
def delegation_get(delegation_id: int):
    conn = db.connect()
    row = conn.execute("SELECT id, state, result, title FROM delegations"
                       " WHERE id=?", (delegation_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)
    return dict(row)


@app.post("/api/delegations/{delegation_id}/restore")
def delegation_restore(delegation_id: int):
    conn = db.connect()
    cur = conn.execute(
        "UPDATE delegations SET state = CASE WHEN result IS NOT NULL"
        " THEN 'done' ELSE 'suggested' END"
        " WHERE id=? AND state='dismissed'", (delegation_id,))
    conn.commit(); conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404)
    return {"ok": True}


@app.post("/api/delegations/{delegation_id}/dismiss")
def delegation_dismiss(delegation_id: int):
    conn = db.connect()
    cur = conn.execute("UPDATE delegations SET state='dismissed' WHERE id=?",
                       (delegation_id,))
    conn.commit(); conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404)
    return {"ok": True}


@app.post("/api/delegations/{delegation_id}/restore")
def delegation_restore(delegation_id: int):
    conn = db.connect()
    cur = conn.execute(
        "UPDATE delegations SET state = CASE WHEN result IS NOT NULL"
        " THEN 'done' ELSE 'suggested' END WHERE id=? AND state='dismissed'",
        (delegation_id,))
    conn.commit(); conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404)
    return {"ok": True}


# ---- undo: every mutation above has an exact reverse -----------------------

@app.post("/api/actions/{action_id}/reopen")
def action_reopen(action_id: int):
    conn = db.connect()
    cur = conn.execute(
        "UPDATE actions SET state='open', snooze_until=NULL,"
        " completed_at=NULL WHERE id=?", (action_id,))
    conn.commit(); conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404)
    return {"ok": True}


@app.post("/api/threads/{thread_id}/unsnooze")
def thread_unsnooze(thread_id: int):
    conn = db.connect()
    cur = conn.execute(
        "UPDATE threads SET snooze_until=NULL WHERE id=?", (thread_id,))
    conn.commit(); conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404)
    return {"ok": True}


@app.post("/api/projects/{project_id}/unarchive")
def project_unarchive(project_id: int):
    conn = db.connect()
    cur = conn.execute(
        "UPDATE projects SET archived_at=NULL WHERE id=?", (project_id,))
    conn.commit(); conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404)
    return {"ok": True}


# Quick-add: "send the quote by Friday", "$1500 invoice to Bluefin".
# Naive on purpose — good enough to file most one-liners; anything unmatched
# lands on the board as an unassigned action rather than getting lost.
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday"]


def _parse_due(text: str, now: datetime) -> tuple[str | None, str]:
    m = re.search(
        r"\b(?:due|by)\s+(today|tomorrow|next week|"
        r"mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:rs(?:day)?)?|"
        r"fri(?:day)?|sat(?:urday)?|sun(?:day)?|"
        r"\d{1,2}/\d{1,2})\b", text, re.I)
    if not m:
        return None, text
    tok = m.group(1).lower()
    cleaned = (text[:m.start()] + text[m.end():]).strip(" ,.-")
    if tok == "today":
        target = now
    elif tok == "tomorrow":
        target = now + timedelta(days=1)
    elif tok == "next week":
        target = now + timedelta(days=(7 - now.weekday()) or 7)
    elif "/" in tok:
        month, day = (int(x) for x in tok.split("/"))
        target = now.replace(month=month, day=day)
        if target < now:
            target = target.replace(year=target.year + 1)
    else:
        idx = next(i for i, d in enumerate(WEEKDAYS) if d.startswith(tok[:3]))
        delta = (idx - now.weekday()) % 7 or 7
        target = now + timedelta(days=delta)
    return target.replace(hour=17, minute=0, second=0, microsecond=0).isoformat(), cleaned


@app.post("/api/quickadd")
def quickadd(body: QuickAddBody):
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "empty")
    now = datetime.now()
    conn = db.connect()

    due, cleaned = _parse_due(text, now)

    money = None
    m = re.search(r"\$\s?([\d,]+(?:\.\d{2})?)(k?)", cleaned, re.I)
    if m:
        amount = float(m.group(1).replace(",", ""))
        if m.group(2).lower() == "k":
            amount *= 1000
        money = int(amount * 100)

    project_id = None
    project_name = None
    lowered = cleaned.lower()
    for p in _active(conn):
        names = [p["name"].lower()] + \
            [k.lower() for k in json.loads(p["keywords"] or "[]")]
        if any(n and n in lowered for n in names):
            project_id = p["id"]
            project_name = p["name"]
            break

    conn.execute(
        "INSERT INTO actions (project_id, title, due_at, money_cents, source)"
        " VALUES (?,?,?,?, 'quickadd')",
        (project_id, cleaned, due, money))
    if project_id:
        conn.execute("UPDATE projects SET last_touch_at=? WHERE id=?",
                     (now.isoformat(), project_id))
    conn.commit(); conn.close()
    return {"ok": True, "project": project_name, "due_at": due,
            "title": cleaned}


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/")
def index():
    # no-cache: browsers must revalidate so design updates appear on reload
    return FileResponse(STATIC / "index.html",
                        headers={"Cache-Control": "no-cache"})


@app.middleware("http")
async def api_no_store(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(Exception)
async def quiet_errors(request, exc):
    # Fail quietly: the frontend keeps its last good state on any 5xx.
    return JSONResponse(status_code=500, content={"ok": False})
