#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Deal Room generator v2 (2026-07-22). A working surface, not a report mirror.
Source of truth = the deal record (`v_export_deals`), read live by default since
ORDER 29a; panhandle-team-deals.json is the same data exported to a file, and
`--files` builds from it instead (schema v2: activity + next_step + key_dates + docs).
Salesforce stays the paperwork rail (ETLs, signature agreements, corporate invoicing);
this room is where the deals get worked: triage queue, phase board, per-deal detail.
Usage:  python3 build-deal-room.py [--records|--files] [deals.json] [out.html]
Defaults (when run from DNA/Team/live-boards/): JSON at ../../Deal Management/panhandle-team-deals.json,
HTML written next to this script as deal-room-panhandle.html.
Records mode needs psycopg and the exporter credential; without either it says so
and falls back to the file, so the room never goes dark.
Doctrine: DNA/ux-doctrine.md (all 11 laws) + DNA/writing-rules.md on every visible string.
"""
import json, os, sys

# ORDER 29a (2026-07-31): the room reads the RECORD by default — the same
# `v_export_deals` the deals file is exported from — rather than the file the
# exporter wrote. The file keeps being generated, and `--files` still builds from
# it byte for byte, so no other reader loses anything; this only stops the room
# from deriving today's queue from an export that may have failed hours ago.
# Both modes use the same template transforms; whole-output parity requires
# comparing them on the same data.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from lib.drive_recovery import RecoveryArgumentError, parse_recovery_controls
from lib.record_sources import MODE_FILES, MODE_RECORDS, load_deals_doc, source_note

try:
    CONTEXT = parse_recovery_controls(sys.argv[1:], "deal-room file export")
except RecoveryArgumentError as exc:
    sys.exit(f"deal-room: {exc}")
if CONTEXT.args:
    sys.exit(f"deal-room: unexpected surface argument(s): {' '.join(CONTEXT.args)}")

MODE = MODE_FILES if CONTEXT.recovery else MODE_RECORDS
ROOT = str(CONTEXT.vault) if CONTEXT.recovery else REPO
JSON_PATH = os.path.join(ROOT, "DNA", "Deal Management", "panhandle-team-deals.json")
OUT_DIR = os.path.join(REPO, "out", "recovery" if CONTEXT.recovery else "")
OUT_PATH = os.path.join(OUT_DIR, "deal-room-panhandle.html")

# File mode reads exactly the path it was handed, as it always has. Records mode
# ignores that path: there is no file in it to read, only the view behind it.
data = load_deals_doc(None, MODE) if MODE == MODE_RECORDS else json.load(open(JSON_PATH))
deals = data["deals"]
STAMP = data.get("captured", "")

# Strip fields the surface must never show (placeholders + internal IDs stay in the files).
CLEAN = []
for d in deals:
    CLEAN.append({k: d.get(k) for k in [
        "name","company","contact","phone","email","phase","owner","city","referral",
        "txn","ptype","etl","created","seg","note","tier","carr_status",
        "activity","next_step","key_dates","docs","outcome","closed","won_value"]})

# ---- what the record says, read by judgment (2026-09-18) ----------------
# The room already shows every field the record holds. What it could not show
# is a READING of them: how far a deal has actually moved, who owes the next
# move, and whether a silence means anything. ops/jev_deal_read.py answers
# those three, and refuses to answer for a deal whose record is too thin --
# which, measured on 2026-09-18, is 67 of the 74 live deals. That refusal is
# the point and it is rendered, not hidden: a reader must be able to see that
# the room is quiet about a deal because the record is empty.
#
# EVERY FAILURE PATH LANDS ON THE SAME FLOOR. No credential, no network, a
# refused request, a view that moved: READ stays empty and the room builds
# exactly as it did before this block existed. Set CARR_DEAL_ROOM_NO_READ=1 to
# skip it deliberately.
READ, READ_SUMMARY, READ_NOTE = {}, {}, ""
if MODE == MODE_RECORDS and not os.environ.get("CARR_DEAL_ROOM_NO_READ"):
    try:
        from ops import jev_deal_read
        _results = jev_deal_read.read_deals()
        READ_SUMMARY = jev_deal_read.summarise(_results)
        _seen: dict[str, int] = {}
        for _r in _results:
            _seen[_r["name"]] = _seen.get(_r["name"], 0) + 1
        for _r in _results:
            # A duplicated deal name cannot be matched to one card, so it is
            # left unread rather than attached to whichever card sorts first.
            if _seen[_r["name"]] != 1:
                continue
            READ[_r["name"]] = {
                "judged": _r.get("judged"),
                "reason": _r.get("reason"),
                "movement": _r.get("movement"),
                "rung": _r.get("movement_rung"),
                "rungs": _r.get("movement_rungs"),
                "level": _r.get("movement_level"),
                "confidence": _r.get("movement_confidence"),
                "waiting_on": _r.get("waiting_on"),
                "waiting_confidence": _r.get("waiting_on_confidence"),
                "silence_is_bad": _r.get("silence_is_bad"),
                "quiet_days": _r.get("days_since_record_touched"),
                "evidence_chars": _r.get("evidence_chars"),
            }
    except Exception as _exc:
        READ, READ_SUMMARY = {}, {}
        READ_NOTE = f"The reading did not run: {_exc}"

for _d in CLEAN:
    _d["read"] = READ.get(_d.get("name"))

PAYLOAD = json.dumps(CLEAN, ensure_ascii=False).replace("</", "<\\/")
READ_PAYLOAD = json.dumps(
    {"summary": READ_SUMMARY, "note": READ_NOTE}, ensure_ascii=False
).replace("</", "<\\/")

CSS = r"""
:root{--bg:#0e2236;--panel:#153350;--panel2:#183b5c;--line:#27506f;--ink:#f4f8fc;--muted:#9db3c8;
--orange:#ec7a2c;--orange-soft:#f3a05f;--green:#41c08a;--amber:#e8b54a;--red:#e86a6a;--blue:#4a90d9;
--cold:#6f88a0;--chip:#0b1b2b;}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}*{transition:none!important;animation:none!important}}
body{background:var(--bg);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
color:var(--ink);-webkit-font-smoothing:antialiased}
button{font:inherit;color:inherit;background:none;border:none;cursor:pointer}
button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--orange);outline-offset:2px;border-radius:6px}
a{color:var(--orange-soft)}
.wrap{max-width:1220px;margin:0 auto;padding:24px 20px 70px}
.kicker{display:inline-block;background:var(--orange);color:#12253a;font-weight:800;font-size:11px;
letter-spacing:.12em;text-transform:uppercase;padding:5px 10px;border-radius:5px;margin-bottom:12px}
h1{font-size:27px;letter-spacing:-.01em}
.lede{color:var(--muted);font-size:13px;margin-top:8px;max-width:840px;line-height:1.55}
.lede b{color:var(--orange-soft)}
.tiles{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:22px 0 6px}
.tile{background:linear-gradient(160deg,var(--panel2),var(--panel));border:1px solid var(--line);
border-radius:11px;padding:14px;text-align:left}
.tile .n{font-size:26px;font-weight:800;line-height:1}
.tile .l{font-size:10.5px;color:var(--muted);margin-top:7px;text-transform:uppercase;letter-spacing:.06em}
.tile.warn .n{color:var(--red)}.tile.amber .n{color:var(--amber)}
@media(max-width:820px){.tiles{grid-template-columns:repeat(2,1fr)}}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:18px 0 6px}
.tabs{display:flex;gap:6px;border-bottom:1px solid var(--line);margin-top:14px}
.tab{color:var(--muted);font-size:13.5px;font-weight:650;padding:11px 13px;min-height:44px;
border-bottom:2px solid transparent;margin-bottom:-1px;border-radius:7px 7px 0 0}
.tab:hover{color:var(--ink);background:rgba(255,255,255,.03)}
.tab.active{color:#fff;border-bottom-color:var(--orange);background:rgba(236,122,44,.08)}
.tab .c{font-size:11px;color:var(--muted);background:var(--chip);border:1px solid var(--line);
border-radius:20px;padding:1px 7px;margin-left:6px}
.tab.active .c{color:var(--orange-soft)}
.seg{display:flex;gap:0;border:1px solid var(--line);border-radius:20px;overflow:hidden}
.seg button{color:var(--muted);font-size:12.5px;font-weight:650;padding:8px 14px;min-height:40px}
.seg button.active{background:rgba(236,122,44,.14);color:#fff}
#q{flex:1;min-width:200px;background:var(--chip);border:1px solid var(--line);color:var(--ink);
font:inherit;font-size:13px;padding:10px 14px;border-radius:20px;min-height:44px}
select{background:var(--chip);border:1px solid var(--line);color:var(--ink);font:inherit;font-size:12.5px;
padding:8px 10px;border-radius:10px;min-height:40px}
.sechead{display:flex;align-items:center;gap:10px;margin:26px 2px 12px}
.sechead h2{font-size:15.5px}
.sechead .line{flex:1;height:1px;background:var(--line)}
.sechead .cnt{color:var(--muted);font-size:12px}
.sechead .act{color:var(--orange-soft);font-size:12.5px;font-weight:650;padding:6px 10px;min-height:40px;border:1px solid var(--line);border-radius:20px}
.sechead .act:hover{border-color:var(--orange)}
.qrow{display:flex;align-items:flex-start;gap:12px;background:var(--panel);border:1px solid var(--line);
border-radius:12px;padding:13px 15px;margin-bottom:9px;cursor:pointer;text-align:left;width:100%}
.qrow:hover{border-color:var(--orange)}
.qrow .body{flex:1;min-width:0}
.qrow .top{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.qrow .nm{font-size:14px;font-weight:700}
.qrow .why{font-size:12.5px;color:var(--muted);margin-top:5px;line-height:1.5}
.qrow .doit{flex:0 0 auto;color:var(--orange-soft);font-size:12px;font-weight:650;border:1px solid var(--line);
border-radius:20px;padding:8px 12px;min-height:40px;white-space:nowrap;align-self:center;display:inline-flex;align-items:center}
.qrow .doit:hover{border-color:var(--orange)}
.pill{display:inline-block;font-size:10.5px;font-weight:700;padding:3px 9px;border-radius:20px;letter-spacing:.02em;border:1px solid}
.p-red{background:rgba(232,106,106,.14);color:var(--red);border-color:rgba(232,106,106,.4)}
.p-amber{background:rgba(232,181,74,.14);color:var(--amber);border-color:rgba(232,181,74,.4)}
.p-blue{background:rgba(74,144,217,.14);color:#8fc0ef;border-color:rgba(74,144,217,.4)}
.p-cold{background:rgba(111,136,160,.14);color:#b6c6d6;border-color:rgba(111,136,160,.4)}
.p-green{background:rgba(65,192,138,.14);color:var(--green);border-color:rgba(65,192,138,.4)}
.p-orange{background:rgba(236,122,44,.14);color:var(--orange-soft);border-color:rgba(236,122,44,.4)}
.p-dim{background:var(--chip);color:var(--muted);border-color:var(--line);font-weight:600}
.own{font-size:10.5px;font-weight:700;border:1px solid;border-radius:20px;padding:2px 8px;white-space:nowrap}
.emptynote{color:var(--muted);font-size:13px;background:var(--panel);border:1px dashed var(--line);
border-radius:12px;padding:16px;line-height:1.6}
.board{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;align-items:start}
@media(max-width:980px){.board{display:flex;overflow-x:auto;padding-bottom:8px}.col{min-width:240px}}
.col{background:rgba(11,27,43,.35);border:1px solid var(--line);border-radius:12px;padding:10px}
.colhead{display:flex;align-items:center;gap:7px;padding:4px 4px 10px}
.colhead .dot{width:9px;height:9px;border-radius:50%}
.colhead h3{font-size:13px}
.colhead .c{margin-left:auto;font-size:11px;color:var(--muted)}
.bcard{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 11px;
margin-bottom:8px;cursor:pointer;width:100%;text-align:left}
.bcard:hover{border-color:var(--orange)}
.bcard .nm{font-size:13px;font-weight:650;line-height:1.3}
.bcard .co{font-size:11px;color:var(--muted);margin-top:2px}
.bcard .ft{display:flex;align-items:center;gap:6px;margin-top:7px}
.adot{width:8px;height:8px;border-radius:50%;flex:0 0 auto}
.a-red{background:var(--red)}.a-amber{background:var(--amber)}.a-blue{background:var(--blue)}
.a-cold{background:var(--cold)}.a-green{background:var(--green)}.a-dim{background:#3d5d7c}
.bcard .st{font-size:10.5px;color:var(--muted)}
.prosp{margin-top:18px}
.list .qrow{margin-bottom:8px}
.overlay{position:fixed;inset:0;background:rgba(5,14,23,.72);display:none;z-index:40;padding:4vh 16px}
.overlay.open{display:flex;justify-content:center;align-items:flex-start}
.sheet{background:var(--panel);border:1px solid var(--line);border-radius:16px;max-width:680px;width:100%;
max-height:92vh;overflow-y:auto;padding:22px 24px 26px;position:relative}
.sheet .x{position:absolute;top:14px;right:14px;color:var(--muted);font-size:20px;width:44px;height:44px;border-radius:10px}
.sheet .x:hover{color:#fff;background:rgba(255,255,255,.06)}
.sheet h2{font-size:20px;padding-right:44px}
.sheet .co{color:var(--muted);font-size:13px;margin-top:3px}
.sheet .chips{display:flex;flex-wrap:wrap;gap:7px;margin:12px 0}
.sheet .contact{font-size:13px;line-height:1.7;color:#d7e3ef;border-top:1px solid rgba(39,80,111,.5);
padding-top:12px;margin-top:4px}
.meter{display:flex;align-items:center;gap:0;margin:16px 0 6px}
.mstep{flex:1;display:flex;flex-direction:column;align-items:center;gap:6px;position:relative}
.mstep .md{width:13px;height:13px;border-radius:50%;background:var(--panel2);border:2px solid var(--cold);z-index:2}
.mstep.done .md{background:var(--orange);border-color:var(--orange)}
.mstep.now .md{background:#fff;border-color:var(--orange);width:16px;height:16px}
.mstep .ml{font-size:10px;color:var(--muted);text-align:center}
.mstep.now .ml{color:var(--orange-soft);font-weight:700}
.mstep::before{content:"";position:absolute;top:6px;left:-50%;width:100%;height:2px;background:var(--line);z-index:1}
.mstep:first-child::before{display:none}
.mstep.done::before,.mstep.now::before{background:var(--orange)}
.block{border-top:1px solid rgba(39,80,111,.5);margin-top:16px;padding-top:14px}
.block h4{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px}
.block p{font-size:13px;line-height:1.6}
.nextbox{background:rgba(236,122,44,.07);border:1px solid rgba(236,122,44,.35);border-radius:11px;padding:12px 14px}
.nextbox .nt{font-size:13px;line-height:1.55}
.nextbox .nmeta{font-size:11.5px;color:var(--muted);margin-top:6px}
.nextbox .nmeta b{color:var(--orange-soft)}
/* What the record says -- the reading panel. Same glass family as the rest of
   the room: panel ground, one hairline, orange only where it is the light in
   the frame. The bar is the only new shape and it is deliberately flat. */
.readbox{background:linear-gradient(180deg,rgba(74,144,217,.09),rgba(21,51,80,.55));
border:1px solid var(--line);border-radius:12px;padding:13px 15px;margin:14px 0}
.readbox h4{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-bottom:9px}
.readbox.unread{background:none;border-style:dashed}
.readrung{font-size:13px;color:var(--ink)}
.readrung b{color:var(--orange-soft);font-size:15px}
.readconf{float:right;font-size:11px;color:var(--muted)}
.readbar{height:5px;border-radius:3px;background:var(--chip);margin:9px 0 10px;overflow:hidden}
.readbar span{display:block;height:100%;border-radius:3px;
background:linear-gradient(90deg,var(--blue),var(--orange))}
.readlevel{font-size:12.5px;line-height:1.5;color:var(--ink)}
.readrow{display:flex;justify-content:space-between;gap:14px;font-size:12px;margin-top:8px;
padding-top:8px;border-top:1px solid rgba(39,80,111,.6)}
.readrow .rk{color:var(--muted)}
.readrow .rv{text-align:right}
.rbad{color:var(--red)} .rmid{color:var(--amber)} .rok{color:var(--green)}
.readfoot{font-size:11px;color:var(--muted);line-height:1.5;margin-top:10px}
.readnote{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--blue);
border-radius:9px;padding:10px 13px;margin:0 0 16px;font-size:12.5px;color:var(--muted);line-height:1.55}
.readnote b{color:var(--ink)}
.kd{display:grid;grid-template-columns:1fr 1fr;gap:8px}
@media(max-width:560px){.kd{grid-template-columns:1fr}}
.kd .k{background:var(--chip);border:1px solid var(--line);border-radius:9px;padding:9px 11px}
.kd .k .kl{font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.kd .k .kv{font-size:13px;font-weight:650;margin-top:3px}
.doclink{display:block;font-size:12.5px;padding:8px 2px;text-decoration:none;line-height:1.4}
.doclink:hover{text-decoration:underline}
.tl{list-style:none}
.tl li{position:relative;padding:0 0 14px 22px;font-size:12.5px;line-height:1.55;color:#d7e3ef}
.tl li::before{content:"";position:absolute;left:5px;top:6px;width:8px;height:8px;border-radius:50%;background:var(--orange)}
.tl li::after{content:"";position:absolute;left:8px;top:16px;bottom:-2px;width:2px;background:rgba(39,80,111,.6)}
.tl li:last-child::after{display:none}
.tl .td{color:var(--orange-soft);font-weight:700;font-size:11.5px;display:block}
.meta-dim{font-size:11.5px;color:var(--muted);line-height:1.6}
.actions{display:flex;flex-wrap:wrap;gap:9px;margin-top:16px}
.abtn{border:1px solid var(--line);border-radius:22px;padding:11px 16px;font-size:12.5px;font-weight:650;
color:var(--ink);min-height:44px;text-decoration:none;display:inline-flex;align-items:center}
.abtn:hover{border-color:var(--orange)}
.abtn.primary{background:var(--orange);color:#12253a;border-color:var(--orange)}
.abtn.primary:hover{background:var(--orange-soft)}
.note{font-size:11.5px;color:var(--amber);margin-top:10px}
.foot{color:var(--muted);font-size:11.5px;margin-top:34px;line-height:1.7;border-top:1px solid var(--line);padding-top:16px}
#toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%) translateY(80px);background:#0b1b2b;
border:1px solid var(--orange);color:var(--ink);font-size:13px;padding:12px 18px;border-radius:12px;
z-index:60;transition:transform .25s;max-width:90vw}
#toast.show{transform:translateX(-50%) translateY(0)}
.wonbanner{background:rgba(65,192,138,.12);border:1px solid rgba(65,192,138,.45);color:var(--green);
font-size:13px;font-weight:650;padding:10px 14px;border-radius:11px;margin:12px 0 2px}
.hide{display:none!important}
"""

JS = r"""
var DEALS = __DATA__;
var READ = __READ__;
var PHASES = ["Research","Negotiation","Legal","Due Diligence","Closing"];
var PHASE_COLOR = {"Research":"#e8b54a","Negotiation":"#41c08a","Legal":"#ec7a2c","Due Diligence":"#4a90d9","Closing":"#7ad0a6","Pending":"#6f88a0"};
var OWN_COLOR = {"Joe":"#ec7a2c","Dell":"#4a90d9"};
var TEAM={"Joe":1,"Dell":1};
function isReferred(d){return !!d.owner&&!TEAM[d.owner];}
var state = {tab:"today", owner:"all", q:"", seg:"all", sort:"attention"};

function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];});}
function parseDate(s){
  if(!s) return null;
  var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if(m) return new Date(+m[1], +m[2]-1, +m[3]);
  m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(s);
  if(m) return new Date(+m[3], +m[1]-1, +m[2]);
  return null;
}
var MONTHS=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
function fmt(s){var d=parseDate(s);if(!d)return s||"";var y=(d.getFullYear()!==(new Date()).getFullYear())?(" "+d.getFullYear()):"";return MONTHS[d.getMonth()]+" "+d.getDate()+y;}
function today0(){var t=new Date();return new Date(t.getFullYear(),t.getMonth(),t.getDate());}
function daysUntil(s){var d=parseDate(s);if(!d)return null;return Math.round((d-today0())/86400000);}
function lastTouch(d){var a=d.activity||[];return a.length?a[a.length-1].date:null;}

function attention(d){
  if(d.outcome==="won") return {b:"won",rank:7};
  if(d.outcome==="lost") return {b:"lost",rank:8};
  if(d.tier==="pending") return {b:"prospect",rank:6};
  var ns=d.next_step;
  if(ns&&ns.due){var du=daysUntil(ns.due);
    if(du!==null&&du<0) return {b:"overdue",rank:0,days:-du,due:ns.due};
    if(du!==null&&du<=10) return {b:"due",rank:1,days:du,due:ns.due};}
  if(ns&&ns.waiting_on) return {b:"waiting",rank:2,who:ns.waiting_on};
  if(ns) return {b:"ontrack",rank:5};
  var lt=lastTouch(d);
  if(lt){var q=-daysUntil(lt); if(q>14) return {b:"quiet",rank:3,days:q}; return {b:"ontrack",rank:5};}
  return {b:"unenriched",rank:4};
}
function chipFor(d,a){
  if(a.b==="overdue") return '<span class="pill p-red">Past due '+esc(fmt(a.due))+'</span>';
  if(a.b==="due") return '<span class="pill p-amber">'+(a.days===0?"Due today":"Due "+esc(fmt(a.due)))+'</span>';
  if(a.b==="waiting") return '<span class="pill p-blue">Waiting: '+esc(a.who)+'</span>';
  if(a.b==="quiet") return '<span class="pill p-cold">Quiet '+a.days+' days</span>';
  if(a.b==="unenriched") return '<span class="pill p-dim">No story yet</span>';
  if(a.b==="prospect") return '<span class="pill p-dim">Prospect</span>';
  if(a.b==="won") return '<span class="pill p-green">Won'+(d.closed?" "+esc(fmt(d.closed)):"")+'</span>';
  if(a.b==="lost") return '<span class="pill p-dim">Passed</span>';
  var pc=PHASE_COLOR[d.phase]||"#6f88a0";
  return '<span class="pill" style="background:'+pc+'22;color:'+pc+';border-color:'+pc+'55">'+esc(d.phase||"Open")+'</span>';
}
function ownChip(d){
  if(isReferred(d)) return '<span class="own" style="border-color:#6f88a0;color:#b6c6d6">Referred in</span>';
  var oc=OWN_COLOR[d.owner]||"#6f88a0";
  return '<span class="own" style="border-color:'+oc+';color:'+oc+'">'+esc((d.owner||"").split(" ")[0])+'</span>';}
function whyLine(d){
  if(d.next_step&&d.next_step.text) return d.next_step.text;
  var a=d.activity||[];
  if(a.length) return a[a.length-1].text;
  if(d.carr_status) return d.carr_status;
  return "No activity on record yet. One pasted Copilot summary fills this card in.";
}
function trunc(s,n){s=String(s||"");return s.length>n?s.slice(0,n-1).trim()+"…":s;}
function latestShort(d){return trunc(whyLine(d),150);}

function enrichPrompt(d){return "Enrich "+d.name+" in the Deal Room. Here is the latest from the deal's email folder:\n\n[paste the Copilot summary here]";}
function checkinPrompt(d){return "Draft a check-in for "+d.name+(d.company&&d.company!==d.name?" ("+d.company+")":"")+". The deal has gone quiet; latest on record: "+trunc(whyLine(d),200)+" Draft only, Joe sends.";}
function followupPrompt(d){return "Draft a follow-up for "+d.name+(d.company&&d.company!==d.name?" ("+d.company+")":"")+" on the "+(d.phase||"open")+" deal. Where it stands: "+trunc(whyLine(d),200)+" Draft only, Joe sends.";}
function batchPrompt(){return "Enrich the Deal Room. Copilot summaries for several deals below:\n\n[paste the summaries here]";}

function copyText(t,msg){
  function done(){toast(msg||"Copied. Paste it into a new chat and the room updates.");}
  function fallback(){var ta=document.createElement("textarea");ta.value=t;ta.style.position="fixed";ta.style.opacity="0";
    document.body.appendChild(ta);ta.select();try{document.execCommand("copy");done();}catch(e){toast("Copy failed. Select the card text instead.");}
    document.body.removeChild(ta);}
  if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(t).then(done,fallback);}
  else fallback();
}
var toastTimer=null;
function toast(msg){var t=document.getElementById("toast");t.textContent=msg;t.classList.add("show");
  clearTimeout(toastTimer);toastTimer=setTimeout(function(){t.classList.remove("show");},2800);}

function matches(d){
  if(state.owner==="ref"){if(!isReferred(d))return false;}
  else if(state.owner!=="all"&&(d.owner||"").split(" ")[0]!==state.owner) return false;
  if(state.seg==="music"&&d.seg!=="Cadence Studio") return false;
  if(state.seg==="core"&&d.seg==="Cadence Studio") return false;
  if(state.q){var blob=[d.name,d.company,d.contact,d.city,d.seg,d.owner,d.phase,d.referral,whyLine(d)].join(" ").toLowerCase();
    if(blob.indexOf(state.q)<0) return false;}
  return true;
}
function idxOf(d){return DEALS.indexOf(d);}

function qrow(d,actionLabel,actionFn){
  var a=attention(d);
  return '<div class="qrow" role="button" tabindex="0" onclick="openDetail('+idxOf(d)+')" onkeydown="if(event.key===\'Enter\')openDetail('+idxOf(d)+')">'
   +'<div class="body"><div class="top"><span class="nm">'+esc(d.name)+'</span>'+chipFor(d,a)+ownChip(d)+'</div>'
   +'<div class="why">'+esc(latestShort(d))+'</div></div>'
   +'<button class="doit" onclick="event.stopPropagation();'+actionFn+'(DEALS['+idxOf(d)+'])">'+actionLabel+'</button></div>';
}
function copyEnrich(d){copyText(enrichPrompt(d));}
function copyCheckin(d){copyText(checkinPrompt(d),"Check-in prompt copied. Paste it into a new chat.");}
function copyFollowup(d){copyText(followupPrompt(d),"Follow-up prompt copied. Paste it into a new chat.");}
function copyBatch(){copyText(batchPrompt(),"Batch prompt copied. Paste it with the summaries into a new chat.");}

function section(title,count,actHtml){
  return '<div class="sechead"><h2>'+title+'</h2><span class="cnt">'+count+'</span><div class="line"></div>'+(actHtml||"")+'</div>';
}

function renderToday(el){
  var pool=DEALS.filter(matches);
  var act=pool.filter(function(d){return d.tier!=="pending"&&!d.outcome;});
  var buckets={overdue:[],due:[],waiting:[],quiet:[],unenriched:[],ontrack:[]};
  act.forEach(function(d){var a=attention(d);if(buckets[a.b])buckets[a.b].push(d);});
  buckets.overdue.sort(function(x,y){return daysUntil(x.next_step.due)-daysUntil(y.next_step.due);});
  buckets.due.sort(function(x,y){return daysUntil(x.next_step.due)-daysUntil(y.next_step.due);});
  buckets.quiet.sort(function(x,y){return attention(y).days-attention(x).days;});
  var stageRank=function(d){var i=PHASES.indexOf(d.phase);return i<0?-1:i;};
  buckets.unenriched.sort(function(x,y){return stageRank(y)-stageRank(x);});
  var h="";
  var now=buckets.overdue.concat(buckets.due);
  h+=section("Act now",now.length+" deal"+(now.length===1?"":"s"));
  if(now.length){now.forEach(function(d){h+=qrow(d,"Log what happened","copyEnrich");});}
  else h+='<div class="emptynote">Nothing is past due or due this week. The queues below are how it stays that way.</div>';
  if(buckets.waiting.length){
    h+=section("Waiting on someone",buckets.waiting.length+" deal"+(buckets.waiting.length===1?"":"s"));
    buckets.waiting.forEach(function(d){h+=qrow(d,"Copy a nudge draft","copyFollowup");});}
  if(buckets.quiet.length){
    h+=section("Gone quiet",buckets.quiet.length+" deal"+(buckets.quiet.length===1?"":"s"));
    buckets.quiet.forEach(function(d){h+=qrow(d,"Copy a check-in draft","copyCheckin");});}
  if(buckets.unenriched.length){
    h+=section("No story yet",buckets.unenriched.length+" deal"+(buckets.unenriched.length===1?"":"s"),
      '<button class="act" onclick="copyBatch()">Copy the batch update prompt</button>');
    var show=buckets.unenriched.slice(0,6);
    show.forEach(function(d){h+=qrow(d,"Copy the update prompt","copyEnrich");});
    var more=buckets.unenriched.length-show.length;
    if(more>0) h+='<div class="emptynote">And '+more+' more without a story, furthest along shown first. They all live under All deals. Ask Copilot for their email summaries and paste the whole batch into one chat.</div>';}
  if(buckets.ontrack.length){
    h+='<div class="meta-dim" style="margin-top:20px">On track and not near a deadline: '+buckets.ontrack.length+' deal'+(buckets.ontrack.length===1?"":"s")+'. Find them on the Board or under All deals.</div>';}
  el.innerHTML=h||'<div class="emptynote">No deals match this filter.</div>';
}

function bcard(d){
  var a=attention(d);
  var dotClass={overdue:"a-red",due:"a-amber",waiting:"a-blue",quiet:"a-cold",unenriched:"a-dim",ontrack:"a-green",prospect:"a-dim",won:"a-green",lost:"a-dim"}[a.b];
  var st={overdue:"past due",due:(a.days===0?"due today":"due "+fmt(a.due)),waiting:"waiting",quiet:"quiet "+a.days+"d",
    unenriched:"no story yet",ontrack:"on track",prospect:"prospect",won:"won"+(d.closed?" "+fmt(d.closed):""),lost:"passed"}[a.b];
  return '<button class="bcard" onclick="openDetail('+idxOf(d)+')"><div class="nm">'+esc(d.name)+'</div>'
   +(d.company&&d.company!==d.name?'<div class="co">'+esc(d.company)+'</div>':'')
   +'<div class="ft"><span class="adot '+dotClass+'"></span><span class="st">'+esc(st)+'</span><span style="margin-left:auto">'+ownChip(d)+'</span></div></button>';
}
function renderBoard(el){
  var pool=DEALS.filter(matches);
  var h=readBanner();
  h+='<div class="board">';
  PHASES.forEach(function(ph){
    var col=pool.filter(function(d){return d.phase===ph&&d.tier!=="pending"&&!d.outcome;});
    var pc=PHASE_COLOR[ph];
    h+='<div class="col"><div class="colhead"><span class="dot" style="background:'+pc+'"></span><h3>'+ph+'</h3><span class="c">'+col.length+'</span></div>';
    if(col.length) col.forEach(function(d){h+=bcard(d);});
    else h+='<div class="meta-dim" style="padding:6px 4px">Empty.</div>';
    h+='</div>';});
  h+='</div>';
  var pend=pool.filter(function(d){return d.tier==="pending"&&!d.outcome;});
  if(pend.length){
    h+='<div class="prosp">'+section("Prospects",pend.length+" not yet signed");
    h+='<div class="board" style="grid-template-columns:repeat(auto-fill,minmax(220px,1fr))">';
    pend.forEach(function(d){h+=bcard(d);});
    h+='</div></div>';}
  var closed=pool.filter(function(d){return d.outcome;});
  if(closed.length){
    closed.sort(function(x,y){return (parseDate(y.closed)||0)-(parseDate(x.closed)||0);});
    h+='<div class="prosp">'+section("Recently closed",closed.length+" signed and done");
    h+='<div class="board" style="grid-template-columns:repeat(auto-fill,minmax(220px,1fr))">';
    closed.forEach(function(d){h+=bcard(d);});
    h+='</div></div>';}
  el.innerHTML=h;
}

function renderAll(el){
  var pool=DEALS.filter(matches);
  var s=state.sort;
  pool=pool.slice();
  pool.sort(function(x,y){
    if(s==="attention"){var r=attention(x).rank-attention(y).rank;if(r)return r;return (x.name||"").localeCompare(y.name||"");}
    if(s==="name")return (x.name||"").localeCompare(y.name||"");
    if(s==="phase"){var px=PHASES.indexOf(x.phase),py=PHASES.indexOf(y.phase);return (px<0?9:px)-(py<0?9:py);}
    if(s==="owner")return (x.owner||"").localeCompare(y.owner||"");
    return 0;});
  var h='<div class="list">';
  pool.forEach(function(d){h+=qrow(d,"Open","openDetailBtn");});
  h+='</div>';
  el.innerHTML=pool.length?h:'<div class="emptynote">No deals match. Clear the search or the owner filter.</div>';
}
function refMap(pool){
  var map={};
  function add(key,type,d){if(!map[key])map[key]={type:type,deals:[]};map[key].deals.push(d);}
  pool.forEach(function(d){
    if(isReferred(d)) add(d.owner,"agent",d);
    if(d.referral) add(d.referral,"source",d);
  });
  return map;
}
function renderRefs(el){
  var pool=DEALS.filter(matches);
  var map=refMap(pool);
  var keys=Object.keys(map).sort(function(a,b){return map[b].deals.length-map[a].deals.length||a.localeCompare(b);});
  if(!keys.length){el.innerHTML='<div class="emptynote">No referral sources on record for this filter.</div>';return;}
  var h='<div class="meta-dim" style="margin:6px 0 14px">Who sends us business, counted from each deal. Referral sources come from the deal record; out-of-market CARR agents count through the deals they own here. What we owe back, and each vendor'+String.fromCharCode(39)+'s record, lives in the network brain (DNA/Network/deals.md).</div>';
  keys.forEach(function(k){
    var e=map[k];
    var tag=e.type==="agent"?' <span class="pill p-cold">Out-of-market CARR agent</span>':'';
    h+='<div class="sechead"><h2>'+esc(k)+tag+'</h2><span class="cnt">'+e.deals.length+' deal'+(e.deals.length===1?'':'s')+'</span><div class="line"></div></div>';
    h+='<div class="board" style="grid-template-columns:repeat(auto-fill,minmax(240px,1fr))">';
    e.deals.forEach(function(d){h+=bcard(d);});
    h+='</div>';
  });
  el.innerHTML=h;
}
function openDetailBtn(d){openDetail(idxOf(d));}

// WHAT THE RECORD SAYS. Three judgments read off the deal record by
// ops/jev_deal_read.py: how far the deal has actually moved, who owes the next
// move, and whether its silence means anything. A deal whose record is too
// thin to read says so in plain words instead of showing a number -- that
// refusal is the honest half of this panel and is never dressed up as a score.
var WAITING_LABEL = {
  client:"the client", counterparty:"the landlord or seller",
  carr:"us", market:"the market — nothing fits yet",
  third_party:"a lender, architect, contractor or the city",
  client_timing:"nobody — the client's own timing",
  not_recorded:"not recorded"};
function readHtml(d){
  var r=d.read;
  if(!r) return '';
  if(!r.judged){
    return '<div class="readbox unread"><h4>What the record says</h4>'+
      '<div class="emptynote">'+esc(r.reason||"Not read.")+'</div></div>';
  }
  var pct=Math.max(0,Math.min(100,(r.movement/(r.rungs-1))*100));
  var h='<div class="readbox"><h4>What the record says</h4>';
  h+='<div class="readrung">Step <b>'+r.rung+'</b> of '+r.rungs+
     '<span class="readconf">read with '+Math.round(r.confidence*100)+'% confidence</span></div>';
  h+='<div class="readbar"><span style="width:'+pct.toFixed(0)+'%"></span></div>';
  h+='<div class="readlevel">'+esc(r.level)+'</div>';
  h+='<div class="readrow"><span class="rk">Next move belongs to</span><span class="rv">'+
     esc(WAITING_LABEL[r.waiting_on]||r.waiting_on)+'</span></div>';
  if(r.quiet_days!==null&&r.quiet_days!==undefined){
    var bad=r.silence_is_bad>=0.6, ok=r.silence_is_bad<=0.3;
    var verdict=bad?"long enough to be a problem":(ok?"a normal wait":"worth a look");
    h+='<div class="readrow"><span class="rk">Quiet for</span><span class="rv">'+
       r.quiet_days+' day'+(r.quiet_days===1?'':'s')+' — <b class="'+
       (bad?'rbad':(ok?'rok':'rmid'))+'">'+verdict+'</b></span></div>';
  }
  h+='<div class="readfoot">Read from '+r.evidence_chars+
     ' characters of recorded evidence. A reading, not a forecast — no number '+
     'here has been checked against a deal whose outcome is known. Of the '+
     'ninety-nine closed deals only two carry enough written down to check a '+
     'reading against, and both of those were won, so a wrong reading has '+
     'nothing on record to be caught by.</div>';
  return h+'</div>';
}
function readBanner(){
  var s=READ.summary||{};
  if(READ.note) return '<div class="readnote">'+esc(READ.note)+'</div>';
  if(!s.deals) return '';
  return '<div class="readnote">Read off the record: <b>'+s.judged+'</b> of '+s.deals+
    ' live deals held enough to read. <b>'+(s.no_evidence||0)+'</b> hold too little — '+
    'a name, a phase and a city, and nothing recorded about what has happened.</div>';
}

function meterHtml(d){
  if(d.tier==="pending") return "";
  var i=(d.outcome==="won")?PHASES.length:PHASES.indexOf(d.phase);
  if(i<0) return "";
  var h='<div class="meter">';
  PHASES.forEach(function(ph,j){
    var cls=j<i?"done":(j===i?"now":"");
    h+='<div class="mstep '+cls+'"><span class="md"></span><span class="ml">'+ph+'</span></div>';});
  return h+'</div>';
}
function openDetail(i){
  var d=DEALS[i];if(!d)return;
  var a=attention(d);
  var h='<button class="x" onclick="closeDetail()" aria-label="Close">&#10005;</button>';
  h+='<h2>'+esc(d.name)+'</h2>';
  if(d.company&&d.company!==d.name)h+='<div class="co">'+esc(d.company)+'</div>';
  h+='<div class="chips">'+chipFor(d,a)+ownChip(d);
  if(d.seg)h+='<span class="pill p-dim">'+esc(d.seg)+'</span>';
  if(d.txn)h+='<span class="pill p-dim">'+esc(d.txn)+'</span>';
  if(d.ptype)h+='<span class="pill p-dim">'+esc(d.ptype)+'</span>';
  if(d.city)h+='<span class="pill p-dim">'+esc(d.city)+'</span>';
  h+='</div>';
  if(d.outcome==="won")h+='<div class="wonbanner">\u2713 Deal won'+(d.closed?" \u00b7 executed "+esc(fmt(d.closed)):"")+(d.won_value?" \u00b7 "+esc(d.won_value)+" delivered to the client":"")+'</div>';
  var cl=[];
  if(d.contact)cl.push(esc(d.contact));
  if(d.phone)cl.push('<a href="tel:'+esc(String(d.phone).replace(/[^0-9+]/g,""))+'">'+esc(d.phone)+'</a>');
  if(d.email)cl.push('<a href="mailto:'+esc(d.email)+'">'+esc(d.email)+'</a>');
  if(cl.length)h+='<div class="contact">'+cl.join(" &nbsp;&middot;&nbsp; ")+'</div>';
  h+=meterHtml(d);
  h+=readHtml(d);
  h+='<div class="block"><h4>Next step</h4>';
  if(d.next_step&&d.next_step.text){
    h+='<div class="nextbox"><div class="nt">'+esc(d.next_step.text)+'</div><div class="nmeta">';
    var nm=[];
    if(d.next_step.due)nm.push('Due <b>'+esc(fmt(d.next_step.due))+'</b>');
    if(d.next_step.waiting_on)nm.push('Waiting on <b>'+esc(d.next_step.waiting_on)+'</b>');
    h+=(nm.length?nm.join(" &nbsp;&middot;&nbsp; "):"No date attached.");
    h+='</div></div>';}
  else h+='<div class="emptynote">No next step on record. Paste the deal’s Copilot summary into a chat and this box fills in.</div>';
  h+='</div>';
  if(d.key_dates&&d.key_dates.length){
    h+='<div class="block"><h4>Key dates</h4><div class="kd">';
    d.key_dates.forEach(function(k){h+='<div class="k"><div class="kl">'+esc(k.label)+'</div><div class="kv">'+esc(fmt(k.date))+'</div></div>';});
    h+='</div></div>';}
  if(d.docs&&d.docs.length){
    h+='<div class="block"><h4>Documents</h4>';
    d.docs.forEach(function(doc){h+='<a class="doclink" href="'+esc(doc.url)+'" target="_blank" rel="noopener">'+esc(doc.label)+' ↗</a>';});
    h+='</div>';}
  h+='<div class="block"><h4>The story so far</h4>';
  var acts=(d.activity||[]).slice().reverse();
  if(acts.length){h+='<ul class="tl">';
    acts.forEach(function(x){h+='<li><span class="td">'+esc(fmt(x.date))+'</span>'+esc(x.text)+'</li>';});
    h+='</ul>';}
  else if(d.carr_status)h+='<p>'+esc(d.carr_status)+'</p>';
  else h+='<div class="emptynote">Nothing logged yet. Ask Copilot to summarize this deal’s email folder, paste it into a chat, and the story appears here.</div>';
  h+='</div>';
  var md=[];
  if(isReferred(d))md.push("Referred in by "+esc(d.owner)+", an out-of-market CARR agent");
  if(d.referral)md.push("Came in via "+esc(d.referral));
  if(d.etl)md.push("Agreement signed "+esc(fmt(d.etl)));
  if(d.created)md.push("Opened "+esc(fmt(d.created)));
  if(md.length)h+='<div class="block meta-dim">'+md.join(" &nbsp;&middot;&nbsp; ")+'</div>';
  if(d.note)h+='<div class="note">⚠ '+esc(d.note)+'</div>';
  h+='<div class="actions">';
  if(!d.outcome){h+='<button class="abtn primary" onclick="copyEnrich(DEALS['+i+'])">Log what happened</button>'
   +'<button class="abtn" onclick="copyFollowup(DEALS['+i+'])">Copy a follow-up draft</button>';}
  h+=(d.email?'<a class="abtn" href="mailto:'+esc(d.email)+'">Email '+esc((d.contact||"them").split(" ")[0])+'</a>':'');
  h+='</div>';
  document.getElementById("sheet").innerHTML=h;
  document.getElementById("overlay").classList.add("open");
  document.body.style.overflow="hidden";
}
function closeDetail(){document.getElementById("overlay").classList.remove("open");document.body.style.overflow="";}
document.addEventListener("keydown",function(e){
  if(e.key==="Escape")closeDetail();
  if(e.key==="/"&&document.activeElement!==document.getElementById("q")){e.preventDefault();document.getElementById("q").focus();}});

function counts(){
  var act=DEALS.filter(function(d){return d.tier!=="pending"&&!d.outcome;});
  var c={overdue:0,due:0,waiting:0,quiet:0,unenriched:0,ontrack:0};
  act.forEach(function(d){var a=attention(d);if(c[a.b]!==undefined)c[a.b]++;});
  c.actnow=c.overdue+c.due;c.active=act.length;
  c.won=DEALS.filter(function(d){return d.outcome==="won";}).length;
  c.attention=c.actnow+c.waiting+c.quiet;
  return c;
}
function render(){
  var c=counts();
  document.getElementById("t-actnow").textContent=c.actnow;
  document.getElementById("t-wait").textContent=c.waiting;
  document.getElementById("t-quiet").textContent=c.quiet;
  document.getElementById("t-nostory").textContent=c.unenriched;
  document.getElementById("t-open").textContent=c.active;
  document.getElementById("c-today").textContent=c.attention+c.unenriched;
  document.getElementById("c-all").textContent=DEALS.length;
  document.getElementById("c-refs").textContent=Object.keys(refMap(DEALS)).length;
  document.querySelectorAll(".tab").forEach(function(t){t.classList.toggle("active",t.dataset.t===state.tab);});
  document.querySelectorAll(".seg button").forEach(function(b){b.classList.toggle("active",b.dataset.o===state.owner);});
  document.getElementById("segsel").classList.toggle("hide",false);
  document.getElementById("sortsel").classList.toggle("hide",state.tab!=="all");
  var el=document.getElementById("view");
  if(state.tab==="today")renderToday(el);
  else if(state.tab==="board")renderBoard(el);
  else if(state.tab==="refs")renderRefs(el);
  else renderAll(el);
}
document.querySelectorAll(".tab").forEach(function(t){t.onclick=function(){state.tab=t.dataset.t;render();};});
document.querySelectorAll(".seg button").forEach(function(b){b.onclick=function(){state.owner=b.dataset.o;render();};});
document.getElementById("q").oninput=function(){state.q=this.value.toLowerCase().trim();render();};
document.getElementById("segsel").onchange=function(){state.seg=this.value;render();};
document.getElementById("sortsel").onchange=function(){state.sort=this.value;render();};
document.getElementById("overlay").onclick=function(e){if(e.target===this)closeDetail();};
render();
"""

BODY = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>The Deal Room</title>
<style>__CSS__</style></head><body><div class="wrap">
<span class="kicker">Panhandle Team &middot; Working Pipeline</span>
<h1>The Deal Room</h1>
<div class="lede">Every open deal, worked from one place. <b>Today</b> is the queue: what needs a touch, what we are waiting on, what has gone quiet. Salesforce keeps the paperwork (agreements and invoicing); the story on each card comes from pasted Copilot email summaries, and one paste updates the room. Pulled __STAMP__.</div>
<div class="tiles">
 <div class="tile warn"><div class="n" id="t-actnow">0</div><div class="l">Act now</div></div>
 <div class="tile"><div class="n" id="t-wait">0</div><div class="l">Waiting on someone</div></div>
 <div class="tile amber"><div class="n" id="t-quiet">0</div><div class="l">Gone quiet</div></div>
 <div class="tile"><div class="n" id="t-nostory">0</div><div class="l">No story yet</div></div>
 <div class="tile"><div class="n" id="t-open">0</div><div class="l">Active deals</div></div>
</div>
<div class="tabs">
 <button class="tab active" data-t="today">Today <span class="c" id="c-today">0</span></button>
 <button class="tab" data-t="board">Board</button>
 <button class="tab" data-t="all">All deals <span class="c" id="c-all">0</span></button>
 <button class="tab" data-t="refs">Referrals <span class="c" id="c-refs">0</span></button>
</div>
<div class="bar">
 <div class="seg">
  <button class="active" data-o="all">Everyone</button>
  <button data-o="Joe">Joe</button>
  <button data-o="Dell">Dell</button>
  <button data-o="ref">Referred in</button>
 </div>
 <input id="q" placeholder="Search deals, people, cities, companies&hellip;" aria-label="Search deals">
 <select id="segsel" aria-label="Segment">
  <option value="all">All segments</option>
  <option value="core">Panhandle book</option>
  <option value="music">Cadence Studio rollout</option>
 </select>
 <select id="sortsel" class="hide" aria-label="Sort">
  <option value="attention">Needs attention first</option>
  <option value="name">By name</option>
  <option value="phase">By phase</option>
  <option value="owner">By owner</option>
 </select>
</div>
<div id="view"></div>
<div class="foot">Salesforce holds the agreements and the invoicing; this room is where the deals get worked. It is rebuilt from the deal file by build-deal-room.py, and the refresh is one motion: ask Copilot for a deal's email summary, paste it into any chat, and the card updates. Commission and close-date figures from Salesforce are placeholders and are deliberately not shown. Refresh method and rules: DNA/Deal Management/deal-enrichment-sop.md.</div>
</div>
<div class="overlay" id="overlay" role="dialog" aria-modal="true"><div class="sheet" id="sheet"></div></div>
<div id="toast" role="status"></div>
<script>__JS__</script></body></html>"""

DOC = (BODY.replace("__CSS__", CSS)
           .replace("__STAMP__", STAMP)
           .replace("__JS__", JS.replace("__DATA__", PAYLOAD)
                       .replace("__READ__", READ_PAYLOAD)))
if CONTEXT.recovery:
    DOC = "<!-- RECOVERY NONCANONICAL projection; never source truth. -->\n" + DOC

os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
open(OUT_PATH, "w").write(DOC)
n_story = sum(1 for d in deals if (d.get("activity") or d.get("carr_status")))
n_next = sum(1 for d in deals if d.get("next_step"))
print("wrote", OUT_PATH, "|", len(deals), "deals |", n_story, "with a story |", n_next,
      "with a next step | source:", source_note(MODE))
