"""Fetch Google Calendar events into the events table.

Window sync: the fetch window (7 days back → 60 days forward) is wiped and
rewritten each run, so cancelled events disappear and edits take effect.
No classifier needed — shoot detection and project routing are heuristics.
"""
import json
import re
from datetime import datetime, timedelta

SHOOT_RE = re.compile(
    r"\b(shoot|shooting|filming|on[- ]set|production day|photo day)\b", re.I)


def _project_matchers(conn) -> list[tuple[int, list[str]]]:
    out = []
    for p in conn.execute(
            "SELECT id, name, contacts, keywords FROM projects"
            " WHERE archived_at IS NULL"):
        needles = [p["name"].lower()]
        needles += [k.lower() for k in json.loads(p["keywords"] or "[]")]
        needles += [c.get("name", "").lower()
                    for c in json.loads(p["contacts"] or "[]") if c.get("name")]
        out.append((p["id"], [n for n in needles if len(n) >= 4]))
    return out


def _route(matchers, *texts) -> int | None:
    hay = " ".join(t.lower() for t in texts if t)
    for pid, needles in matchers:
        if any(n in hay for n in needles):
            return pid
    return None


def fetch(service, conn, days_back: int = 7, days_fwd: int = 60) -> dict:
    now = datetime.now().astimezone()
    t_min = now - timedelta(days=days_back)
    t_max = now + timedelta(days=days_fwd)
    matchers = _project_matchers(conn)

    calendars = service.calendarList().list().execute().get("items", [])
    use = [c for c in calendars
           if c.get("primary") or c.get("selected")]

    rows = []
    for cal in use:
        page_token = None
        while True:
            resp = service.events().list(
                calendarId=cal["id"], singleEvents=True, orderBy="startTime",
                timeMin=t_min.isoformat(), timeMax=t_max.isoformat(),
                maxResults=250, pageToken=page_token).execute()
            for e in resp.get("items", []):
                if e.get("status") == "cancelled":
                    continue
                start = e.get("start", {})
                end = e.get("end", {})
                all_day = "date" in start
                start_at = start.get("dateTime", start.get("date"))
                end_at = end.get("dateTime", end.get("date"))
                if not start_at:
                    continue
                # All-day events arrive as bare dates; normalize to full ISO
                # so day-window comparisons bucket them correctly.
                if all_day:
                    start_at += "T00:00:00"
                    end_at = (end_at or start_at[:10]) + "T00:00:00"
                title = e.get("summary", "(untitled)")
                loc = e.get("location", "")
                desc = e.get("description", "")
                rows.append((
                    f'{cal["id"]}:{e["id"]}', title, loc,
                    start_at[:19], (end_at or "")[:19], int(all_day),
                    int(bool(SHOOT_RE.search(f"{title} {desc}"))),
                    _route(matchers, title, loc, desc),
                    now.isoformat(timespec="seconds")))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    conn.execute("DELETE FROM events WHERE start_at >= ? AND start_at <= ?",
                 (t_min.isoformat()[:19], t_max.isoformat()[:19]))
    conn.executemany(
        "INSERT OR REPLACE INTO events (gcal_event_id, title, location,"
        " start_at, end_at, all_day, is_shoot, project_id, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return {"calendars": len(use), "events": len(rows)}
