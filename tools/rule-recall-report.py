#!/usr/bin/env python3
"""Render the recall audit from explicit read-only snapshots; no record writes."""
import argparse
import csv
import hashlib
import importlib.util
import re
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads(Path(path).read_text())


REDACTIONS: list[tuple[re.Pattern[str], str]] = []
# Section keys, titles and document names can identify a person; the
# published audit carries the stable ID and counts only.
PUBLIC_SECTION_FIELDS = {"id", "version", "observed_read_calls", "status"}


def public_sections(sections):
    return [{k: s[k] for k in sorted(PUBLIC_SECTION_FIELDS)} for s in sections]


def redact(value):
    if isinstance(value, str):
        for pattern, alias in REDACTIONS:
            value = pattern.sub(alias, value)
        return value
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    return value


def prepare_labels(values):
    spec = importlib.util.spec_from_file_location("client_label_names", ROOT / "ops/githooks/client-name-warn.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tokens = " " + " ".join(module.tokens(json.dumps(values, ensure_ascii=False))) + " "
    names = sorted((name for name in module.load_names() if " " + name + " " in tokens), key=len, reverse=True)
    REDACTIONS[:] = [(re.compile(r"\b" + r"[^a-zA-Z0-9]*".join(name.split()) + r"\b", re.I),
                      "Historical example " + str(i + 1)) for i, name in enumerate(names)]


def cell(value):
    text = redact(str(value))
    if text.startswith("https://github.com/jbookout/carr-system/pull/"):
        return '<a href="' + html.escape(text, quote=True) + '">PR #' + html.escape(text.rsplit('/', 1)[-1]) + '</a>'
    return html.escape(text)


def table(headers, rows):
    return '<div class="scroll"><table><thead><tr>' + ''.join('<th>' + html.escape(x) + '</th>' for x in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + cell(c) + '</td>' for c in row) + '</tr>' for row in rows) + '</tbody></table></div>'


def page(title, subtitle, body):
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>''' + html.escape(title) + '''</title><style>
:root{color-scheme:dark;font:16px/1.55 system-ui;background:#080d18;color:#e4eaf5}body{margin:0}main{max-width:1500px;margin:auto;padding:42px 28px}h1{font-size:clamp(30px,5vw,52px);letter-spacing:-.04em;margin:12px 0}h2{margin-top:38px;color:#a8c6ff}p{max-width:1000px}nav{display:flex;gap:20px;flex-wrap:wrap}a{color:#a8c6ff}small{color:#99a9c4}.stat{font-size:32px;color:#65dcb4}.card{border:1px solid #273650;background:#10192a;border-radius:14px;padding:20px;margin:24px 0}.scroll{overflow:auto;max-height:720px;border:1px solid #26344b;border-radius:12px}table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:12px 14px;text-align:left;vertical-align:top;border-bottom:1px solid #26344b;min-width:90px}th{position:sticky;top:0;background:#152139;z-index:1}tr:hover{background:#152139}input{font:inherit;border-radius:8px;border:1px solid #415678;background:#10192a;color:inherit;padding:12px;width:min(92%,600px);margin:14px 0}button{font:inherit;background:#203553;color:inherit;border:1px solid #415678;border-radius:8px;padding:10px;cursor:pointer}details{margin:20px 0}summary{cursor:pointer}footer{margin-top:40px;color:#9faec5}@media(prefers-reduced-motion:no-preference){main{animation:appear .3s ease}a,button{transition:background .15s} @keyframes appear{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}}
</style><main><nav><a href="followup.html">Earlier exercise</a><a href="dead-rules.html">Delivery silence</a><a href="design.html">Guaranteed delivery</a></nav><h1>''' + html.escape(title) + '</h1><p>' + html.escape(subtitle) + '''</p><label>Search all rows <input id="filter" type="search" placeholder="Rule, source, status or subject"></label><button onclick="document.getElementById('filter').value='';filterRows('')">Clear search</button>''' + body + '''<footer>Read-only audit • 5 October 2026 • No rule amendments, retirements, production installation or merge. Counts are snapshot evidence; empty observations do not establish obsolete policy. Historical client labels are pseudonyms; stable IDs locate canonical records.</footer></main><script>function filterRows(q){q=q.toLowerCase();document.querySelectorAll('tbody tr').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(q))}document.getElementById('filter').addEventListener('input',e=>filterRows(e.target.value))</script></html>'''


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--evidence', type=Path, required=True)
    ap.add_argument('--triage', type=Path, required=True)
    args = ap.parse_args()
    p = args.evidence
    standing, usage, benchmark = (load(p / name) for name in ('standing.json', 'usage.json', 'benchmark.json'))
    rules = {r['id']: r for r in standing['shared_rules'] + standing['personal_rules']}
    classes = load(ROOT / 'ops/config/rule-classes.v1.json')['rules']
    routes = load(ROOT / 'ops/config/rule-routes.v1.json')['rules']
    prs = {x['number']: x for name in ('prs.json', 'wr19-prs.json') for x in load(p / name) if 'error' not in x}
    recommendations = []
    for n, pr in sorted(prs.items()):
        status = 'built' if pr.get('mergedAt') else 'not built'
        why = 'Source merged; this proves the named source slice, not whole-program delivery or installation.' if status == 'built' else 'PR remains unmerged; no completion claimed.'
        if n in (278, 286, 325, 727, 729, 1326, 1328, 1375):
            status = 'partly built'
            why = {278:'Typed records and lifecycle exist. Four claimed consumers were never built; September27 ruling replaces those phantom routes with ordinary rule delivery.',286:'Lifecycle/activation tooling exists, but typed classification did not provide consumption at the binding moment.',325:'Activation hardening shipped; active deferred guidance is not evidence of a working consumer.',727:'Core preview and budget check built in shadow. End-to-end relevant-rule recall remained unproven.',729:'Eligibility and batch staging built; zero unexplained omissions was not achieved. No permission to flip the selector follows.',1326:'Every old corpus row has a route shape, but route shape was confused with moment recall. Six counterexamples reproduced here; new active IDs also outgrew the static catalogue.',1328:'Index/full boot built for 71 rules. The refusal cap and outage escape allowed unread work; this PR closes those paths and retains all 199.',1375:'240-case benchmark and measurement shipped. 583/717 availability is the baseline finding, not a completed recall fix.'}[n]
        recommendations.append([pr['title'],pr['url'],status,pr.get('mergeCommit',{}).get('oid','unmerged') if pr.get('mergeCommit') else 'unmerged',why])
    prior = [
      ('Convert the42 unbuilt enforceable rules into refusal controls','audits/rule-enforceability-audit-2026-08-14.tsv; PR183,210,211','partly built','PR183 1e36d33d; PR210 d842897a; PR211 7eb37c3f','Individual backfills shipped; a class-D label still does not deliver binding text. PR124 installed the original one-repo Write control; its PR names Bash writes as uncovered and source edits as allowed.'),
      ('Make load-layer selection safe through a clean shadow week','rule-delivery-load-layers#the-three-layers; WR-000027; audits/rule-delivery-finding-cause-split-2026-08-27.md','partly built','PR725; drift observer and clean-week checker in source','All 57 epoch observations had findings; declarations alone left 51, trimming alone 55, both 16. No measured zero-miss completion was found.'),
      ('Disposition14 earlier shadow findings and install their source remedies','audits/rule-delivery-shadow-adjudication-2026-08-26.v1.json','partly built','Reviewed artifact and exact transcript/hash bindings present','Artifact explicitly appends no dispositions. Source remedies require merge plus installed readback; cannot infer those effects from the report.'),
      ('Read doctrine at explicit action moments and grade its coverage','decision65a81f32; Sept27 outcome audit','not built','Benchmark doctrine labels exist in cases.v2; no section event registry','Audit observed14/15 prompts lacking doctrine and3 doctrine verbs in2642 commands. Full 2272-section startup was rejected; selection must use explicit domain events.'),
      ('Finish an outcome audit instead of launching more control-plane systems','decision97bc04b5; federated census PR1467','partly built','PR1467 2f531c29; current census source','Inventory and dependencies built; findings remain work, not proof of implemented user outcomes. No child work request created here.'),
      ('Keep rules until held-out and real-turn relevant recall reaches100%','decision974bb6c8; decision6be835ff','partly built','This PR: policy, fail-closed boot, renderer and72-case benchmark','Before this change proof was global/absent. Current real-turn applicability labels are insufficient for any exemption; every rule stays full boot.'),
      ('Retire four phantom typed-guidance consumers; preserve active source rules','decisione6d45284; guidance registry active manifest','partly built','Live manifest has132 deferred entries; original active rules still available','Procedure launcher, reviewer rubric, partner preference and precedent consumers were promised as architecture; never established as four active delivery consumers.'),
      ('Run monthly promote/prune, conflict, obsolescence and usage audits','playbook-review#run-procedure-the-routine-itself; run ledger; sweep-sop','partly built','Store procedure and August15 ledger; PR219,225,358 in precedents','Ledger still describes retiring Drive size rows and old paths. No September completion receipt was found in the searched ledger/precedents; direct read counts are not run receipts.'),
      ('Apply July15 card-text ban-list promotion','playbook-review run ledger: July15 item1','not built','Approved recommendation; loop83 named by ledger','Ledger says read-only scheduled skill prevented application. Later completion not established by this audit.'),
      ('Remove Make.com framing and duplicate lead-system rule copy','playbook-review run ledger: July15 items2–3','partly built','T27 and closed loop62 recorded in ledger','Duplicate loop62 closed; shared-DNA Make.com change proposed, no exact later applied readback in searched evidence.'),
      ('Correct generated core measurement, split hot/backlog and retire snapshots at release','sweep-sop July16/August15 ledger','built','Store ledger records before/after and exact governed targets','Historical fixes executed; later surface migration makes those byte budgets stale rather than proving current policy failure.'),
      ('Rebuild the system report card before the September audit','find-precedent ruling: report-card rubric drift, August6','not built','Ruling requires rebuilt instrument or no September run','No completion receipt in the searched playbook ledger and precedents. Scope-specific source delivery remains unverified.'),
      ('Replace semantic guesswork with startup plus exact action triggers and coverage CI','decision33cd8385; PR1326,1328,1529','partly built','Deterministic selector, route coverage and boot source present','Coverage checks prove reachability/shape, not every applicable occurrence or live adapter invocation; this PR adds retention as the safety invariant.'),
      ('Strengthen steering-eval evidence against denominator shrinkage and stale summaries','Dot10031759-R-study-pstack.txt recommendation4; PR1507','built','PR1507; current independently recomputed paired cohorts','Current source checks frozen expectations and source replay. This PR keeps JIT labels fixed when expanding boot.'),
      ('Apply the context audit deletion, conflicts, outcome and workaround tests','2026-07-30-context-audit-unhobbling; playbook-review context audit','partly built','Four prompts and guard integrated into doctrine','Procedure text exists; whole-corpus contradiction and one-at-a-time removal experiments have no completed outcome receipt here. Startup shrink remains deferred by Oct5 ruling.'),
      ('Complete deferred playbook/factory verification recommendations without another rule corpus','Dot10031759-R-study-pstack.txt recommendations1–10 and50 skill rows','partly built','Existing Design Manager specification; CARR eval fixes merged','Cross-repository verification is product-specific and not delivered by installing skills. Do not build factory work in this recall PR.'),
      ('Finish old evidence and census branches against current source','Dot03-L-unfinished-branch-plans.txt; Dot050-T-triage-04.txt','partly built','PR1467 current federated census; old branches individually referenced by reports','Reports recommend redesign/rebase, not merging old branch tips. Branch source/operational authority dependencies are recorded; source merge alone is not installed proof.'),
      ('Close review-required trigger and model-effect control-flow gaps','Dot10041832-AF-carr-system-1531.txt; PR1529,1533','partly built','Current deterministic hook budgets; sibling precision owns selector follow-through','Report is static counterexample evidence. Cache/evidence findings require exact current-source validation; not all are recall work.'),
    ]
    recommendations.extend(prior)
    with (ROOT / 'audits/guidance-migration-manifest.v1.tsv').open() as handle:
        migration = list(csv.DictReader(handle, delimiter='\t'))
    for row in migration:
        recommendations.append([row['plain_name']+' → '+row['proposed_type'], 'guidance-migration-manifest.v1.tsv:'+row['source_id'], 'partly built', 'PR278/286/325; typed registry lifecycle', 'Consumer: '+row['consumer']+'. Trigger: '+row['activation_trigger']+'. Verification: '+row['verification']+'. Source '+('active in current scope' if row['source_id'] in rules else 'absent from current Joe-scoped active set; no lifecycle inference')+'. Four deferred consumer families never established as complete routes.'])
    triage = load(args.triage)
    for row in triage['rows']:
        recommendations.append([row['name']+' → '+row['verdict'], 'rules-triage/triage.json:'+row['short_id'], 'built' if row['verdict']=='ALREADY-ENFORCED' else 'not built', '; '.join(row.get('evidence',[])), row['reason']+' Audit-only82 proposed rules; no activation, retirement or source effect performed by that exercise.'])
    doc_sources = [x.name for x in p.glob('doc-*.json')]
    collections = ['Live standing-context full and all 7 boot pages (168 shared, 31 personal)','doctrine-index: 265 documents; active section catalogue: 2281','search-doctrine: 7 queries; retrieve live canonical search','Full doctrine reads: '+', '.join(doc_sources),'Decision history: 40 guidance/routing/playbook/prose-related records; find-precedent: 8 playbook rulings','loop-board (60 visible rows), loop-headers (14 blocks), current-work-requests (captured inventory; closed WR19 recovered through history)','GitHub: 22 candidate PR reads, 28 WR19 PR search hits, all-state guidance/rule delivery/prose PR search; PR124 recovered on bounded retry','Full git history of rule-classification.v1.csv, rule-classes.v1.json and rule-enforcement-map.json','Tracked audits: enforceability, 93-row typed manifest, curation review/batch, shadow adjudication and cause split','Canonical out/ report path census, rulebench report/results, rules-triage 82-row report, unfinished census brief','Model Room / Dot: 478 report files searched for rule/guidance/prose/playbook/doctrine audit terms; 6 reports matched; related recommendations attributed in table','Local delivery logs, hook telemetry, gate decisions, route receipts and CARR Claude project transcripts: '+str(len(usage['inventory']))+' files (individual sanitized names in inventory.json)']
    prepare_labels([recommendations, usage['sections'], classes])
    (p / 'followup-data.json').write_text(json.dumps(redact({'label_status':'Client names are pseudonyms; source IDs remain canonical','collections':collections,'recommendations':recommendations}),indent=2)+'\n')
    body = '<div class="card"><span class="stat">'+str(len(recommendations))+'</span> recommendation rows recovered. Source delivery repeatedly preceded proof that guidance reached its binding moment.</div>'
    body += '<h2>Collections searched</h2><ul>'+''.join('<li>'+html.escape(x)+'</li>' for x in collections)+'</ul><p>Negative findings are bounded by these collections. Search limits, unlogged retrieval and provider history prevent an exhaustive claim about all possible historical evidence. Source merge, store admission and live consumption are separate facts.</p>'
    body += '<h2>Recommendation → delivery evidence → remaining work</h2>'+table(['Recommendation','Source','Status','Commit / PR evidence','Why unfinished or scope of completion'],recommendations)
    (p / 'followup.html').write_text(page('The earlier exercise did produce work','The missing follow-through was proving consumption, closing audit findings, and completing deferred consumers. This table preserves the work that did ship.',body))
    zeros = set(usage['deliveries_30d']['zero'])
    mechanical = {rid for rid,r in routes.items() if r.get('note','').startswith('Candidate event route')}
    dead = []
    design = []
    for rid, rule in sorted(rules.items()):
        cls = classes.get(rid,{})
        entry = routes.get(rid,{'routes':[],'moment':'new active rule: no committed route'})
        events = [r for r in entry['routes'] if r['kind'] in ('trigger','path_rule')]
        gates = [r.get('gate') for r in entry['routes'] if r['kind']=='gate']
        exact = bool(events) and cls.get('class') in ('b','d')
        q1 = 'YES: named tool/verb/path/command event; semantic clauses still retained' if exact else 'NO: current moment needs judgment or has no complete event binding'
        q2 = 'not reached' if exact else ('YES: frequent conduct/judgment' if cls.get('always_on') else 'NO: occasional subject/judgment')
        q3 = 'not reached' if exact or cls.get('always_on') else 'Retain boot; declare the subject before '+entry['moment']+'; propose that event contract to Joe'
        route_names = '; '.join(json.dumps(x,sort_keys=True,separators=(',',':')) for x in entry['routes']) or 'no current route'
        design.append([cls.get('summary','Unclassified rule')+' ('+rid+')',entry['moment'],q1,q2,q3,route_names,'FULL BOOT before every ordinary tool; no proven exemption', 'candidate repaired here' if rid in mechanical else 'existing candidate / boot fallback'])
        if rid == '99e951b9':
            cause,remedy='Filesystem index subject needs review against current record homes','Mechanical write route repaired; propose generated/database index contract to Joe; retain boot'
        elif rid in mechanical:
            cause = 'Wrong binding moment: gate shape existed without a matching pre-event full-text route'
            remedy = 'Mechanical candidate route repaired here; full boot retained; prove replacement before any removal'
        elif any(r['kind']=='duplicate' for r in entry['routes']):
            cause = 'Duplicate of '+next(r['survivor'] for r in entry['routes'] if r['kind']=='duplicate')
            remedy = 'Merge/retire proposal for Joe; retain full boot until approved'
        elif not events:
            cause,remedy='Prose with no detectable moment / gate-only receipt path','Retain full boot; do not infer retirement from silence'
        elif cls.get('class')=='c':
            cause,remedy='Wrong binding moment: topic judgment routed to a later tool proxy','Keep full boot; propose earlier explicit subject event; zero occurrences unproven'
        else:
            cause,remedy='Candidate trigger is reachable; occurrence or telemetry gap unproven','Keep with reason: infrequent business event remains valid; inspect adapter and receipts, never auto-retire'
        dead.append([cls.get('summary','Unclassified rule')+' ('+rid+')',usage['deliveries_30d']['counts'][rid],usage['deliveries_14d']['counts'][rid], str(usage['gate_firings_30d'][rid])+' attributed; total unknown',cause if rid in zeros else 'Observed full-text deliveries; no dead-rule claim',remedy if rid in zeros else 'Keep; full boot until complete replacement proof'])
    sections = usage['sections']
    unread = [s for s in sections if not s['observed_read_calls']]
    read_docs = {s['slug'] for s in sections if s['observed_read_calls']}
    unread_docs = {s['slug'] for s in sections} - read_docs
    sections = public_sections(sections)
    sanitized = [{'collection':str(Path(x['collection']).relative_to(Path.home())) if str(x['collection']).startswith(str(Path.home())+'/') else x['collection'],**{k:v for k,v in x.items() if k!='collection'}} for x in usage['inventory']]
    (p / 'inventory.json').write_text(json.dumps(sanitized,indent=2)+'\n')
    (p / 'rule-data.json').write_text(json.dumps(redact({'label_status':'Display metadata with client pseudonyms; use stable section IDs for canonical lookup','rules':dead,'routes':design,'sections':sections,'unread_document_count':len(unread_docs),'window_start':usage['deliveries_30d']['start'],'window_end':usage['now'],'limitations':usage['limitations']}),indent=2)+'\n')
    body = f'<div class="card"><span class="stat">{len(zeros)} / {len(rules)}</span> active rules with zero observed full-text deliveries in 30 days.<br>{len(unread)} / {len(sections)} active doctrine sections with no observed direct read request. {len(unread_docs)} documents have no observed direct section or document read.</div>'
    body += '<p>Window: '+html.escape(usage['deliveries_30d']['start'])+' through '+html.escape(usage['now'])+'. Active means the live Joe-scoped standing-context set: 199, not the 210-row committed evaluation corpus. Deliveries are deduplicated full-text receipts and complete boot observations. Route matches, summaries and overflow pointers do not count.</p>'
    body += '<p>'+str(usage['unattributed_gate_firings'])+' refusals lack rule attribution. Per-rule attributed zeros therefore cannot establish “never fired.” Hook invocation meters have no rule IDs. Topic absence, wiped history, tool-only sessions and search/retrieve may leave reads unobserved.</p>'
    body += '<h2>All active rules: observed counts, cause and remedy</h2>'+table(['Rule','Full-text deliveries in 30 days','Full-text deliveries in 14 days','Gate firings in 30 days','Cause (zero rows only)','Remedy'],dead)
    body += '<h2>Every active doctrine section, including playbooks</h2><p>These count direct read requests in available CARR project transcripts. A request is not proof of successful response or obedience. Zero is a review candidate, never permission to retire. Freshness/version and task relevance govern the next read.</p>'+table(['Stable section ID','Version','Observed read calls','Status'],[[s['id'],s['version'],s['observed_read_calls'],s['status']] for s in sections])
    body += '<details><summary>Named source inventory and parsing limits</summary>'+table(['Collection','Rows','In window','Invalid / missing'],[[x['collection'],x.get('rows'),x.get('in_window'),str(x.get('invalid',0))+(' missing' if x.get('missing') else '')] for x in sanitized])+'</details>'
    (p / 'dead-rules.html').write_text(page('Find silence without inventing obsolescence','Delivery and enforcement are measured separately. Missing attribution is reported as unknown; no rule is retired by this audit.',body))
    body = f'<div class="card"><span class="stat">583 / 717 → {benchmark["available_applications"]} / 717</span><br>81.3% → 100% full-text availability on the same 72 held-out cases. Candidate boot: 199 rules, {benchmark["approx_tokens"]:,} estimated tokens, {benchmark["pages"]} pages; explicit 80,000-token budget.</div>'
    body += '<p>The guarantee is delivery before an ordinary tool effect when the installed adapter invokes the gate. Every rule stays in full boot until a replacement is proven. Outages, three refusals, missing deployment and unwritable state now hold the effect. Rule reads and discovery stay available. This does not prove that a model obeys every rule, that every client invokes the adapter, or that production has this unmerged change.</p>'
    body += '<h2>One invariant; two separate measurements</h2>'+table(['Stage','Condition','Result'],[['Boot','All live binding text read, complete digest/pages/length verified','Allow ordinary tools'],['Replacement admission','Not built. Committed proof files cannot show that a replacement route ran; admission needs native, independently verifiable occurrence evidence and route receipts that exclude boot','Every rule keeps full boot'],['Health','Zero observed full-text deliveries in 14 days','Dedup open/update loop owned by orchestrator; listed remediation, verification and auto-clear in row']])
    body += '<p>JIT precision keeps its frozen owed-rule labels. Larger startup availability cannot remove labels or count as a precision improvement. The baseline report measured 583/717 on this frozen split; after is independently rendered from the live 199-rule snapshot. No rule yet has sufficient real-turn applicability labels, so no exemption is admitted.</p>'
    body += '<h2>Ordered decision procedure for all 199 rules</h2><p>Q1: detectable named event? Q2: judgment applying nearly every turn? Q3: what earlier structured event or approved rewrite would make it detectable, otherwise retain boot? Routes below name candidates; existence is never credited as 100% moment coverage. The full boot protects semantic clauses while each narrower route earns its proof.</p>'+table(['Rule','Moment','Q1','Q2','Q3','Named route / gate','Guaranteed current delivery','Source disposition'],design)
    body += '<h2>Frozen benchmark observations</h2>'+table(['Case','Required','Full text available','Missing'],[[x['id'],len(x['gold']),len(x['available']),', '.join(x['missing']) or 'none'] for x in benchmark['observations']])
    body += '<h2>Proof and remaining operational limits</h2><p>Fixture SHA256: '+benchmark['fixture_sha256']+'. Boot digest: '+benchmark['boot_digest']+'. Full boot cost rises from 34,845 to '+f'{benchmark["approx_tokens"]:,}'+' estimated tokens. No paid Claude call or Jev call was used. Tests prove fail-closed behavior and counterexamples, not installation. Current real logs are before-change evidence; real-turn applicability proof and distinct live Claude/Codex invocation remain required before shrinking boot.</p>'
    (p / 'design.html').write_text(page('Guarantee delivery by retaining unproven rules','No route replaces startup text: an admission path needs native occurrence evidence that does not yet exist. There are zero exemptions.',body))
    print(json.dumps({'recommendations':len(recommendations),'rules':len(design),'zero':len(zeros),'sections':len(sections),'unread_sections':len(unread),'unread_documents':len(unread_docs)}))


if __name__ == '__main__':
    main()
