#!/usr/bin/env python3
"""
build-graph-notes.py — derived Obsidian graph notes for the CARR AI vault.

Emits one small .md note per entity into <vault>/Graph/ so Obsidian's graph
shows the REAL system: vendors, worked leads, clients, deals — with every
relationship the data actually encodes rendered as a [[wikilink]] edge.
DERIVED VIEW: regenerate any time; never hand-edit Graph/. The truth is the
record layer (v_export_vendors / v_export_leads / v_export_clients /
v_export_deals), read live by default since ORDER 29a; the four generated files
(vendors.xlsx, lead-registry.xlsx, client-roster.xlsx, panhandle-team-deals.json)
are the same data exported, and `--files` still builds the graph from them.

Design bar (Joe, 2026-07-24): complete and truthful, legible via the semantic
palette (graph color groups live in .obsidian/graph.json, keyed to the tags
written here). Anomalies are the point: a vendor with no edges never referred;
a floating lead has no source; an unlinked referral string matched nobody.

Identity rule: edges link on exact IDs (V-###, C-###, L-###) or exact
case-insensitive full-name/company match ONLY. A partial or surname match is
never linked (standing rule: a lone surname never merges records) — the
unlinked text stays visible in the note as a "(no exact match)" line.

Usage: python3 build-graph-notes.py [--records|--files] [CARR_ROOT]
"""
import sys, os, re, json, glob, shutil, unicodedata
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.drive_recovery import RecoveryArgumentError, parse_recovery_controls
from lib.record_sources import (MODE_FILES, MODE_RECORDS, load_clients, load_deals_doc,
                                load_leads, load_party_links, load_ref_index, load_vendors,
                                resolve_mode, source_note)

# [loop #133] THE TWO PARTNERS ARE NODES OF THIS GRAPH, not just owner colours.
#
# THE DEFECT. v_party_graph held 31 edges and this script drew 24. Six of the
# seven it dropped were Joe's own `can_introduce` edges — Heather Lavigne,
# Josh Dunmore, Bruce Partridge, Justin Gale, Katherine Welby, Gary Trellis — so
# 100% of the "Joe can introduce you to X" class, which is the single most
# valuable edge class in the referral engine, was missing from the one surface
# built to show relationships. Joe is party P-1084 and carries no client, lead or
# vendor row (correctly — he is the agent, not his own prospect), so
# v_party_graph resolves his endpoint to NULL and the ref_node map below, built
# only from V-/C-/L- ids, could never contain him.
#
# WHY THE NAMES ARE HARDCODED, since that deserves an answer. The record layer
# has no partner flag: `actor` knows who Joe and Dell are, but carr_reader and
# carr_exporter have no grant on it, and there is nothing on party.kind that
# separates an agent from any other person. Two names in one constant, resolved
# against v_ref_index at run time and reported if they do not resolve, is honest
# about that; inventing a heuristic ("whoever sources the most can_introduce
# edges") would be a guess wearing the costume of a rule. When the record layer
# grows a real partner marker, read it here and delete this list.
#
# BOTH PARTNERS GET A NODE EVEN WITH ZERO EDGES. Dell has no logged intro edges
# today, and his node rendering as an isolated dot is the correct picture — it
# says his side of the network is uncaptured, which is exactly the class of
# anomaly the design bar above asks this graph to show.
PARTNER_NAMES = ("Joe Bookout", "Dell McCraney")

# Which role a party's ref should come from when it carries more than one. The
# same preference v_party_graph's own `party_ref` CTE uses (client, then vendor,
# then lead), so a name resolved here and a ref resolved by the view never
# disagree about which record represents a person. One real lead is the live
# case: party P-0384 carries BOTH C-155 and L-208.
# (Example sanitized 2026-08-06, ORDER 42b — the original named the real lead.)
REF_PREFERENCE = ("client", "vendor", "lead", "party")

try:
    _RECOVERY = parse_recovery_controls(sys.argv[1:], "graph-notes canonical record ingress")
except RecoveryArgumentError as exc:
    raise SystemExit(f"graph-notes: {exc}") from exc
if _RECOVERY.args:
    raise SystemExit(f"graph-notes: unexpected argument: {_RECOVERY.args[0]}")
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MODE = MODE_FILES if _RECOVERY.recovery else MODE_RECORDS
ROOT = str(_RECOVERY.vault) if _RECOVERY.recovery else REPO
OUT  = os.path.join(REPO, "out", "recovery" if _RECOVERY.recovery else "", "Graph")

VERT_TAGS = {"dental":"dental","dentist":"dental","oral":"dental","ortho":"dental",
             "medical":"medical","medicine":"medical","cardio":"medical","derm":"medical",
             "kidney":"medical","nephro":"medical","urgent":"medical","primary":"medical",
             "pediatric":"medical","psych":"medical","wound":"medical","dpc":"medical",
             "vet":"vet","chiro":"chiro","vision":"vision","opto":"vision","eye":"vision",
             "therapy":"therapy","pt ":"therapy","physical":"therapy","occupational":"therapy",
             "counsel":"therapy","behavioral":"therapy","wellness":"therapy","women":"therapy"}

def vert_tag(text):
    t = (text or "").lower()
    for k, v in VERT_TAGS.items():
        if k in t: return v
    return None

def sanitize(name, fallback_id=""):
    s = unicodedata.normalize("NFKD", str(name or fallback_id or "unnamed"))
    s = re.sub(r'[\\/:*?"<>|#^\[\]{}]', "", s).strip()
    return re.sub(r"\s+", " ", s) or (fallback_id or "unnamed")

def slug(name, taken, fallback_id=""):
    s = base = sanitize(name, fallback_id)
    n = 2
    while s.lower() in taken:
        s = f"{base} ({fallback_id or n})"; n += 1
    taken.add(s.lower())
    return s

def unique_node(name, kind, taken, fallback_id=""):
    """Node titles must be unique across ALL of Graph/, not per subfolder.

    Obsidian resolves [[Name]] by basename across the whole vault, so two notes
    sharing a basename in different folders make every link to that name
    ambiguous — Obsidian silently picks one and the other node becomes
    unreachable, rendering as an isolated dot no matter how many links point at
    the name. Measured 2026-07-25: 41 duplicate titles affecting 88 notes,
    because leads and deals each deduped against their own private set instead of
    a shared one.

    A client and their deal are legitimately separate records, so on collision we
    suffix with the record TYPE rather than a meaningless (2). Clients and vendors
    are registered first and keep the clean name as the canonical person record.
    """
    base = sanitize(name, fallback_id)
    s = base
    if s.lower() in taken:
        s, n = f"{base} ({kind})", 2
        while s.lower() in taken:
            s = f"{base} ({kind} {n})"; n += 1
    taken.add(s.lower())
    return s

def owner_tag(owner):
    """Owner is the biggest-picture separator in the graph (Joe, 2026-07-25):
    which half of the business a record belongs to. It lives in frontmatter but
    not in tags, and Obsidian colour groups key on tags — so emit it as one."""
    o = s(owner).strip().lower()
    if o.startswith("joe"):    return "owner-joe"
    if o.startswith("dell") or o.startswith("wayne"): return "owner-dell"
    if o.startswith("shared") or o.startswith("both"): return "owner-shared"
    return "owner-unassigned"

def fm(lines):
    return "---\n" + "\n".join(lines) + "\n---\n"

def esc(v):
    v = str(v if v is not None else "").strip()
    return f'"{v}"' if v and (":" in v or v.startswith(("'", '"', "[", "{"))) else (v or '""')

def s(v):  # cell → clean str
    return str(v).strip() if v not in (None, "") else ""

def datestr(v):
    v = s(v)
    return v.split(" ")[0] if v else ""

# ---------- load sources ----------
# Row order is load-bearing: node titles are deduped in the order records arrive,
# so a record that arrives second gets the "(kind)" suffix. The record path keeps
# the exporters' own ordering for exactly this reason.
vendors = load_vendors(ROOT, MODE)
leads   = load_leads(ROOT, MODE)
clients = load_clients(ROOT, MODE)
deals   = load_deals_doc(ROOT, MODE)["deals"]
# [ORDER 32] The intro graph's REAL edges (v_party_graph), or None in files mode.
party_links = load_party_links(MODE)
# [loop #133] Every ref the record layer carries, so an edge endpoint with no
# business ref can still be identified. None in files mode, same as above.
ref_index = load_ref_index(MODE)
# THE HAND-WRITTEN DOSSIER NAMES, and why records mode no longer defers to them.
#
# A client whose record names a detail file used to have its graph node TITLED
# after that file and no node FILE generated at all — "the hand note wins as the
# node", with the skip a few hundred lines below. That was right while the notes
# existed: a person owned the file and the graph linked to it.
#
# THE NOTES ARE GONE. They lived in DNA/Clients/prospects in the Drive vault and
# the 2026-08-19 cutoff retired them. Deferring to a file that cannot exist on
# any machine does not preserve a link; it guarantees a broken one. Measured
# 2026-08-23: 23 of 201 client rows name a detail file, so 23 client nodes were
# titles pointing at nothing, and the graph generated no page for any of them.
#
# So in records mode the dossier no longer wins and every client gets a real
# generated node. This DOES change those 23 node titles, from the dossier stem
# to the same slug every other client gets. That is a deliberate trade: a
# generated page a reader can open beats a title that resolves nowhere, and the
# links it replaces were already dead rather than about to be.
#
# FILES MODE IS UNCHANGED, because it means a recovery operator has deliberately
# pointed the root at a real vault, where those notes are present and are still
# the better node. It also no longer dies when the directory is absent: a
# missing tree there is a reported finding rather than a traceback.
detail_files: dict[str, str] = {}
if MODE != "records":
    _prospect_dir = os.path.join(ROOT, "DNA/Clients/prospects")
    if os.path.isdir(_prospect_dir):
        detail_files = {os.path.splitext(f)[0].lower(): os.path.splitext(f)[0]
                        for f in os.listdir(_prospect_dir) if f.endswith(".md")}
    else:
        print(f"graph: no prospect dossier directory at {_prospect_dir}; "
              "client nodes will use generated slugs", file=sys.stderr)

# ---------- index for exact-match linking ----------
taken: set[str] = set()
vend_by_id: dict[str, str] = {}
vend_by_name: dict[str, str] = {}
for v in vendors:
    vid = s(v.get("ID"))
    node = slug(s(v.get("Name")) or s(v.get("Company")), taken, vid)
    v["_node"] = node
    if vid: vend_by_id[vid.upper()] = node
    for key in (s(v.get("Name")), s(v.get("Company"))):
        if key: vend_by_name.setdefault(key.lower(), node)

cli_by_id: dict[str, str] = {}
cli_by_name: dict[str, str] = {}
for c in clients:
    cid = s(c.get("Client ID"))
    det = s(c.get("Detail File"))
    base = os.path.splitext(os.path.basename(det))[0].lower() if det else ""
    c["_detail"] = detail_files.get(base)          # hand note wins as the node
    c["_node"] = c["_detail"] or slug(s(c.get("Name")) or s(c.get("Practice / Entity")), taken, cid)
    # a hand-written dossier name bypasses slug(), so register it explicitly or
    # a later lead/deal can mint the same title and both become unreachable
    if c["_detail"]: taken.add(c["_detail"].lower())
    if cid: cli_by_id[cid.upper()] = c["_node"]
    for key in (s(c.get("Name")), s(c.get("Practice / Entity"))):
        if key: cli_by_name.setdefault(key.lower(), c["_node"])

def find_vendor(text):
    t = s(text)
    if not t: return None, ""
    m = re.search(r"V-[A-Z]+-\d+", t.upper())
    if m and m.group(0) in vend_by_id: return vend_by_id[m.group(0)], t
    return vend_by_name.get(t.lower()), t

def find_client(text):
    t = s(text)
    if not t: return None, ""
    m = re.search(r"C-\d+", t.upper())
    if m and m.group(0) in cli_by_id: return cli_by_id[m.group(0)], t
    return cli_by_name.get(t.lower()), t

# ---------- write ----------
# Temp-and-swap (orchestrator-lane corrective, 2026-07-25): build the whole tree in
# Graph.tmp first, swap in only on success — a mid-run crash (malformed source xlsx)
# used to leave Graph/ empty or half-built with no rollback.
FINAL_OUT = OUT
OUT = FINAL_OUT.rstrip("/\\") + ".tmp"
if os.path.isdir(OUT): shutil.rmtree(OUT)
# 'partners' added by loop #133. build-graph-structure.py enumerates only the
# four pipeline folders, so a partner note joins no stage pole and no owner pole —
# which is right: Joe is not a lead, a client or a deal. He is a node with edges.
for sub in ("vendors", "leads", "clients", "deals", "partners"): os.makedirs(os.path.join(OUT, sub))

# .txt not .md — Obsidian graphs every markdown file, so a README with no
# links renders as a floating dot in the middle of the graph.
open(os.path.join(OUT, "README.txt"), "w").write(
    "# Graph/ — derived Obsidian nodes (DO NOT HAND-EDIT)\n\n"
    "Generated by carr-system/pipelines/build-graph-notes.py from the record layer\n"
    "(v_export_vendors, v_export_leads, v_export_clients, v_export_deals — the same\n"
    "records vendors.xlsx, lead-registry.xlsx, client-roster.xlsx and\n"
    "panhandle-team-deals.json are exported from).\n"
    "Regenerate with `~/carr-system/run.sh graph`. Edits here are overwritten and never\n"
    "flow back to the data — fix the source, then regenerate.\n")

counts: dict[str, int] = {"vendors":0, "leads":0, "clients":0, "deals":0}

# [ORDER 32] node title -> the file that node was written to, so the intro-graph
# pass can append edges to notes this script actually owns. A node with no entry
# here (a hand-maintained client dossier outside Graph/) is NEVER written to —
# it is reported instead.
node_file: dict[str, str] = {}
lead_by_id: dict[str, str] = {}

for v in vendors:
    vid, node = s(v.get("ID")), v["_node"]
    tags = ["vendor", "network", owner_tag(v.get("Owner"))]
    vt = vert_tag(s(v.get("Vertical"))+" "+s(v.get("Category")))
    if vt: tags.append(vt)
    stage = s(v.get("Stage")).lower()
    tags.append("active" if s(v.get("Referral-active?")).lower() in ("yes","y","true","active") else "dormant")
    body = [fm([f"type: vendor", f"id: {esc(vid)}", f"category: {esc(v.get('Category'))}",
                f"owner: {esc(v.get('Owner') or 'unassigned')}", f"stage: {esc(v.get('Stage'))}",
                f"last-touch: {esc(datestr(v.get('Last Touch')))}",
                "tags: [" + ", ".join(tags) + "]"])]
    body.append(f"**{s(v.get('Name')) or node}** — {s(v.get('Company'))} · {s(v.get('Category'))} · {s(v.get('Territory'))} {s(v.get('State'))}\n")
    # [ORDER 32] The Links REGEX is now the files-mode fallback only. In records
    # mode the intro-graph pass below writes these edges from party_link — the
    # actual record — which is strictly more than this regex ever saw: it catches
    # C-/L-/T- refs and the exact-name matches too, and it carries the real edge
    # kind instead of labelling everything "knows".
    if party_links is None:
        links = []
        for m in re.findall(r"V-[A-Z]+-\d+", s(v.get("Links")).upper()):
            if m in vend_by_id and vend_by_id[m] != node: links.append(f"- knows [[{vend_by_id[m]}]]")
        if links: body.append("## Links\n" + "\n".join(sorted(set(links))) + "\n")
    p = os.path.join(OUT, "vendors", node + ".md")
    open(p, "w").write("\n".join(body))
    node_file[node] = p
    counts["vendors"] += 1

for c in clients:
    if c["_detail"]: continue                      # hand-maintained note is the node
    cid, node = s(c.get("Client ID")), c["_node"]
    status = s(c.get("Status")).lower()
    stat_tag = "won" if "won" in status else ("active" if any(k in status for k in ("active","negotiat","outreach","research")) else ("dormant" if "paus" in status else "cold"))
    tags = ["client", stat_tag, owner_tag(c.get("Owner"))]
    vt = vert_tag(s(c.get("Specialty / Type"))+" "+s(c.get("Deal Type")))
    if vt: tags.append(vt)
    body = [fm([f"type: client", f"id: {esc(cid)}", f"status: {esc(c.get('Status'))}",
                f"owner: {esc(c.get('Owner') or 'unassigned')}", f"vertical: {esc(c.get('Specialty / Type'))}",
                "tags: [" + ", ".join(tags) + "]"])]
    body.append(f"**{s(c.get('Name')) or node}** — {s(c.get('Practice / Entity'))} · {s(c.get('Specialty / Type'))} · {s(c.get('Market / Location'))}\n")
    links = []
    referral_node, ref_txt = find_vendor(c.get("Referral Source"))
    if referral_node: links.append(f"- referred by [[{referral_node}]]")
    elif ref_txt: links.append(f"- referral source: {ref_txt} (no exact match)")
    if links: body.append("## Links\n" + "\n".join(links) + "\n")
    p = os.path.join(OUT, "clients", node + ".md")
    open(p, "w").write("\n".join(body))
    node_file[node] = p
    counts["clients"] += 1

lead_taken: set[str] = set()
for l in leads:
    lid = s(l.get("Lead ID"))
    det = s(l.get("Detail File"))
    det_node = detail_files.get(os.path.splitext(os.path.basename(det))[0].lower()) if det else None
    cname = s(l.get("Contact Name"))
    if not cname or cname.startswith("("):          # placeholder, not a name
        cname = s(l.get("Practice"))
    node = unique_node(cname, "lead", taken, lid)
    stage = s(l.get("Stage")).lower()
    stat_tag = "active" if any(k in stage for k in ("active","engaged","outreach")) else ("won" if "won" in stage else ("dormant" if any(k in stage for k in ("paus","hold","do-not")) else "cold"))
    tags = ["lead", stat_tag, owner_tag(l.get("Owner"))]
    vt = vert_tag(s(l.get("Specialty")))
    if vt: tags.append(vt)
    body = [fm([f"type: lead", f"id: {esc(lid)}", f"stage: {esc(l.get('Stage'))}",
                f"owner: {esc(l.get('Owner') or 'unassigned')}", f"segment: {esc(l.get('Segment'))}",
                f"last-touch: {esc(datestr(l.get('Last Touch')))}",
                "tags: [" + ", ".join(tags) + "]"])]
    body.append(f"**{s(l.get('Contact Name')) or node}** — {s(l.get('Practice'))} · {s(l.get('Specialty'))} · {s(l.get('City/Market'))}\n")
    links = []
    src_node, src_txt = find_vendor(l.get("Source Detail (V-ID / event / referrer)"))
    if src_node: links.append(f"- sourced by [[{src_node}]]")
    elif s(l.get("Source Type")) or src_txt:
        links.append(f"- source: {s(l.get('Source Type'))} {src_txt}".rstrip() + ("" if not src_txt else " (no exact vendor match)" if not src_node else ""))
    if det_node: links.append(f"- client dossier: [[{det_node}]]")
    else:
        c_node, _ = find_client(l.get("Contact Name"))
        if c_node: links.append(f"- possibly same person as [[{c_node}]] (name match — confirm)")
    if links: body.append("## Links\n" + "\n".join(links) + "\n")
    p = os.path.join(OUT, "leads", node + ".md")
    open(p, "w").write("\n".join(body))
    node_file[node] = p
    if lid: lead_by_id[lid.upper()] = node
    counts["leads"] += 1

# Territory footprint (AL gulf coast + FL panhandle). Used ONLY to separate deals CARR
# represents directly (deep green) from out-of-market deals referred to another CARR agent
# for a referral fee (light green). A deal with no city recorded is neither — it is tagged
# #deal-unclassified and stays visibly unknown rather than being guessed into a lane.
TERRITORY = ["pensacola","navarre","freeport","gulf shores","destin","miramar","santa rosa","panama",
             "crestview","fort walton","mobile","dothan","tallahassee","foley","daphne","inlet beach",
             "loxley","30a","niceville","gulf breeze","pace","milton","spanish fort","fairhope",
             "enterprise","ozark","chipley","marianna","defuniak"]

def deal_lane(deal):
    """Prefer Salesforce's own Out-of-Market flag (written into the JSON as `lane`
    by diff-salesforce-deals.py). Fall back to the city heuristic only for a deal
    that has never been through a Salesforce read; a blank city stays unknown
    rather than being guessed into a lane."""
    lane = s(deal.get("lane")).lower()
    if lane in ("territory", "national"): return lane
    c = s(deal.get("city")).lower()
    if not c: return None
    return "territory" if any(t in c for t in TERRITORY) else "national"

deal_taken: set[str] = set()
for d in deals:
    node = unique_node(s(d.get("name")) or s(d.get("company")), "deal", taken, s(d.get("txn")))
    phase = s(d.get("phase")).lower()
    tags = ["deal", "won" if "closed" in phase or "won" in phase else ("cold" if "pending" in phase else "active"),
            owner_tag(d.get("owner"))]
    lane = deal_lane(d)
    tags.append({"territory":"deal-territory", "national":"deal-national"}.get(lane, "deal-unclassified"))
    vt = vert_tag(s(d.get("seg"))+" "+s(d.get("ptype")))
    if vt: tags.append(vt)
    lane_txt = {"territory":"CARR represents (full commission)",
                "national":"referred out to a local CARR agent (referral fee back to Dell; Joe's share rides on it)",
                None:"LANE UNKNOWN — no city recorded, needs a verdict"}[lane]
    body = [fm([f"type: deal", f"phase: {esc(d.get('phase'))}", f"owner: {esc(d.get('owner') or 'unassigned')}",
                f"segment: {esc(d.get('seg'))}", f"lane: {lane or 'unclassified'}",
                "tags: [" + ", ".join(tags) + "]"])]
    body.append(f"**{s(d.get('name'))}** — {s(d.get('company'))} · {s(d.get('seg'))} · {s(d.get('city'))} · phase: {s(d.get('phase'))}\n")
    body.append(f"*Lane: {lane_txt}*\n")
    links = []
    c_node, _ = find_client(d.get("company"))
    if not c_node: c_node, _ = find_client(d.get("contact"))
    if not c_node: c_node, _ = find_client(d.get("name"))
    if c_node: links.append(f"- client: [[{c_node}]]")
    referral_node, ref_txt = find_vendor(d.get("referral"))
    if referral_node: links.append(f"- referred by [[{referral_node}]]")
    elif ref_txt: links.append(f"- referral: {ref_txt} (no exact match)")
    own = s(d.get("owner")).lower()
    if links: body.append("## Links\n" + "\n".join(links) + "\n")
    open(os.path.join(OUT, "deals", node + ".md"), "w").write("\n".join(body))
    counts["deals"] += 1

# ---------- [loop #133] the partners, and the name->ref resolver ----------
# Built AFTER every pipeline note so the partner nodes cannot steal a title from a
# real record, and BEFORE the intro pass so their notes exist to be written to.
partner_stats: dict[str, Any] = {"nodes": 0, "unresolved": [], "ambiguous_names": [], "recovered": 0}
partner_ref_node: dict[str, str] = {}
name_to_ref: dict[str, str] = {}

if ref_index is not None:
    # ONE ENTRY PER NAME, AND ONLY WHEN THE NAME IS UNAMBIGUOUS. 30-plus names in
    # this book belong to two different live parties (one real client's name is
    # C-006 and C-050's survivor; another real name is two different bankers),
    # and the standing identity rule says a name that could mean two people links
    # to neither. So the index counts DISTINCT PARTY, not distinct row: one real
    # lead holds two refs (C-155 client, L-208 lead) on ONE party and is
    # therefore unambiguous, which is exactly the seventh dropped edge.
    # Tombstones are excluded outright — a merged row must never be the thing a
    # name resolves to. (Examples sanitized 2026-08-06, ORDER 42b — the
    # originals named real clients/leads/vendors.)
    _live_by_name: dict[str, list[dict[str, Any]]] = {}
    for r in ref_index:
        if r["merged"] or not r["party_id"]:
            continue
        _live_by_name.setdefault((r["name"] or "").strip().lower(), []).append(r)

    def name_ref(name):
        """The one live ref this exact full name means, or None. Never a guess."""
        rows = _live_by_name.get(s(name).lower())
        if not rows:
            return None
        if len({r["party_id"] for r in rows}) != 1:
            partner_stats["ambiguous_names"].append(s(name))
            return None
        rows = sorted(rows, key=lambda r: (REF_PREFERENCE.index(r["kind"])
                                           if r["kind"] in REF_PREFERENCE else 99, r["ref"]))
        return rows[0]["ref"]

    for pname in PARTNER_NAMES:
        pref = name_ref(pname)
        if not pref:
            partner_stats["unresolved"].append(pname)
            continue
        node = unique_node(pname, "partner", taken, pref)
        body = [fm([f"type: partner", f"id: {esc(pref)}", f"owner: {esc(pname)}",
                    "tags: [" + ", ".join(["partner", "network", owner_tag(pname)]) + "]"]),
                f"**{pname}** — CARR agent · healthcare tenant and buyer representation\n",
                "*This is the PERSON, not the ★ JOE / ★ DELL owner pole. The pole groups every\n"
                "record that partner owns; this node carries the intro-graph edges the partner\n"
                "is an endpoint of — the \"I can introduce you to X\" relationships.*\n"]
        p = os.path.join(OUT, "partners", node + ".md")
        open(p, "w").write("\n".join(body))
        node_file[node] = p
        partner_ref_node[pref.upper()] = node
        partner_stats["nodes"] += 1
else:
    def name_ref(name):
        return None

# ---------- [ORDER 32] the intro graph, rendered from party_link ----------
# Joe: "i like visualized data the most." This is the one graph with revenue on
# it — who can introduce whom — and until now it was invisible: the notes carried
# a regex over the Links PROSE that could only ever see vendor-to-vendor V-refs,
# so the intro that matters (a vendor reaching a CLIENT) drew no line at all.
#
# EXACT-MATCH-ONLY, and here that costs nothing: these edges are already record
# rows keyed by ref, so the mapping is ref -> the node title this script just
# wrote. No name matching, no partial matching, no surname anywhere. An endpoint
# whose ref names no node in Graph/ is COUNTED AND NAMED, never dropped quietly —
# an intro path that silently vanishes from the picture is worse than one the
# picture admits it cannot draw.
#
# The edges are appended AFTER every note exists, because an edge can point at a
# lead or a client whose node had not been minted when its own note was written.
intro_stats: dict[str, Any] = {"edges": 0, "rendered": 0, "unmapped_from": [], "unmapped_to": [],
                               "unwritable": []}
if party_links is not None:
    ref_node: dict[str, str] = {}
    ref_node.update({k.upper(): v for k, v in vend_by_id.items()})
    ref_node.update({k.upper(): v for k, v in cli_by_id.items()})
    ref_node.update({k.upper(): v for k, v in lead_by_id.items()})
    ref_node.update(partner_ref_node)

    def endpoint(ref, name):
        """(node title or None, the ref it resolved to). Ref first, exact name second.

        [loop #133] A NULL ref is not "no such party" — v_party_graph simply has no
        business ref to hand over, for one of two reasons, and both are recoverable
        here without loosening the identity rule one inch:
          · the party is a bare party (Joe, P-1084) — name_ref finds it, and the
            partner pass above has already minted the node;
          · the link still points at a MERGED party (P-0365 -> P-0384, Okafor) — the
            tombstone is excluded from the name index, so the exact name resolves
            to the SURVIVOR's preferred ref (C-155) and the edge lands on the one
            node that record has. The already-live duplicate of that same edge
            dedups against it in the set() below.
        Still exact-match-only: full name, case-insensitive, one live party or
        nothing. No partial match, no surname, no scoring.
        """
        r = s(ref).upper()
        if r:
            return ref_node.get(r), r
        alt = name_ref(name)
        if alt:
            node = ref_node.get(alt.upper())
            if node:
                partner_stats["recovered"] += 1
            return node, alt.upper()
        return None, ""

    by_source: dict[str, list[str]] = {}
    for e in party_links:
        intro_stats["edges"] += 1
        fnode, fr = endpoint(e["from_ref"], e["from_name"])
        tnode, to = endpoint(e["to_ref"], e["to_name"])
        if not fnode:
            # Name it by whatever identifies it — the ref when there is one, the
            # name when the null endpoint could not be resolved either. "->" with
            # a blank on the left told nobody anything.
            intro_stats["unmapped_from"].append(fr or f"(no ref) {s(e['from_name'])}"); continue
        if not tnode:
            intro_stats["unmapped_to"].append(
                f"{fr}->{to or '(no ref) ' + s(e['to_name'])}"); continue
        if fnode == tnode:
            continue                                    # a self-link draws nothing
        if fnode not in node_file:
            # A hand-maintained client dossier is the node, and this script does
            # not own that file. Reported, never edited.
            intro_stats["unwritable"].append(f"{fr} ({fnode})"); continue
        by_source.setdefault(fnode, []).append(
            f"- {s(e['kind']).replace('_', ' ')} → [[{tnode}]]"
            + (f"  <!-- {to} -->" if to else ""))

    for fnode, lines in by_source.items():
        with open(node_file[fnode], "a") as fh:
            fh.write("\n## Intro graph\n" + "\n".join(sorted(set(lines))) + "\n")
        intro_stats["rendered"] += len(set(lines))

# success — swap the finished tree into place
if os.path.isdir(FINAL_OUT): shutil.rmtree(FINAL_OUT)
os.rename(OUT, FINAL_OUT)
print("Graph notes written:", counts, "→", FINAL_OUT, "| source:", source_note(MODE))
if party_links is None:
    print("Intro graph: SKIPPED — files mode has no party_link twin; "
          "vendor notes carry the legacy Links regex instead.")
else:
    print(f"Intro graph: {intro_stats['edges']} party_link edge(s) · "
          f"{intro_stats['rendered']} rendered as wikilinks · "
          f"unmapped source refs {len(intro_stats['unmapped_from'])} "
          f"{sorted(set(intro_stats['unmapped_from'])) or ''} · "
          f"unmapped targets {len(intro_stats['unmapped_to'])} "
          f"{sorted(set(intro_stats['unmapped_to'])) or ''} · "
          f"source note not owned by Graph/ {len(intro_stats['unwritable'])} "
          f"{sorted(set(intro_stats['unwritable'])) or ''}")
    # [loop #133] The edge count above is now the WHOLE view (31), not the 24 the
    # loader used to pre-filter, so this line has to say what happened to the
    # difference or the totals stop reconciling.
    print(f"Partners: {partner_stats['nodes']} node(s) · "
          f"{partner_stats['recovered']} edge endpoint(s) with no business ref recovered "
          f"by exact name"
          + (f" · UNRESOLVED partner(s) {sorted(partner_stats['unresolved'])}"
             if partner_stats["unresolved"] else "")
          + (f" · names too ambiguous to resolve "
             f"{sorted(set(partner_stats['ambiguous_names']))}"
             if partner_stats["ambiguous_names"] else ""))
