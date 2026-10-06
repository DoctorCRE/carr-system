"""rule_trigger_compile.py — Jev judges a rule once, when it is taught or changed.

THE PROBLEM, measured on 2026-09-25. ops/jev_rule_select.advise() ran at every
UserPromptSubmit: one ranking Choice plus one binding Noul for each of twenty
shortlisted rules, 21 Jev requests a prompt. That day it ran 632 times (13,272
requests, about half of all Jev traffic), 514 of them on machine-generated
task notifications rather than partner messages, and the verdicts it paid for
were mostly thrown away afterwards: only pack-layer rules can be delivered at
that seam (lib/rule_delivery_preuse.semantic_delivery), and it judged the
whole roster.

THE RE-ENGINEERED SHAPE. The judgment "which cues should surface this rule"
does not change from turn to turn; it changes when the rule does. So Jev is
asked ONCE PER RULE here, and its answers are stored as deterministic triggers
keyed to the rule's statement digest:

  * code proposes a bounded candidate set per rule — distinctive words and
    phrases of the statement, the pack's own keyword list, quoted forms the
    rule itself names, verbs, command families and paths it mentions, and
    n-grams from real moments where Jev previously judged the rule binding;
  * Jev decides, one Noul per candidate in ONE request per rule, which of
    them signal the rule's binding moment, which are near-misses (on topic,
    usually not binding), and whether the rule is instead always-on or has no
    reliable surface cue at all (a RESIDUAL rule, the only kind still judged
    at run time — ops/rule_trigger_delivery.py);
  * ops/rule-jit-compile.py turns the stored answers into rows of the one
    compiled trigger table (ops/config/rule-jit-triggers.v1.json), so run-time
    delivery is a pure match with no Jev call.

A LIBRARY. The command line is ops/rule-trigger-compile.py; this file carries
no entrypoint construct, for the sealed-inventory reason
ops/typesafe_client.py documents.
"""

import hashlib
import json
import math
import os
import re
import subprocess
from collections import Counter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(REPO, "ops", "config")
CORPUS_PATH = os.path.join(CONFIG, "rule-selection-corpus.v1.json")
MAP_PATH = os.path.join(CONFIG, "rule-enforcement-map.json")
TRIAGE_PATH = os.path.join(CONFIG, "rule-triage.v1.json")
TOOLS_JS = os.path.join(REPO, "mcp-server", "src", "tools.js")
OUTPUT_PATH = os.path.join(CONFIG, "rule-jev-triggers.v1.json")
HISTORY_LOG = os.path.join(REPO, "out", "jev-rule-select.jsonl")

SCHEMA = "rule-jev-triggers/v1"

# Candidate budget per rule. One request carries every question for one rule,
# so these bound the request, not the number of requests.
MAX_WORDS = 14
MAX_PHRASES = 10
MAX_QUOTED = 8
MAX_HISTORY = 10
MAX_NEAR_MISS = 8

# Decision floors. Surfacing is biased toward delivery on purpose: a false
# positive costs one paragraph a session already has room for, a false
# negative is a rule that never arrives.
SURFACE_AT = 0.50
NEAR_MISS_AT = 0.60

# Words that name the house and its assistants rather than any rule's moment:
# nearly every partner message and notification carries one, so as a
# single-word statement candidate they fire a rule everywhere. A phrase that
# contains one ("carr surface") is still a candidate. "joe" and "dell" are
# already STOPWORDS.
HOUSE_WORDS = frozenset({"carr", "claude"})
ALWAYS_ON_AT = 0.70
NO_CUE_AT = 0.70

STOPWORDS = frozenset("""
a about above after again against all also always am an and any are as at be
because been before being below between both but by can cannot could did do
does doing done down during each either else ever every few for from further
had has have having he her here hers him his how however i if in into is it
its itself just less like made make many may me might more most much must my
never no nor not now of off on once one only or other our ours out over own
per rather same say says she should since so some still such than that the
their theirs them then there these they this those though through thus to too
under until up upon us very via was way we were what when where whether which
while who whom whose why will with within without would yes yet you your
yours joe dell rule rules session sessions thing things time never ever
""".split())

WORD = re.compile(r"[a-z][a-z0-9'-]{2,}")
QUOTED = re.compile(r"[\"“]([^\"”]{6,80})[\"”]")
BACKTICK = re.compile(r"`([^`]{2,80})`")
PATHLIKE = re.compile(r"(?<![\w/])((?:[\w.-]+/)+[\w.*-]+)")
COMMAND_HEADS = ("git", "gh", "./run.sh", "run.sh", "python", "python3", "npm",
                 "npx", "node", "psql", "launchctl", "wrangler", "curl", "rm")
TOOL_CUES = {
    "Agent": re.compile(r"\b(sub-?agents?|delegat\w*|fan-?out|worker agents?)\b", re.I),
    "WebFetch": re.compile(r"\b(url|link|web ?page|fetch|article|x\.com)\b", re.I),
    "WebSearch": re.compile(r"\b(web search|search the web|look (?:it )?up online)\b", re.I),
}


def sha256_text(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def pack_rules(corpus=None, enforcement_map=None):
    """Every pack-layer rule as {id, gist, statement, packs}.

    Pack layer is the only layer the partner-message seam can deliver: layer
    zero is already loaded at boot, and control-layer rules are delivered by
    the gate that enforces them."""
    corpus = corpus or load_json(CORPUS_PATH)
    enforcement_map = enforcement_map or load_json(MAP_PATH)
    layers = enforcement_map.get("rule_load_layers", {})
    rows = []
    for row in corpus.get("rules", []):
        entry = layers.get(row.get("id"))
        if (isinstance(entry, dict) and entry.get("load_layer") == "pack"
                and entry.get("packs") and (row.get("statement") or "").strip()):
            rows.append({"id": row["id"], "gist": row.get("gist", ""),
                         "statement": row["statement"],
                         "packs": sorted(entry["packs"])})
    return sorted(rows, key=lambda r: r["id"])


def canonical_history_log():
    """The selector log in the canonical checkout, which every worktree shares
    (the same resolution ops/typesafe_client.py uses for its call log)."""
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=REPO, capture_output=True, text=True, timeout=5, check=True).stdout.strip()
        if common:
            return os.path.join(os.path.dirname(common), "out", "jev-rule-select.jsonl")
    except Exception:
        pass
    return HISTORY_LOG


def known_verbs(path=TOOLS_JS):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return set()
    return set(re.findall(r'^  "([a-z0-9-]+)": \{', text, re.M))


def _words(text):
    return [w.strip("'-") for w in WORD.findall(text.lower())
            if w.strip("'-") not in STOPWORDS and not re.fullmatch(r"[0-9a-f]{8}", w)]


def _bigrams(words):
    return [f"{a} {b}" for a, b in zip(words, words[1:])]


def _idf(documents):
    df = Counter()
    for doc in documents:
        df.update(set(doc))
    n = max(1, len(documents))
    return {term: math.log((1 + n) / (1 + count)) + 1.0 for term, count in df.items()}


def load_history(path=HISTORY_LOG, before=None):
    """[(situation, surfaced ids)] from the selector's own log, optionally only
    rows logged before a split point (a row index), for held-out replay."""
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                rows.append((row.get("situation") or "", list(row.get("surfaced") or [])))
    except OSError:
        return []
    return rows[:before] if before is not None else rows


def candidates(rule, *, all_rules, pack_keywords, verbs, history=()):
    """The bounded candidate set Jev judges for one rule. Deterministic."""
    text = f"{rule.get('gist', '')}\n{rule['statement']}"
    lower = text.lower()
    out = []   # (kind, value, origin)

    def add(kind, value, origin):
        value = value.strip()
        if value and (kind, value) not in {(k, v) for k, v, _ in out}:
            out.append((kind, value, origin))

    docs = {r["id"]: _words(f"{r.get('gist', '')} {r['statement']}") for r in all_rules}
    idf_words = _idf(list(docs.values()))
    idf_pairs = _idf([_bigrams(d) for d in docs.values()])
    mine = docs.get(rule["id"]) or _words(text)
    tf = Counter(mine)
    singles = [(w, c) for w, c in tf.items() if w not in HOUSE_WORDS]
    for word, _ in sorted(singles, key=lambda kv: (-kv[1] * idf_words.get(kv[0], 1.0), kv[0]))[:MAX_WORDS]:
        add("keyword", word, "statement")
    tf2 = Counter(_bigrams(mine))
    for pair, _ in sorted(tf2.items(), key=lambda kv: (-kv[1] * idf_pairs.get(kv[0], 1.0), kv[0]))[:MAX_PHRASES]:
        add("keyword", pair, "statement")
    for quoted in QUOTED.findall(text)[:MAX_QUOTED]:
        cleaned = re.sub(r"\bX\b", "", quoted).strip(" .,:;-·").lower()
        if 2 <= len(cleaned.split()) <= 8:
            add("keyword", cleaned, "quoted")
    for pack in rule["packs"]:
        for word in pack_keywords.get(pack, []):
            add("keyword", str(word).lower(), f"pack:{pack}")
    for token in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)+", lower):
        if token in verbs:
            add("verb", token, "statement")
    for snippet in BACKTICK.findall(text):
        head = snippet.split()[0] if snippet.split() else ""
        if head in COMMAND_HEADS and len(snippet.split()) >= 2:
            add("command", " ".join(snippet.split()[:2]), "statement")
    for path in PATHLIKE.findall(text):
        path = path.rstrip(".,;:")
        if not re.match(r"[A-Za-z_.]", path):
            continue
        if "." in path.rsplit("/", 1)[-1] or path.endswith("/*"):
            add("path", path, "statement")
    for tool, cue in TOOL_CUES.items():
        if cue.search(text):
            add("tool", tool, "statement")
    # Moments where Jev already judged this rule binding. Terms that are far
    # more frequent there than across all logged moments are cues the
    # statement's own wording may not use.
    if history:
        bound = [s for s, ids in history if rule["id"] in ids]
        if bound:
            base = Counter(w for s, _ in history for w in set(_words(s)))
            here = Counter(w for s in bound for w in set(_words(s)))
            floor = max(2, 0.1 * len(bound))
            scored = {}
            for word, count in here.items():
                if count < floor or not base[word] or word in HOUSE_WORDS \
                        or not re.fullmatch(r"[a-z][a-z'-]*", word):
                    continue
                lift = (count / len(bound)) / (base[word] / len(history))
                if lift > 1.5:
                    scored[word] = (count / len(bound)) * math.log(lift)
            for word, _ in sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_HISTORY]:
                add("keyword", word, "history")
    # Near-miss probes: a common phrase containing one of this rule's words,
    # taken from OTHER rules, where the word is likely used in another sense.
    near = []
    own_pairs = set(_bigrams(mine))
    elsewhere = Counter(p for rid, d in docs.items() if rid != rule["id"] for p in _bigrams(d))
    for kind, word, _ in list(out):
        if kind != "keyword" or " " in word:
            continue
        options = [(c, p) for p, c in elsewhere.items()
                   if word in p.split() and p not in own_pairs and c >= 2]
        if options:
            near.append(max(options)[1])
        if len(near) >= MAX_NEAR_MISS:
            break
    return out, sorted(set(near))


def questions_for(rule, cands, near, client):
    """ONE request per rule: a Noul per candidate, a Noul per near-miss probe,
    and two rule-level Nouls. Returns (questions, state, index)."""
    state = {"rule": {"title": rule.get("gist", ""), "statement": rule["statement"][:6000],
                      "packs": rule["packs"]},
             "candidates": {}, "near_miss_probes": {}}
    questions, index = {}, {}
    for i, (kind, value, origin) in enumerate(cands):
        qid = f"c{i:02d}"
        state["candidates"][qid] = {"kind": kind, "value": value}
        index[qid] = ("candidate", kind, value, origin)
        questions[qid] = client.noul(
            f"Seeing the {kind} in `candidates.{qid}.value` in a partner message, "
            "a notification, or a tool call is a strong sign that the rule in "
            "`rule` may be about to bind: its condition could be met in that "
            "moment, so a session would want the rule in front of it.",
            true="This cue typically appears exactly when the rule's condition is "
                 "at stake. Delivering the rule whenever it appears is worth it.",
            false="The cue is generic, unrelated, or merely shares the rule's "
                  "topic without signalling its binding moment.")
    for j, phrase in enumerate(near):
        qid = f"n{j:02d}"
        state["near_miss_probes"][qid] = phrase
        index[qid] = ("near_miss", "keyword", phrase, "near_miss")
        questions[qid] = client.noul(
            f"The phrase in `near_miss_probes.{qid}` shares a word with the rule in "
            "`rule` but, when it appears, the rule usually does NOT bind — a "
            "near-miss that should suppress the shared word as a trigger.",
            true="A moment containing this phrase is about something else; the "
                 "shared word is used in a different sense.",
            false="This phrase is itself a sign the rule may bind, or is unrelated.")
    questions["always_on"] = client.noul(
        "The rule in `rule` governs nearly EVERY message or turn regardless of "
        "topic — for example how every message is closed or formatted.",
        true="It applies to almost every turn, so it should simply always be loaded.",
        false="It applies only in particular situations.")
    questions["no_cue"] = client.noul(
        "Whether the rule in `rule` binds depends on meaning that NO specific "
        "word, phrase, command, tool, or file path reliably signals.",
        true="Only a reading of what the moment means can tell; no surface cue works.",
        false="There are recognisable words, commands, tools or paths that "
              "signal its binding moment.")
    return questions, state, index


def interpret(rule, answer, index, *, model):
    """Turn one rule's answers into its compiled entry. Pure."""
    answers = (answer or {}).get("answers") or {}

    def p(qid):
        body = answers.get(qid) or {}
        value = body.get("noul")
        return float(value) if isinstance(value, (int, float)) else None

    triggers = {"keywords": {}, "verbs": {}, "commands": {}, "paths": {}, "tools": {}}
    negatives = {}
    plural = {"keyword": "keywords", "verb": "verbs", "command": "commands",
              "path": "paths", "tool": "tools"}
    for qid, (role, kind, value, _origin) in index.items():
        prob = p(qid)
        if prob is None:
            continue
        if role == "candidate" and prob >= SURFACE_AT:
            triggers[plural[kind]][value] = round(prob, 4)
        elif role == "near_miss" and prob >= NEAR_MISS_AT:
            negatives[value] = round(prob, 4)
    # A near-miss only means something beside a positive it would mask.
    positive_words = {w for k in triggers["keywords"] for w in k.split()}
    negatives = {k: v for k, v in negatives.items()
                 if positive_words & set(k.split()) and k not in triggers["keywords"]}
    always = p("always_on")
    no_cue = p("no_cue")
    has_trigger = any(triggers.values())
    if always is not None and always >= ALWAYS_ON_AT:
        mode = "always_on"
    elif not has_trigger or (no_cue is not None and no_cue >= NO_CUE_AT):
        mode = "residual"
    else:
        mode = "triggered"
    return {
        "id": rule["id"],
        "packs": rule["packs"],
        "statement_sha256": sha256_text(rule["statement"]),
        "mode": mode,
        "triggers": {k: dict(sorted(v.items())) for k, v in triggers.items()},
        "negatives": dict(sorted(negatives.items())),
        "always_on_probability": always,
        "no_cue_probability": no_cue,
        "model": model,
    }


def compile_rule(rule, *, all_rules, pack_keywords, verbs, history, client, ask):
    """Judge one rule with exactly one Jev request. `ask` is the typesafe
    client's ask(); it is injected so the selftest never spends."""
    cands, near = candidates(rule, all_rules=all_rules, pack_keywords=pack_keywords,
                             verbs=verbs, history=history)
    questions, state, index = questions_for(rule, cands, near, client)
    import importlib.util
    spec = importlib.util.spec_from_file_location("jev_semantic", os.path.join(REPO, "ops", "jev_semantic.py"))
    semantic = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(semantic)
    answer = semantic.ask(state, questions, transport=ask, caller="rule_trigger_compile",
                          version="vendor-v1", facets=["semantic_creation"] )
    proposal = interpret(rule, answer, index, model=answer.get("model") or "unknown")
    return dict(proposal, proposed_mode=proposal.get("mode"), mode="review_required")


def document(entries):
    entries = sorted(entries, key=lambda e: e["id"])
    rules = {e["id"]: e for e in entries if e.get("mode") != "review_required"}
    proposals = {e["id"]: e for e in entries if e.get("mode") == "review_required"}
    counts = Counter(e["mode"] for e in rules.values())
    return {
        "schema": SCHEMA,
        "generated_note": (
            "Written only by ops/rule-trigger-compile.py: one Jev request per "
            "pack-layer rule, keyed to the rule's statement digest. "
            "ops/rule-jit-compile.py turns these into rows of "
            "ops/config/rule-jit-triggers.v1.json. Never hand-edit."),
        "thresholds": {"surface_at": SURFACE_AT, "near_miss_at": NEAR_MISS_AT,
                       "always_on_at": ALWAYS_ON_AT, "no_cue_at": NO_CUE_AT},
        "counts": {"rules": len(rules), **{k: counts[k] for k in sorted(counts)}},
        "rules": rules,
        "proposals": proposals,
    }


def canonical_bytes(doc):
    return (json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def load_compiled(path=OUTPUT_PATH):
    """The compiled document, or None when missing or malformed. None means
    run-time delivery falls back to judging, never to silence."""
    try:
        doc = load_json(path)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA \
            or not isinstance(doc.get("rules"), dict):
        return None
    return doc


def stale_or_missing(doc, rules):
    """Rule ids whose statement changed since compile, or that were never
    compiled. These are recompiled by --changed and judged at run time until
    then."""
    compiled = (doc or {}).get("rules") or {}
    out = []
    for rule in rules:
        entry = compiled.get(rule["id"])
        if (not isinstance(entry, dict) or entry.get("mode") not in {"triggered", "residual", "always_on"}
                or entry.get("statement_sha256") != sha256_text(rule["statement"])):
            out.append(rule["id"])
    return sorted(out)


def coverage_problems(doc, rules):
    """The safety property: every pack-layer rule is compiled against its
    current text AND is deliverable — at least one trigger, or always-on, or
    residual (judged at run time). Returns human-readable problems."""
    problems = [f"{rid}: not compiled against its current statement"
                for rid in stale_or_missing(doc, rules)]
    problems += [f"{rid}: proposal requires independent review"
                 for rid in sorted((doc or {}).get("proposals") or {})]
    compiled = (doc or {}).get("rules") or {}
    live = {r["id"] for r in rules}
    for rid, entry in sorted(compiled.items()):
        if rid not in live:
            problems.append(f"{rid}: compiled but no longer an active pack-layer rule")
            continue
        mode = entry.get("mode")
        has = any((entry.get("triggers") or {}).get(k) for k in
                  ("keywords", "verbs", "commands", "paths", "tools"))
        if mode not in {"triggered", "residual", "always_on"}:
            problems.append(f"{rid}: unknown mode {mode!r}")
        elif mode == "triggered" and not has:
            problems.append(f"{rid}: triggered with no trigger")
    return problems


# ------------------------------------------------------------------ rows

def _alternation(terms):
    parts = []
    for term in sorted(terms, key=lambda t: (-len(t), t)):
        escaped = re.escape(term).replace(r"\ ", r"\s+")
        left = r"\b" if re.match(r"\w", term[0]) else ""
        right = r"\b" if re.match(r"\w", term[-1]) else ""
        parts.append(f"{left}{escaped}{right}")
    return "|".join(parts)


def prompt_keywords(entry):
    """Keep compiled cues at SURFACE_AT, excluding bare house words.

    The same cues feed rows and runtime probability attribution. Required
    human actions must still surface when runtime judgment is unavailable.
    """
    keywords = ((entry or {}).get("triggers") or {}).get("keywords") or {}
    return {k: p for k, p in keywords.items()
            if isinstance(p, (int, float)) and p >= SURFACE_AT and k not in HOUSE_WORDS}


def trigger_rows(doc):
    """Rows for ops/config/rule-jit-triggers.v1.json, one group per rule and
    kind. prompt_regex runs at UserPromptSubmit only; verb, bash_family and
    path_pattern join the existing PreToolUse rail unchanged."""
    rows = []
    for rid, entry in sorted(((doc or {}).get("rules") or {}).items()):
        if entry.get("mode") not in {"triggered", "residual"}:
            continue
        trig = entry.get("triggers") or {}
        packs = sorted(entry.get("packs") or [])
        keywords = prompt_keywords(entry)
        if keywords:
            row = {"kind": "prompt_regex", "pattern": _alternation(keywords),
                   "packs": packs, "rule_ids": [rid], "source": "jev_compiled"}
            # interpret()'s rule, re-applied to the keywords that survive: a
            # near-miss only means something beside a positive it would mask.
            words = {w for k in keywords for w in k.split()}
            negatives = [n for n in (entry.get("negatives") or {}) if words & set(n.split())]
            if negatives:
                row["negative_pattern"] = _alternation(negatives)
            rows.append(row)
        verbs = sorted(trig.get("verbs") or [])
        tools = sorted(trig.get("tools") or [])
        if verbs or tools:
            alternatives = [f"__{re.escape(v)}$" for v in verbs] + [f"^{re.escape(t)}$" for t in tools]
            rows.append({"kind": "verb", "pattern": "|".join(alternatives), "packs": packs,
                         "rule_ids": [rid], "source": "jev_compiled"})
        if trig.get("commands"):
            rows.append({"kind": "bash_family", "pattern": _alternation(trig["commands"]),
                         "packs": packs, "rule_ids": [rid], "source": "jev_compiled"})
        for path in sorted(trig.get("paths") or []):
            # Tool inputs carry absolute paths and fnmatch matches the whole
            # string, so an unanchored repo path is prefixed with `*`.
            rows.append({"kind": "path_pattern",
                         "pattern": path if path.startswith(("*", "/")) else f"*{path}",
                         "packs": packs, "rule_ids": [rid], "source": "jev_compiled"})
    return rows
