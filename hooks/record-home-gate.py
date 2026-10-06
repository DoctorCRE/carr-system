#!/usr/bin/env python3
"""record-home-gate.py — the PreToolUse DENY gate on records written to markdown.

WHY THIS EXISTS. Joe, 2026-08-03: "you cant write a .md file you have to write to
the database... it is one of the worst mistakes that can be made in this database
system. writing a .md file creates a big issue. its undetectable to the system."

He is right about undetectable, and that is the whole defect. Every OTHER control
in this system that matters has a mechanism behind it: destructive Bash has
guard-unattended.py, taught-rule shape has rule-shape-gate.py, prose quality has
lint-gate.py, the ledger rules have ledger-sweep.py. Shared rule "findings and
record updates go into the DATABASE, never into a markdown report" is the one
major rule enforced ONLY by a session remembering it at the moment of writing.

It failed twice in one sitting on 2026-08-03, in a session that had loaded the
rule and recited it back at session start. That is the same failure ledger-sweep
was built for, and its docstring already carries the finding: "The only control
that worked unassisted all night was a hook."

THE FOUR CAUSES, because a gate that treats only the symptom moves the leak:

  1. THE RULE'S OWN CARVE-OUT IS A TRAP. It ends "Narrative and doctrine files
     stay markdown; this rule is about RECORDS and FINDINGS." A handoff packet,
     a status writeup, an audit report and a checkpoint all LOOK like narrative
     and are made almost entirely of records: findings, decisions, open items.
     The rule as written permits the reading that broke it.
  2. NOTHING DETECTS IT. Write and Edit on a .md are completely unguarded. A
     wrong DB write throws; a wrong markdown write succeeds silently and reaches
     nobody — no query finds it, no export renders it, the other partner's
     sessions never see it. Silent success is the worst possible failure shape.
  3. A SKILL MANDATES IT. Both /handoff skills exist to produce "a drop-in
     markdown packet" and file it into CARR. The system instructs sessions to do
     the thing. That contradiction is tracked separately and is not this file's
     job, but no gate fixes a workflow that tells sessions to break the rule.
  4. FRICTION ASYMMETRY. Write always works. A verb needs a schema load, a fresh
     UUID and a base_version read. Under time pressure the cheap path wins, which
     is a design fact about the tooling, not a character fact about the session.

This file addresses cause 2 only, and only cause 2. That is honest scoping, not
a claim to have solved the problem.

WHAT IT DENIES, and every rule here is PATH-STRUCTURAL rather than content-
sniffing, on purpose. lint-gate.py's own doctrine says a gate that fires on
everything is a gate people learn to ignore, and a content heuristic on prose
this varied would misfire constantly and burn the gate's credibility in a day.

  A. ANY write or edit to a GENERATED RENDER. Already a hard rule with no
     mechanism until now. Pure path match, zero ambiguity, zero false positives.
  B. CREATING A NEW .md under 00_Context/, the vault ROOT, or out/. New-file
     creation is the tell: narrative and doctrine files already EXIST and get
     edited, while records arrive as brand-new files. This is the exact shape of
     every instance of the defect on record, including the cloud session that
     hand-wrote a DECISIONS.md duplicating what add-loop already does.
  C. ANY write or edit under 00_Context/handoffs/. A handoff is the purest form
     of records wearing narrative clothing, and it is the shape that failed on
     2026-08-03. The record layer already has the receiving end: catch-me-up and
     today-triage brief a new session from the DB, with no file to point at.
  D. Content carrying a RULE UUID next to a rule verb (teach/activate/retire/
     amend/supersede). That combination is never narrative — it is a record. This
     is the one content check, kept deliberately narrow.

PHASE 0 UPGRADE (2026-08-07, doctrine-store build — council decision 82a2fb62,
rules 14181e60 + 20dfdfcc): the per-class rules B (new .md in 00_Context/root)
and C (handoffs) are SUPERSEDED by vault-wide DENY BY DEFAULT on every .md
write, create and edit alike, governed by the exact-path manifest in
hooks/md_manifest.py. The old known-limit paragraph ("a session determined to
write records into an EXISTING narrative file under DNA/ can still do it") is
CLOSED — that was the residual hole Joe's database-first ruling eliminated.

WHAT IT DOES NOT TOUCH. Anything outside the CARR vault: the repo (git-owned),
scratchpads (the ungoverned annex), Life AI, and the ~/.claude / My Drive/.claude
skill trees (harness-required; repo-canonical from P6). Non-markdown files,
except generated renders which are denied at any extension.

FAILS CLOSED ON DENY, OPEN ON ERROR. Exit 2 + stderr is the deny path, matching
guard-unattended.py and for the same reason: the structured JSON contract needs
exit 0, so on any build that does not parse it, exit 0 reads as ALLOW and the
gate fails open silently. Any INTERNAL error allows the call, because a gate that
wedges a session costs more than the marginal safety of failing closed on a
single-operator machine.
"""

import json
from datetime import datetime, timezone
import os
import re
import sys

# Script-relative, NOT os.path.expanduser("~/carr-system") — see the same fix
# in test-ledger-sweep.py and test-review-council.py. On a GitHub runner the
# repo is checked out to /home/runner/work/carr-system/carr-system, outside
# $HOME, so the four ~/carr-system-literal paths below silently pointed at
# files that do not exist there: generated_paths() raising FileNotFoundError
# is caught and falls back to the 16-path GENERATED_FALLBACK list, which is
# missing the dossier/ledger/dictionary entries generated_paths() itself
# parses from exporters/targets.py at call time. That is undetectable from
# most test cases (an .md file denied for the wrong reason still reads as
# DENY), but it is exactly wrong for a generated dossier that ALSO sits under
# an ALLOW_PREFIX directory (DNA/Clients/prospects/): with the real target
# list unavailable, the dossier is never recognized as generated and falls
# through to the prefix allowance meant for hand-authored intake files,
# flipping the verdict from DENY to ALLOW.
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _vault():
    """This machine's vault. The literal below is one Drive ACCOUNT's mount, so
    on a second machine it resolved to a path that does not exist and every
    vault protection in this gate silently matched nothing (2026-08-10 audit).
    Keep it as the first candidate — the primary machine resolves identically,
    so nothing changes there — and fall back to whatever vault is really here."""
    pinned = os.path.expanduser(
        "~/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI")
    if os.path.isdir(pinned):
        return pinned
    try:
        from gate_paths import vault_roots
        roots = vault_roots()
    except Exception:
        roots = ()
    return roots[0].rstrip("/") if roots else pinned


VAULT = _vault()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:                                    # telemetry only — never load-bearing
    import hook_meter
    LOG = hook_meter.guard_log_path(REPO)
except Exception:                       # a missing meter must not change a verdict
    LOG = os.path.join(REPO, "out", "hook-guard.log")

# --- A. generated renders ----------------------------------------------------
# READ FROM THE EXPORTER, NOT RETYPED. The first cut of this file hardcoded 16
# paths lifted from exporters/targets.py by hand, and the IT sweep on 2026-08-03
# measured the result: 16 guarded against 41 real targets, TWENTY-SEVEN
# unguarded — including all 23 client dossiers, which is exactly where the sweep's
# worst finding points (four live doctrine files tell sessions to type deal
# updates into those dossiers, and the export rewrites them several times a day,
# so anything typed there is gone by morning and nothing reports it missing).
#
# A duplicated list is the same two-homes disease this whole system is built to
# avoid, one layer down. So the set is PARSED from targets.py at call time —
# statically, via ast, with no import and no dependency, because the hook runs
# under /usr/bin/env python3 rather than the repo venv and must not need it.
#
# TWO THINGS THE FLAT DIFF GOT WRONG, and both matter:
#
#   * CLAUDE.md is a PARTIAL target. Only the block between the rule-gist-index
#     markers is generated; the rest is hand-authored doctrine that Joe edits
#     often. Denying the whole file would block real work, and partial.py already
#     fails loudly on missing or mangled markers, so a bad edit is caught at
#     export time. Partial targets are EXCLUDED from the deny set on purpose.
#   * The dossiers are a DIRECTORY (DOSSIER_DIR), not a fixed list. Guarding the
#     directory covers the 23 that exist and every one minted later; enumerating
#     them would reopen the same gap the day a new client lands.
TARGETS_PY = os.path.join(REPO, "exporters", "targets.py")
PARTIAL_TARGET_KEYS = {"compiled-rules-gist-index"}


def generated_paths():
    """(exact vault-relative paths, generated directories) parsed from targets.py.

    Falls back to GENERATED_FALLBACK on any parse failure and LOGS that it did,
    so a silently degraded gate is still discoverable — same convention as the
    other hooks' allow-on-error logging.
    """
    import ast
    exact, dirs = set(), set()
    tree = ast.parse(open(TARGETS_PY).read())

    def module_consts(path):
        """module-level NAME = "literal" pairs, or {} when unreadable."""
        try:
            out = {}
            for n in ast.parse(open(path).read()).body:
                if (isinstance(n, ast.Assign) and len(n.targets) == 1
                        and isinstance(n.targets[0], ast.Name)
                        and isinstance(n.value, ast.Constant)
                        and isinstance(n.value.value, str)):
                    out[n.targets[0].id] = n.value.value
            return out
        except Exception:
            return {}

    consts, dict_nodes = {}, {}
    for node in tree.body:
        # Two of the eventual paths (DICT_REL, the two ledger RELs) are not
        # defined here at all — they arrive as `from .dictionary import DICT_REL`
        # and `from .ledger_targets import HUNT_REL as LEDGER_HUNT_REL`. Parsing
        # only this file's own assignments silently missed them, which the test
        # suite caught. Follow relative imports one level into the same package.
        if isinstance(node, ast.ImportFrom) and node.level and node.module:
            sib = os.path.join(os.path.dirname(TARGETS_PY), f"{node.module}.py")
            sibling = module_consts(sib)
            for alias in node.names:
                if alias.name in sibling:
                    consts[alias.asname or alias.name] = sibling[alias.name]
            continue
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            continue
        name, val = node.targets[0].id, node.value
        if isinstance(val, ast.Constant) and isinstance(val.value, str):
            consts[name] = val.value
        elif isinstance(val, ast.Dict):
            dict_nodes[name] = val

    def as_str(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            return n.value
        if isinstance(n, ast.Name):
            return consts.get(n.id)
        return None

    # every loop file is a target; LOOP_TARGETS maps name -> relative path
    for v in (dict_nodes.get("LOOP_TARGETS") or ast.Dict(keys=[], values=[])).values:
        s = as_str(v)
        if s:
            exact.add(s)

    for k, v in zip((dict_nodes.get("TARGETS") or ast.Dict(keys=[], values=[])).keys,
                    (dict_nodes.get("TARGETS") or ast.Dict(keys=[], values=[])).values):
        key = as_str(k) if k is not None else None
        if key in PARTIAL_TARGET_KEYS:          # partial render — see the note above
            continue
        if isinstance(v, ast.Tuple) and v.elts:
            s = as_str(v.elts[0])
            if s:
                exact.add(s)

    # THE DOSSIER DIRECTORY IS NOT ALL GENERATED, and blanket-guarding it was a
    # defect in this file's first cut. DNA/Clients/prospects/ holds 23 generated
    # dossiers AND 2 hand-authored files (a national-account enterprise file and
    # an intake) with no GENERATED banner, and the client-intake agent
    # deliberately writes `<name>-intake.md` there by hand. A directory rule
    # blocks those too — over-blocking a partner's own writing surface is how a
    # gate gets switched off, and switched off it protects nothing.
    #
    # The exporter enumerates the real ones in DOSSIER_FILES precisely because a
    # directory listing is the wrong source ("enumerated by the RECORD LAYER, not
    # by a directory listing"). Guard exactly that list, and it stays current
    # with the exporter for free: a new dossier lands in DOSSIER_FILES the moment
    # its client gets a notes_path.
    #
    # THE LIST IS READ FROM THE LOCAL ROSTER, not parsed out of targets.py
    # (WR-000049): its keys are client names, so it left the public tree for the
    # gitignored exporters/dossier-roster.local.json. The gate loads the SAME
    # reader targets.py uses (exporters/dossier_roster.py, stdlib-only, loaded by
    # path so the hook still needs no repo import), so the two cannot disagree.
    # A missing roster is LOGGED, never silent: the dossier layer then guards
    # nothing, and the vault .md deny-by-default (B) is what still holds.
    dossier_dir = (consts.get("DOSSIER_DIR") or "").rstrip("/")
    if dossier_dir:
        for name in dossier_roster_names():
            exact.add(f"{dossier_dir}/{name}")

    if not exact:
        raise ValueError("parsed no targets")
    return exact, dirs


DOSSIER_ROSTER_PY = os.path.join(REPO, "exporters", "dossier_roster.py")


def dossier_roster_names():
    """Dossier filenames from the local roster, via exporters/dossier_roster.py.

    Loaded by file path with importlib (no package import, no venv). An absent
    roster yields no names and one log line per call; a malformed one raises,
    which generated_paths()'s caller turns into the logged FALLBACK set.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("carr_dossier_roster", DOSSIER_ROSTER_PY)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load {DOSSIER_ROSTER_PY}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    names = sorted(mod.load_roster())
    if not names:
        log(f"DOSSIER-ROSTER absent — {mod.roster_status()}; the dossier layer guards no "
            f"file, layer B (.md deny-by-default) still applies")
    return names


# Used only when the parse fails. Deliberately the ORIGINAL sixteen: a stale
# guard on sixteen files beats no guard at all, and the log line says it happened.
GENERATED_FALLBACK = {
    "00_Context/compiled-rules-joe.md",
    "00_Context/decision-history.md",
    "00_Context/idea-bank.md",
    "00_Context/open-loops-backlog.md",
    "00_Context/open-loops.md",
    "DNA/Clients/client-roster.xlsx",
    "DNA/Clients/clients-active.md",
    "DNA/Deal Management/panhandle-team-deals.json",
    "DNA/Leads/lead-registry.xlsx",
    "DNA/Leads/lead-router-2026-07-13.xlsx",
    "DNA/Network/introduction-rules.md",
    "DNA/Network/vendors.xlsx",
    "DNA/Team/action-required.md",
    "DNA/Team/team-loops.md",
    "DNA/compiled-rules-dell.md",
    "DNA/compiled-rules-shared.md",
}

# --- B. the vault-wide .md deny-by-default (Phase 0, 2026-08-07) -------------
# The manifest is a sibling module so guard-unattended.py's Bash door uses the
# SAME code (rule a8c55a47). The old NEW_MD_DENIED_DIRS / HANDOFF_DIR class
# rules are superseded: both are strict subsets of deny-by-default.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from md_manifest import md_write_verdict  # noqa: E402
from corpus_renders import verdict as corpus_verdict  # noqa: E402
# Shared with hooks/guard-unattended.py, which refuses the SHELL spelling of
# the same write. One memory, so a record refused through either door is
# recognised at the other (rule 76a53dfe).
from refused_content import REFUSAL, remember_refusal, was_refused  # noqa: E402

# --- D. the one content check ------------------------------------------------
RULE_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
RULE_VERB = re.compile(r"\b(teach|activate-rule|retire-rule|amend-rule|supersedes?|"
                       r"proposed rule|active rule)\b", re.I)

USE_INSTEAD = ("Use a verb: log-decision (a settled call and why), add-loop (an open item), "
               "record-finding (research/verification), teach (a standing rule), "
               "update-loop / close-loop. The record layer is the memory; a markdown file "
               "reaches nobody and no query finds it.")


def log(msg):
    """Timestamped and self-identifying. Before 2026-08-03 no hook stamped its
    lines, so out/hook-guard.log could not answer "when did this fire" or even
    "which gate wrote this" — 51 lines with test fixtures indistinguishable from
    production denials. A log you cannot read chronologically is an artifact,
    not a check."""
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with open(LOG, "a") as fh:
            fh.write(f"{ts} record-home-gate {msg.rstrip()}\n")
    except Exception:
        pass


def rel_to_vault(path):
    """Vault-relative POSIX path, or None when the file is outside the vault."""
    try:
        ap = os.path.realpath(os.path.expanduser(path))
        av = os.path.realpath(VAULT)
        if ap == av or not ap.startswith(av + os.sep):
            return None
        return os.path.relpath(ap, av).replace(os.sep, "/")
    except Exception:
        return None


def check(tool, ti):
    path = ti.get("file_path") or ti.get("filePath") or ""
    if not path:
        return None

    # The delegation latch is mutable only by delegation-gate.py itself.  A
    # normal Edit/Write/apply_patch call could otherwise release a task by
    # changing the same state ledger the gate trusts.  This is deliberately
    # narrower than the record-home policy: it names exactly the control-plane
    # files and leaves ordinary out/ artifacts writable.
    try:
        control = {
            os.path.realpath(os.path.join(REPO, "out", "delegation-gate-state.json")),
            os.path.realpath(os.path.join(REPO, "out", "delegation-gate-state.json.lock")),
        }
        real_path = os.path.realpath(os.path.expanduser(path))
        if real_path in control or os.path.basename(real_path).startswith(".delegation-gate-"):
            return "delegation control-plane state is maintained only by delegation-gate.py"
    except Exception:
        pass

    # A0. CORPUS RENDERS — checked FIRST and deliberately ahead of the vault
    # test below, because two of the three corpus roots sit OUTSIDE the vault:
    # `drive:` paths live under My Drive/.claude and `home:` paths under
    # ~/.claude. Both would hit `rel is None` and be waved through, which is
    # precisely the hole that let the 2026-08-09 chair-bars edits land on eight
    # renders. Added 2026-08-10 on Joe's instruction; rationale in
    # hooks/corpus_renders.py.
    reason = corpus_verdict(path, tool.lower() + "'s change")
    if reason:
        return reason

    rel = rel_to_vault(path)
    if rel is None:
        return None                                   # outside the vault: not ours

    # A. generated renders — any tool, any extension
    try:
        exact, dirs = generated_paths()
    except Exception as exc:
        log(f"FALLBACK generated-list parse failed ({exc}) — guarding {len(GENERATED_FALLBACK)} "
            f"hardcoded paths only")
        exact, dirs = GENERATED_FALLBACK, set()

    if rel in exact:
        return (f"'{rel}' is a GENERATED render — hand-editing it is overwritten by the next "
                f"export and the change is lost. Change the record through a verb, or change "
                f"the exporter in ~/carr-system/exporters/. {USE_INSTEAD}")

    for d in dirs:
        if rel.startswith(d):
            return (f"'{rel}' sits under '{d}', which the exporter GENERATES in full. The client "
                    f"dossiers are rewritten several times a day — a deal update typed here is "
                    f"gone by morning and nothing reports it missing. This is the single worst "
                    f"case of the defect in the system, because four doctrine files still tell "
                    f"sessions to write here. {USE_INSTEAD}")

    if not rel.lower().endswith(".md"):
        return None                                   # B-D are markdown-only

    # B (PHASE 0 of the doctrine-store build, 2026-08-07, superseding the old
    # per-class rules B and C). DENY BY DEFAULT: any vault .md write — create OR
    # edit — is closed unless the exact-path manifest allows it. The old B
    # (new-file-in-00_Context) and C (handoffs) are strict subsets of this and
    # their history lives in git. Manifest and rationale: hooks/md_manifest.py,
    # design/doctrine-store-build-2026-08-07.md, council decision 82a2fb62.
    reason = md_write_verdict(rel)
    if reason:
        return reason

    # D. a rule id next to a rule verb is a record, never narrative
    body = ti.get("content") or ti.get("new_string") or ""
    if isinstance(body, str) and RULE_UUID.search(body) and RULE_VERB.search(body):
        return (f"'{rel}' carries a rule id alongside a rule verb, which makes it a RECORD, not "
                f"narrative. Rule text lives in the rule store and renders through the "
                f"compiled-rules exports. {USE_INSTEAD}")

    return None


PATCH_FILE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+?)\s*$", re.M)
PATCH_MOVE = re.compile(r"^\*\*\* Move to: (.+?)\s*$", re.M)


def check_apply_patch(ti, cwd):
    """Apply the same record-home policy to Codex's patch transport.

    Claude exposes a file path and content separately as Write/Edit tool inputs.
    Codex exposes one patch string through ``apply_patch``.  The policy belongs
    here, not in a second manifest: extract every target from the standard patch
    header and pass it through ``check``.  A delete-only patch is allowed so an
    agent can remove an already-created forbidden markdown file; creating or
    editing one remains denied.
    """
    # Codex's freeform apply_patch call supplies the patch itself as a string;
    # Claude-style callers use {command: patch}. Both are the same transport
    # policy and must remain equally inspectable.
    command = ti.get("command") if isinstance(ti, dict) else ti
    if not isinstance(command, str):
        return "Codex apply_patch did not provide a parseable patch command; denying the write."

    targets = PATCH_FILE.findall(command)
    if not targets:
        return "Codex apply_patch named no file targets; denying an uninspectable write."

    base = cwd if isinstance(cwd, str) and cwd else os.getcwd()
    for operation, raw_path in targets:
        if operation == "Delete":
            continue
        path = os.path.expanduser(raw_path)
        if not os.path.isabs(path):
            path = os.path.join(base, path)
        reason = check("apply_patch", {"file_path": path, "content": command})
        if reason:
            return reason
    # A move is emitted inside an Update File stanza. Inspect the destination
    # too, otherwise a non-markdown source could be renamed into vault Markdown
    # without ever presenting the destination to the policy.
    for raw_path in PATCH_MOVE.findall(command):
        path = os.path.expanduser(raw_path)
        if not os.path.isabs(path):
            path = os.path.join(base, path)
        reason = check("apply_patch", {"file_path": path, "content": command})
        if reason:
            return reason
    return None


def main():
    tool = ""
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:                          # fail OPEN
        log(f"ALLOW(parse-error) {exc}")
        sys.exit(0)

    try:
        tool = payload.get("tool_name") or payload.get("toolName") or ""
        ti = payload.get("tool_input") or payload.get("toolInput") or {}
        if tool in ("apply_patch", "functions.apply_patch"):
            reason = check_apply_patch(ti, payload.get("cwd"))
        elif tool in ("Write", "Edit", "MultiEdit") and isinstance(ti, dict):
            reason = check(tool, ti)
        else:
            sys.exit(0)

        session = payload.get("session_id") or payload.get("sessionId") or ""
        body = ti.get("content") or ti.get("new_string") or "" if isinstance(ti, dict) else ""

        if reason:
            # A refusal is only half the rule. Remember WHAT was refused, so the
            # same record cannot simply be written somewhere this gate does not
            # look (rule 76a53dfe, whose own text records that happening).
            remember_refusal(body, session)
            log(f"DENY {tool} :: {reason[:220]}")
            print(f"BLOCKED by the CARR record-home gate: {reason}", file=sys.stderr)
            sys.exit(2)

        # The path is allowed — but if this is the content a gate just refused,
        # allowing it here is the hole rather than the exception. Scratch writes
        # unrelated to any refusal never reach this branch, which is nearly all
        # of them.
        hidden, share = was_refused(body, session)
        if hidden:
            log(f"DENY {tool} :: re-routed refused content ({share:.2f})")
            print(REFUSAL.format(pct=round(share * 100)), file=sys.stderr)
            sys.exit(2)
        sys.exit(0)
    except Exception as exc:
        if tool in ("apply_patch", "functions.apply_patch"):  # Codex writes
            log(f"DENY apply_patch internal-error {exc}")
            print("BLOCKED by the CARR record-home gate: could not inspect Codex patch.",
                  file=sys.stderr)
            sys.exit(2)
        log(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
