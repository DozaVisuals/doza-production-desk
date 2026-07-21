"""Fetch CLI — step 2 of the pipeline (Gmail + Calendar → SQLite staging).

  ./venv/bin/python -m refresh.fetch --backfill 45     first run
  ./venv/bin/python -m refresh.fetch                   incremental since last run
  ./venv/bin/python -m refresh.fetch --dump out.json   also dump unclassified
                                                       threads for inspection

Step 3 adds classification on top; step 5 schedules the whole thing.
"""
import argparse
import json
import sys
from datetime import datetime, timedelta

from app import db
from . import fetch_gcal, fetch_gmail, google_client

OVERLAP_HOURS = 2      # re-scan a little history so nothing slips between runs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", type=int, metavar="DAYS",
                    help="fetch this many days of history (first run: 45)")
    ap.add_argument("--max", type=int, dest="max_threads",
                    help="cap thread count (for testing)")
    ap.add_argument("--dump", metavar="PATH",
                    help="write unclassified raw threads to a JSON file")
    args = ap.parse_args()

    conn = db.connect()
    db.init(conn)

    try:
        creds = google_client.load_credentials()
    except google_client.NotAuthorized as e:
        print(f"✗ {e}")
        return 1

    account_email = db.get_state(conn, "account_email", "")
    last = db.get_state(conn, "last_gmail_fetch_at")
    if args.backfill:
        since = datetime.now() - timedelta(days=args.backfill)
    elif last:
        since = datetime.fromisoformat(last) - timedelta(hours=OVERLAP_HOURS)
    else:
        print("First run — defaulting to --backfill 45.")
        since = datetime.now() - timedelta(days=45)

    started = datetime.now()
    print(f"Fetching Gmail threads since {since:%Y-%m-%d %H:%M} …")
    gm = fetch_gmail.fetch(google_client.gmail_service(creds), conn,
                           since, account_email, args.max_threads)
    print(f"  {gm['listed']} threads listed · {gm['stored']} new · "
          f"{gm['updated']} updated · {gm['skipped_noise']} noise · "
          f"{gm['errors']} errors")

    print("Fetching Calendar events (−7d … +60d) …")
    gc = fetch_gcal.fetch(google_client.calendar_service(creds), conn)
    print(f"  {gc['events']} events across {gc['calendars']} calendars")

    db.set_state(conn, "last_gmail_fetch_at",
                 started.isoformat(timespec="seconds"))

    if args.dump:
        rows = conn.execute(
            "SELECT payload FROM raw_threads WHERE classified_at IS NULL"
            " ORDER BY last_message_at DESC").fetchall()
        with open(args.dump, "w") as f:
            json.dump([json.loads(r["payload"]) for r in rows], f, indent=1)
        print(f"  dumped {len(rows)} unclassified threads → {args.dump}")

    pending = conn.execute("SELECT COUNT(*) c FROM raw_threads"
                           " WHERE classified_at IS NULL").fetchone()["c"]
    print(f"Done. {pending} threads await classification (step 3).")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
