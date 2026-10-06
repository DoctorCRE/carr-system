"""review_findings_ledger.py — carry-forward of code-review findings across
the commits of one pull request, and the disposition each one ends with.

WHY THIS EXISTS. engineering-workflow-sop section 15 ("The continuous loop"
and "The findings ledger"): on a PR that receives new commits, fixed findings
resolve, unresolved findings carry forward, and the review never restarts
from scratch; every finding ends with a disposition (fixed, dismissed with
reason, accepted risk). Before this module, council rows were bound to one
commit and no later request read them. Open loop 676 tracks the build.

THE STORAGE CHOICE, decided 2026-09-30 before any code and confirmed by Jev
(ask-jev receipt 53fd096e-06dc-4301-b4f3-39942ca42e76, storage ->
b_append_disposition_row at 1.0, read source -> r1_inline_in_request at 0.93):

  (a) an existing verb that updates a finding's status: NONE EXISTS. record_flag
      is append-only; no verb sets a status on an existing row.
  (b) CHOSEN. Each disposition is a NEW record-finding row, kind
      "code_review_disposition", filed against the commit under review, whose
      value names the prior finding (flag_id + index + commit). record-finding
      already documents this use: "Set disputed/superseded when filing against
      an earlier finding." No new verb, schema change, migration or deploy.
      The finding's current state is computed from the SET of its rows by
      resolve_ledger_dispositions(): newest reviewed head wins, and at the
      same head the most conservative disposition wins. Append order never
      decides it.
  (c) a new verb or migration: not needed for storage.

THE READ SIDE. No deployed verb reads v_code_finding or any record_flag value,
and the runner talks to the Worker only through the reviewer bearers. So the
REQUEST carries the prior findings inline (see validate_carry_forward), each
naming the record-layer flag_id of the review row it came from, and the caller
(the orchestrator) supplies them — flatten_review_findings() turns a recorded
review into that shape. When a read verb over v_code_finding ships, the source
swaps without touching the prompt or the disposition path.

THE PRIOR-FINDING SHAPE (one entry per finding, not per review row):
  flag_id             REQUIRED uuid: the record-finding row the finding sits in
  commit_sha          REQUIRED sha: that row's commit; must be in prior_commits
  index               REQUIRED int >= 0: position in that row's review.findings
  title               REQUIRED non-empty string
  severity            REQUIRED blocker | major | minor | nit
  detail, location    optional strings (location may be null)
  reviewer            optional string: which seat raised it
  ledger_disposition  optional {disposition, reason}: the finding's EFFECTIVE
                      disposition on the record, as resolve_ledger_dispositions()
                      computes it. fixed, dismissed and accepted_risk
                      are CLOSED — the finding is not re-raised and not
                      re-recorded. still_present (or absent) is OPEN.

Stdlib only, like the runner.
"""
from __future__ import annotations

import re
import uuid
from typing import Optional

CARRY_FORWARD_VERSION = "review-carry-forward-1.0.0"
DISPOSITION_KIND = "code_review_disposition"
DISPOSITION_SCHEMA = "carr.review-finding-disposition.v1"

# The vocabulary, exactly (Joe's 2026-09-30 build order, step 3).
DISPOSITIONS = ("fixed", "dismissed", "accepted_risk", "still_present")
REASON_REQUIRED = frozenset({"dismissed", "accepted_risk"})
CLOSED = frozenset({"fixed", "dismissed", "accepted_risk"})

# What a REVIEWER may say about a prior finding. accepted_risk is a human's
# call and never a reviewer's, so it is not offered; not_applicable is how a
# reviewer dismisses, and its evidence becomes the dismissal's reason.
REVIEWER_CLASSIFICATIONS = ("fixed", "still_present", "not_applicable")
DISPOSITION_BY_CLASSIFICATION = {
    "fixed": "fixed",
    "still_present": "still_present",
    "not_applicable": "dismissed",
}

# Each disposition's epistemic_status, inside record-finding's declared enum.
EPISTEMIC_BY_DISPOSITION = {
    "fixed": "superseded",       # the prior finding no longer describes the code
    "still_present": "reproduced",  # re-observed on the new commit
    "dismissed": "disputed",     # the finding is argued not to hold
    "accepted_risk": "accepted",  # true, and knowingly kept
}

SEVERITIES = ("blocker", "major", "minor", "nit")
SHA_RE = re.compile(r"[0-9a-fA-F]{7,40}")


class CarryForwardError(ValueError):
    """Malformed carry-forward data in a request. The message names the field."""


class DispositionError(ValueError):
    """A disposition outside the vocabulary, or missing its required reason."""


def is_active(req: dict) -> bool:
    """Carry-forward is on only when the request names a PR. A request without
    `pr` behaves exactly as it did before this module existed."""
    return "pr" in req


def _same_commit(a: str, b: str) -> bool:
    a, b = a.lower(), b.lower()
    return a.startswith(b) or b.startswith(a)


def _nonblank(v) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _validate_ledger_disposition(ld, where: str) -> None:
    if not isinstance(ld, dict):
        raise CarryForwardError(f"{where}.ledger_disposition must be an object")
    d = ld.get("disposition")
    if d not in DISPOSITIONS:
        raise CarryForwardError(
            f"{where}.ledger_disposition.disposition must be one of "
            f"{', '.join(DISPOSITIONS)}, got {d!r}")
    if d in REASON_REQUIRED and not _nonblank(ld.get("reason")):
        raise CarryForwardError(
            f"{where}.ledger_disposition: {d} requires a non-empty reason")


def validate_carry_forward(req: dict) -> None:
    """Raise CarryForwardError naming the exact field, or return None. Never
    guesses a fix: malformed prior data fails the request visibly."""
    has_pr = "pr" in req
    for f in ("prior_commits", "prior_findings"):
        if f in req and not has_pr:
            raise CarryForwardError(f"{f} is only valid with pr (the PR identity)")
    if not has_pr:
        return

    pr = req["pr"]
    if not isinstance(pr, dict):
        raise CarryForwardError("pr must be an object {number, branch}")
    if "number" not in pr and "branch" not in pr:
        raise CarryForwardError("pr must name a number, a branch, or both")
    if "number" in pr and (isinstance(pr["number"], bool) or not isinstance(pr["number"], int)
                           or pr["number"] <= 0):
        raise CarryForwardError(f"pr.number must be a positive integer, got {pr['number']!r}")
    if "branch" in pr and not _nonblank(pr["branch"]):
        raise CarryForwardError("pr.branch must be a non-empty string")
    if req.get("kind") != "code":
        raise CarryForwardError('pr (carry-forward) needs kind="code"; a design review has no commits')

    current = str(req.get("evidence", {}).get("commit_sha", ""))
    commits = req.get("prior_commits")
    if not isinstance(commits, list) or not commits:
        raise CarryForwardError("prior_commits must be a non-empty array of shas when pr is given")
    for i, sha in enumerate(commits):
        if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
            raise CarryForwardError(f"prior_commits[{i}] is not a sha: {sha!r}")
        if current and _same_commit(sha, current):
            raise CarryForwardError(
                f"prior_commits[{i}] is the commit under review ({sha}); prior means earlier")

    priors = req.get("prior_findings")
    if priors is None:
        raise CarryForwardError(
            "prior_findings is required when pr is given (an empty array means the prior "
            "rounds found nothing)")
    if not isinstance(priors, list):
        raise CarryForwardError("prior_findings must be an array")
    seen = set()
    for i, p in enumerate(priors):
        where = f"prior_findings[{i}]"
        if not isinstance(p, dict):
            raise CarryForwardError(f"{where} must be an object")
        # ONE spelling per finding identity: the canonical lowercase hyphenated
        # form str(uuid.UUID(x)) produces. Accepting other spellings would let
        # the same finding pass duplicate detection twice as P1 and P2.
        fid = p.get("flag_id")
        try:
            canonical = str(uuid.UUID(fid)) if isinstance(fid, str) else None
        except ValueError:
            canonical = None
        if canonical is None:
            raise CarryForwardError(f"{where}.flag_id is not a uuid: {fid!r}")
        if fid != canonical:
            raise CarryForwardError(
                f"{where}.flag_id must be the canonical lowercase hyphenated uuid {canonical!r}, "
                f"got {fid!r}")
        sha = p.get("commit_sha")
        if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
            raise CarryForwardError(f"{where}.commit_sha is not a sha: {sha!r}")
        if not any(_same_commit(sha, c) for c in commits):
            raise CarryForwardError(f"{where}.commit_sha {sha!r} is not one of prior_commits")
        idx = p.get("index")
        if isinstance(idx, bool) or not isinstance(idx, int) or idx < 0:
            raise CarryForwardError(f"{where}.index must be an integer >= 0, got {idx!r}")
        if not _nonblank(p.get("title")):
            raise CarryForwardError(f"{where}.title must be a non-empty string")
        if p.get("severity") not in SEVERITIES:
            raise CarryForwardError(
                f"{where}.severity must be one of {', '.join(SEVERITIES)}, got {p.get('severity')!r}")
        for f in ("detail", "reviewer"):
            if f in p and p[f] is not None and not isinstance(p[f], str):
                raise CarryForwardError(f"{where}.{f} must be a string")
        if "location" in p and p["location"] is not None and not isinstance(p["location"], str):
            raise CarryForwardError(f"{where}.location must be a string or null")
        if "ledger_disposition" in p:
            _validate_ledger_disposition(p["ledger_disposition"], where)
        key = (str(p["flag_id"]).lower(), idx)
        if key in seen:
            raise CarryForwardError(f"{where} is a duplicate of an earlier entry (same flag_id and index)")
        seen.add(key)


def is_closed(prior: dict) -> bool:
    ld = prior.get("ledger_disposition")
    return isinstance(ld, dict) and ld.get("disposition") in CLOSED


def open_prior_findings(req: dict) -> list[dict]:
    """The prior findings still open, in request order, each given a short id
    (P1, P2, ...) the reviewer answers by. Closed ones are never re-raised."""
    if not is_active(req):
        return []
    opens = [p for p in req.get("prior_findings") or [] if not is_closed(p)]
    return [{"id": f"P{n}", "prior": p} for n, p in enumerate(opens, start=1)]


# ── prompt pieces ────────────────────────────────────────────────────────
# Each returns "" when carry-forward is off, so the no-PR prompt is
# byte-identical to the one the runner rendered before this module existed.

def prompt_prior_block(req: dict) -> str:
    if not is_active(req):
        return ""
    pr = req["pr"]
    pr_label = " ".join(x for x in (
        f"#{pr['number']}" if "number" in pr else "",
        f"(branch {pr['branch']})" if "branch" in pr else "") if x)
    opens = open_prior_findings(req)
    closed_n = sum(1 for p in req.get("prior_findings") or [] if is_closed(p))
    lines = [
        f"PRIOR FINDINGS ({CARRY_FORWARD_VERSION}) — this commit is a later round of pull",
        f"request {pr_label}. Earlier commits reviewed: {', '.join(req['prior_commits'])}.",
    ]
    if opens:
        lines.append("For EACH finding below, look at THIS commit and classify it as \"fixed\",")
        lines.append("\"still_present\", or \"not_applicable\" (the finding was wrong or no longer")
        lines.append("applies), with concrete \"evidence\" (file:line or what you checked).")
        for o in opens:
            p = o["prior"]
            loc = p.get("location") or "no location"
            lines.append(f"- {o['id']} [{p['severity']}] {p['title']} — {loc} "
                         f"(record flag {p['flag_id']} #{p['index']}, commit {p['commit_sha']})")
            if p.get("detail"):
                lines.append(f"    {p['detail']}")
    else:
        lines.append("Every earlier finding is already closed; there is nothing to classify.")
    if closed_n:
        lines.append(f"({closed_n} earlier finding(s) are closed as fixed, dismissed or accepted "
                     f"risk and are deliberately not listed.)")
    return "\n".join(lines) + "\n\n"


def prompt_contract_field(req: dict) -> str:
    if not is_active(req):
        return ""
    return (',\n  "prior_finding_dispositions": [\n'
            '    {\n'
            '      "id": "P1",\n'
            '      "classification": "fixed" | "still_present" | "not_applicable",\n'
            '      "evidence": "what on this commit shows it"\n'
            '    }\n'
            '  ]')


def prompt_rules(req: dict) -> str:
    if not is_active(req):
        return ""
    return ("\nPRIOR FINDINGS RULES. Give exactly one prior_finding_dispositions entry per P-id\n"
            "listed above, and none for any other id. Do not repeat a prior finding in\n"
            "\"findings\"; a still-present one is reported only through its disposition.\n"
            "Do not hunt for new problems in code the earlier rounds already cleared unless\n"
            "this commit changed it.\n")


# ── the disposition row ─────────────────────────────────────────────────

def build_disposition_payload(req: dict, prior: dict, disposition: str,
                              reason: Optional[str], evidence: Optional[str],
                              backend: str, actor_slug: Optional[str] = None,
                              incident_ref: Optional[str] = None) -> dict:
    """Pure function: the record-finding arguments for ONE disposition of ONE
    prior finding. Refuses (DispositionError) a value outside the vocabulary,
    a dismissed/accepted_risk without a reason, and a blank incident_ref.

    INCIDENT BACK-LINK (section 15: "an incident links back to any finding
    that predicted it"). No link verb joins an incident to a record_flag row:
    open-incident's related_kind is run | deployment | work_request | defect |
    decision, and record-evidence-subject-link belongs to the CRE lifecycle
    store. So the link is a MANUAL two-step recipe over existing verbs. The
    runner never files it on its own, because it never knows about incidents:
      1. open-incident with `observed` naming the predicting finding, e.g.
         "predicted by record flag aaaaaaaa-0000-4000-8000-000000000001 #0".
      2. record-finding with the arguments this function returns when called
         with incident_ref, e.g.
           build_disposition_payload(req, prior, "still_present", None,
               "the null deref in INC-0042 is this finding", "claude",
               actor_slug="claude-orchestrator", incident_ref="INC-0042")
         which stores value.incident_ref = "INC-0042" beside value.prior
         {flag_id, index}. The row is filed under the filer's own identity.
    Both halves are append-only rows readers can join; no new incident
    tooling."""
    if disposition not in DISPOSITIONS:
        raise DispositionError(
            f"disposition must be one of {', '.join(DISPOSITIONS)}, got {disposition!r}")
    if disposition in REASON_REQUIRED and not _nonblank(reason):
        raise DispositionError(f"{disposition} requires a non-empty reason")
    if incident_ref is not None and not _nonblank(incident_ref):
        raise DispositionError("incident_ref, when given, must be a non-empty string")
    slug = actor_slug or f"{backend}-reviewer"
    current = req["evidence"]["commit_sha"]
    value = {
        "schema": DISPOSITION_SCHEMA,
        "disposition": disposition,
        "evidence": evidence,
        "prior": {
            "flag_id": prior["flag_id"],
            "commit_sha": prior["commit_sha"],
            "index": prior["index"],
            "title": prior["title"],
            "severity": prior["severity"],
            "reviewer": prior.get("reviewer"),
        },
        "pr": req.get("pr"),
        "commit_sha": current,
        "reviewer": backend,
        "request_id": req.get("request_id"),
        "carry_forward_contract": CARRY_FORWARD_VERSION,
    }
    if isinstance(reason, str) and reason.strip():
        value["reason"] = reason.strip()
    if isinstance(incident_ref, str) and incident_ref.strip():
        value["incident_ref"] = incident_ref.strip()
    return {
        "idempotency_key": str(uuid.uuid4()),
        "subject": f"commit:{current}",
        "kind": DISPOSITION_KIND,
        "value": value,
        "found": True,
        "epistemic_status": EPISTEMIC_BY_DISPOSITION[disposition],
        "source": f"{slug} / {CARRY_FORWARD_VERSION} / request {req.get('request_id')}",
    }


def reconcile_dispositions(opens: list[dict], review: dict) -> tuple[list[dict], list[str]]:
    """Match a reviewer's prior_finding_dispositions to the open prior findings.
    Returns (accepted, problems). accepted entries are
    {"id", "prior", "disposition", "reason", "evidence"}; every open finding
    the reviewer did not validly classify, and every entry that names an id
    never sent, becomes a problem. A non-empty problems list fails the
    reviewer visibly; the valid entries are still recorded.

    CONTRADICTIONS NEVER CLOSE A FINDING. Every id that appears in more than
    one entry is found in a first pass, before any entry is accepted, and that
    id gets NO disposition at all (it stays open for the next round), whatever
    the entries say. Other ids' valid classifications are kept.

    ZERO OPEN IS NOT A FREE PASS. With nothing sent, an absent or empty array
    is clean; any non-empty array is unsolicited output and a problem."""
    problems: list[str] = []
    accepted: list[dict] = []
    entries = review.get("prior_finding_dispositions") if isinstance(review, dict) else None
    if not opens:
        if isinstance(entries, list) and entries:
            problems.append(f"prior_finding_dispositions has {len(entries)} entr(y/ies) but no "
                            f"prior finding was sent for classification")
        elif entries is not None and not isinstance(entries, list):
            problems.append("prior_finding_dispositions is present but not an array")
        return accepted, problems
    by_id = {o["id"]: o for o in opens}
    if not isinstance(entries, list):
        return accepted, [f"{o['id']} unclassified: review has no prior_finding_dispositions array"
                          for o in opens]
    counts: dict[str, int] = {}
    for e in entries:
        if isinstance(e, dict) and isinstance(e.get("id"), str):
            counts[e["id"]] = counts.get(e["id"], 0) + 1
    duplicated = {pid for pid, k in counts.items() if k > 1 and pid in by_id}
    for dup_id in sorted(duplicated):
        problems.append(f"{dup_id} classified {counts[dup_id]} times; no disposition recorded "
                        f"for it, it stays open")
    classified = set()
    reported = set(duplicated)  # ids already named by a problem, matched exactly (never by substring)
    for n, e in enumerate(entries):
        if not isinstance(e, dict):
            problems.append(f"prior_finding_dispositions[{n}] is not an object")
            continue
        pid = e.get("id")
        # Type-check BEFORE any lookup: an unhashable id ([] or {}) must fail
        # this reviewer, never raise out of the request.
        if not isinstance(pid, str):
            problems.append(f"prior_finding_dispositions[{n}] has a non-string id {pid!r}")
            continue
        if pid not in by_id:
            problems.append(f"prior_finding_dispositions[{n}] names id {pid!r}, which was never sent")
            continue
        if pid in duplicated:
            continue
        cls = e.get("classification")
        if cls not in REVIEWER_CLASSIFICATIONS:
            problems.append(f"{pid} classification {cls!r} is not one of "
                            f"{', '.join(REVIEWER_CLASSIFICATIONS)}")
            reported.add(pid)
            continue
        evidence = e.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            problems.append(f"{pid} classified {cls} with no evidence")
            reported.add(pid)
            continue
        classified.add(pid)
        disposition = DISPOSITION_BY_CLASSIFICATION[cls]
        reason = f"not applicable: {evidence.strip()}" if cls == "not_applicable" else None
        accepted.append({"id": pid, "prior": by_id[pid]["prior"], "disposition": disposition,
                         "reason": reason, "evidence": evidence.strip()})
    for o in opens:
        if o["id"] not in classified and o["id"] not in reported:
            problems.append(f"{o['id']} unclassified: the reviewer returned no disposition for it")
    return accepted, problems


# ── effective state: the resolution policy for disposition rows ─────────
#
# Append order is NOT a resolution policy: two reviewers on the same commit
# write rows in whatever order they happen to finish, and a delayed older
# round can land after a newer one. So the effective disposition of one prior
# finding is computed from the SET of its rows, never from their order:
#
#   1. Keep only the rows filed at the NEWEST reviewed head among them, by the
#      PR's commit order (oldest -> newest). A row from an older round never
#      overrides a newer head, whenever it arrived.
#   2. Among the rows at that head, the MOST CONSERVATIVE disposition wins —
#      the one claiming the least remediation:
#        still_present  (nothing resolved; the finding stays OPEN)
#        accepted_risk  (real, knowingly kept, not remediated)
#        dismissed      (argued not to apply, not remediated)
#        fixed          (claims the code changed to resolve it)
#      So any reviewer still seeing the problem keeps it open.
#   3. Ties on disposition break on the reason text, so the chosen reason is
#      deterministic too.
CONSERVATISM_RANK = {"still_present": 0, "accepted_risk": 1, "dismissed": 2, "fixed": 3}


def resolve_ledger_dispositions(rows: list[dict], commit_order: list[str]) -> dict:
    """rows: disposition row values ({disposition, reason?, commit_sha,
    prior: {flag_id, index}}). commit_order: the PR's reviewed commits, oldest
    first, including the current head. Returns {(flag_id, index): {disposition,
    reason, commit_sha}} per the policy above. A row whose commit is not in
    commit_order, or whose disposition is outside the vocabulary, fails
    visibly (CarryForwardError) rather than being guessed at."""
    def position(sha) -> int:
        for i, c in enumerate(commit_order):
            if isinstance(sha, str) and SHA_RE.fullmatch(sha) and _same_commit(sha, c):
                return i
        raise CarryForwardError(f"disposition row commit {sha!r} is not in the PR's commit order")

    grouped: dict[tuple, list[tuple[int, dict]]] = {}
    for n, r in enumerate(rows):
        if not isinstance(r, dict) or not isinstance(r.get("prior"), dict):
            raise CarryForwardError(f"disposition row {n} has no prior object")
        if r.get("disposition") not in CONSERVATISM_RANK:
            raise CarryForwardError(f"disposition row {n} has disposition {r.get('disposition')!r}")
        key = (str(r["prior"].get("flag_id")).lower(), r["prior"].get("index"))
        grouped.setdefault(key, []).append((position(r.get("commit_sha")), r))

    resolved = {}
    for key, items in grouped.items():
        head = max(pos for pos, _ in items)
        at_head = [r for pos, r in items if pos == head]
        best = min(at_head, key=lambda r: (CONSERVATISM_RANK[r["disposition"]], r.get("reason") or ""))
        resolved[key] = {"disposition": best["disposition"], "reason": best.get("reason"),
                         "commit_sha": best["commit_sha"]}
    return resolved


def flatten_review_findings(flag_id: str, commit_sha: str, reviewer: str, review: dict) -> list[dict]:
    """Turn ONE recorded review (the value.review of a code_review row, plus
    that row's flag_id and commit) into prior_findings entries for the next
    round. Refuses a review whose findings is not a list."""
    findings = review.get("findings") if isinstance(review, dict) else None
    if not isinstance(findings, list):
        raise CarryForwardError("review.findings must be an array to carry forward")
    out = []
    for i, f in enumerate(findings):
        if not isinstance(f, dict):
            raise CarryForwardError(f"review.findings[{i}] is not an object")
        out.append({
            "flag_id": flag_id, "commit_sha": commit_sha, "index": i, "reviewer": reviewer,
            "severity": f.get("severity"), "title": f.get("title"),
            "detail": f.get("detail"), "location": f.get("location"),
        })
    return out
