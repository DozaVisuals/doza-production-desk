"""SQLite schema and connection helpers.

One database file at data/doza.db. All paths resolve relative to the project
root so the server and refresh script work no matter what cwd they start in
(launchd starts processes at /).
"""
import os
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
# DOZA_DB overrides the database file (demo mode, tests)
DB_PATH = Path(os.environ.get("DOZA_DB", DATA_DIR / "doza.db"))

STATUSES = [
    "Lead", "Negotiating", "Booked", "Production",
    "Post", "Review", "Delivered", "Invoiced", "Paid",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    client        TEXT,
    kind          TEXT NOT NULL DEFAULT 'client',  -- client | product
    status        TEXT NOT NULL DEFAULT 'Lead',
    waiting       INTEGER NOT NULL DEFAULT 0,      -- ball is in someone else's court
    notes         TEXT,
    contacts      TEXT,      -- JSON [{"name":..., "email":...}] used to route threads
    keywords      TEXT,      -- JSON ["brand film", "bluefin"] extra routing hints
    money_cents   INTEGER,   -- primary amount at stake, if known
    deadline_at   TEXT,      -- project-level deadline, ISO date
    last_touch_at TEXT,      -- last relevant email/event/interaction
    archived_at   TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Next actions. Sourced from the classifier, quick-add, or seeds.
-- The classifier may INSERT suggestions but never sets state='done'.
CREATE TABLE IF NOT EXISTS actions (
    id           INTEGER PRIMARY KEY,
    project_id   INTEGER REFERENCES projects(id),
    title        TEXT NOT NULL,
    due_at       TEXT,
    money_cents  INTEGER,
    source       TEXT NOT NULL DEFAULT 'ai',       -- ai | quickadd | seed
    state        TEXT NOT NULL DEFAULT 'open',     -- open | done | snoozed
    snooze_until TEXT,
    ref          TEXT,                             -- source gmail thread id
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);

-- One row per relevant Gmail thread. Powers the waiting-on lists.
CREATE TABLE IF NOT EXISTS threads (
    id              INTEGER PRIMARY KEY,
    gmail_thread_id TEXT UNIQUE,
    project_id      INTEGER REFERENCES projects(id),
    subject         TEXT,
    snippet         TEXT,        -- latest relevant excerpt
    counterpart     TEXT,        -- who's on the other end, display name
    counterpart_email TEXT,
    last_direction  TEXT,        -- inbound | outbound (relative to the owner)
    last_message_at TEXT,
    waiting_on      TEXT,        -- them | me | none
    snooze_until    TEXT,
    is_noise        INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY,
    gcal_event_id TEXT UNIQUE,
    title         TEXT,
    location      TEXT,
    start_at      TEXT,
    end_at        TEXT,
    all_day       INTEGER NOT NULL DEFAULT 0,
    is_shoot      INTEGER NOT NULL DEFAULT 0,
    project_id    INTEGER REFERENCES projects(id),
    updated_at    TEXT
);

-- Per-project activity feed shown when a board card expands.
CREATE TABLE IF NOT EXISTS activity (
    id          INTEGER PRIMARY KEY,
    project_id  INTEGER REFERENCES projects(id),
    kind        TEXT,     -- email | event | signal | note
    summary     TEXT,
    occurred_at TEXT,
    ref         TEXT      -- gmail thread id / gcal event id
);

-- Status signals the classifier detects (quote_sent, contract_signed,
-- deposit_received, feedback_received, delivery_confirmed, invoice_sent,
-- payment_received). Kept separate so the later invoice-aging view can
-- read invoice_sent/payment_received without reparsing anything.
CREATE TABLE IF NOT EXISTS signals (
    id          INTEGER PRIMARY KEY,
    project_id  INTEGER REFERENCES projects(id),
    kind        TEXT NOT NULL,
    detail      TEXT,
    money_cents INTEGER,
    occurred_at TEXT,
    ref         TEXT
);

-- Staging area: raw Gmail threads exactly as fetched, before classification.
-- Step 3's classifier reads rows where classified_at IS NULL, writes its
-- conclusions to threads/actions/activity/signals, then stamps classified_at.
-- A thread that gains new messages gets classified_at reset to NULL.
CREATE TABLE IF NOT EXISTS raw_threads (
    gmail_thread_id TEXT PRIMARY KEY,
    subject         TEXT,
    last_message_at TEXT,
    last_direction  TEXT,     -- inbound | outbound, relative to the owner
    payload         TEXT NOT NULL,   -- compact JSON handed to the classifier
    fetched_at      TEXT,
    classified_at   TEXT
);

-- Work Claude can take off the owner's plate. A post-refresh pass suggests rows
-- (state='suggested'); the Go button runs them headless and the deliverable
-- lands in `result`. Claude only ever produces drafts here — nothing is sent.
CREATE TABLE IF NOT EXISTS delegations (
    id           INTEGER PRIMARY KEY,
    project_id   INTEGER REFERENCES projects(id),
    title        TEXT NOT NULL,      -- "Draft the reply to Dana"
    why          TEXT,               -- "unblocks the Aug 4 board meeting"
    kind         TEXT,               -- draft_reply | draft_doc | summarize | prep
    ref          TEXT,               -- source gmail thread id, if any
    prompt       TEXT NOT NULL,      -- self-contained task for the writer run
    state        TEXT NOT NULL DEFAULT 'suggested',
                 -- suggested | running | done | failed | dismissed
    result       TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);

-- Senders the classifier has learned to skip. Grows over time.
CREATE TABLE IF NOT EXISTS sender_blocklist (
    email    TEXT PRIMARY KEY,
    reason   TEXT,
    added_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Sync bookkeeping: gmail history id, last refresh timestamps, oauth state.
CREATE TABLE IF NOT EXISTS sync_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_actions_state   ON actions(state, due_at);
CREATE INDEX IF NOT EXISTS idx_threads_waiting ON threads(waiting_on, last_message_at);
CREATE INDEX IF NOT EXISTS idx_events_start    ON events(start_at);
CREATE INDEX IF NOT EXISTS idx_activity_proj   ON activity(project_id, occurred_at);
"""


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def get_state(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO sync_state(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
