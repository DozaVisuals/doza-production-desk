# Friday digest — production desk

[[OWNER_CONTEXT]]

Write the owner's end-of-week review from the JSON input file whose path
is given at the end of this prompt. It contains the last 7 days of their
production desk: per-project activity, status signals, actions completed,
actions still open, and both waiting lists.

Write exactly three short sections, in their desk's plain, calm voice:

**What moved** — real progress: deliveries, signatures, payments, shoots,
decisions. One line each, lead with the project name.

**What stalled** — things that went quiet or slipped: unanswered quotes,
aging invoices, projects untouched all week. Say how long.

**What's owed** — the sharpest few items for Monday: replies they owe,
deadlines inside the next week, money to chase.

Rules: ground every line in the input, never invent. ≤5 lines per section;
skip a section's weakest items rather than pad. No preamble, no sign-off,
no markdown headers other than the three bold section titles above. Total
under 180 words. Output ONLY the digest text.

Input file to read now:
