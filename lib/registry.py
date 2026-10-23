"""Canonical lead export columns and ID formatting for registry audits."""
import re

# The canonical Registry vocabulary, in order. A write that cannot produce every
# one of these halts. Lane 3 (the last three) is the reason this list is here.
REGISTRY_COLUMNS = [
    "Lead ID", "Date In", "Owner", "Stage", "Segment", "Contact Name",
    "Practice", "Specialty", "City/Market", "County", "Email", "Phone",
    "Source Type", "Source Detail (V-ID / event / referrer)", "Report-Back Due",
    "Drip Campaign", "Drip Added", "Next Action", "Next Action Date",
    "Last Touch", "SF Deal", "Detail File", "Notes",
    "Est-Lease-Event", "Event-Source", "Event-Confidence",
]

# IDs deliberately taken out of service. Never reissued, never reported as loss.
#   L-162 — Dell's joint-test row, 2026-07-09; deleted same day after Joe
#           confirmed T6 (twin live). Intake Log: "L-162 stays retired".
#   L-209, L-210 — never real leads; correlate with a documented Neon
#           database-branch rehearsal that never touched production. Zero
#           creation/deletion trace in the production event tables. Joe
#           approved retirement 2026-08-14 (registry-audit "[5] ids" gap).
#   L-212, L-213, L-220 — never real leads; no trace in production, the repo,
#           or the vault. Joe approved retirement 2026-08-14 (registry-audit
#           "[5] ids" gap).
RETIRED_IDS = {162, 209, 210, 212, 213, 220}

ID_RE = re.compile(r"^L-0*(\d+)$")


def _s(v):
    return str(v if v is not None else "").strip()


def id_num(v):
    """'L-164' -> 164. None for anything that is not a well-formed Lead ID."""
    m = ID_RE.match(_s(v))
    return int(m.group(1)) if m else None


def fmt_id(n):
    return "L-%03d" % n
