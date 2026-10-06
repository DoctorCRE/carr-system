"""rule_delivery_eval.py — score every rule-delivery path against gold labels.

WHY THIS EXISTS (Joe, 2026-09-26: "I'm not convinced that our rule system is
effective enough in its current state"). Rules reach a session by several
independent paths, and until this file none of them had a labelled precision
or recall: the drift observer logs "needed but not loaded" from a keyword
classifier, the Jev selectors log what they surfaced, and the nightly replay
is deterministic with its judge stubbed. Each log measures a path against
itself. This measures every path against the same hand-labelled answer key.

WHAT A CASE IS. A situation — the partner's prompt (or a machine
notification), plus, when there is one, the tool calls the session made in
that turn — and the gold set of rule ids whose condition actually binds
there. `disputed` ids are excluded from both sides of the count.

REPLAY, NOT RE-IMPLEMENTATION (Jev, architecture_or_design, 2026-09-26: 1.00
for replaying real code over re-implementing it or scoring old logs). Each
adapter calls the production selection function itself:

  prompt_compiled   ops/rule_trigger_delivery.advise with the Jev judgment
                    disabled (compiled prompt_regex rows + always-on only),
                    then the hook's own pack-layer filter
                    (lib/rule_delivery_preuse.semantic_delivery);
  prompt_full       the same call with the budgeted Jev judgment live — what
                    the UserPromptSubmit hook actually delivers;
  jit_pretooluse    hooks/rule-pack-preuse-reselection.py's own
                    matched_triggers()/_matches() on each tool call;
  layered_triggers  the same hook's route rail: routed_rule_ids() over
                    ops/config/rule-routes.v1.json, unioned with the compiled
                    table rows it carries in the same door call, on each tool
                    call (exact matches; no dedupe state is read). Charged for
                    gold of every layer, since routes cover every layer. The
                    current boot and historical layer-zero map are scored
                    beside it;
  drift_shadow      hooks/rule-pack-drift-gate.py as shipped: mode shadow
                    loads nothing;
  drift_if_acting   the same gate's evaluate(): the packs it says the turn
                    needed, delivered as those packs' pack-layer rules;
  boot_layer0       historical enforcement-map layer-zero selection.
  boot_always_on    current Joe-scoped always-on ids from rule-classes.v1.json,
                    the class contract used by standing-context's live boot.

DRY RUN. Nothing here writes a production log, cache or audit file: the
selector log goes to os.devnull, no session id is passed (so no dedupe or
verdict cache is read or written), Jev's per-call receipts go to a sink the
caller names, and jev_judge.record() is replaced by a no-op on the harness's
private copy of the module (its outage row would otherwise land in
out/jev-judge.jsonl). The selftest snapshots GUARDED_OUT_FILES before and
after a run and fails on any change. The standing-context door that fetches
selected rule text is not replayed. The actual boot adapter uses the same
committed class contract as the live Worker and excludes other partners'
personal rules; live rule statements remain in the store.

RESPONSIBILITY UNIVERSES. A path is charged only for gold rules it is
responsible for: the prompt and JIT paths for pack-layer rules (the current
always-on set is loaded at boot; control-layer rules are delivered by the gate
that enforces them), the legacy selector and the system rows for everything. A
delivered rule that is not gold is a false positive wherever it came from.

METRICS (Jev, verification_selection, 2026-09-26): the headline is SYSTEM
recall — the union of what actually reaches a session (0.84 over per-path
micro numbers); per-stratum recall (0.88); notification turns scored apart
from the pooled human numbers (0.85); pack-level scoring for the
pack-granular drift observer (0.86).

A LIBRARY. The command line is tools/rule-delivery-eval.py; this file carries
no entrypoint construct, for the sealed-inventory reason
ops/typesafe_client.py documents.
"""

import concurrent.futures as cf
import importlib.util
import json
import os
import re
from collections import Counter

CASES_SCHEMA = "rule-delivery-eval-cases/v1"
CASES_SCHEMA_V2 = "rule-delivery-eval-cases/v2"
REPORT_SCHEMA = "rule-delivery-eval-report/v1"
STRATA = ("engineering", "deals_clients", "comms", "scheduling", "notifications")
# v2 (cases.v2.json, dense gold over every live rule) is stratified by the
# kinds of turn the rule system actually misses on, not by v1's topics.
STRATA_V2 = ("engineering", "deals_clients", "chat_only", "merge_release",
             "agent_dispatch", "salesforce_browser", "tour_maps", "notifications")
STRATA_BY_SCHEMA = {CASES_SCHEMA: STRATA, CASES_SCHEMA_V2: STRATA_V2}
SPLITS = ("train", "test")
# A doctrine section ref: the store's document id, '#', its section id.
# Opaque ids on purpose: doctrine slugs and section keys carry person and
# practice names, which a committed fixture must not.
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
DOCTRINE_REF = re.compile(rf"^{_UUID}#{_UUID}$")
MACHINE_STRATA = ("notifications",)
# The adapters that can deliver doctrine refs (declared, not inferred from a
# run's output, so a doctrine path failing on every case still scores 0%).
DOCTRINE_PATHS = ("doctrine_search",)
EVAL_SESSION = "rule-delivery-eval"

# Files a dry run must never create or modify, relative to <repo>/out.
GUARDED_OUT_FILES = (
    "rule-trigger-delivery.jsonl", "rule-prompt-delivered.json",
    "jev-rule-select.jsonl", "jev-rule-select-cache.json", "jev-judge.jsonl",
    "rule-delivery-shadow.jsonl", "jev-calls.jsonl",
)

PACK_LAYER = "pack"


# ------------------------------------------------------------------ loading

def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_cases(path, split=None):
    """Validated cases from a cases file (schema CASES_SCHEMA or
    CASES_SCHEMA_V2) or a JSONL file of case objects. Raises ValueError on
    anything malformed.

    `split` ("train" or "test") keeps only that split of a v2 file. TUNING MAY
    READ ONLY THE TRAIN SPLIT; the test split is the held-out 30 per cent
    (ops/rule_gold_label.assign_splits) and exists to be scored, not studied."""
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    if str(path).endswith(".jsonl"):
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        strata = set(STRATA) | set(STRATA_V2)
    else:
        doc = json.loads(text)
        if doc.get("schema") not in STRATA_BY_SCHEMA:
            raise ValueError(f"{path}: schema is not {CASES_SCHEMA} or {CASES_SCHEMA_V2}")
        strata = set(STRATA_BY_SCHEMA[doc["schema"]])
        rows = doc.get("cases")
    if split is not None and split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{path}: no cases")
    seen = set()
    cases = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise ValueError(f"{path}: case without an id")
        if row["id"] in seen:
            raise ValueError(f"{path}: duplicate case id {row['id']}")
        seen.add(row["id"])
        if row.get("stratum") not in strata:
            raise ValueError(f"{row['id']}: unknown stratum {row.get('stratum')!r}")
        if row.get("split") is not None and row["split"] not in SPLITS:
            raise ValueError(f"{row['id']}: unknown split {row.get('split')!r}")
        if split is not None and row.get("split") != split:
            continue
        if not isinstance(row.get("prompt"), str):
            raise ValueError(f"{row['id']}: prompt must be a string")
        gold = row.get("gold")
        if not isinstance(gold, list) or not all(isinstance(g, str) for g in gold):
            raise ValueError(f"{row['id']}: gold must be a list of rule ids")
        calls = row.get("tool_calls") or []
        if not isinstance(calls, list) or not all(
                isinstance(c, dict) and isinstance(c.get("tool_name"), str) for c in calls):
            raise ValueError(f"{row['id']}: tool_calls must be [{{tool_name, tool_input}}]")
        # gold_doctrine: the second target set, doctrine section refs as
        # opaque store ids ("<document id>#<section id>"). A slug-shaped ref
        # is refused here, for any cases file: slugs carry names.
        doctrine = row.get("gold_doctrine") or []
        if not isinstance(doctrine, list) or not all(
                isinstance(ref, str) and DOCTRINE_REF.match(ref) for ref in doctrine):
            raise ValueError(f"{row['id']}: gold_doctrine must be ['<document id>#<section id>', ...] (store uuids, not slugs)")
        cases.append({"id": row["id"], "stratum": row["stratum"], "prompt": row["prompt"],
                      "tool_calls": calls, "gold": sorted(set(gold)),
                      "gold_doctrine": sorted(set(doctrine)),
                      "disputed": sorted(set(row.get("disputed") or [])),
                      **({"judged_rules": row["judged_rules"]} if "judged_rules" in row else {}),
                      "unjudged_rules": sorted(set(row.get("unjudged_rules") or [])),
                      **({"doctrine_judged": row["doctrine_judged"]} if "doctrine_judged" in row else {}),
                      "split": row.get("split")})
    if not cases:
        raise ValueError(f"{path}: no cases in split {split!r}")
    return cases


def rule_meta(repo, corpus=None):
    """{rule id: {"layer", "packs"}} from the reviewed map. With `corpus` (a
    list of live rule ids), rules the map does not tag are added as
    "untagged" so a gold label on them is still counted."""
    path = os.path.join(repo, "ops", "config", "rule-enforcement-map.json")
    with open(path, "r", encoding="utf-8") as handle:
        layers = json.load(handle).get("rule_load_layers") or {}
    meta = {rid: {"layer": row.get("load_layer") or "untagged",
                  "packs": sorted(row.get("packs") or [])}
            for rid, row in layers.items() if isinstance(row, dict)}
    for rid in corpus or ():
        meta.setdefault(rid, {"layer": "untagged", "packs": []})
    return meta


def boot_always_on_ids(repo, sponsor="joe"):
    """Ids the live boot would render in full for one sponsor.

    The runtime scopes active store rows first, then applies the class file's
    personal boundary; every rule in scope keeps its full text. The committed
    class file contains classified ids; this replays selection without store
    access.
    """
    path = os.path.join(repo, "ops", "config", "rule-classes.v1.json")
    with open(path, "r", encoding="utf-8") as handle:
        rows = json.load(handle)["rules"]
    return {rid for rid, row in rows.items() if row.get("personal_to") in (None, sponsor)}


def universes(meta, names, *, boot_ids=None):
    if "boot_always_on" in names and boot_ids is None:
        raise ValueError("boot_always_on universe requires current boot ids")
    pack = {rid for rid, row in meta.items() if row["layer"] == PACK_LAYER}
    everything = set(meta)
    layer0 = {rid for rid, row in meta.items() if row["layer"] == "layer0"}
    table = {"prompt_compiled": pack, "prompt_full": pack, "jit_pretooluse": pack,
             "drift_shadow": pack, "drift_if_acting": pack, "boot_layer0": layer0,
             "boot_always_on": set(boot_ids or ()),
             "layered_triggers": everything,
             "system_moment": everything,
             "system_moment_packlayer": pack,
             "system_moment_plus_drift": everything, "system_scoped_boot": everything,
             # The doctrine search door delivers doctrine only; it owes no rule.
             "doctrine_search": set()}
    return {name: table.get(name, everything) for name in names}


# ------------------------------------------------------------------ dry-run Jev

class JevProxy:
    """The typesafe client with every request's call receipt sent to a sink.

    Question builders are the real ones; only ask() is wrapped, so the
    production selector builds exactly the questions it builds live."""

    def __init__(self, tsc, calls_log):
        self._tsc = tsc
        self.calls_log = calls_log
        self.noul, self.choice, self.score = tsc.noul, tsc.choice, tsc.score

    def ask(self, state, questions, **kwargs):
        kwargs.pop("caller", None)
        kwargs.pop("version", None)
        kwargs["calls_log"] = self.calls_log
        semantic = _load(os.path.join(os.path.dirname(os.path.dirname(__file__)), "ops", "jev_semantic.py"), "jev_semantic_eval")
        return semantic.ask(state, questions, client=self._tsc, caller="rule_delivery_eval",
                            version="vendor-v1", **kwargs)


def _quiet_judge(repo, tag):
    """A private jev_judge whose record() writes nothing."""
    module = _load(os.path.join(repo, "ops", "jev_judge.py"), f"jev_judge_quiet_{tag}")
    module.record = lambda *args, **kwargs: {}
    return module


def _quiet_rule_trigger_delivery(repo, tag):
    """A private ops/rule_trigger_delivery whose Jev siblings cannot log."""
    rtd = _load(os.path.join(repo, "ops", "rule_trigger_delivery.py"), f"rtd_eval_{tag}")
    original = rtd._sibling

    def sibling(name):
        return _quiet_judge(repo, tag) if name == "jev_judge" else original(name)
    rtd._sibling = sibling
    return rtd


# ------------------------------------------------------------------ adapters

def _synthetic_turn(case):
    """Transcript-shaped records for one case: the prompt, then the calls."""
    user = {"type": "user", "message": {"role": "user", "content": case["prompt"]}}
    if case["prompt"].lstrip().startswith("<task-notification>"):
        user["origin"] = {"kind": "task-notification"}
    records = [user]
    if case["tool_calls"]:
        records.append({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"eval-{i}", "name": call["tool_name"],
             "input": call.get("tool_input")}
            for i, call in enumerate(case["tool_calls"])]}})
    return records


def _raise(*_args, **_kwargs):
    raise RuntimeError("Jev judgment disabled for this adapter")


def _run_verb(repo, verb, args):
    import subprocess
    proc = subprocess.run(["./run.sh", "call", verb, json.dumps(args)],
                          cwd=str(repo), capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=300)
    return json.loads(proc.stdout)


_DOC_IDS: dict[str, dict[str, str]] = {}


def doctrine_doc_ids(repo):
    """doc slug -> store document id, from one doctrine-index read (cached)."""
    key = str(repo)
    if key not in _DOC_IDS:
        docs = _run_verb(repo, "doctrine-index", {}).get("documents") or []
        _DOC_IDS[key] = {d["slug"]: d["id"] for d in docs
                         if d.get("slug") and d.get("id")}
    return _DOC_IDS[key]


def doctrine_search_refs(repo, prompt, limit=10):
    """What a session gets if it asks the doctrine door itself: search-doctrine
    on the turn's text through ./run.sh call (read-only, deterministic FTS).
    NOT an automatic path: it measures what the door would have found.
    Refs are opaque store ids (<document id>#<section id>), as in the gold."""
    query = " ".join(prompt.replace("<", " ").split())[:300]
    hits = _run_verb(repo, "search-doctrine",
                     {"q": query, "limit": limit}).get("hits") or []
    ids = doctrine_doc_ids(repo)
    return {f"{ids[h['doc_slug']]}#{h['section_id']}" for h in hits
            if h.get("section_id") and ids.get(h.get("doc_slug"))}


def build_adapters(repo, *, jev="off", client_factory=None, calls_log=os.devnull,
                   doctrine_search=False):
    """Adapters for every path. `jev` is "off" (deterministic paths only) or
    "live" (adds prompt_full). `client_factory(calls_log)`
    returns the Jev client; default is JevProxy over ops/typesafe_client."""
    import sys
    repo = str(repo)
    if repo not in sys.path:
        sys.path.insert(0, repo)
    from lib import rule_delivery_preuse as preuse
    from pathlib import Path
    repo_path = Path(repo)
    meta = rule_meta(repo)
    layer0 = {rid for rid, row in meta.items() if row["layer"] == "layer0"}
    pack_layer = {rid for rid, row in meta.items() if row["layer"] == PACK_LAYER}
    always_on = boot_always_on_ids(repo)

    def prompt_delivery(rtd, text, **kwargs):
        rows = rtd.advise(text, session_id=None, log_path=os.devnull, **kwargs)
        ids, packs = preuse.semantic_delivery(repo_path, [row["id"] for row in rows])
        return {"rules": set(ids), "packs": set(packs)}

    rtd_off = _quiet_rule_trigger_delivery(repo, "off")

    def prompt_compiled(case):
        return prompt_delivery(rtd_off, case["prompt"], rank=_raise, ask=_raise)

    hook = _load(os.path.join(repo, "hooks", "rule-pack-preuse-reselection.py"),
                 "preuse_hook_eval")

    def jit(case):
        rules = set()
        for index, call in enumerate(case["tool_calls"]):
            payload = {"hook_event_name": "PreToolUse", "tool_name": call["tool_name"],
                       "tool_input": call.get("tool_input"), "session_id": EVAL_SESSION,
                       "tool_use_id": f"eval-{case['id']}-{index}"}
            if hook._matches(payload):
                rules.update(hook.scheduled_rule_ids())
                continue
            rows = hook.matched_triggers(payload)
            if rows:
                rules.update(preuse.merge_trigger_delivery(rows)[2])
        return {"rules": rules, "packs": {p for r in rules for p in meta.get(r, {}).get("packs", [])}}

    def layered(case):
        rules = set()
        for index, call in enumerate(case["tool_calls"]):
            payload = {"hook_event_name": "PreToolUse", "tool_name": call["tool_name"],
                       "tool_input": call.get("tool_input"), "session_id": EVAL_SESSION,
                       "tool_use_id": f"eval-{case['id']}-{index}"}
            if hook._matches(payload):
                # The scheduled rail still owns a background Bash call.
                rules.update(hook.scheduled_rule_ids())
                continue
            routed = hook.routed_rule_ids(payload)
            rows = hook.matched_triggers(payload)
            table = preuse.merge_trigger_delivery(rows)[2] if rows else []
            rules.update(routed)
            rules.update(table)
        return {"rules": rules, "packs": {p for r in rules for p in meta.get(r, {}).get("packs", [])}}

    drift = _load(os.path.join(repo, "hooks", "rule-pack-drift-gate.py"), "drift_gate_eval")
    triggers, members, _digest = drift.load_packs()

    def drift_needed(case):
        return drift.evaluate(_synthetic_turn(case), triggers, members)["needed"]

    def drift_shadow(case):
        drift_needed(case)  # the gate still runs; in shadow it loads nothing
        return {"rules": set(), "packs": set()}

    def drift_acting(case):
        needed = set(drift_needed(case))
        rules = {rid for pack in needed for rid in members.get(pack, []) if rid in pack_layer}
        return {"rules": rules, "packs": needed}

    def boot(case):
        return {"rules": set(layer0), "packs": set()}

    def live_boot(case):
        return {"rules": set(always_on), "packs": set()}

    adapters = [
        {"name": "prompt_compiled", "select": prompt_compiled, "jev": False},
        {"name": "jit_pretooluse", "select": jit, "jev": False,
         "applies": lambda case: bool(case["tool_calls"])},
        {"name": "layered_triggers", "select": layered, "jev": False,
         "applies": lambda case: bool(case["tool_calls"])},
        {"name": "drift_shadow", "select": drift_shadow, "jev": False},
        {"name": "drift_if_acting", "select": drift_acting, "jev": False},
        {"name": "boot_layer0", "select": boot, "jev": False},
        {"name": "boot_always_on", "select": live_boot, "jev": False},
    ]
    if doctrine_search:
        adapters.append({"name": "doctrine_search", "jev": True,
                         "select": lambda case: {"rules": set(), "packs": None,
                                                 "doctrine": doctrine_search_refs(repo, case["prompt"])}})
    if jev == "live":
        if client_factory is None:
            tsc = _load(os.path.join(repo, "ops", "typesafe_client.py"), "tsc_eval")
            client_factory = lambda sink: JevProxy(tsc, sink)  # noqa: E731
        client = client_factory(calls_log)
        rtd_live = _quiet_rule_trigger_delivery(repo, "live")

        def prompt_full(case):
            return prompt_delivery(rtd_live, case["prompt"], client=client, ask=client.ask)

        adapters += [{"name": "prompt_full", "select": prompt_full, "jev": True}]
    elif jev != "off":
        raise ValueError("jev must be 'off' or 'live'")
    return adapters


def run_adapters(cases, adapters, *, workers=1):
    """({path: {case id: {"rules": set, "packs": set|None}}}, {path: {case id: error}}).

    A raising adapter is recorded and scored as delivering nothing — which is
    what the production hooks do when their selector fails."""
    deliveries, errors = {}, {}
    for adapter in adapters:
        applies = adapter.get("applies") or (lambda case: True)
        todo = [case for case in cases if applies(case)]
        out, errs = {}, {}

        def one(case, adapter=adapter):
            try:
                result = adapter["select"](case)
                return case["id"], {"rules": set(result.get("rules") or ()),
                                    "packs": (set(result["packs"]) if result.get("packs")
                                              is not None else None),
                                    "doctrine": set(result.get("doctrine") or ())}, None
            except Exception as exc:  # noqa: BLE001 - recorded, scored as empty
                return case["id"], {"rules": set(), "packs": set()}, type(exc).__name__
        if workers > 1 and adapter.get("jev"):
            with cf.ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(one, todo))
        else:
            results = [one(case) for case in todo]
        for case_id, result, error in results:
            out[case_id] = result
            if error:
                errs[case_id] = error
        deliveries[adapter["name"]] = out
        errors[adapter["name"]] = errs
    return deliveries, errors


SYSTEM_ROWS = ("system_moment", "system_moment_packlayer", "system_moment_plus_drift",
               "system_scoped_boot")


def add_system_rows(deliveries):
    """The system rows, built from the per-path deliveries.

    WHY "MOMENT" AND NOT "EVERYTHING IN CONTEXT". While ops.rule_delivery_policy
    is 'shadow', standing-context recites EVERY active rule at boot as a gist
    line (mcp-server/src/doctrine.js, the `enforcing` branch), so availability
    is total by construction and says nothing. What varies is whether the
    binding rule's full text is put in front of the session at the moment it
    binds, which is what the prompt and JIT paths do. So:

      system_moment            prompt path (the Jev judgment when run, else
                               the compiled rows) plus JIT, charged for gold
                               of every layer;
      system_moment_packlayer  the same deliveries, charged only for
                               pack-layer gold (the layer those paths serve);
      system_moment_plus_drift the same plus the drift observer acting;
      system_scoped_boot       the moment paths plus the current always-on
                               boot. Historical reports without that adapter
                               retain their legacy layer-zero interpretation.
    """
    prompt = deliveries.get("prompt_full") or deliveries.get("prompt_compiled") or {}
    jit = deliveries.get("jit_pretooluse") or {}
    boot = deliveries.get("boot_always_on") or deliveries.get("boot_layer0") or {}
    drift = deliveries.get("drift_if_acting") or {}
    rows = {name: {} for name in SYSTEM_ROWS}
    for case_id in set(prompt) | set(boot) | set(drift):
        moment = set(prompt.get(case_id, {}).get("rules") or ())
        moment |= jit.get(case_id, {}).get("rules") or set()
        doctrine = set(prompt.get(case_id, {}).get("doctrine") or ())
        doctrine |= jit.get(case_id, {}).get("doctrine") or set()
        rows["system_moment"][case_id] = {"rules": moment, "packs": None, "doctrine": doctrine}
        rows["system_moment_packlayer"][case_id] = {"rules": moment, "packs": None}
        rows["system_moment_plus_drift"][case_id] = {
            "rules": moment | (drift.get(case_id, {}).get("rules") or set()), "packs": None}
        rows["system_scoped_boot"][case_id] = {
            "rules": moment | (boot.get(case_id, {}).get("rules") or set()), "packs": None,
            "doctrine": doctrine | (boot.get(case_id, {}).get("doctrine") or set())}
    deliveries.update(rows)
    return deliveries


# ------------------------------------------------------------------ scoring

def confusion(gold, delivered):
    """(true positives, false positives, misses) as sorted lists."""
    return (sorted(gold & delivered), sorted(delivered - gold), sorted(gold - delivered))


def prf(tp, fp, fn, tp_recall=None):
    """Precision over everything delivered; recall over the path's universe.

    `tp_recall` is the true positives INSIDE the universe. A path may deliver
    a correct rule outside its universe (a JIT row that emits a control-layer
    rule, say): precision credits it, but it must not lift recall above what
    the universe's own gold allows. Defaults to `tp`."""
    tp_recall = tp if tp_recall is None else tp_recall
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp_recall / (tp_recall + fn) if tp_recall + fn else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision and recall else (0.0 if precision is not None and recall is not None
                                        else None))
    return {"tp": tp, "tp_in_universe": tp_recall, "fp": fp, "fn": fn,
            "precision": None if precision is None else round(precision, 4),
            "recall": None if recall is None else round(recall, 4),
            "f1": None if f1 is None else round(f1, 4)}


def deliveries_from_report(report):
    """Rebuild {path: {case id: {"rules", "packs"}}} from a saved report, so a
    run's deliveries can be re-scored against other labels (a consensus gold
    set, say) without asking Jev again. System rows are rebuilt by the caller."""
    out = {}
    for case_id, paths in (report.get("per_case") or {}).items():
        for name, row in paths.items():
            if name in SYSTEM_ROWS:
                continue
            packs = row.get("packs")
            out.setdefault(name, {})[case_id] = {"rules": set(row.get("raw_delivered")
                                                              or row.get("delivered") or ()),
                                                 "packs": set(packs) if packs is not None
                                                 else None,
                                                 "doctrine": set(row.get("doctrine_delivered")
                                                                 or ())}
    return out


def score(cases, deliveries, path_universes, meta, labelled=None, classes=None, groups=None,
          doctrine_labelled=None, doctrine_paths=None):
    """The report. See the module docstring for what is counted where.

    `classes`, when given, is {rule id: class} (ops/rule_gold_label.rule_classes:
    always_on, action_point, topic, gate_named). Each path then also reports
    `by_class` over the human cases: a miss and a hit count against the class
    of the gold rule, a false positive against the class of the delivered one.
    `groups` ({rule id: group}) is a second, orthogonal reporting split counted
    the same way into `by_group`: today "guidance_deferred" (the 93 rules
    audits/guidance-migration-manifest.v1.tsv retyped as guidance in August,
    which standing-context defers to consumers never built) against "other".

    `labelled`, when given, is the set of rule ids the gold labellers could
    choose from. A delivered id outside it cannot be judged right or wrong, so
    it is set aside (counted per path as `outside_labelled`) rather than
    charged as a false positive.

    DOCTRINE is scored the same way. `doctrine_labelled`, when given, is
    {case id: set of section refs the labellers judged for that case} (its
    shortlist). A delivered ref outside the case's shortlist was never
    labelled, so it is set aside (`doctrine.outside_labelled`), not charged.
    And a path is scored on doctrine only if it DELIVERS doctrine.
    `doctrine_paths`, when given, DECLARES which paths can deliver doctrine
    (DOCTRINE_PATHS for the harness's own adapters): those are always scored,
    so a doctrine path that errors on every case reads 0%, not "does not
    deliver"; every other path reports doctrine None. Without it, a path is
    scored when it returned a doctrine ref for some case in this run."""
    by_id = {case["id"]: case for case in cases}
    report = {"schema": REPORT_SCHEMA, "cases": len(cases),
              "strata": dict(Counter(case["stratum"] for case in cases)),
              "paths": {}, "per_case": {case["id"]: {} for case in cases}}
    for name, per_case in deliveries.items():
        outside = Counter()
        universe = path_universes.get(name, set(meta))
        human = [0, 0, 0, 0]  # tp, fp, fn, tp inside the universe
        strata = {}
        packs = [0, 0, 0]
        packs_scored = False
        notes = {"cases": 0, "cases_with_delivery": 0, "fp": 0, "tp": 0, "fn": 0,
                 "delivered": 0}
        misses, false_pos = Counter(), Counter()
        by_class = {}
        by_group = {}
        covered = [0, 0]  # human cases owing at least one rule, and of those fully served
        # Doctrine, the second target set, scored apart from rules over every
        # case (machine turns too): gold_doctrine against delivered refs.
        doctrine = [0, 0, 0]
        doctrine_strata = {}
        doctrine_outside = 0
        doctrine_scored = False
        delivers_doctrine = (name in doctrine_paths if doctrine_paths is not None
                             else any(out.get("doctrine") for out in per_case.values()))
        for case_id, out in per_case.items():
            case = by_id.get(case_id)
            if case is None:
                continue
            disputed = set(case.get("disputed") or ())
            gold_all = set(case["gold"]) - disputed
            gold = gold_all & universe
            raw = set(out["rules"])
            delivered = raw - disputed
            case_labelled = set(case["judged_rules"]) if "judged_rules" in case else labelled
            if case_labelled is not None:
                outside.update(delivered - case_labelled)
                delivered &= case_labelled
                gold_all &= case_labelled
                gold &= case_labelled
            tp, fp, fn = confusion(gold_all, delivered)
            fn = [rid for rid in fn if rid in universe]
            tp_u = [rid for rid in tp if rid in universe]
            report["per_case"][case_id][name] = {"delivered": sorted(delivered),
                                                 "raw_delivered": sorted(raw),
                                                 "packs": (sorted(out["packs"])
                                                           if out.get("packs") is not None
                                                           else None),
                                                 "tp": tp, "fp": fp, "fn": fn}
            misses.update(fn)
            false_pos.update(fp)
            if "gold_doctrine" in case and delivers_doctrine:
                doctrine_scored = True
                dgold = set(case.get("gold_doctrine") or ())
                dgot = set(out.get("doctrine") or ())
                if "doctrine_judged" in case or doctrine_labelled is not None:
                    judged = set(case.get("doctrine_judged", (doctrine_labelled or {}).get(case_id) or ())) | dgold
                    doctrine_outside += len(dgot - judged)
                    dgot &= judged
                dtp, dfp, dfn = confusion(dgold, dgot)
                for table in (doctrine, doctrine_strata.setdefault(case["stratum"], [0, 0, 0])):
                    table[0] += len(dtp)
                    table[1] += len(dfp)
                    table[2] += len(dfn)
                report["per_case"][case_id][name]["doctrine_fn"] = dfn
                report["per_case"][case_id][name]["doctrine_delivered"] = sorted(
                    out.get("doctrine") or ())
            if case["stratum"] in MACHINE_STRATA:
                notes["cases"] += 1
                notes["cases_with_delivery"] += 1 if delivered else 0
                notes["fp"] += len(fp)
                notes["tp"] += len(tp)
                notes["fn"] += len(fn)
                notes["delivered"] += len(delivered)
            else:
                human[0] += len(tp)
                human[1] += len(fp)
                human[2] += len(fn)
                human[3] += len(tp_u)
                if gold:
                    covered[0] += 1
                    covered[1] += 0 if fn else 1
                for table, split in ((by_class, classes), (by_group, groups)):
                    if split is None:
                        continue
                    for bucket, ids in ((0, tp), (1, fp), (2, fn), (3, tp_u)):
                        for rid in ids:
                            table.setdefault(split.get(rid, "unclassed"),
                                             [0, 0, 0, 0])[bucket] += 1
            # Every stratum gets a row, notifications included: v2 reports
            # recall per stratum for all eight. The pooled human figures above
            # still leave machine turns out.
            row = strata.setdefault(case["stratum"], [0, 0, 0, 0, 0])
            row[0] += len(tp)
            row[1] += len(fp)
            row[2] += len(fn)
            row[3] += len(tp_u)
            row[4] += 1
            if out.get("packs") is not None:
                packs_scored = True
                gold_packs = {p for rid in gold if meta.get(rid, {}).get("layer") == PACK_LAYER
                              for p in meta[rid]["packs"]}
                ptp, pfp, pfn = confusion(gold_packs, set(out["packs"]))
                packs[0] += len(ptp)
                packs[1] += len(pfp)
                packs[2] += len(pfn)
        n = notes["cases"]
        report["paths"][name] = {
            "universe_size": len(universe),
            # Only cases in this run's case set: a rescore over one split
            # carries deliveries for the other split too.
            "cases_scored": sum(1 for case_id in per_case if case_id in by_id),
            "human": prf(*human),
            # Jev (verification_selection, 2026-09-26) ranked "share of cases
            # with any miss" among the acceptance numbers: a case is served
            # only when EVERY rule it owes arrives.
            "cases_fully_served": {"cases_owing": covered[0], "fully_served": covered[1],
                                   "share": round(covered[1] / covered[0], 4) if covered[0] else None},
            "by_stratum": {st: {**prf(*row[:4]), "cases": row[4]}
                           for st, row in sorted(strata.items())},
            "notifications": {**notes,
                              "delivered_mean": round(notes["delivered"] / n, 3) if n else None},
            "packs": prf(*packs) if packs_scored else None,
            "by_class": ({cls: prf(*row) for cls, row in sorted(by_class.items())}
                         if classes is not None else None),
            "by_group": ({grp: prf(*row) for grp, row in sorted(by_group.items())}
                         if groups is not None else None),
            "doctrine": ({**prf(*doctrine), "outside_labelled": doctrine_outside}
                         if doctrine_scored else None),
            "doctrine_by_stratum": ({st: prf(*row) for st, row in sorted(doctrine_strata.items())}
                                    if doctrine_scored else None),
            "outside_labelled": sorted(outside.items(), key=lambda kv: (-kv[1], kv[0])),
            "misses": sorted(misses.items(), key=lambda kv: (-kv[1], kv[0])),
            "false_positives": sorted(false_pos.items(), key=lambda kv: (-kv[1], kv[0])),
        }
    return report


def _dig(report_path, dotted):
    value = report_path
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return KeyError
        value = value[part]
    return value


def check_expectations(report, expectations, tolerance=1e-6):
    """Mismatch lines for {path: {"dotted.metric": expected}}. Empty is a pass."""
    problems = []
    for path, wanted in expectations.items():
        got_path = report["paths"].get(path)
        if got_path is None:
            problems.append(f"{path}: not in report")
            continue
        for dotted, expected in wanted.items():
            got = _dig(got_path, dotted)
            same = (got == expected if not isinstance(expected, float) or got is None
                    or got is KeyError
                    else abs(float(got) - expected) <= tolerance)
            if got is KeyError or not same:
                problems.append(f"{path}: {dotted} expected {expected!r}, got "
                                f"{'missing' if got is KeyError else repr(got)}")
    return problems


# ------------------------------------------------------------------ rendering

def one_line(statement, limit=110):
    text = " ".join((statement or "").split())
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    return first if len(first) <= limit else first[:limit - 1].rstrip() + "…"


def _pct(value):
    return "–" if value is None else f"{100 * value:.0f}%"


def render_markdown(report, statements=None, *, top=10, paths=None):
    statements = statements or {}
    order = paths or [p for p in SYSTEM_ROWS + ("prompt_full",
                                  "prompt_compiled", "jit_pretooluse",
                                  "layered_triggers", "drift_shadow", "drift_if_acting",
                                  "boot_always_on", "boot_layer0",
                                  "doctrine_search")
                      if p in report["paths"]]
    lines = [f"Cases: {report['cases']} ({', '.join(f'{k} {v}' for k, v in sorted(report['strata'].items()))})",
             "",
             "| path | cases | human P | human R | human F1 | cases fully served | notif. cases w/ delivery | notif. FP | pack P | pack R |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for name in order:
        row = report["paths"][name]
        h, n, pk = row["human"], row["notifications"], row["packs"] or {}
        lines.append(f"| {name} | {row['cases_scored']} | {_pct(h['precision'])} | "
                     f"{_pct(h['recall'])} | {_pct(h['f1'])} | "
                     f"{_pct((row.get('cases_fully_served') or {}).get('share'))} | "
                     f"{n['cases_with_delivery']}/{n['cases']} | {n['fp']} | "
                     f"{_pct(pk.get('precision'))} | {_pct(pk.get('recall'))} |")
    present = set(report["strata"])
    strata = ([s for s in STRATA_V2 if s in present] if present - set(STRATA)
              else [s for s in STRATA if s not in MACHINE_STRATA])
    for metric in ("recall", "precision"):
        lines += ["", f"{metric.capitalize()} by stratum:", "",
                  "| path | " + " | ".join(strata) + " |", "|---|" + "---|" * len(strata)]
        for name in order:
            by = report["paths"][name]["by_stratum"]
            lines.append(f"| {name} | " + " | ".join(_pct((by.get(s) or {}).get(metric))
                                                     for s in strata) + " |")
    if any(report["paths"][name].get("by_class") for name in order):
        classes = ("always_on", "action_point", "topic", "gate_named")
        lines += ["", "Recall / precision by rule class (human cases):", "",
                  "| path | " + " | ".join(classes) + " |", "|---|" + "---|" * len(classes)]
        for name in order:
            by = report["paths"][name].get("by_class") or {}
            lines.append(f"| {name} | " + " | ".join(
                f"{_pct((by.get(c) or {}).get('recall'))} / {_pct((by.get(c) or {}).get('precision'))}"
                for c in classes) + " |")
    for name in [p for p in ("system_moment", "prompt_full") if p in report["paths"]]:
        row = report["paths"][name]
        lines += ["", f"Top misses — {name}:", "", "| rule | cases | summary |", "|---|---|---|"]
        lines += [f"| {rid} | {n} | {one_line(statements.get(rid))} |"
                  for rid, n in row["misses"][:top]]
        lines += ["", f"Top false positives — {name}:", "", "| rule | cases | summary |",
                  "|---|---|---|"]
        lines += [f"| {rid} | {n} | {one_line(statements.get(rid))} |"
                  for rid, n in row["false_positives"][:top]]
    if any(report["paths"][name].get("doctrine") for name in order):
        lines += ["", "Doctrine (second target: section refs), all cases:", "",
                  "| path | doctrine P | doctrine R | TP | FN | set aside (not labelled) |",
                  "|---|---|---|---|---|---|"]
        for name in order:
            d = report["paths"][name].get("doctrine")
            if d is None:
                lines.append(f"| {name} | does not deliver doctrine | | | | |")
                continue
            lines.append(f"| {name} | {_pct(d.get('precision'))} | {_pct(d.get('recall'))} | "
                         f"{d.get('tp', '–')} | {d.get('fn', '–')} | "
                         f"{d.get('outside_labelled', 0)} |")
    group_names = sorted({g for name in order
                          for g in (report["paths"][name].get("by_group") or {})})
    if group_names:
        lines += ["", "Recall / precision by rule group (human cases):", "",
                  "| path | " + " | ".join(group_names) + " |",
                  "|---|" + "---|" * len(group_names)]
        for name in order:
            by = report["paths"][name].get("by_group") or {}
            lines.append(f"| {name} | " + " | ".join(
                f"{_pct((by.get(g) or {}).get('recall'))} / {_pct((by.get(g) or {}).get('precision'))}"
                for g in group_names) + " |")
    return "\n".join(lines) + "\n"
