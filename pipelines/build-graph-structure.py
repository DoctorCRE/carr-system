#!/usr/bin/env python3
"""
build-graph-structure.py — the SKELETON of the people graph.
(Replaces build-graph-hubs.py, which emitted flat attribute tags with no hierarchy.)

THE MODEL (Joe, 2026-07-25 — this is the spec, quoted):
  "LEADS all in one web, connected to PROSPECTS all in one web, connected to
   DEALS all in one web. On the sides you'd have DELL connected to his leads,
   prospects and deals, and JOE connected to his. VENDORS connected to the leads,
   prospects and deals that they are involved with. The Lead Board connected to
   all the leads that are sitting in the board."

        ⚑ sources ──▶ ① LEADS ──▶ ② CLIENTS & PROSPECTS ──▶ ③ DEALS
                          ▲               ▲                    ▲
        ★ JOE ────────────┴───────────────┴────────────────────┤
        ★ DELL ───────────┴───────────────┴────────────────────┘
                                 ◆ VENDORS ─┘

WHAT WENT WRONG IN v1, so it is not repeated
  v1 emitted flat attribute hubs (Firm, Market, Owner, Category…) with no
  hierarchy. The two Owner hubs pulled 493 edges and rendered as two giant stars
  with nothing to give them shape — a hairball. The fix applied then was to
  DELETE the owner hubs. That was wrong: owner poles are wanted. The real defect
  was the MISSING BACKBONE. With stage nodes present, an owner pole reads as a
  pole because every record also belongs to a stage, so the layout has two axes
  instead of one.

  Kept from v1: taxonomy buckets (Category, 47 members of "Banker/Lender") stay
  out — they are labels, not structure. Firm and source clusters stay in, capped,
  because they are small and they say something. Deal lane stays out: it is
  already colour.

NON-DESTRUCTIVE: writes only to Graph/hubs/. Never edits the entity notes.
"""
import sys, os, re, json, shutil, unicodedata
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.drive_recovery import RecoveryArgumentError, parse_recovery_controls
from lib.record_sources import (MODE_FILES, MODE_RECORDS, load_clients, load_deals_doc,
                                load_leads, load_vendors, resolve_mode, source_note)

try:
    _RECOVERY = parse_recovery_controls(sys.argv[1:], "graph-structure canonical record ingress")
except RecoveryArgumentError as exc:
    raise SystemExit(f"graph-structure: {exc}") from exc
if _RECOVERY.args:
    raise SystemExit(f"graph-structure: unexpected argument: {_RECOVERY.args[0]}")
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MODE = MODE_FILES if _RECOVERY.recovery else MODE_RECORDS
ROOT = str(_RECOVERY.vault) if _RECOVERY.recovery else REPO
GRAPH = os.path.join(REPO, "out", "recovery" if _RECOVERY.recovery else "", "Graph")
OUT = os.path.join(GRAPH, "hubs")

# ORDER 29a: the same four records build-graph-notes.py just used, read the same
# way. The skeleton has to agree with the notes it hangs off; two readers with two
# different sources would put a record in a pole its own note never mentioned.
VENDORS = load_vendors(ROOT, MODE)
LEADS = load_leads(ROOT, MODE)
CLIENTS = load_clients(ROOT, MODE)
DEALS = load_deals_doc(ROOT, MODE)["deals"]

def s(v): return str(v if v is not None else "").strip()

# ---------- node titles build-graph-notes.py actually wrote ----------
node_titles, kind_of = {}, {}
for sub in ("vendors", "leads", "clients", "deals"):
    d = os.path.join(GRAPH, sub)
    if not os.path.isdir(d): continue
    for fn in os.listdir(d):
        if fn.endswith(".md"):
            t = os.path.splitext(fn)[0]
            node_titles[t.lower()] = t
            kind_of[t] = sub

def node_for(*names, kind=None):
    for n in names:
        n = s(n)
        if not n: continue
        if kind and (k := f"{n} ({kind})".lower()) in node_titles:
            return node_titles[k]
        if n.lower() in node_titles: return node_titles[n.lower()]
    return None

# ---------- the skeleton ----------
STAGE = {"leads": "① LEADS", "clients": "② CLIENTS & PROSPECTS",
         "deals": "③ DEALS", "vendors": "◆ VENDORS"}
PIPELINE = ["① LEADS", "② CLIENTS & PROSPECTS", "③ DEALS"]

members = defaultdict(set)    # structure node -> entity nodes
flows   = defaultdict(set)    # structure node -> structure nodes

for t, sub in kind_of.items():
    members[STAGE[sub]].add(t)
for a, b in zip(PIPELINE, PIPELINE[1:]):
    flows[a].add(b)                       # leads → clients → deals, in sequence
flows["◆ VENDORS"].update(PIPELINE)       # vendors reach into all three

NOISE = {"", "n/a", "na", "none", "tbd", "(tbd)", "unknown", "-", "—",
         "unassigned", "(enrich)", "other", "other/not recorded"}
JUNK = re.compile(r"tbd|enrich|not recorded|^\(|^\?+$", re.I)

def clean(v):
    k = s(v)
    return "" if (k.lower() in NOISE or JUNK.search(k) or len(k) < 2) else k

def owner_pole(v):
    o = s(v).lower()
    if o.startswith("joe"): return "★ JOE"
    if o.startswith(("dell", "wayne")): return "★ DELL"
    if o.startswith(("shared", "both")): return "★ SHARED"
    return ""

def norm_firm(v):
    t = clean(v)
    if not t: return ""
    t = re.sub(r"[,.]?\s*(inc|llc|pllc|pa|pc|p\.a\.|l\.l\.c\.)\.?$", "", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()

def source_node(label):
    """A lead source is a real actor in the system — the Lead Board, the website,
    the SBDC, a named referrer. It gets a node and feeds the LEADS web."""
    lab = clean(label)
    if not lab or len(lab) > 45: return ""
    n = f"⚑ {lab}"
    flows[n].add("① LEADS")
    return n

for v in VENDORS:
    n = node_for(v.get("Name"), v.get("Company"))
    if not n: continue
    if (p := owner_pole(v.get("Owner"))): members[p].add(n)
    if (f := norm_firm(v.get("Company"))): members[f"🏢 {f}"].add(n)

for l in LEADS:
    n = node_for(l.get("Contact Name"), l.get("Practice"), kind="lead")
    if not n: continue
    if (p := owner_pole(l.get("Owner"))): members[p].add(n)
    if (f := norm_firm(l.get("Practice"))): members[f"🏢 {f}"].add(n)
    if (sn := source_node(l.get("Source Type"))): members[sn].add(n)
    det = clean(l.get("Source Detail (V-ID / event / referrer)"))
    if det and not re.match(r"^V-[A-Z]+-\d+$", det.upper()):
        if (sn := source_node(det)): members[sn].add(n)

for c in CLIENTS:
    n = node_for(c.get("Name"), c.get("Practice / Entity"))
    if not n: continue
    if (p := owner_pole(c.get("Owner"))): members[p].add(n)
    if (f := norm_firm(c.get("Practice / Entity"))): members[f"🏢 {f}"].add(n)
    if (r := clean(c.get("Referral Source"))) and len(r) < 45:
        members[f"⚑ {r}"].add(n)

for d in DEALS:
    n = node_for(d.get("name"), d.get("contact"), d.get("company"), kind="deal")
    if not n: continue
    if (p := owner_pole(d.get("owner"))): members[p].add(n)
    if (r := clean(d.get("referral"))) and len(r) < 45:
        members[f"⚑ {r}"].add(n)

if os.path.isdir(OUT): shutil.rmtree(OUT)
os.makedirs(OUT, exist_ok=True)

def safe(name):
    x = unicodedata.normalize("NFKD", name)
    return re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|#^\[\]{}]', "", x)).strip() or "unnamed"

def tag_for(t):
    if t.startswith("★"): return "pole"
    if t.startswith(("①", "②", "③", "◆")): return "stage"
    if t.startswith("🏢"): return "firm"
    return "source"

# Poles and stages are ALWAYS drawn — they are the skeleton, so their size is the
# point rather than a problem. Firm/source clusters must group >1 record and must
# not swallow the graph.
MAX = max(40, int(0.15 * len(node_titles)))
written = edges = 0
skipped = []
for title, mem in sorted(members.items()):
    tag = tag_for(title)
    if tag not in ("pole", "stage"):
        if len(mem) < 2: continue
        if len(mem) > MAX: skipped.append((len(mem), title)); continue
    # a pole carries its own owner tag so the anchor is the same colour as the
    # cloud of records hanging off it
    extra = {"★ JOE": ", owner-joe", "★ DELL": ", owner-dell", "★ SHARED": ", owner-shared"}.get(title, "")
    body = ["---", "type: structure", f"structure: {tag}", f"members: {len(mem)}",
            f"tags: [structure, struct-{tag}{extra}]", "---", "", f"# {title}", ""]
    if flows.get(title):
        body += ["*Feeds → " + ", ".join(f"[[{safe(x)}]]" for x in sorted(flows[title])) + "*", ""]
    body += [f"- [[{m}]]" for m in sorted(mem)] + [""]
    open(os.path.join(OUT, f"{safe(title)}.md"), "w", encoding="utf-8").write("\n".join(body))
    written += 1; edges += len(mem) + len(flows.get(title, ()))

# The legend is a REAL node, linked to the skeleton it explains. A README that
# floats alone is a note nothing points at — the fix is to connect it, not to
# hide it behind a .txt extension. Here it doubles as the key to the symbols.
legend = ["---", "type: legend", "tags: [structure, struct-legend]", "---", "",
          "# 📖 LEGEND — people graph", "",
          "**DERIVED. Never hand-edit `Graph/`.** Regenerate: `~/carr-system/run.sh graph`.", "",
          "| Symbol | Meaning |", "|---|---|",
          "| ① ② ③ | the pipeline, in sequence — leads become clients become deals |",
          "| ◆ | vendors, reaching into all three stages |",
          "| ★ | owner poles — Joe's records and Dell's |",
          "| ⚑ | where a lead came from: Lead Board, CARR Website, SBDC, a named referrer |",
          "| 🏢 | colleagues at the same firm |", "",
          "Colour: cyan = Joe's · orange = Dell's · white = the backbone · "
          "green = a source. Unowned records keep their record-type colour.", "",
          "## The skeleton", ""]
legend += [f"- [[{safe(t)}]]" for t in sorted(members) if tag_for(t) in ("pole", "stage")]
open(os.path.join(OUT, "📖 LEGEND — people graph.md"), "w", encoding="utf-8").write("\n".join(legend) + "\n")
written += 1

if skipped:
    print(f"\nskipped {len(skipped)} oversized non-structural clusters (>{MAX}):")
    for n, t in sorted(skipped, reverse=True): print(f"   {n:4d}  {t}")
poles  = sum(1 for t in members if tag_for(t) == "pole")
stages = sum(1 for t in members if tag_for(t) == "stage")
print(f"\nstructure: {written} nodes, {edges} edges  ({poles} poles, {stages} stages, "
      f"{written-poles-stages} firm/source clusters) | source: {source_note(MODE)}")
