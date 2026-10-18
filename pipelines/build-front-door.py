# build-front-door.py — self-contained generator for The Front Door (DNA/Team/front-door.html)
# Regenerate: python3 build-front-door.py  (writes front-door.html beside it). Edit the GROUPS list to change tiles.
# Mirrors the lead board's build-lead-board.py pattern so the Front Door is regenerable, not hand-edited.

import urllib.parse, html, json, re, os, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "front-door.html")
if sys.argv[1:]:
    raise SystemExit("build-front-door: no folder or recovery arguments are accepted")
REMINDER = (
    "Use the CARR record-layer verbs and canonical carr-system repository context only. "
    "Do not attach external folders. Start with "
    "standing-context; use doctrine-index, search-doctrine, and read-doctrine for doctrine, "
    "and the purpose-built record verbs for business records."
)
DOCTRINE = (
    "Run this as a guided flow: say up front how many questions it will be, ask one at a "
    "time with multiple choice wherever possible and fill-in-the-blank otherwise. Capture "
    "the core record first through its verb, then offer to fill in the rest later."
)

PROMPTS = {
    "New Prospect": "Ask whether this is a lead or active client, then gather the minimum facts one at a time. CALL find(query) first. find returns role or group refs, not a party_id. If find returns any live match, stop and report exactly: existing-party UUID resolution/intake seam unavailable. Never pass a find ref as party_id. Only when find returns no live match and this is a genuinely new person, CALL add-party(idempotency_key,name) and read its returned party_id; never continue on needs_confirm. Then, for a lead, CALL new-lead(idempotency_key,party_id,stage). For an active client, CALL new-client(idempotency_key,party_id,status,acquisition_source,research_evidence). Use a fresh idempotency_key for each intended write. Owner is derived by the server from the authenticated actor; never ask for or pass owner. If the selected verb cannot represent the intake, name the missing intake seam and stop.",
    "New Vendor": "Gather name, category, category ref code, stage, territory, and source one at a time. CALL find(query) first. find returns role or group refs, not a party_id. If find returns any live match, stop and report exactly: existing-party UUID resolution/intake seam unavailable. Never pass a find ref as party_id. Only when find returns no live match and this is a genuinely new person or organization, CALL add-party(idempotency_key,name) and read its returned party_id; never continue on needs_confirm. CALL new-vendor(idempotency_key,party_id,category,ref_code,stage,research_evidence) with a fresh idempotency_key. Owner is derived by the server from the authenticated actor; never ask for or pass owner. new-vendor does not accept territory: only when territory was supplied, read the freshly created vendor and its version, then CALL update-vendor(idempotency_key,vendor,base_version,fields) with fields containing only territory and a different fresh idempotency_key. Stop if a fresh base_version is unavailable.",
    "Log My Day": "Debrief the day one item at a time. Use find before naming a record; use log-activity for meetings, introductions, and relationship events, add-deal-note or set-next-action for deal facts, and add-loop only for genuinely blocked work. Name any item whose record verb is missing instead of filing it elsewhere.",
    "New Idea": "Ask for the idea and why it matters, then CALL add-loop(idempotency_key,kind,owner,body) with kind idea and owner Joe. If it is actionable work rather than an idea, apply the deferral gate before using add-loop; do not create an inbox file.",
    "Log an Event": "Read the introduction-rules doctrine with search-doctrine and read-doctrine. Gather event type, hosts, date, cost, attendees, and outcomes one at a time. Resolve hosts with find and record the event through log-activity. Stop on any attendee or relationship you cannot resolve; never write an event log file.",
    "Import a List": "Ask me to attach the list and give its source. Preview and deduplicate each person with find. The front door has no approved bulk contact-list importer: name that canonical import seam as unavailable and stop after the reviewed preview; do not claim an import or write registry/vendor files.",
    "File a Comp": "Gather the real deal terms and their source one at a time. There is no canonical comp-write verb exposed at this front door: name the comp-ingress seam as unavailable and stop with a structured preview; do not write a comp row or file.",
    "Teach a Rule": "Read applicable doctrine with search-doctrine and read-doctrine, then ask for the exact statement in Joe's words. CALL teach(idempotency_key,statement,human_quote) first and read the returned proposed rule_id. Inspect the authoritative response: if it does not supply the exact installed policy_kind and control_keys for that rule, report pending seam: exact installed control contract unavailable, and stop. If and only if it supplies both, CALL approve-rule(idempotency_key,rule_id,policy_kind,control_keys,reason). Never invent controls, skip teach, or fabricate activation.",
    "Vendor to Vendor Match": "Resolve the target vendor with find, use who-do-we-know for relationship paths, and read introduction rules with the doctrine verbs. Present only evidence-backed compatible pairs and apply the politics block; this is advisory and writes nothing.",
    "Vendor to Client Match": "Resolve the named client or vendor with find-and-catch-up, read the matching doctrine with doctrine verbs, and use who-do-we-know for relationship evidence. Present an evidence-backed match only; this is advisory and writes nothing.",
    "Build a Tour Packet": "Ask for the client, market, criteria, and approved properties one at a time. Resolve the client with find-and-catch-up and read the tour doctrine through doctrine verbs. Use prepare-document only if a registered tour-packet template exists; otherwise name the missing document-template seam and stop.",
    "Deal Comparison": "Ask for the comparison type and source documents, resolve the client/deal with find-and-catch-up, and read the comparison doctrine through doctrine verbs. Use prepare-document only with a registered matching template; otherwise name the missing template seam and stop. Reconcile every displayed number to its source.",
    "Draft an LOI": "Resolve the client and deal with find-and-catch-up, read negotiation, lease-review, and writing doctrine through doctrine verbs, then gather terms one at a time. Use prepare-document only with a registered LOI template; otherwise name the missing template seam and stop. Draft only; never send.",
    "Renewal Workup": "Resolve the client and deal with find-and-catch-up, read renewal and lease-estimator doctrine through doctrine verbs, and gather current terms one at a time. Use prepare-document only with a registered renewal-comparison template; otherwise name the missing template seam and stop. Label estimates and reconcile outputs.",
    "Deal One-Pager": "Resolve the client/deal with find-and-catch-up and read the comparison doctrine through doctrine verbs. Use prepare-document only with a registered purchase-vs-lease one-page template and verified source numbers; otherwise name the missing template seam and stop.",
    "What's Waiting On Me": "Call today-triage and work from its returned records only. Present the single oldest actionable item first. Use complete-action, set-next-action, triage-item, promote-pool, or decline-candidate only when that exact returned item and Joe's answer fit the verb; then read back the result before continuing.",
    "Open Morning Brief": "Call morning-brief with no arguments. It derives your authenticated Joe or Dell context and returns the live record-native morning sections. If any section is unavailable, say so; never substitute an old local brief file or call it empty.",
    "Write a Post": "Ask for topic, platform, and angle one at a time. Use the write-content skill and doctrine verbs for voice rules. Produce a draft and graphic for review; do not publish or invent a substance-bank write.",
    "Audit Writing": "Ask for the text, read writing doctrine through search-doctrine and read-doctrine, and provide review-only findings. Do not rewrite or write a record unless asked.",
    "Research a Market": "Ask for market and scope, then perform sourced research and verify each claim. Use record-finding only for evidence that belongs to an existing resolved record; there is no general market-report write verb, so name that seam and return the sourced result without claiming it was saved.",
    "Who Do We Know": "Ask for the target, resolve it with find, and call who-do-we-know. Read introduction rules with doctrine verbs and apply the politics check before presenting paths. This is read-only.",
    "Hand to Dell": "Ask for the handoff and what it unblocks, then use add-loop with kind team_loop and owner Dell. Read the created loop back before confirming; never write a team-board row.",
    "Prep for a Meeting": "Ask who, when, and purpose. Resolve the person with find-and-catch-up, then call prepare-conversation. Use the returned records and doctrine verbs only; produce a concise brief and do not claim a file was created.",
    "Draft a Follow-Up": "Resolve the lead with find-and-catch-up, read outreach and writing doctrine with doctrine verbs, and ask whether they replied. Draft the next touch only from returned history. Never send; log-outreach is used only after Joe actually sends.",
    "Open the Dashboard": "Call today-triage, deal-room-board, and loop-board, then show a compact live summary from those returned records. Do not rebuild or open a legacy dashboard file.",
    "Open the Lead Board": "Call lead-hot and claim-card, then show this week's actionable leads from those returned records. Do not open or rebuild a lead-board file.",
    "Open the Deal Room": "Call deal-room-board and show signed, on-deck, and hot deal records from its response. Do not refresh or open a Deal Room file.",
}


def canonical_prompt(label):
    """Return the explicit record-native contract for one named tile."""
    try:
        prompt = PROMPTS[label]
    except KeyError as exc:
        raise ValueError(f"Front Door tile has no explicit canonical prompt: {label}") from exc
    return REMINDER + " " + prompt + " " + DOCTRINE


GROUPS = [
    ("Add to the system", [("New Prospect", "Lead or client, one flow"), ("New Vendor", "Referral partner"), ("Log My Day", "Debrief that routes everything"), ("New Idea", "Park it before it's lost"), ("Log an Event", "Happy hour, panel, lunch-and-learn"), ("Import a List", "CSV of contacts"), ("File a Comp", "Real deal terms only"), ("Teach a Rule", "Intro politics, permanent")]),
    ("Deals & matchmaking", [("Vendor to Vendor Match", "Partners who should know each other"), ("Vendor to Client Match", "Right vendor for a client"), ("Build a Tour Packet", "Client-ready property tour"), ("Deal Comparison", "Lease, purchase, or multi-option, side by side"), ("Draft an LOI", "Letter of intent, ready to send"), ("Renewal Workup", "What a represented renewal saves"), ("Deal One-Pager", "Branded purchase-vs-lease summary")]),
    ("Get work done", [("What's Waiting On Me", "Clear it, one at a time"), ("Write a Post", "Your voice + a graphic"), ("Audit Writing", "Review only, no rewrite"), ("Research a Market", "Sourced pull"), ("Who Do We Know", "Warmest path to anyone"), ("Hand to Dell", "Partner handoff"), ("Prep for a Meeting", "Everything we know, one page"), ("Draft a Follow-Up", "Next touch, in your voice")]),
]

def link(label):
    return "claude://cowork/new?q="+urllib.parse.quote(canonical_prompt(label),safe="")
def tiles_html(btns):
    out=[]
    for lbl,sub in btns:
        href=link(lbl); pj=json.dumps(canonical_prompt(lbl))
        assert len(urllib.parse.quote(canonical_prompt(lbl),safe=""))<14000, lbl
        out.append(f'''<div class="tile"><a class="tile-main" target="_blank" rel="noopener" href="{html.escape(href)}"><span class="tile-label">{html.escape(lbl)}</span><span class="tile-sub">{html.escape(sub)}</span></a><button class="tile-copy" data-prompt={html.escape(pj,quote=True)} title="Copy the prompt">copy</button></div>''')
    return ''.join(out)
def section(name,btns,cls="group"):
    return f'''<section class="{cls}"><div class="group-head"><span class="bar"></span>{html.escape(name)}</div><div class="grid">{tiles_html(btns)}</div></section>'''

lookup={lbl:sub for _,bs in GROUPS for (lbl,sub) in bs}
EVERYDAY=[(lbl,lookup[lbl]) for lbl in ["What's Waiting On Me","Log My Day","New Prospect","Draft a Follow-Up","Prep for a Meeting","New Vendor"]]
BOARDS=[
 ("Open Morning Brief","Your live morning few"),
 ("Open the Dashboard","Your whole system, live"),
 ("Open the Lead Board","This week, hot, segments"),
 ("Open the Deal Room","Signed, on deck, hot"),
]
everyday_html=section("Start here, your everyday few",EVERYDAY,"group everyday")
boards_html=section("See the boards",BOARDS)
more_html=''.join(section(g,b) for g,b in GROUPS)
nbtn=len(EVERYDAY)+len(BOARDS)+sum(len(b) for _,b in GROUPS)

HTML=f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CARR | The Front Door</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Oswald:wght@400;500;600;700&family=Montserrat:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  /* CARR design tokens (law 11: tokens before layout). Consume these; do not hardcode new colors/fonts. */
  :root{{
    --navy:#002F6C;--navy-deep:#00224D;--orange:#F57F29;
    --color-text:#ffffff;--color-muted:rgba(255,255,255,.62);--focus:#FF9D4D;
    --color-success:#3FB68B;--color-warning:#F5B841;--color-danger:#E5533D;
    --head:'Oswald','Archivo Narrow',sans-serif;--body:'Montserrat','Helvetica Neue',Arial,sans-serif;
    --space-1:4px;--space-2:8px;--space-3:12px;--space-4:16px;--space-6:24px;--space-8:32px;
    --radius-sm:7px;--radius-md:10px;--radius-lg:14px;
    --shadow-sm:0 2px 8px rgba(0,15,40,.25);--shadow-md:0 12px 30px rgba(0,15,40,.35);--shadow-lg:0 16px 34px rgba(0,15,40,.4);
    --duration-fast:.12s;--duration-base:.2s;--ease-out:cubic-bezier(.2,.7,.3,1);
  }}
  *{{margin:0;padding:0;box-sizing:border-box;-webkit-font-smoothing:antialiased;}}
  a:focus-visible,button:focus-visible,summary:focus-visible{{outline:3px solid var(--focus);outline-offset:3px;border-radius:var(--radius-sm);}}
  :focus:not(:focus-visible){{outline:none;}}
  html,body{{min-height:100%;}}
  body{{padding:36px 20px 64px;background:radial-gradient(120% 90% at 82% 0%,#063a82 0%,var(--navy) 46%,var(--navy-deep) 100%);background-attachment:fixed;font-family:var(--body);color:#fff;}}
  .wrap{{max-width:900px;margin:0 auto;}}
  .head{{text-align:center;margin-bottom:8px;}}
  .wordmark{{font-family:var(--head);font-weight:700;letter-spacing:.16em;font-size:20px;text-transform:uppercase;}}
  .wordmark .b{{display:inline-block;width:30px;height:4px;background:var(--orange);border-radius:2px;vertical-align:middle;margin-left:9px;transform:translateY(-3px);}}
  h1{{font-family:var(--head);font-weight:600;font-size:46px;line-height:1.02;margin-top:14px;letter-spacing:-.01em;}}
  .lede{{margin:14px auto 4px;max-width:600px;font-size:15px;line-height:1.5;color:rgba(255,255,255,.72);}}
  .group{{margin-top:32px;}}
  .group.everyday{{margin-top:30px;padding:20px;border-radius:16px;background:linear-gradient(160deg,rgba(245,127,41,.10),rgba(245,127,41,.03));border:1px solid rgba(245,127,41,.28);}}
  .group-head{{font-family:var(--head);font-weight:600;text-transform:uppercase;letter-spacing:.14em;font-size:15px;color:var(--orange);display:flex;align-items:center;margin-bottom:16px;}}
  .group-head .bar{{display:inline-block;width:26px;height:3px;background:var(--orange);border-radius:2px;margin-right:12px;}}
  .grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;}}
  @media(max-width:640px){{.grid{{grid-template-columns:1fr;}}h1{{font-size:38px;}}}}
  .tile{{position:relative;border-radius:14px;background:linear-gradient(160deg,rgba(255,255,255,.07),rgba(255,255,255,.025));border:1px solid rgba(255,255,255,.10);transition:transform .12s ease,box-shadow .12s ease,border-color .12s ease;}}
  .tile:hover{{transform:translateY(-2px);border-color:rgba(245,127,41,.55);box-shadow:0 16px 34px rgba(0,15,40,.4);}}
  .tile-main{{display:block;padding:20px 20px 18px;text-decoration:none;color:#fff;}}
  .tile-label{{display:block;font-family:var(--head);font-weight:600;font-size:21px;letter-spacing:.01em;}}
  .tile-sub{{display:block;margin-top:4px;font-size:13px;color:rgba(255,255,255,.6);}}
  .tile-copy{{position:absolute;top:12px;right:12px;background:transparent;border:1px solid rgba(255,255,255,.22);color:rgba(255,255,255,.66);font-family:var(--head);font-weight:500;text-transform:uppercase;letter-spacing:.08em;font-size:11px;padding:4px 9px;border-radius:7px;cursor:pointer;transition:all .12s ease;}}
  .tile-copy:hover{{border-color:var(--orange);color:var(--orange);}}
  .tile-copy.done{{border-color:var(--orange);color:var(--orange);background:rgba(245,127,41,.12);}}
  details.more{{margin-top:34px;}}
  details.more>summary{{list-style:none;cursor:pointer;font-family:var(--head);font-weight:600;text-transform:uppercase;letter-spacing:.12em;font-size:14px;color:var(--orange);display:inline-flex;align-items:center;gap:12px;padding:13px 22px;border:1px solid rgba(245,127,41,.4);border-radius:10px;transition:all .12s ease;}}
  details.more>summary:hover{{background:rgba(245,127,41,.10);}}
  details.more>summary::-webkit-details-marker{{display:none;}}
  details.more>summary::after{{content:"+";font-size:20px;line-height:1;}}
  details.more[open]>summary::after{{content:"\\2013";}}
  .foot{{margin-top:40px;font-size:13px;line-height:1.6;color:rgba(255,255,255,.55);text-align:center;max-width:640px;margin-left:auto;margin-right:auto;}}
  .foot b{{color:rgba(255,255,255,.8);font-weight:600;}}
  #toast{{position:fixed;left:50%;bottom:28px;transform:translateX(-50%) translateY(20px);opacity:0;pointer-events:none;background:var(--orange);color:var(--navy-deep);font-family:var(--head);font-weight:600;letter-spacing:.02em;font-size:14px;padding:12px 22px;border-radius:10px;box-shadow:0 12px 30px rgba(245,127,41,.4);transition:all .2s ease;}}
  #toast.show{{opacity:1;transform:translateX(-50%) translateY(0);}}
  @media(prefers-reduced-motion:reduce){{
    *,*::before,*::after{{transition-duration:.001ms !important;animation-duration:.001ms !important;animation-iteration-count:1 !important;scroll-behavior:auto !important;}}
    .tile:hover{{transform:none;}}
  }}
</style>
</head>
<body>
  <div class="wrap">
    <div class="head">
      <div class="wordmark">CARR<span class="b"></span></div>
      <h1>The Front Door</h1>
      <p class="lede">Tap what you need. A new session opens with the task written in, you review, hit send, and answer one question at a time. Start with your everyday few. The full set is under All actions.</p>
    </div>
    {everyday_html}
    {boards_html}
    <details class="more"><summary>All actions</summary>{more_html}</details>
    <p class="foot">Every tile opens a fresh session with the canonical record workflow loaded, and nothing sends on its own, so you always review first. Each session tells you in plain words what it changed, and you can say <b>undo</b> to reverse the last thing it did. If a tile does not open the app, tap <b>copy</b> and paste into a new session.</p>
  </div>
  <div id="toast">Prompt copied</div>
<script>
  var toast=document.getElementById('toast');
  function flash(){{toast.classList.add('show');setTimeout(function(){{toast.classList.remove('show');}},1400);}}
  document.querySelectorAll('.tile-copy').forEach(function(btn){{
    btn.addEventListener('click',function(e){{
      e.preventDefault();
      var text=btn.getAttribute('data-prompt');
      var ok=function(){{btn.classList.add('done');btn.textContent='copied';flash();setTimeout(function(){{btn.classList.remove('done');btn.textContent='copy';}},1600);}};
      if(navigator.clipboard&&navigator.clipboard.writeText){{navigator.clipboard.writeText(text).then(ok).catch(function(){{fallback(text,ok);}});}}
      else{{fallback(text,ok);}}
    }});
  }});
  document.querySelectorAll('.tile-main').forEach(function(a){{a.addEventListener('click',function(){{try{{var q=(a.getAttribute('href').split('q=')[1]||'');var text=decodeURIComponent(q);var ta=document.createElement('textarea');ta.value=text;ta.style.position='fixed';ta.style.opacity='0';document.body.appendChild(ta);ta.focus();ta.select();document.execCommand('copy');document.body.removeChild(ta);toast.textContent='Prompt copied. Paste into the new chat (Cmd+V)';flash();setTimeout(function(){{toast.textContent='Prompt copied';}},2800);}}catch(e){{}}}});}});
  function fallback(text,ok){{var ta=document.createElement('textarea');ta.value=text;ta.style.position='fixed';ta.style.opacity='0';document.body.appendChild(ta);ta.focus();ta.select();try{{document.execCommand('copy');ok();}}catch(err){{}}document.body.removeChild(ta);}}
</script>
</body>
</html>'''

open(OUTPUT,'w',encoding='utf-8').write(HTML)
print("generated ->", OUTPUT)
visible=' '.join(re.findall(r'>([^<>]+)<',HTML))
assert '—' not in visible, "em-dash in visible copy"
low=visible.lower()
hits=[b for b in ['delve','seamless','unlock','elevate','leverage','robust','tapestry','realm'] if b in low]
hrefs=[html.unescape(x) for x in re.findall(r'href="(claude://[^"]+)"',HTML)]
nofolder=all('&folder=' not in x for x in hrefs)
allcanonical=all(urllib.parse.parse_qs(urllib.parse.urlparse(x.replace('claude://','https://')).query)['q'][0].startswith("Use the CARR record-layer verbs") for x in hrefs)
print("total tiles:",nbtn,"| links:",len(hrefs),"| AI-tell:",hits,"| folder none:",nofolder,"| canonical all:",allcanonical)
print("has everyday:","everyday" in HTML,"| has details:","<details" in HTML,"| boards:","See the boards" in HTML)
