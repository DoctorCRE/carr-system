#!/usr/bin/env python3
"""The room-bridge side of one server-issued Engineering Passport dispatch.

This process receives no database credential.  The controller has already
claimed the immutable job and passed only the server-issued envelope plus the
accepted slice.  It launches a fresh *dedicated* Codex desk through the same
Hermes dispatch wire ordinary local work uses, validates the typed receipt
before it returns it, and never writes the record layer itself.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import dispatch  # noqa: E402
import desks  # noqa: E402
import engineering_passport  # noqa: E402

ENGINEERING_DESK = "engineering-codex"
DESK_SPEC_PATH = REPO / "ops" / "config" / "engineering-codex-desk.v1.json"
# The Engineering controller does not inherit the general router's optional
# registry-location override.  This fixed per-user registry is part of the
# local adapter identity, just like the fixed desk name below.
DEDICATED_REGISTRY_PATH = Path.home() / ".config" / "carr" / "hermes-desks.json"
DISPATCH_MINIMUM_RUNWAY = timedelta(seconds=930)
EXECUTOR_TIMEOUT_SECONDS = 900
EXECUTOR_RECEIPT_RESERVE_SECONDS = 120
AUTHORIZED_WRITABLE_ROOTS = (
    "/Users/booko/carr-system/.git",
    "/Users/booko/carr-system/out",
)
AUTHORIZED_CODEX_CONFIG_OVERRIDES = (
    "sandbox_workspace_write.network_access=true",
    'features.network_proxy={enabled=true,domains={"github.com"="allow","api.github.com"="allow"}}',
)
REQUIRED_RULE_PACKS = (
    "engineering-git",
    "delegation-council",
    "scheduled-automation",
    "source-study",
)
STANDING_CONTEXT_STORE_KEY = "carr_engineering_standing_context_rules_v1"
STANDING_CONTEXT_RULE_CHUNK_SIZE = 8
REQUIRED_RULE_PACKS_JSON = json.dumps(list(REQUIRED_RULE_PACKS), separators=(",", ":"))
STANDING_CONTEXT_NATIVE_PROJECTION_JS = r'''// @exec: {"max_output_tokens": 3000}
const requiredPacks = __REQUIRED_RULE_PACKS_JSON__;
const toolName = "mcp__carr__standing_context";
const result = await tools.mcp__carr__standing_context({packs: requiredPacks});
const textBlocks = Array.isArray(result?.content)
  ? result.content.filter((item) => item?.type === "text" && typeof item.text === "string")
  : [];
if (textBlocks.length !== 1) throw new Error("standing-context returned an unsupported native CallToolResult");
const response = JSON.parse(textBlocks[0].text);
const delivery = response?.rule_delivery;
if (!delivery || !Array.isArray(delivery.declared_packs)) {
  throw new Error("standing-context native response omitted rule_delivery.declared_packs");
}
const packsNotFound = Array.isArray(delivery.packs_not_found) ? delivery.packs_not_found : [];
const sharedRules = Array.isArray(response.shared_rules) ? response.shared_rules : [];
const personalRules = Array.isArray(response.personal_rules) ? response.personal_rules : [];
const rules = [
  ...sharedRules.map((rule) => ({scope:"shared",...rule})),
  ...personalRules.map((rule) => ({scope:"personal",...rule})),
];
store(__STANDING_CONTEXT_STORE_KEY_JSON__, {rules, next: 0});
text(JSON.stringify({
  schema_version:"engineering-standing-context-native-projection.v1",
  provenance:"native_call_tool_result",
  source_call:{tool_name:toolName,input:{packs:requiredPacks}},
  ok:response.ok === true,
  recite:response.recite,
  identity:response.identity,
  rule_delivery:{
    mode:delivery.mode,
    declared_packs:delivery.declared_packs,
    packs_not_found:packsNotFound,
  },
  verification:{
    exact_required_packs:JSON.stringify(delivery.declared_packs) === JSON.stringify(requiredPacks),
    packs_not_found_empty:packsNotFound.length === 0,
  },
  rule_counts:{shared:sharedRules.length,personal:personalRules.length,total:rules.length},
  rule_chunk:{store_key:__STANDING_CONTEXT_STORE_KEY_JSON__,size:__RULE_CHUNK_SIZE__,next:0},
}));'''.replace(
    "__REQUIRED_RULE_PACKS_JSON__", REQUIRED_RULE_PACKS_JSON,
).replace(
    "__STANDING_CONTEXT_STORE_KEY_JSON__", json.dumps(STANDING_CONTEXT_STORE_KEY),
).replace("__RULE_CHUNK_SIZE__", str(STANDING_CONTEXT_RULE_CHUNK_SIZE))
STANDING_CONTEXT_RULE_CHUNK_JS = r'''// @exec: {"max_output_tokens": 3000}
const key = __STANDING_CONTEXT_STORE_KEY_JSON__;
const state = load(key);
if (!state || !Array.isArray(state.rules) || !Number.isInteger(state.next)) {
  throw new Error("standing-context native rule store is unavailable");
}
const start = state.next;
const end = Math.min(start + __RULE_CHUNK_SIZE__, state.rules.length);
const rules = state.rules.slice(start, end);
store(key, {rules: state.rules, next: end});
text(JSON.stringify({
  schema_version:"engineering-standing-context-native-rule-chunk.v1",
  provenance:"native_call_tool_result",
  source_call:{tool_name:"mcp__carr__standing_context"},
  start,
  end,
  total:state.rules.length,
  remaining:state.rules.length-end,
  rules,
}));'''.replace(
    "__STANDING_CONTEXT_STORE_KEY_JSON__", json.dumps(STANDING_CONTEXT_STORE_KEY),
).replace("__RULE_CHUNK_SIZE__", str(STANDING_CONTEXT_RULE_CHUNK_SIZE))
# The immutable packet deliberately projects no accepted plan caps or runbook.
# The child hydrates them itself from the two existing read-only verbs, bound
# to the controller plan; a mismatch refuses before any repository work.
SOURCE_MERGE_TOKEN = re.compile(r"source[_-]merge", re.IGNORECASE)
RUNBOOK_STORE_KEY = "carr_engineering_runbook_body_v1"
RUNBOOK_CHUNK_CHARS = 4000
ENGINEERING_SOURCE_HELPER_PATH = HERE / "engineering_source_projection.js"
ENGINEERING_SOURCE_HELPER_SHA256 = "3cd0cd7f1ef18940dd5232bd3a72611618b0137b398e86ffc974777bc64294a4"
ENGINEERING_SOURCE_HELPER_BYTE_LENGTH = 17068
def _engineering_source_helper_interpreter() -> str:
    """Resolve the interpreter the reader command runs under, on any checkout.

    The dedicated desk runs on Joe's machine, where the repository venv always
    exists, and that interpreter stays the first choice.  It cannot be the ONLY
    choice: a hosted runner installs requirements.lock into setup-python and
    never builds a repository venv, and Dell's machine has no venv before its
    migration runs.  ops/ci.sh already resolves exactly this pair for the same
    reason.  Pinning the venv unconditionally made the shipped reader command
    exit 127 everywhere the venv is absent -- a real portability break that a
    Mac could never see.  sys.executable keeps the fallback an absolute path,
    which is what the child's exec_command needs when PATH is minimal.
    """
    venv = REPO / ".venv" / "bin" / "python"
    if os.access(venv, os.X_OK):
        return str(venv)
    if sys.executable and os.access(sys.executable, os.X_OK):
        return sys.executable
    return "python3"


def _engineering_source_helper_read_command(path: Path) -> str:
    reader = (
        "import hashlib,json,pathlib;"
        f"b=pathlib.Path({json.dumps(str(path))}).read_bytes();"
        'print(json.dumps({"schema_version":"engineering-source-helper-read.v1",'
        '"byte_length":len(b),"sha256":hashlib.sha256(b).hexdigest(),'
        '"code":b.decode("utf-8")},separators=(",",":")))'
    )
    return f"{shlex.quote(_engineering_source_helper_interpreter())} -c {shlex.quote(reader)}"


ENGINEERING_SOURCE_HELPER_READ_COMMAND = _engineering_source_helper_read_command(
    ENGINEERING_SOURCE_HELPER_PATH)
ENGINEERING_SOURCE_NATIVE_LOADER_JS_TEMPLATE = r'''// @exec: {"max_output_tokens": 3000}
const expected = __EXPECTED_BINDING_JSON__;
const expectedHelper = {
  sha256: __HELPER_SHA256_JSON__,
  byte_length: __HELPER_BYTE_LENGTH__,
};
const helperRead = await tools.exec_command({
  cmd: __HELPER_READ_COMMAND_JSON__,
  workdir: __REPO_JSON__,
  yield_time_ms: 10000,
  max_output_tokens: 16000,
});
if (!helperRead || helperRead.exit_code !== 0) {
  throw new Error("engineering source hydration refused: helper reader command failed");
}
let helper;
try {
  helper = JSON.parse(helperRead.output);
} catch (_error) {
  throw new Error("engineering source hydration refused: helper reader returned invalid JSON");
}
if (!helper || typeof helper !== "object" || Array.isArray(helper)
    || Object.keys(helper).sort().join(",") !== "byte_length,code,schema_version,sha256"
    || helper.schema_version !== "engineering-source-helper-read.v1"
    || typeof helper.code !== "string") {
  throw new Error("engineering source hydration refused: helper reader returned an invalid shape");
}
if (helper.sha256 !== expectedHelper.sha256
    || helper.byte_length !== expectedHelper.byte_length
    || helper.code.length !== expectedHelper.byte_length) {
  throw new Error("engineering source hydration refused: helper digest or byte length mismatch");
}
const projection = await eval(helper.code);
if (!projection || projection.schema_version !== "engineering-source-native-projection.v1") {
  throw new Error("engineering source hydration refused: helper returned no exact projection");
}
text(JSON.stringify(projection));'''.replace(
    "__HELPER_SHA256_JSON__", json.dumps(ENGINEERING_SOURCE_HELPER_SHA256),
).replace(
    "__HELPER_BYTE_LENGTH__", str(ENGINEERING_SOURCE_HELPER_BYTE_LENGTH),
).replace(
    "__HELPER_READ_COMMAND_JSON__", json.dumps(ENGINEERING_SOURCE_HELPER_READ_COMMAND),
).replace(
    "__REPO_JSON__", json.dumps(str(REPO)),
)
RUNBOOK_NATIVE_CHUNK_JS = r'''// @exec: {"max_output_tokens": 3000}
const key = __RUNBOOK_STORE_KEY_JSON__;
const state = load(key);
if (!state || typeof state.text !== "string" || !Number.isInteger(state.next)) {
  throw new Error("engineering runbook native store is unavailable");
}
const start = state.next;
let end = Math.min(start + __RUNBOOK_CHUNK_CHARS__, state.text.length);
// Never split a surrogate pair across chunks; the reader sees whole characters.
if (end < state.text.length && end - start > 1) {
  const code = state.text.charCodeAt(end - 1);
  if (code >= 0xd800 && code <= 0xdbff) end -= 1;
}
const chunk = state.text.slice(start, end);
store(key, {...state, next: end});
text(JSON.stringify({
  schema_version:"engineering-runbook-native-chunk.v1",
  provenance:"native_call_tool_result",
  source_call:{tool_name:"mcp__carr__doctrine_sections"},
  section_id:state.section_id,
  current_version:state.current_version,
  content_hash:state.content_hash,
  start,
  end,
  total:state.text.length,
  remaining:state.text.length-end,
  text:chunk,
}));'''.replace(
    "__RUNBOOK_STORE_KEY_JSON__", json.dumps(RUNBOOK_STORE_KEY),
).replace("__RUNBOOK_CHUNK_CHARS__", str(RUNBOOK_CHUNK_CHARS))
# Facts only the fresh child can truthfully observe.  Each placeholder is a
# null the existing receipt validator rejects, so an unfilled template can
# never persist as evidence.
RECEIPT_TEMPLATE_PLACEHOLDER_PATHS = (
    "source_evidence.worktree_ref",
    "source_evidence.branch_ref",
    "source_evidence.source_sha",
    "reset_reconstruction.reconstruction_free",
    "executor_claim.claimed_at",
)
# The prompt task binding keeps the exact eight keys the rule-pack drift gate
# parses; controller-only fields ride in the hydration binding instead.
PROMPT_TASK_EXCLUDED_KEYS = frozenset({"claim_lease_expires_at", "work_request_ref"})
# The independently qualified GitHub route: per-command overrides only, so no
# Git configuration changes, no credential ever printed or stored by the
# child, no interactive prompt, and no hook bypass.
GITHUB_GIT_COMMAND_PREFIX = (
    "git -c url.https://github.com/.insteadOf=git@github.com: -c credential.helper= "
    "-c 'credential.helper=!gh auth git-credential' -c credential.interactive=never "
    "-c core.askPass=/bin/false"
)


# A pointer to operator-authored restrictions, not a caller-selected authority.
# The live revision must bind the exact envelope before the child may use it.
R09_ASSIGNMENT = {
    "section_id": "4b671fec-bc0b-4dad-a91d-2f154e0b6f7b",
    "scope_section_id": "dcc241e4-9454-49a4-8e62-839d25a0c449",
    "scope_sha256": "3e854d36d91f5bba3540274417f45838682d1d149ba6d856de9f683d74ceaa6e",
    "packet_section_id": "5845e7b4-3b51-431a-835f-51d21c06977d",
    "packet_sha256": "4a34daafdbce5fa95dbcdb0bc12e40e255ed7ca328afe4bf528a322b789faeda",
    # Return constraints derived from that pinned scope; native hydration
    # cross-checks them against the current record before any repository work.
    "expected_worktree_ref": "worktree:sha256:4fdd7e2e37d820d398d38225de2960380b715527f5e962f1d1e6dbad3cdf3b1b",
    "expected_branch_ref": "branch:sha256:88653a24843986c51ca26c9025a1466463f4e5c9717a07ba84c76afd13995449",
}
GITHUB_WORKTREE_COMMAND_PREFIX = (
    GITHUB_GIT_COMMAND_PREFIX + " -c 'alias.carr-worktree=!./run.sh worktree' carr-worktree"
)
ASSIGNMENT_NATIVE_CHUNK_JS = RUNBOOK_NATIVE_CHUNK_JS.replace(
    "carr_engineering_runbook_body_v1", "carr_engineering_assignment_body_v1"
).replace("engineering-runbook-native-chunk.v1", "engineering-assignment-native-chunk.v1").replace(
    "engineering runbook native store", "engineering assignment native store"
).replace(
    'provenance:"native_call_tool_result"', 'provenance:"derived_from_verified_native_records"'
)


class DispatchRefusal(RuntimeError):
    pass


def slice_requires_source_merge(slice_row: dict) -> bool:
    """True only when the accepted slice text or checks literally name source_merge."""
    fragments = [slice_row.get(field) for field in ("objective", "definition_of_done", "scope_boundary")]
    for check in slice_row.get("planned_checks") or []:
        if isinstance(check, dict):
            fragments.extend((check.get("check_ref"), check.get("failure_condition")))
    return any(isinstance(fragment, str) and SOURCE_MERGE_TOKEN.search(fragment) for fragment in fragments)


def source_hydration_binding(task: dict, plan: dict, slice_row: dict) -> dict:
    """The exact expectations the child verifies the live source against."""
    ref = task.get("work_request_ref")
    if not isinstance(ref, str) or not ref.strip():
        raise DispatchRefusal("engineering controller task has no canonical Work Request ref")
    return {
        "work_request_ref": ref,
        "work_request": plan["work_request"],
        "accepted_plan_revision": plan["accepted_plan_revision"],
        "slice_ref": task["slice_ref"],
        "source_merge_required": slice_requires_source_merge(slice_row),
    }


def bind_operator_assignment(binding: dict, task: dict, envelope: dict) -> dict:
    """Only the reviewed WR70/R09 route may resolve this stable section pointer."""
    if binding["work_request_ref"] == "WR-000070" and binding["slice_ref"] == "R09":
        if not isinstance(task.get("attempt_id"), str) or not task["attempt_id"].strip():
            raise DispatchRefusal("engineering controller task has no attempt id")
        if binding["accepted_plan_revision"] != {
            "id": "PLAN-745ea4f7e374-v1", "revision": 1,
            "digest": "sha256:745ea4f7e3745c86ee6aae273e5ec915a539493f4a1b0dafe2144b7d3d70eb60",
        }:
            raise DispatchRefusal("R09 assignment route requires its exact accepted plan")
        binding = {**binding, "operator_assignment": {
            **R09_ASSIGNMENT, "envelope_id": envelope["envelope_id"],
            "envelope_digest": engineering_passport.base.execution_envelope_digest(envelope),
            "attempt_id": task["attempt_id"],
        }}
    return binding


def require_assignment_return(receipt: dict, binding: dict) -> None:
    """A completed claim must describe the worktree and branch in the pinned scope.

    Blocked reports may truthfully describe a failed/pre-existing checkout. This
    does not prove a clean diff: exact-head changed-path review remains required.
    """
    assignment = binding.get("operator_assignment")
    if assignment and receipt["outcome"] == "claimed_complete":
        source = receipt["source_evidence"]
        if (source["worktree_ref"] != assignment["expected_worktree_ref"]
                or source["branch_ref"] != assignment["expected_branch_ref"]):
            raise DispatchRefusal("completed receipt source does not match the dispatcher assignment")


def validate_receipt_document(value: dict) -> dict:
    """Read-only preflight using the same validator as the dispatch boundary."""
    if not isinstance(value, dict) or set(value) != {"receipt", "plan", "envelope"}:
        raise DispatchRefusal("receipt preflight requires receipt, plan and envelope")
    engineering_passport.validate_engineering_slice_receipt(
        value["receipt"], value["plan"], value["envelope"])
    return {"ok": True, "validation": "engineering-slice-receipt.v1", "persisted": False}


def engineering_source_loader_js(binding: dict) -> str:
    return ENGINEERING_SOURCE_NATIVE_LOADER_JS_TEMPLATE.replace(
        "__EXPECTED_BINDING_JSON__", json.dumps(binding, sort_keys=True, separators=(",", ":")))


def build_engineering_slice_receipt_template(packet: dict, task: dict, envelope: dict,
                                             slice_row: dict, executor_slug: str) -> dict:
    """Exact engineering-slice-receipt.v1 field set with blocked-safe defaults."""
    attempt_id = task.get("attempt_id")
    if not isinstance(attempt_id, str) or not attempt_id.strip():
        raise DispatchRefusal("engineering controller task has no attempt id")
    identity = envelope["server_binding"]["identity"]
    adapter_binding = envelope["server_binding"]["adapter"]
    return {
        "schema_version": "engineering-slice-receipt.v1",
        "envelope_digest": packet["envelope_digest"],
        "attempt_id": attempt_id,
        "slice_ref": packet["slice_ref"],
        "plan_digest": packet["plan_digest"],
        "attribution": {
            "actor_ref": identity["agent_principal_id"],
            "session_ref": envelope["agent_session"]["id"],
            "adapter_ref": adapter_binding["adapter_id"],
        },
        "planned_resource_refs": list(slice_row["declared_resource_refs"]),
        "actual_resource_refs": [],
        "planned_component_refs": list(slice_row["declared_component_refs"]),
        "actual_component_refs": [],
        "checks": [{"check_ref": check["check_ref"], "state": "not_run", "evidence_refs": []}
                   for check in packet["planned_checks"]],
        "outcome": "blocked",
        "artifact_refs": [],
        "evidence_refs": [],
        "deviations": [],
        "source_evidence": {"worktree_ref": None, "branch_ref": None, "source_sha": None, "evidence_refs": []},
        "reset_reconstruction": {"fresh_session": True, "inherited_transcript_used": False,
                                 "reconstruction_free": None, "remediation_action": None},
        "executor_claim": {"claim_state": "executor_claim", "claimed_by": executor_slug, "claimed_at": None},
        "independent_verification_required": True,
    }


def _git_common_dir() -> Path:
    """Resolve the shared Git metadata root without trusting a child process."""
    dotgit = REPO / ".git"
    if dotgit.is_dir():
        common = dotgit.resolve()
    elif dotgit.is_file():
        try:
            marker = dotgit.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise DispatchRefusal("Engineering repository Git metadata is unavailable") from exc
        prefix = "gitdir: "
        if not marker.startswith(prefix) or "\n" in marker:
            raise DispatchRefusal("Engineering repository Git metadata is malformed")
        git_dir = Path(marker[len(prefix):])
        if not git_dir.is_absolute():
            git_dir = REPO / git_dir
        git_dir = git_dir.resolve()
        commondir = git_dir / "commondir"
        if commondir.is_file():
            try:
                common_ref = commondir.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise DispatchRefusal("Engineering repository common Git metadata is unavailable") from exc
            if not common_ref or "\n" in common_ref:
                raise DispatchRefusal("Engineering repository common Git metadata is malformed")
            common = (git_dir / common_ref).resolve()
        else:
            common = git_dir
    else:
        raise DispatchRefusal("Engineering repository Git metadata is unavailable")
    if not common.is_dir() or not (common / "HEAD").is_file() or common.name != ".git":
        raise DispatchRefusal("Engineering repository common Git metadata is invalid")
    return common


def _dedicated_writable_roots() -> list[str]:
    """The exact two shared lock roots authorized for repository-write slices."""
    common = _git_common_dir()
    shared_out = (common.parent / "out").resolve()
    if not shared_out.is_dir():
        raise DispatchRefusal("Engineering shared output root is unavailable")
    roots = [str(common), str(shared_out)]
    if tuple(roots) != AUTHORIZED_WRITABLE_ROOTS:
        raise DispatchRefusal("Engineering desk writable roots do not match the authorized machine boundary")
    return roots


def _canonical_utc_second(value: object) -> datetime:
    """Accept only the exact server timestamp representation in an envelope."""
    if not isinstance(value, str):
        raise DispatchRefusal("engineering envelope authority expiry is malformed")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise DispatchRefusal("engineering envelope authority expiry is malformed") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise DispatchRefusal("engineering envelope authority expiry is malformed")
    return parsed


def _require_dispatch_runway(envelope: dict, claim_lease_expires_at: object) -> None:
    """Refuse unless every authority can outlive the fixed 900s timeout."""
    if not isinstance(envelope, dict):
        raise DispatchRefusal("engineering envelope is malformed")
    expiry = _canonical_utc_second(envelope.get("expires_at"))
    agent_session = envelope.get("agent_session")
    if not isinstance(agent_session, dict):
        raise DispatchRefusal("engineering envelope agent session is malformed")
    session_expiry = _canonical_utc_second(agent_session.get("lease_expires_at"))
    if session_expiry != expiry:
        raise DispatchRefusal("engineering envelope and session expiry do not match")
    claim_expiry = _canonical_utc_second(claim_lease_expires_at)
    checked_at = datetime.now(timezone.utc)
    if (expiry - checked_at < DISPATCH_MINIMUM_RUNWAY
            or claim_expiry - checked_at < DISPATCH_MINIMUM_RUNWAY):
        raise DispatchRefusal("engineering envelope authority runway is insufficient")


def _safe_child_env() -> dict[str, str]:
    """Give Codex its local runtime, never the controller's DB capability."""
    allowed = ("HOME", "PATH", "LANG", "LC_ALL", "TMPDIR", "TERM")
    return {key: os.environ[key] for key in allowed if os.environ.get(key)}


def _read_request() -> dict:
    try:
        raw = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError) as exc:
        raise DispatchRefusal("engineering controller input is not JSON") from exc
    if not isinstance(raw, dict) or set(raw) != {"desk", "envelope", "task", "executor_slug"}:
        raise DispatchRefusal("engineering controller input has an unsupported shape")
    if not isinstance(raw["desk"], str) or not raw["desk"].strip():
        raise DispatchRefusal("engineering controller desk is missing")
    if raw["executor_slug"] != "codex":
        raise DispatchRefusal("engineering controller only supports the server-bound Codex executor")
    if not isinstance(raw["task"], dict):
        raise DispatchRefusal("engineering controller task is missing")
    return raw


def _prompt(packet: dict, task: dict, source_loader_js: str, receipt_template: dict,
            source_merge_required: bool, source_envelope: dict) -> str:
    """One exact execution request.  The executor cannot select authority."""
    source_merge_note = (
        "The accepted slice names source_merge: the projection carries the exact accepted "
        "authorized_paths, and they are the only paths this slice may change."
        if source_merge_required else
        "This slice does not name source_merge; the projection carries source_merge only when "
        "the accepted plan declares it, and its absence is not a blocker."
    )
    return (
        "You are the fresh, dedicated Codex executor for one bounded CARR Engineering Passport slice.\n\n"
        "RULE-DELIVERY WORKFLOW: engineering-slice\n"
        f"RULE-DELIVERY PACKS: {','.join(REQUIRED_RULE_PACKS)}\n"
        "FIRST: call `standing-context` with exactly this input and read the returned rules: "
        f"{{\"packs\":{REQUIRED_RULE_PACKS_JSON}}}. "
        "Do not pass `workflow`: standing-context also interprets that field as a pack name, and "
        "`engineering-slice` is a workflow label rather than a canonical rule pack. "
        "The direct tool is available inside `functions.exec` as "
        "`tools.mcp__carr__standing_context`; do not inspect or print `ALL_TOOLS`. Run the exact "
        "native projection code below in one `functions.exec` call. It stores the complete delivered "
        "shared and personal rule arrays while printing the small authoritative gate projection first.\n\n"
        "STANDING-CONTEXT NATIVE PROJECTION CODE (exact):\n"
        f"{STANDING_CONTEXT_NATIVE_PROJECTION_JS}\n\n"
        "A `rule-jit-trigger-delivery/v1` PreToolUse additional-context receipt is supplemental JIT "
        "delivery from a separate local selector. Obey its delivered rules, but never treat its "
        "`declared_packs`, identity, or receipt id as the native standing-context response above. "
        "A `rule-route-trigger-delivery/v1` receipt is the same kind of supplemental delivery, "
        "chosen by exact route matchers: obey every rule in its `rules`, fetch every id in its "
        "`overflow` or `not_found` with standing-context `rule_ids` before acting on it, and treat "
        "any context beginning `RULE ROUTE` as rules NOT delivered that you must fetch the same way. "
        "REFUSE before inspecting the envelope, source, or job if that call fails, reports any "
        "packs_not_found, or does not read back all four canonical names. Never substitute an alias or "
        "a full-set fallback. After that gate passes, run the exact chunk code below repeatedly in "
        "`functions.exec`, reading every returned rule chunk, until `remaining` is zero. Never print "
        "the raw CallToolResult.\n\n"
        "STANDING-CONTEXT NATIVE RULE CHUNK CODE (repeat until remaining=0):\n"
        f"{STANDING_CONTEXT_RULE_CHUNK_JS}\n\n"
        "ACCEPTED SOURCE HYDRATION (after every rule chunk is read, before any repository work): the "
        "immutable packet below describes the accepted slice but deliberately projects no accepted plan caps "
        "and no runbook. Run the short exact native source loader below in one `functions.exec` call. The "
        "loader reads one tracked controller helper exactly once, verifies its pinned byte length and SHA-256 "
        "before evaluation, and refuses before either MCP source call on any read, shape, length, or digest "
        "mismatch. The reviewed helper calls `tools.mcp__carr__engineering_passport_source` with the canonical Work Request ref and "
        "`tools.mcp__carr__doctrine_sections` for the accepted runbook section, validates the current Work "
        "Request id/version/digest and accepted plan ref/revision/digest against this controller binding, "
        "verifies the current runbook body against the accepted content hash, stores the complete runbook "
        "body, and prints one bounded source projection. If it throws, REFUSE with a typed blocked receipt: "
        "never retry with a different Work Request, never print the raw CallToolResult, and never read a "
        f"local, ignored, or cached runbook file in its place. {source_merge_note}\n\n"
        "ENGINEERING SOURCE NATIVE LOADER CODE (exact):\n"
        f"{source_loader_js}\n\n"
        "After it prints, run the exact runbook chunk code below repeatedly in `functions.exec`, reading "
        "every returned chunk of the runbook body, until `remaining` is zero. Do not begin repository work "
        "before the runbook is fully read.\n\n"
        "RUNBOOK NATIVE CHUNK CODE (repeat until remaining=0):\n"
        f"{RUNBOOK_NATIVE_CHUNK_JS}\n\n"
        "When the source projection has operator_assignment, it is the exact dispatcher restriction for "
        "this envelope. Before selecting or creating a worktree, read its complete supplemental scope and "
        "implementation packet using the following chunk code until remaining=0. Its specific worktree "
        "creation method supersedes historical examples in the packets. Missing/stale/mismatched assignment "
        "is a hard refusal; never invent a name or use another worktree.\n\n"
        f"ASSIGNMENT CHUNK CODE (only when operator_assignment is present):\n{ASSIGNMENT_NATIVE_CHUNK_JS}\n\n"
        "For an assigned worktree, first verify a fresh GitHub origin/main equals source_main; create ONLY "
        "the assigned helper_name, then rename to the assigned branch. Verify the registered path, branch, "
        "clean HEAD and standard plumbing before edits. Only assignment.paths may change.\n\n"
        "The controller—not you—owns the database lease, identity, authority, and lifecycle. "
        "Do not connect directly to any database, do not claim/retry/complete a job, do not reuse a session, "
        "and do not widen the accepted slice. Work only inside the controller's isolated Git worktree.\n\n"
        f"HARD EXECUTION BUDGET: the local adapter stops this native turn after {EXECUTOR_TIMEOUT_SECONDS} seconds. "
        f"Reserve the final {EXECUTOR_RECEIPT_RESERVE_SECONDS} seconds for the required commit/push/PR steps when "
        "the slice is complete and for the single typed JSON receipt in every outcome. Never begin a broad or "
        "unbounded check late in the turn. Run the smallest declared-scope fixture/snapshot checks first; broader "
        "repository gates belong after those bounded checks and only when enough time remains. If completion cannot "
        "be proven inside the budget, stop work and return a typed blocked receipt before the adapter deadline.\n\n"
        "RECOVERY FIRST: this fresh native session may follow an expired predecessor envelope. Before broad source "
        "reconstruction, inspect the current branch and Git status. If the isolated worktree already contains only "
        "declared-scope files for this exact slice, treat them as an untrusted checkpoint: review them, continue them, "
        "and cite fresh checks. If a clean replacement runtime has no checkpoint in its worktree, inspect local Git "
        "branches named for this exact slice for a predecessor's committed checkpoint; review the exact commit and "
        "declared-scope diff before recovering it onto a fresh current-main branch. Never select work from another "
        "slice, push an unreviewed checkpoint, or reuse a predecessor envelope. Do not discard or recreate valid "
        "declared-scope progress merely because the native session is fresh; never inherit the predecessor transcript "
        "or its unverified conclusions.\n\n"
        "REPOSITORY NETWORK AUTH (per command, exact): run every GitHub network Git command (fetch, ls-remote, "
        f"push) as `{GITHUB_GIT_COMMAND_PREFIX} <subcommand>`, passing these `-c` overrides on that one command "
        "only. Never run `git config`, never edit `.git/config` or `~/.gitconfig`, never print, log, echo, or "
        "store a token or credential, never pass `--no-verify` or otherwise bypass hooks, and never change the "
        "desk, model, or envelope. This route is not push authorization: a push is authorized only by the "
        "envelope's allowed actions and is proven only when the actual command succeeds. If the command fails, "
        "stop and return a typed blocked receipt naming the failure.\n\n"
        "WORKTREE HELPER AUTH: direct Git -c options do not reach a separately invoked shell script. "
        "Invoke the sanctioned helper through this exact per-command Git shell alias, which propagates "
        "those same options to its nested fetch without changing any config file: "
        f"`{GITHUB_WORKTREE_COMMAND_PREFIX} <assigned-helper-name> --from <verified-source-main>`. "
        "Run from the canonical repository. Do not invoke plain run.sh worktree in the child. An internal "
        "fetch warning is a refusal even when the helper returns zero; preserve any created tree.\n\n"
        "Complete the bounded slice below. Run the declared checks and preserve any unrelated dirty work. "
        "If the work cannot be completed within the envelope, return a typed failed or blocked receipt; do not "
        "invent success. Your final response must be a single JSON object and nothing else: an exact "
        "engineering-slice-receipt.v1 bound to this envelope and attempt. It must include every planned check, "
        "metadata-only/redacted evidence digests where required, source evidence, fresh-session reconstruction, "
        "and executor_claim.claimed_by exactly `codex`. Independent verification remains required.\n\n"
        "RECEIPT TEMPLATE (exact engineering-slice-receipt.v1 field set derived from this packet, task, and "
        "envelope; blocked-safe defaults):\n"
        f"{json.dumps(receipt_template, sort_keys=True, separators=(',', ':'))}\n"
        "Start from this template. Replace every null placeholder at exactly these paths with the value you "
        f"truthfully observed: {', '.join(RECEIPT_TEMPLATE_PLACEHOLDER_PATHS)}. "
        "source_evidence.worktree_ref and branch_ref are opaque identifiers, NEVER paths or raw branch "
        "names: for each actually observed path/name use worktree:sha256:<lowercase SHA256 of UTF-8 path> "
        "and branch:sha256:<lowercase SHA256 of UTF-8 branch name>. Run both Git observations inside "
        "the assigned worktree: `git rev-parse --show-toplevel` supplies the absolute root path with no "
        "trailing slash; `git rev-parse --abbrev-ref HEAD` supplies the short branch name with no "
        "refs/heads/ prefix. Remove only the command's terminating newline before hashing each UTF-8 "
        "value; do not hash the desk cwd, a subdirectory, or a fully qualified branch ref. "
        "These labels describe observations, "
        "not authority. If setup stopped before an observation, use worktree:unobserved, branch:unobserved, "
        "and source_sha=unobserved for the respective unknown fields; never claim existence or a commit "
        "you did not inspect. The identifier grammar is ^[A-Za-z][A-Za-z0-9._:-]{2,127}$. "
        "Before responding, run this exact read-only preflight command: "
        f"`{shlex.quote(str(REPO / '.venv' / 'bin' / 'python'))} "
        f"{shlex.quote(str(HERE / 'engineering_dispatch_adapter.py'))} --validate-receipt` "
        "with JSON stdin containing exactly receipt, plan (CONTROLLER TASK engineering_plan), and envelope "
        "(RECEIPT VALIDATION SOURCE ENVELOPE below). It validates without persisting. Fix only the current "
        "unsubmitted report from observed facts; never repair/import a prior attempt receipt. "
        "Keep every field name exactly as written, add no field, drop no field, and locally check your final "
        "object against the template's field names before answering. Move a check from not_run only to a "
        "state you actually reached, attach evidence digests only for evidence you actually produced, and "
        "never invent evidence, artifacts, deviations, or passed checks. Leave outcome blocked unless the "
        "accepted definition of done is truly met or the work truly failed.\n\n"
        f"RECEIPT VALIDATION SOURCE ENVELOPE (immutable, original digest authority):\n{json.dumps(source_envelope, sort_keys=True, separators=(',', ':'))}\n\n"
        f"SERVER-ISSUED SLICE PACKET (immutable):\n{json.dumps(packet, sort_keys=True, separators=(',', ':'))}\n\n"
        f"CONTROLLER TASK BINDING (immutable):\n{json.dumps(task, sort_keys=True, separators=(',', ':'))}"
    )


def _desk_spec() -> dict:
    try:
        value = json.loads(DESK_SPEC_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DispatchRefusal("tracked Engineering desk configuration is unavailable") from exc
    expected = {"schema_version", "name", "kind", "model", "effort", "cwd", "sandbox", "room_seat"}
    if not isinstance(value, dict) or set(value) != expected:
        raise DispatchRefusal("tracked Engineering desk configuration has an unsupported shape")
    if (value["schema_version"] != "engineering-codex-desk.v1" or value["name"] != ENGINEERING_DESK
            or value["kind"] != "codex-session" or value["cwd"] != "{{REPO}}"
            or value["room_seat"] is not None):
        raise DispatchRefusal("tracked Engineering desk configuration is not dedicated and unseated")
    for field in ("model", "effort", "sandbox"):
        if not isinstance(value[field], str) or not value[field].strip():
            raise DispatchRefusal("tracked Engineering desk configuration is incomplete")
    return value


def install_dedicated_codex_desk(registry: desks.Registry) -> dict:
    """Bootstrap only the tracked unseated desk, then return its exact readback."""
    spec = _desk_spec()
    entry = registry.register(spec["name"], spec["kind"], model=spec["model"], effort=spec["effort"],
                              cwd=str(REPO), sandbox=spec["sandbox"],
                              add_dirs=_dedicated_writable_roots())
    return _dedicated_codex_desk(registry)


def _dedicated_codex_desk(registry: desks.Registry) -> dict:
    """Resolve the one reviewed local adapter before it sees an envelope."""
    try:
        entry = registry.resolve(ENGINEERING_DESK)
    except desks.DeskError as exc:
        raise DispatchRefusal("dedicated Engineering Codex desk is unavailable") from exc
    # A corrupted local registry must fail before a Passport packet reaches a
    # different model, a Claude socket, or a desk with unspecified execution
    # characteristics.  The fixed desk itself is the local native surface.
    spec = _desk_spec()
    allowed_fields = {
        "name", "kind", "model", "effort", "cwd", "sandbox", "add_dirs", "room_seat", "thread_id", "registered_at",
        # These are bridge-owned liveness/auth observations, never execution
        # choices. They are allowed to change without widening the desk.
        "last_seen", "last_live", "last_auth", "last_auth_at",
    }
    if set(entry) - allowed_fields:
        raise DispatchRefusal("dedicated Engineering desk has an unapproved execution field")
    expected = {"kind": spec["kind"], "model": spec["model"], "effort": spec["effort"],
                "cwd": str(REPO), "sandbox": spec["sandbox"],
                "add_dirs": _dedicated_writable_roots()}
    if entry.get("name") != ENGINEERING_DESK or any(entry.get(key) != value for key, value in expected.items()) or entry.get("room_seat") is not None:
        raise DispatchRefusal("dedicated Engineering desk is not a modeled Codex session")
    if (entry.get("thread_id") is not None and not isinstance(entry["thread_id"], str)) or (
            "registered_at" in entry and not isinstance(entry["registered_at"], str)) or (
            "last_seen" in entry and not isinstance(entry["last_seen"], str)) or (
            "last_live" in entry and not isinstance(entry["last_live"], bool)) or (
            "last_auth" in entry and entry["last_auth"] is not None and not isinstance(entry["last_auth"], bool)) or (
            "last_auth_at" in entry and not isinstance(entry["last_auth_at"], str)):
        raise DispatchRefusal("dedicated Engineering desk metadata is invalid")
    return entry


def run(request: dict, *, dispatch_fn=dispatch.dispatch, registry: desks.Registry | None = None) -> dict:
    if request.get("desk") != ENGINEERING_DESK:
        raise DispatchRefusal("engineering controller attempted to select a different desk")
    _dedicated_codex_desk(registry or desks.Registry(DEDICATED_REGISTRY_PATH))
    task = request["task"]
    plan = task.get("engineering_plan")
    slice_row = task.get("engineering_slice")
    envelope = request["envelope"]
    if envelope.get("server_binding", {}).get("adapter", {}).get("surface") != "codex_desktop":
        raise DispatchRefusal("engineering envelope does not select the Codex desktop adapter")
    # Reject malformed authority before contract parsing, and repeat the same
    # check directly beside dispatch below as the final launch boundary.
    _require_dispatch_runway(envelope, task.get("claim_lease_expires_at"))
    if not isinstance(slice_row, dict) or not isinstance(task.get("slice_ref"), str):
        raise DispatchRefusal("engineering controller task has no exact slice")
    if (not isinstance(plan, dict) or not isinstance(plan.get("work_request"), dict)
            or task.get("work_request") != plan["work_request"].get("id")
            or task.get("work_request") != envelope.get("work_request_id")):
        raise DispatchRefusal("engineering controller task work request is not plan/envelope bound")
    packet = engineering_passport.build_engineering_slice_packet(envelope, plan, task["slice_ref"])
    if packet["slice_ref"] != slice_row.get("slice_ref") or task.get("plan_digest") != packet["plan_digest"]:
        raise DispatchRefusal("engineering controller task does not match its accepted packet")
    hydration = bind_operator_assignment(source_hydration_binding(task, plan, slice_row), task, envelope)
    receipt_template = build_engineering_slice_receipt_template(
        packet, task, envelope, slice_row, request["executor_slug"])
    _require_dispatch_runway(envelope, task.get("claim_lease_expires_at"))
    # The database lease deadline is controller authority, not model input; it
    # must be checked at launch but excluded from the packet/task digest.  The
    # canonical Work Request ref rides in the hydration binding, keeping the
    # task binding exactly the shape the rule-pack drift gate verifies.
    prompt_task = {key: value for key, value in task.items() if key not in PROMPT_TASK_EXCLUDED_KEYS}
    row = dispatch_fn(
        request["desk"],
        _prompt(packet, prompt_task, engineering_source_loader_js(hydration), receipt_template,
                hydration["source_merge_required"], envelope),
        env=_safe_child_env(), fresh=True,
        config_overrides=AUTHORIZED_CODEX_CONFIG_OVERRIDES,
    )
    if not isinstance(row, dict) or row.get("status") != "completed":
        status = row.get("status") if isinstance(row, dict) else "invalid"
        raise DispatchRefusal(f"engineering desk dispatch did not complete: {status}")
    try:
        receipt = json.loads(str(row.get("result") or "").strip())
    except json.JSONDecodeError as exc:
        raise DispatchRefusal("engineering desk did not return one JSON receipt") from exc
    engineering_passport.validate_engineering_slice_receipt(receipt, plan, envelope)
    require_assignment_return(receipt, hydration)
    attribution = receipt.get("attribution")
    identity = envelope.get("server_binding", {}).get("identity", {})
    adapter_binding = envelope.get("server_binding", {}).get("adapter", {})
    expected_attribution = {
        "actor_ref": identity.get("agent_principal_id"),
        "session_ref": envelope.get("agent_session", {}).get("id"),
        "adapter_ref": adapter_binding.get("adapter_id"),
    }
    # Receipt attribution is evidence of the envelope the executor saw, not a
    # second opportunity for a model or local caller to name a principal or
    # native session.  The full envelope validator above establishes shape;
    # this equality establishes the controller boundary.
    if attribution != expected_attribution:
        raise DispatchRefusal("engineering receipt attribution does not match the server binding")
    claim = receipt.get("executor_claim")
    if not isinstance(claim, dict) or claim.get("claimed_by") != request["executor_slug"]:
        raise DispatchRefusal("engineering receipt executor does not match the server binding")
    return {"ok": True, "receipt": receipt,
            "dispatch": {"status": "completed", "thread_id": row.get("thread_id")}}


def main() -> int:
    try:
        if sys.argv[1:] == ["--validate-receipt"]:
            raw = sys.stdin.read(1_000_001)
            if len(raw) > 1_000_000:
                raise DispatchRefusal("receipt preflight input is too large")
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise DispatchRefusal("receipt preflight input is not JSON") from exc
            try:
                result = validate_receipt_document(value)
            except (DispatchRefusal, engineering_passport.EngineeringContractError) as exc:
                # This read-only mode receives no credentials. Diagnostics may
                # include field names or slice refs from the caller's own input.
                print(json.dumps({"ok": False, "error": type(exc).__name__,
                                  "detail": str(exc)}, separators=(",", ":")), file=sys.stderr)
                return 1
            print(json.dumps(result, separators=(",", ":")))
            return 0
        if sys.argv[1:] == ["--preflight"]:
            entry = _dedicated_codex_desk(desks.Registry(DEDICATED_REGISTRY_PATH))
            print(json.dumps({"ok": True, "desk": {key: entry[key] for key in
                  ("name", "kind", "model", "effort", "cwd", "sandbox", "add_dirs")}}, separators=(",", ":")))
            return 0
        if sys.argv[1:] == ["--install-desk"]:
            entry = install_dedicated_codex_desk(desks.Registry(DEDICATED_REGISTRY_PATH))
            print(json.dumps({"ok": True, "desk": {key: entry[key] for key in
                  ("name", "kind", "model", "effort", "cwd", "sandbox", "add_dirs")}}, separators=(",", ":")))
            return 0
        if len(sys.argv) != 1:
            raise DispatchRefusal("engineering adapter received unsupported arguments")
        result = run(_read_request())
    except (DispatchRefusal, engineering_passport.EngineeringContractError, dispatch.DeskError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}, separators=(",", ":")), file=sys.stderr)
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
