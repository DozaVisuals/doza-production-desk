"""The full refresh: fetch Gmail + Calendar, classify, stamp the result.

This is what launchd runs hourly (step 5). Fail-quiet contract: any error
leaves the previous data untouched, sets last_refresh_ok=0, and logs to
data/refresh.log — the dashboard keeps serving the last good state.

  ./venv/bin/python -m refresh.refresh              incremental
  ./venv/bin/python -m refresh.refresh --deep       also re-scan 7 days back
"""
import argparse
import json
import sys
from datetime import datetime, timedelta

from app import db
from . import fetch_gcal, fetch_gmail, google_client
from .classify import classify_pending, log


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deep", action="store_true",
                    help="re-scan the past 7 days (the 7am fuller pass)")
    ap.add_argument("--backfill", type=int, metavar="DAYS")
    args = ap.parse_args()

    conn = db.connect()
    db.init(conn)

    # One refresh at a time: a slow classification must not overlap the next
    # hourly run. Second instance exits quietly — the next hour catches up.
    import fcntl
    lock = open(db.DATA_DIR / "refresh.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("refresh already running — skipping this pass")
        return 0

    started = datetime.now()
    ok = True
    try:
        creds = google_client.load_credentials()
        account_email = db.get_state(conn, "account_email", "")

        last = db.get_state(conn, "last_gmail_fetch_at")
        if args.backfill:
            since = started - timedelta(days=args.backfill)
        elif args.deep or not last:
            since = started - timedelta(days=7)
        else:
            since = datetime.fromisoformat(last) - timedelta(hours=2)

        gm = fetch_gmail.fetch(google_client.gmail_service(creds), conn,
                               since, account_email)
        log(f"fetch gmail: {gm}")
        gc = fetch_gcal.fetch(google_client.calendar_service(creds), conn)
        log(f"fetch gcal: {gc}")
        db.set_state(conn, "last_gmail_fetch_at",
                     started.isoformat(timespec="seconds"))

        totals = classify_pending(conn)
        log(f"classify: {totals}")

        # Re-scout delegations when the board changed (or none are pending).
        from . import delegate
        pending = conn.execute(
            "SELECT COUNT(*) c FROM delegations WHERE state='suggested'"
        ).fetchone()["c"]
        if totals["relevant"] or pending == 0:
            log(f"delegations: {delegate.suggest(conn)} suggested")

        from . import weather
        w = weather.update(conn)
        if w:
            log(f"weather: {w}")

        # Friday 4pm-or-later run writes the week-in-review, once per week.
        if started.weekday() == 4 and started.hour >= 16:
            week = started.strftime("%G-W%V")
            if db.get_state(conn, "digest_week") != week:
                from . import digest
                text = digest.generate(conn)
                if text:
                    db.set_state(conn, "digest", json.dumps({
                        "week": week,
                        "created_at": started.isoformat(timespec="seconds"),
                        "text": text}))
                    db.set_state(conn, "digest_week", week)
                    log("digest written")
    except Exception as e:
        ok = False
        log(f"REFRESH FAILED: {type(e).__name__}: {e}")

    db.set_state(conn, "last_refresh_ok", "1" if ok else "0")
    if ok:
        db.set_state(conn, "last_refresh_at",
                     started.isoformat(timespec="seconds"))
    conn.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
