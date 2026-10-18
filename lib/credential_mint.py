"""The system mints and rotates its own credentials from one root grant per provider.

Joe's ruling 2026-10-12 (decision cbe91287-10a2-48d0-bda7-3b1784b4bb93): "I
don't even want credential minting ... to require me". His recurring chore was
minting a token in a dashboard, pasting it, and doing it again before it
lapsed. This module turns that into a one-time act per provider: Joe places a
ROOT GRANT (ops/config/credential-root-grants.v1.json says which, at what
scope, with which one command), and every night ops/credential-mint.py asks
this module to keep each CHILD credential fresh.

THE INTERFACE is one call, Minter(...).run(), plus load_plan() for anyone who
needs to know which credential comes from which grant. Everything a provider
does differently sits behind three adapters (Cloudflare, Neon, OpenAI) with
the same four methods, and the only side doors are the injected `http`,
`clock`, `run` (subprocess) and `call_verb`, which is how the selftest drives
every path with no network, no CLI and no record layer.

ONE ROTATION, in this order, each step leaving a receipt:

  1. due?      never minted by this lane, or within rotate_within_days of the
               child's expiry. Not due means no live call at all.
  2. mint      a child from the root grant, labelled carr-<credential>-<date>.
  3. verify    a READ-ONLY call made with the NEW child, never the root. A
               child that fails it is revoked and the previous child stays.
  4. store     through lib/token_store.py, the same map and writer
               tools/store-token.py uses. A failed write revokes the child.
               Cloudflare candidates stay in a separate private file until
               fleet installation verifies the live write or restores it.
  5. install   where a tool keeps its own copy (Codex: `codex login
               --with-api-key`, value on stdin). It runs AFTER the store on
               purpose: a failed install leaves the new child stored and the
               previous one untouched and still in use by the tool, and the next
               night retries the install from the store. Installing first would
               leave a tool holding a child the failure path had to revoke.
  6. revoke    the previous child, by id, with the root. A revoke that fails
               is queued and retried every night until it succeeds.

A credential that was placed by hand before its root grant existed has no id
this lane knows, so its first rotation leaves the hand-made one in place and
says so (`unmanaged_previous_left_in_place`).

STATE in out/credential-mint-state.json: per credential the current child's id,
mint date and expiry, plus the queue of children still to revoke, plus a
`minting` record written BEFORE each provider mint (label, then the child's id
and the sha256 fingerprint of its value) so a night that dies between the mint
and the store is finished or cleaned up by the next one. RECEIPTS in
out/credential-mint-receipts.jsonl: {at, credential, provider, step, outcome},
nothing else, ever. Neither file holds a credential value; ids are provider
handles, not secrets.

REACH. Every grant states `reach`: what its root can really do, which for a
Cloudflare token-minting token or a Neon/OpenAI org key is far more than its
one-line scope suggests. The loop that asks Joe for the grant carries it word
for word, and the inventory row that verifies and ages the root (`root_grant`)
must repeat it exactly, or load_plan() refuses.

CODEX BILLING. Installing a minted OpenAI key moves Codex to metered API
billing, so no Codex child is minted until codex_api_billing_approved is true
in the grant file (Joe's explicit yes, by a PR he approves).

A MISSING ROOT GRANT is never a live call. It files (or updates) exactly one
open loop per provider naming the one-time grant, deduplicated by searching
the loop board for the marker [credential-root-grant:<name>] rather than by a
local state file, so a second machine or a wiped out/ cannot double-file it.
A root grant the provider REFUSES (401/403 on a mint) files the same loop with
the reason changed. The first rotation after the grant is placed closes that
loop with its outcome, so placing the grant is the whole of Joe's act.

MACHINES. A child is minted only on a machine its inventory row names, so the
Studio never rotates the MacBook's Codex login out from under it. The fleet's
two names come from lib/machine_role.py: the primary is the Studio, the
secondary the MacBook.

No value is ever printed, logged, written to a receipt, or placed on argv.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from lib import cloudflare_tokens, token_store
from lib.credential_file import carr_config
from lib.github_app_config import CONFIG_PATH as GITHUB_APP_CONFIG, read_config
from lib.machine_role import is_primary
from lib.record_call import VerbResult, call_verb as record_call_verb

REPO = Path(__file__).resolve().parents[1]
GRANTS_PATH = REPO / "ops" / "config" / "credential-root-grants.v1.json"
INVENTORY_PATH = REPO / "ops" / "config" / "credential-inventory.v1.json"
STATE_PATH = REPO / "out" / "credential-mint-state.json"
RECEIPTS_PATH = REPO / "out" / "credential-mint-receipts.jsonl"

RECEIPT_KEYS = ("at", "credential", "provider", "step", "outcome")
AUTH_REFUSED = frozenset({"http_401", "http_403"})

# http(method, url, headers, json_body) -> (status or None when unreachable,
# parsed JSON body or None). The body is only ever read for the named fields an
# adapter needs; nothing logs it.
Http = Callable[[str, str, Mapping[str, str], Any], tuple[int | None, Any]]
Clock = Callable[[], datetime]
Runner = Callable[..., "subprocess.CompletedProcess[Any]"]
VerbCaller = Callable[[str, Mapping[str, Any]], VerbResult]


class PlanError(ValueError):
    """The root-grant file and the inventory disagree; nothing may be minted."""


# ── the plan: which credential comes from which grant ─────────────────────────

@dataclass(frozen=True)
class ChildPlan:
    credential: str
    machines: tuple[str, ...]
    store_name: str | None = None
    install: str | None = None


@dataclass(frozen=True)
class GrantPlan:
    name: str
    provider: str
    store_name: str | None
    scope: str
    reach: str
    one_time_command: str | None
    ttl_days: int | None
    params: Mapping[str, Any]
    children: tuple[ChildPlan, ...]
    self_minting: bool = False
    deferred: str | None = None


@dataclass(frozen=True)
class Plan:
    rotate_within_days: int
    grants: tuple[GrantPlan, ...]


def load_plan(grants_path: Path = GRANTS_PATH, inventory_path: Path = INVENTORY_PATH) -> Plan:
    """Read both files and refuse any disagreement between them."""
    try:
        grants_doc = json.loads(Path(grants_path).read_text(encoding="utf-8"))
        inventory = json.loads(Path(inventory_path).read_text(encoding="utf-8"))["credentials"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PlanError(f"cannot read the credential plan ({type(exc).__name__})") from None
    rows = {row["name"]: row for row in inventory}
    grant_docs = {g["name"]: g for g in grants_doc["grants"]}

    for name, row in rows.items():
        source, holder, root = (row.get("self_mintable_from"), row.get("holder_only"),
                                row.get("root_grant"))
        if sum(bool(x) for x in (source, holder, root)) != 1:
            raise PlanError(f"{name}: needs exactly one of self_mintable_from, holder_only or root_grant")
        if source and source not in grant_docs:
            raise PlanError(f"{name}: self_mintable_from names unknown grant {source!r}")
        if root:
            # The row that health-probes and ages a root grant: it must point at
            # that grant's stored name and repeat its reach word for word, so
            # the inventory never understates what the root can do.
            g = grant_docs.get(root)
            if g is None:
                raise PlanError(f"{name}: root_grant names unknown grant {root!r}")
            if (row.get("probe") or {}).get("store_name") != g.get("store_name"):
                raise PlanError(f"{name}: probes a different name than root {g.get('store_name')}")
            if row.get("reach") != g.get("reach"):
                raise PlanError(f"{name}: reach differs from grant {root}'s reach")

    grants: list[GrantPlan] = []
    for gname, g in grant_docs.items():
        children: list[ChildPlan] = []
        for c in g.get("children", []):
            row = rows.get(c["credential"])
            if row is None or row.get("self_mintable_from") != gname:
                raise PlanError(f"grant {gname}: child {c['credential']} is not an inventory "
                                f"row with self_mintable_from {gname!r}")
            store_name, install = c.get("store_name"), c.get("install")
            if store_name is not None and store_name not in token_store.DESTINATIONS:
                raise PlanError(f"grant {gname}: {store_name} has no token_store destination")
            if install is not None and install not in INSTALLERS:
                raise PlanError(f"grant {gname}: no installer named {install!r}")
            children.append(ChildPlan(c["credential"], tuple(row.get("machines", [])),
                                      store_name, install))
        self_minting = bool(g.get("self_minting"))
        if not str(g.get("reach") or "").strip():
            raise PlanError(f"grant {gname}: no reach (what the grant can really do) is stated")
        if children and not self_minting:
            if gname not in PROVIDERS:
                raise PlanError(f"grant {gname}: no mint adapter")
            if sum(1 for row in rows.values() if row.get("root_grant") == gname) != 1:
                raise PlanError(f"grant {gname}: needs exactly one inventory row with root_grant "
                                f"{gname!r}, so the root itself is verified and aged")
            _check_params(gname, g.get("params") or {})
            if g.get("store_name") not in token_store.DESTINATIONS:
                raise PlanError(f"grant {gname}: root {g.get('store_name')} has no token_store destination")
            if not isinstance(g.get("ttl_days"), int) or any(c.store_name is None for c in children):
                raise PlanError(f"grant {gname}: a minted child needs ttl_days and a store_name")
        grants.append(GrantPlan(gname, g["provider"], g.get("store_name"), g["scope"], g["reach"],
                                g.get("one_time_command"), g.get("ttl_days"),
                                g.get("params") or {}, tuple(children), self_minting,
                                g.get("deferred")))
    for name, row in rows.items():
        source = row.get("self_mintable_from")
        if source and not any(c.credential == name for g in grants if g.name == source
                              for c in g.children):
            raise PlanError(f"{name}: grant {source} does not list it as a child")
    return Plan(int(grants_doc["rotate_within_days"]), tuple(grants))


def _check_params(gname: str, params: Mapping[str, Any]) -> None:
    """Per-provider settings that decide how much a child can do; refused, not
    defaulted, when absent, so no child is minted wider than the file says."""
    if gname == "neon":
        raw = params.get("child_scope")
        scope: Mapping[str, Any] = raw if isinstance(raw, dict) else {}
        kind = scope.get("kind")
        if kind == "project":
            if not str(scope.get("project_id") or "").strip():
                raise PlanError("grant neon: a project-scoped child needs child_scope.project_id")
        elif kind == "organization":
            if not str(scope.get("because") or "").strip():
                raise PlanError("grant neon: an org-wide child needs child_scope.because, "
                                "the recorded reason a project-scoped key cannot serve")
        else:
            raise PlanError("grant neon: child_scope.kind must be 'project' or 'organization'")
    if gname == "openai" and not isinstance(params.get("codex_api_billing_approved"), bool):
        raise PlanError("grant openai: codex_api_billing_approved must be true or false")


def awaiting_billing_yes(grant: GrantPlan, child: ChildPlan) -> bool:
    """A Codex child moves Codex to metered API billing; it waits on Joe's yes."""
    return child.install == "codex" and grant.params.get("codex_api_billing_approved") is not True


# ── provider adapters ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Child:
    child_id: str
    value: str = field(repr=False)


class Provider(Protocol):
    def verify_root(self, token: str) -> bool: ...
    def verify_child(self, token: str) -> bool: ...
    def mint(self, root: str, label: str, expires_at: datetime) -> tuple[Child | None, str]: ...
    def revoke(self, root: str, child_id: str) -> bool: ...


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _status(status: int | None) -> str:
    return "unreachable" if status is None else f"http_{status}"


def _field(body: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(body, dict):
            return None
        body = body.get(key)
    return body


class Cloudflare:
    API = "https://api.cloudflare.com/client/v4"

    def __init__(self, http: Http, params: Mapping[str, Any]):
        self.http, self.params = http, params

    def verify_root(self, token: str) -> bool:
        status, body = self.http("GET", f"{self.API}/user/tokens/verify", _bearer(token), None)
        return status == 200 and _field(body, "result", "status") == "active"

    verify_child = verify_root

    def mint(self, root: str, label: str, expires_at: datetime) -> tuple[Child | None, str]:
        status, body = self.http("GET", f"{self.API}/user/tokens/permission_groups", _bearer(root), None)
        if status != 200:
            return None, _status(status)
        ids = {g.get("name"): g.get("id") for g in (_field(body, "result") or []) if isinstance(g, dict)}
        wanted = list(self.params["permission_groups"])
        if not all(ids.get(name) for name in wanted):
            return None, "permission_group_unresolved"
        payload = {
            "name": label,
            "expires_on": expires_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "policies": [{
                "effect": "allow",
                "resources": {f"com.cloudflare.api.account.{self.params['account_id']}": "*"},
                "permission_groups": [{"id": ids[name]} for name in wanted],
            }],
        }
        status, body = self.http("POST", f"{self.API}/user/tokens", _bearer(root), payload)
        child_id, value = _field(body, "result", "id"), _field(body, "result", "value")
        if status != 200 or not child_id or not value:
            return None, _status(status) if status != 200 else "malformed_reply"
        return Child(str(child_id), str(value)), "ok"

    def revoke(self, root: str, child_id: str) -> bool:
        status, _ = self.http("DELETE", f"{self.API}/user/tokens/{child_id}", _bearer(root), None)
        return status in (200, 404)


class Neon:
    API = "https://console.neon.tech/api/v2"

    def __init__(self, http: Http, params: Mapping[str, Any]):
        self.http, self.org = http, str(params["org_id"])
        scope = params.get("child_scope") or {}
        # Project-scoped org keys have member access to one project and cannot
        # delete it; org-wide ones are admin. load_plan makes the choice explicit.
        self.project_id = str(scope["project_id"]) if scope.get("kind") == "project" else None

    def verify_root(self, token: str) -> bool:
        status, _ = self.http("GET", f"{self.API}/projects?org_id={self.org}&limit=1", _bearer(token), None)
        return status is not None and 200 <= status < 300

    verify_child = verify_root

    def mint(self, root: str, label: str, expires_at: datetime) -> tuple[Child | None, str]:
        payload: dict[str, str] = {"key_name": label}
        if self.project_id is not None:
            payload["project_id"] = self.project_id
        status, body = self.http("POST", f"{self.API}/organizations/{self.org}/api_keys",
                                 _bearer(root), payload)
        child_id, value = _field(body, "id"), _field(body, "key")
        if status is None or not 200 <= status < 300:
            return None, _status(status)
        if child_id is None or not value:
            return None, "malformed_reply"
        return Child(str(child_id), str(value)), "ok"

    def revoke(self, root: str, child_id: str) -> bool:
        status, _ = self.http("DELETE", f"{self.API}/organizations/{self.org}/api_keys/{child_id}",
                              _bearer(root), None)
        return status is not None and (200 <= status < 300 or status == 404)


class OpenAI:
    API = "https://api.openai.com/v1"

    def __init__(self, http: Http, params: Mapping[str, Any]):
        self.http, self.project_name = http, str(params["project_name"])

    def verify_root(self, token: str) -> bool:
        status, _ = self.http("GET", f"{self.API}/organization/projects?limit=1", _bearer(token), None)
        return status == 200

    def verify_child(self, token: str) -> bool:
        status, _ = self.http("GET", f"{self.API}/models", _bearer(token), None)
        return status == 200

    def _project_id(self, root: str) -> tuple[str | None, str]:
        status, body = self.http("GET", f"{self.API}/organization/projects?limit=100", _bearer(root), None)
        if status != 200:
            return None, _status(status)
        for project in _field(body, "data") or []:
            if (isinstance(project, dict) and project.get("name") == self.project_name
                    and project.get("status", "active") == "active" and project.get("id")):
                return str(project["id"]), "ok"
        status, body = self.http("POST", f"{self.API}/organization/projects", _bearer(root),
                                 {"name": self.project_name})
        project_id = _field(body, "id")
        if status != 200 or not project_id:
            return None, _status(status) if status != 200 else "malformed_reply"
        return str(project_id), "ok"

    def mint(self, root: str, label: str, expires_at: datetime) -> tuple[Child | None, str]:
        project_id, detail = self._project_id(root)
        if project_id is None:
            return None, detail
        status, body = self.http("POST", f"{self.API}/organization/projects/{project_id}/service_accounts",
                                 _bearer(root), {"name": label})
        account_id, value = _field(body, "id"), _field(body, "api_key", "value")
        if status != 200 or not account_id or not value:
            return None, _status(status) if status != 200 else "malformed_reply"
        return Child(f"{project_id}/{account_id}", str(value)), "ok"

    def revoke(self, root: str, child_id: str) -> bool:
        project_id, _, account_id = child_id.partition("/")
        status, _ = self.http(
            "DELETE", f"{self.API}/organization/projects/{project_id}/service_accounts/{account_id}",
            _bearer(root), None)
        return status in (200, 404)


PROVIDERS: dict[str, Callable[[Http, Mapping[str, Any]], Provider]] = {
    "cloudflare": Cloudflare,
    "neon": Neon,
    "openai": OpenAI,
}


def install_codex(value: str, run: Runner) -> bool:
    """Codex keeps its own copy in ~/.codex/auth.json; its CLI writes it. The
    value goes on stdin, its output is captured and never read."""
    try:
        proc = run(["codex", "login", "--with-api-key"], input=value, text=True,
                   capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


INSTALLERS: dict[str, Callable[[str, Runner], bool]] = {"codex": install_codex}


def verify_root_grant(grant_name: str, token: str, http: Http | None = None) -> tuple[bool, str]:
    """The read-only check tools/store-token.py runs before saving a root grant."""
    grant = next(g for g in load_plan().grants if g.name == grant_name)
    ok = PROVIDERS[grant_name](http or urllib_http, grant.params).verify_root(token)
    return (True, "verified active") if ok else (False, f"{grant.provider} refused the root grant")


# ── default I/O ───────────────────────────────────────────────────────────────

_BODY_CAP = 1_048_576


def urllib_http(method: str, url: str, headers: Mapping[str, str], body: Any) -> tuple[int | None, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    hdrs = {"User-Agent": "carr-credential-mint/1", **headers}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status, raw = resp.status, resp.read(_BODY_CAP)
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None, None
    try:
        return status, json.loads(raw.decode("utf-8", "replace")) if raw.strip() else None
    except ValueError:
        return status, None


def this_machine() -> str:
    return "studio" if is_primary(str(REPO)) else "macbook"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _verb(verb: str, args: Mapping[str, Any]) -> VerbResult:
    return record_call_verb(verb, args, timeout=60)


# ── the lane ──────────────────────────────────────────────────────────────────

def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _child_destination(store_name: str) -> token_store.Destination:
    if store_name in cloudflare_tokens.TOKEN_NAMES:
        return token_store.Destination("credential-mint-candidates.env", store_name)
    return token_store.destination(store_name)


def _read_child(store_name: str) -> str | None:
    dest = _child_destination(store_name)
    try:
        return token_store.read_env_file(dest.path).get(dest.key) or None
    except OSError:
        return None


@dataclass
class Minter:
    plan: Plan
    machine: str
    http: Http = urllib_http
    clock: Clock = _utc_now
    run: Runner = subprocess.run
    call_verb: VerbCaller = _verb
    state_path: Path = STATE_PATH
    receipts_path: Path = RECEIPTS_PATH
    receipts: list[dict[str, str]] = field(default_factory=list)

    # receipts and state ---------------------------------------------------
    def _receipt(self, credential: str, provider: str, step: str, outcome: str) -> None:
        row = dict(zip(RECEIPT_KEYS, (_iso(self.clock()), credential, provider, step, outcome)))
        self.receipts.append(row)
        self.receipts_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.receipts_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")

    def _load_state(self) -> dict[str, Any]:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        state.setdefault("credentials", {})
        state.setdefault("pending_revoke", [])
        state.setdefault("minting", {})
        return state

    def _save_state(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"schema": "credential-mint-state/v1", **state}, fh, indent=2, sort_keys=True)
        os.replace(tmp, self.state_path)

    # the night ----------------------------------------------------------------
    def run_once(self) -> list[dict[str, str]]:
        state = self._load_state()
        for grant in self.plan.grants:
            here = [c for c in grant.children if self.machine in c.machines]
            if grant.deferred or not here:
                continue
            gated = [c for c in here if awaiting_billing_yes(grant, c)]
            here = [c for c in here if c not in gated]
            if gated and not here:
                # No live call: nothing may be minted until Joe says yes.
                for c in gated:
                    self._receipt(c.credential, grant.name, "mint", "awaiting_joe_billing_yes")
                self._ensure_grant_loop(grant, "billing")
                continue
            if grant.self_minting:
                if not _github_app_key_present():
                    self._receipt(f"root-grant:{grant.name}", grant.name, "root_grant", "absent")
                    self._ensure_grant_loop(grant, "missing")
                continue
            root = token_store.read(str(grant.store_name))
            if root is None:
                # NEVER a live call without the grant.
                self._receipt(f"root-grant:{grant.name}", grant.name, "root_grant", "absent")
                self._ensure_grant_loop(grant, "missing")
                continue
            adapter = PROVIDERS[grant.name](self.http, grant.params)
            self._recover_interrupted_mints(grant, adapter, root, here, state)
            self._retry_revocations(grant, adapter, root, state)
            results = []
            for child in here:
                results.append(self._rotate(grant, adapter, root, child, state))
                if results[-1] in AUTH_REFUSED:
                    self._ensure_grant_loop(grant, "refused")
                    break
            else:
                if "rotated" in results:
                    self._close_grant_loop(grant)
            self._save_state(state)
        self._save_state(state)
        return self.receipts

    def _due(self, entry: Mapping[str, Any] | None) -> bool:
        if not entry:
            return True
        expires = datetime.fromisoformat(entry["expires_at"])
        return self.clock() >= expires - timedelta(days=self.plan.rotate_within_days)

    def _rotate(self, grant: GrantPlan, adapter: Provider, root: str, child: ChildPlan,
                state: dict[str, Any]) -> str:
        name, store_name = child.credential, str(child.store_name)
        entry = state["credentials"].get(name)
        if entry and (entry.get("install_pending") or entry.get("previous_child_id")):
            # An unfinished rotation (a failed install, or one recovered after a
            # crash) finishes before anything new is minted.
            return self._finish_install(grant, adapter, root, child, entry, state, retried=True)
        if not self._due(entry):
            return "not_due"

        now = self.clock()
        label = f"carr-{name}-{now:%Y%m%d}"
        # Written BEFORE the provider is asked, so a crash anywhere between the
        # mint and the store leaves a record the next night acts on, never a
        # live child nobody knows about (see _recover_interrupted_mints).
        state["minting"][name] = {"grant": grant.name, "label": label, "since": _iso(now)}
        self._save_state(state)
        new, detail = adapter.mint(root, label, now + timedelta(days=int(grant.ttl_days or 0)))
        if new is None:
            state["minting"].pop(name, None)
            self._receipt(name, grant.name, "mint", f"failed_{detail}")
            return detail
        state["minting"][name].update(child_id=new.child_id, fingerprint=_fingerprint(new.value))
        self._save_state(state)
        self._receipt(name, grant.name, "mint", "minted")

        if not adapter.verify_child(new.value):
            state["minting"].pop(name, None)
            self._receipt(name, grant.name, "verify", "failed_new_child_revoked_previous_kept")
            self._revoke_or_queue(grant, adapter, root, name, new.child_id, state, "revoke_new")
            return "verify_failed"
        self._receipt(name, grant.name, "verify", "verified")

        try:
            dest = _child_destination(store_name)
            token_store.write_env_line(dest.path, dest.key, new.value)
        except OSError:
            state["minting"].pop(name, None)
            self._receipt(name, grant.name, "store", "failed_new_child_revoked_previous_kept")
            self._revoke_or_queue(grant, adapter, root, name, new.child_id, state, "revoke_new")
            return "store_failed"
        self._receipt(name, grant.name, "store", f"stored_{store_name}")
        self._adopt(grant, child, entry, new.child_id, now, state)
        self._save_state(state)  # the new child is real now; never lose its id
        return self._finish_install(grant, adapter, root, child, state["credentials"][name], state,
                                    retried=False)

    def _adopt(self, grant: GrantPlan, child: ChildPlan, entry: Mapping[str, Any] | None,
               child_id: str, minted: datetime, state: dict[str, Any]) -> None:
        """Track the stored candidate before installation; end the in-flight record."""
        state["credentials"][child.credential] = {
            "grant": grant.name,
            "child_id": child_id,
            "minted_at": _iso(minted),
            "expires_at": _iso(minted + timedelta(days=int(grant.ttl_days or 0))),
            "install_pending": child.install is not None or child.store_name in cloudflare_tokens.TOKEN_NAMES,
            "previous_child_id": (entry or {}).get("child_id"),
        }
        state["minting"].pop(child.credential, None)

    def _recover_interrupted_mints(self, grant: GrantPlan, adapter: Provider, root: str,
                                   here: list[ChildPlan], state: dict[str, Any]) -> None:
        """A `minting` record still present means a night died mid-rotation.
        Its child id and value fingerprint (sha256, never the value) say what
        happened: stored already -> adopt it, so the rotation finishes (install,
        revoke the previous); minted but not stored -> revoke it, it is an orphan;
        no id recorded -> the provider may hold a live child only its label
        identifies, which the receipt names."""
        for child in here:
            pending = state["minting"].get(child.credential)
            if not pending or pending.get("grant") != grant.name:
                continue
            name = child.credential
            child_id = pending.get("child_id")
            if not child_id:
                state["minting"].pop(name, None)
                self._receipt(name, grant.name, "recover_mint",
                              f"no_id_recorded_check_provider_for_label_{pending.get('label')}")
                continue
            stored = _read_child(str(child.store_name))
            if stored is not None and _fingerprint(stored) == pending.get("fingerprint"):
                minted = datetime.fromisoformat(pending["since"])
                self._adopt(grant, child, state["credentials"].get(name), str(child_id), minted, state)
                self._receipt(name, grant.name, "recover_mint", "stored_child_adopted")
            else:
                state["minting"].pop(name, None)
                self._receipt(name, grant.name, "recover_mint", "unstored_child_revoking")
                self._revoke_or_queue(grant, adapter, root, name, str(child_id), state, "revoke_new")
        self._save_state(state)

    def _finish_install(self, grant: GrantPlan, adapter: Provider, root: str, child: ChildPlan,
                        entry: dict[str, Any], state: dict[str, Any], *, retried: bool) -> str:
        name = child.credential
        if child.store_name in cloudflare_tokens.TOKEN_NAMES:
            value = _read_child(str(child.store_name))
            states = cloudflare_tokens.sync(str(child.store_name), value, runner=self.run) if value else {}
            if not states or any(states.get(machine) != "ok" for machine in cloudflare_tokens.MACHINES):
                entry["install_pending"] = True
                self._receipt(name, grant.name, "install", "fleet_unconfirmed_previous_kept_retry_next_night")
                return "install_failed"
            entry["install_pending"] = False
            self._receipt(name, grant.name, "install", "fleet_verified" + ("_on_retry" if retried else ""))
        if child.install is not None:
            value = token_store.read(str(child.store_name))
            if value is None or not INSTALLERS[child.install](value, self.run):
                self._receipt(name, grant.name, "install",
                              "failed_previous_kept_retry_next_night")
                return "install_failed"
            entry["install_pending"] = False
            self._receipt(name, grant.name, "install",
                          f"installed_{child.install}" + ("_on_retry" if retried else ""))
        previous = entry.pop("previous_child_id", None)
        if previous:
            self._revoke_or_queue(grant, adapter, root, name, previous, state, "revoke_previous")
        else:
            self._receipt(name, grant.name, "revoke_previous", "unmanaged_previous_left_in_place")
        return "rotated"

    def _revoke_or_queue(self, grant: GrantPlan, adapter: Provider, root: str, credential: str,
                         child_id: str, state: dict[str, Any], step: str) -> None:
        if adapter.revoke(root, child_id):
            self._receipt(credential, grant.name, step, "revoked")
            return
        state["pending_revoke"].append({"grant": grant.name, "credential": credential,
                                        "child_id": child_id, "since": _iso(self.clock())})
        self._receipt(credential, grant.name, step, "failed_queued_retry_next_night")

    def _retry_revocations(self, grant: GrantPlan, adapter: Provider, root: str,
                           state: dict[str, Any]) -> None:
        keep = []
        for item in state["pending_revoke"]:
            if item["grant"] != grant.name:
                keep.append(item)
            elif adapter.revoke(root, item["child_id"]):
                self._receipt(item["credential"], grant.name, "retry_revoke", "revoked")
            else:
                keep.append(item)
                self._receipt(item["credential"], grant.name, "retry_revoke", "still_pending")
        state["pending_revoke"] = keep

    # the one loop per provider ---------------------------------------------
    def _ensure_grant_loop(self, grant: GrantPlan, reason: str) -> None:
        marker = f"[credential-root-grant:{grant.name}]"
        waiting = ", ".join(c.credential for c in grant.children)
        held_as = grant.store_name or "the key file"
        if reason == "missing":
            headline = f"{marker} Place the {grant.provider} root grant once so CARR mints its own credentials."
            state_line = f"{held_as} is not stored on this machine."
        elif reason == "billing":
            headline = (f"{marker} Say yes to metered Codex billing before CARR mints {grant.provider} keys.")
            state_line = ("Installing a minted key with `codex login --with-api-key` moves Codex from "
                          "ChatGPT-subscription auth to metered API billing. Nothing is minted until a PR "
                          "Joe approves sets codex_api_billing_approved true in "
                          "ops/config/credential-root-grants.v1.json; place the root grant too if it is "
                          "not placed yet.")
        else:
            headline = f"{marker} The {grant.provider} root grant was refused; replace it once."
            state_line = f"{held_as} is stored but the provider refused it (HTTP 401/403) on a mint."
        if reason == "billing":
            detail = (f"{grant.provider} children await Joe's yes to metered Codex billing "
                      f"(codex_api_billing_approved); waiting on it: {waiting}")
        else:
            detail = (f"{grant.provider} root grant {reason}: Joe runs once `{grant.one_time_command}`; "
                      f"waiting on it: {waiting}")
        body = "\n".join([
            headline,
            f"Reach: {grant.reach}",
            f"Scope: {grant.scope}",
            f"Run once: {grant.one_time_command}",
            state_line,
            f"Waiting on it: {waiting}.",
            "Once it is placed, the nightly 'credential mint' step mints, verifies, stores and "
            "rotates these with no further action (Joe's ruling 2026-10-12, decision cbe91287).",
        ])
        tag = f"root-grant:{grant.name}"
        mine = self._grant_loops(grant)
        if mine is None:
            self._receipt(tag, grant.name, "loop", "board_unreadable_not_filed")
            return
        if mine and mine[0].get("blocker_detail") == detail:
            self._receipt(tag, grant.name, "loop", "already_open")
            return
        if mine:
            loop = mine[0]
            args: dict[str, Any] = {"number": str(loop["number"]), "kind": "open_loop",
                                    "base_version": loop.get("version"), "body": body,
                                    "blocker_detail": detail}
            verb = "update-loop"
        else:
            args = {"kind": "open_loop", "owner": "Joe", "domain": "system",
                    "blocker": "human_only", "blocker_detail": detail, "body": body}
            verb = "add-loop"
        args["idempotency_key"] = str(uuid.uuid5(uuid.NAMESPACE_URL,
                                                 f"carr:{verb}:{marker}:{detail}:{args.get('base_version')}"))
        result = self.call_verb(verb, args)
        self._receipt(tag, grant.name, "loop",
                      ("updated" if mine else "filed") if result.ok else f"{verb}_{result.kind}")

    def _grant_loops(self, grant: GrantPlan) -> list[dict[str, Any]] | None:
        """This grant's open loops on the board, or None when the board cannot
        be read (and then nothing is filed or closed rather than guessed)."""
        marker = f"[credential-root-grant:{grant.name}]"
        board = self.call_verb("loop-board", {"kind": "open_loop", "domain": "system",
                                              "search": marker, "limit": 20})
        loops = board.reply.get("loops") if board.ok and isinstance(board.reply, dict) else None
        if not isinstance(loops, list):
            return None
        return [loop for loop in loops
                if isinstance(loop, dict) and marker in str(loop.get("label") or "")]

    def _close_grant_loop(self, grant: GrantPlan) -> None:
        """The grant just minted a child, so the loop that asked for it is done.
        Asked only after a real rotation, so it is never a nightly board read."""
        tag = f"root-grant:{grant.name}"
        for loop in self._grant_loops(grant) or []:
            outcome = (f"The {grant.provider} root grant is placed: the nightly credential mint "
                       f"minted, verified and stored a child from it on {self.clock():%Y-%m-%d}.")
            key = f"carr:close-loop:{tag}:{loop['number']}:{loop.get('version')}"
            result = self.call_verb("close-loop", {
                "number": str(loop["number"]), "kind": "open_loop",
                "base_version": loop.get("version"), "outcome": outcome, "resolution": "done",
                "idempotency_key": str(uuid.uuid5(uuid.NAMESPACE_URL, key))})
            self._receipt(tag, grant.name, "loop", "closed" if result.ok else f"close-loop_{result.kind}")


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _github_app_key_present() -> bool:
    """Presence only; the key is never opened here."""
    try:
        return carr_config(read_config(GITHUB_APP_CONFIG)["key_file"]).is_file()
    except ValueError:
        return False


def report_lines(receipts: list[dict[str, str]]) -> list[str]:
    """What the nightly log shows: names and outcomes, never a value."""
    if not receipts:
        return ["OK credential-mint — nothing due, every root grant present or deferred"]
    return [f"  {r['credential']:<32} {r['step']:<16} {r['outcome']}" for r in receipts]
