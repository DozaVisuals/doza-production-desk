"""Privacy audit: sweep the git-tracked tree for YOUR real data.

Builds a term list from your live database — every counterpart name, email,
domain, project, client, contact, blocklisted sender, calendar-title token,
and money amount — plus optional extras from data/audit_terms.txt (one per
line; that file is gitignored, so your terms never ship). Then greps every
tracked file at HEAD and flags hits. Run before any push:

  ./venv/bin/python tools/audit_privacy.py
"""
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STOP = {"team", "events", "the", "and", "art", "media", "group", "info",
        "accounts", "payable", "procurement", "collection", "financial",
        "hello", "contract", "retainer", "series", "film", "email", "mark",
        "marks", "personal", "production", "shoot", "content", "launch",
        "noise", "starts", "delivery", "cancel", "check-in", "night",
        "support", "video", "motion", "filming", "edits", "assist", "matt",
        "documentary", "anniversary", "powers", "labor", "alex", "studio"}
ALLOWED_EMAILS_RE = re.compile(r"@(example\.com|users\.noreply\.github\.com)$")


def build_terms(db_path: Path) -> tuple[set, set]:
    terms, amounts = set(), set()
    if not db_path.exists():
        return terms, amounts
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    def add(t):
        t = (t or "").strip().lower()
        if len(t) >= 4 and t not in STOP:
            terms.add(t)

    for r in conn.execute("SELECT counterpart, counterpart_email FROM threads"):
        add(r["counterpart"])
        add(r["counterpart_email"])
        for w in (r["counterpart"] or "").replace("'", "").split():
            add(w)
        email = r["counterpart_email"] or ""
        if "@" in email:
            add(email.split("@")[1])
    for r in conn.execute("SELECT name, client, contacts FROM projects"):
        add(r["name"]); add(r["client"])
        for w in (r["name"] or "").split() + (r["client"] or "").split():
            if len(w) >= 5:
                add(w)
        for c in json.loads(r["contacts"] or "[]"):
            add(c.get("name")); add(c.get("email"))
    for r in conn.execute("SELECT email FROM sender_blocklist"):
        add(r["email"])
    for r in conn.execute("SELECT DISTINCT title FROM events"):
        for w in (r["title"] or "").split():
            if len(w) >= 5:
                add(w)
    for tbl in ("signals", "projects", "actions"):
        for r in conn.execute(f"SELECT DISTINCT money_cents c FROM {tbl}"
                              " WHERE money_cents IS NOT NULL"):
            amounts.add(str(r["c"]))
            amounts.add(f"${r['c'] // 100:,}")

    extras = ROOT / "data" / "audit_terms.txt"
    if extras.exists():
        for line in extras.read_text().splitlines():
            add(line)
    return terms, amounts


def main() -> int:
    terms, amounts = build_terms(ROOT / "data" / "doza.db")
    files = subprocess.run(["git", "ls-tree", "-r", "--name-only", "HEAD"],
                           cwd=ROOT, capture_output=True, text=True
                           ).stdout.split()
    findings = []
    email_re = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}")
    for f in files:
        raw = subprocess.run(["git", "show", f"HEAD:{f}"], cwd=ROOT,
                             capture_output=True).stdout
        try:
            txt = raw.decode("utf-8").lower()
        except UnicodeDecodeError:
            continue
        for term in terms:
            if term in txt:
                findings.append((term, f))
        for amt in amounts:
            if len(amt) >= 4 and amt.lower() in txt:
                findings.append((f"amount {amt}", f))
        for m in set(email_re.findall(txt)):
            if not ALLOWED_EMAILS_RE.search(m):
                findings.append((f"email {m}", f))

    print(f"swept {len(files)} tracked files against "
          f"{len(terms)} terms + {len(amounts)} amounts")
    if findings:
        print("FINDINGS (review each — generic words may be fine):")
        for t, f in sorted(set(findings)):
            print(f"  {t}  →  {f}")
        return 1
    print("CLEAN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
