#!/usr/bin/env python3
"""repo-hygiene-janitor.py — R07 disabled-by-default hygiene planner and applicator.

WHAT THIS IS. R03 stopped safely at 312 local branches and handed forward 265
branches for which it found NO machine deletion evidence. This module is the
standing janitor that R03's one-shot sweep became: it takes a census, plans
branch, registered-helper-worktree, and cache cleanup against an immutable
snapshot, RE-AUTHENTICATES every bound fact immediately before any mutation, and
writes one queryable receipt per candidate — including for the candidates it
refuses to touch.

WHAT THIS IS NOT, and the distinction is the whole point. The 265-branch tail is
an input for HUMAN ADJUDICATION. It is not a deletion allowlist, and no code path
here can turn it into one: a tail branch without a per-branch adjudication record
bound to its exact current tip is KEEP, and the reason is recorded by name. The
`<= 40` branch target that motivated the program is a target, never authority —
it appears nowhere in the decision procedure, because a count can never outrank
the deletion law.

THE DELETION LAW, inherited verbatim from tools/r03-settlement-sweep-runner.py
(rule a8c55a47 — the manual path and the automated path are the same law):

  ancestry-merged   the branch's CURRENT tip is still an ancestor of a freshly
                    pinned target, plus a verified tip backup -> `git branch -d`
  squash-merged     a MERGED-into-target pull request whose RECORDED HEAD OID
                    equals the current tip exactly, plus a verified tip backup
                    -> `git branch -D`
  everything else   KEEP, with the reason named.

"Verified backup" means all four of: local `git update-ref`, local readback,
`git push --atomic` to the remote, and an exact `git ls-remote` readback. A
backup that cannot be read back is not a backup, and the deletion never starts.

THE SNAPSHOT IS NOT THE AUTHORITY; THE MUTATION BOUNDARY IS. A plan row records
what was true when the census ran. Immediately before each mutation the janitor
rebuilds every bound fact from live state and re-runs the SAME classifier: a
branch tip that moved, a pinned target that moved, a pull-request head that no
longer matches, a worktree that became dirty or locked or freshly entered by an
R09 entrant, or a cache whose bytes changed all refuse. An entrant that arrives
one microsecond after the census keeps its worktree.

ONE MUTEX, HELD ACROSS THE WHOLE DECISION. `plan()` and `apply()` both refuse
unless this process holds the existing repository-maintenance mutex, so a
competing reaper cannot invalidate a snapshot between the census and the
mutation. `session()` is the intended door and releases on every path.

DEFAULT-OFF IS STRUCTURAL, NOT A FLAG DEFAULT. The command plans and reports;
mutation additionally requires `--execute`, a non-empty
CARR_REPO_HYGIENE_EFFECT_PACKET naming a separately reviewed live-effect packet,
AND a repository that is not the canonical checkout. That last guard is why this
source can express an eventual approved apply while granting no live run here.
This module installs no schedule and enables no service.

EVERY EXTERNAL DEPENDENCE IS INJECTED — the git port, the pull-request evidence
provider, the clock, the worktree liveness probe, the cache digest and refetch
verifier, the R09 entrant reader, and the receipt sink. That is what lets the
test suite drive real git and filesystem mutations inside its own disposable
fixtures while every other edge stays a fake, and it is why no test here needs to
reach a real remote, a real provider, or a real schedule.

INTEROPERATION, all read-only against code this module must never edit:
  hooks/worktree-self-plumb.py    the one repository-maintenance mutex, at
                                  out/worktree-reap.lock — exclusive create,
                                  two-hour stale threshold, owner unlinks.
  tools/room-bridge/worktree_runtime_isolation.py
                                  read_lock_posture(root, owner_alive) — an
                                  `active` or `stale_uncertain` entrant
                                  preserves its worktree regardless of TTL.
  bin/worktree.sh --remove <path> the only worktree removal door.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence
import uuid

CANONICAL_CHECKOUT = Path(os.environ.get("CARR_ROOT") or (Path.home() / "carr-system"))

# The exact protocol hooks/worktree-self-plumb.py implements. Both numbers are
# that hook's, not new ones: a second mutex, or a second staleness threshold,
# would be a second answer to a question that already has one.
MAINTENANCE_LOCK_RELPATH = ("out", "worktree-reap.lock")
LOCK_STALE_SECONDS = 2 * 3600

# A worktree whose newest file mtime is younger than this may still belong to a
# live session. bin/worktree.sh --sweep uses the same shape of guard.
DEFAULT_WORKTREE_TTL_SECONDS = 6 * 3600

# Directories bin/worktree.sh symlinks into every helper. They are this repo's
# plumbing, not a session's work, so neither liveness nor content reads them.
PLUMBING_DIRS = ("out", ".venv", "mcp-server/node_modules")

# Volatile roots. A path here can be reclaimed by the operating system between
# the plan and the apply, so its identity cannot be pinned and it is never
# eligible. /private/tmp is macOS's realpath for /tmp.
VOLATILE_PREFIXES = ("/private/tmp", "/private/var/tmp", "/tmp", "/var/tmp")

# The one bounded removal command for an owned fixture cache. It is a real
# executable invoked as a real subprocess, so the argv the receipt records is
# the argv that actually ran (review finding R07-DOMAIN-002).
CACHE_REMOVE_PROGRAM = "/bin/rm"

# The 265-branch tail is exactly these three markers in R03's appendix:
# 42 closed-unmerged + 189 no-PR + 34 reused-name. The other 46 kept rows
# (36 worktree-held, 4 open-PR, 6 assurance-held) are not tail rows.
TAIL_MARKERS = {
    "KEEP-CLOSED-UNMERGED": "closed_unmerged_pull_request",
    "KEEP-NO-PR": "unmerged_without_pull_request",
    "RETAIN": "reused_branch_name",
}
NON_TAIL_MARKERS = {
    "KEEP-WORKTREE": "worktree_held",
    "KEEP-OPEN-PR": "open_pull_request",
    "HOLD": "assurance_held",
}
_MANIFEST_ROW = re.compile(
    r"^#\s+(?P<marker>KEEP-[A-Z-]+|RETAIN|HOLD)\s+(?P<tip>[0-9a-f]{40})\s+(?P<name>\S+)"
)

DELETED, KEPT, REFUSED, UNKNOWN, INTENT = "deleted", "kept", "refused", "unknown", "intent"


class JanitorRefusal(RuntimeError):
    """Fail closed. Raised only for conditions that invalidate the whole run."""


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_volatile(path: str | Path) -> bool:
    resolved = str(Path(path))
    return any(resolved == p or resolved.startswith(p + os.sep) for p in VOLATILE_PREFIXES)


def tree_digest(path: Path) -> str | None:
    """A content-addressed identity for a directory or file.

    Deterministic over sorted relative paths plus bytes, so two runs of the same
    unchanged tree agree and any byte change moves the digest. A symlink is
    hashed as its target string rather than followed: following it would let a
    swapped link silently authenticate different content. Returns None when the
    path cannot be read, which every caller treats as missing evidence.
    """
    path = Path(path)
    try:
        if path.is_symlink():
            return _digest("symlink:" + os.readlink(path))
        if path.is_file():
            return _digest("file:" + hashlib.sha256(path.read_bytes()).hexdigest())
        if not path.is_dir():
            return None
        parts: list[str] = []
        for current, dirnames, filenames in os.walk(path):
            dirnames.sort()
            for name in sorted(filenames):
                item = Path(current) / name
                relative = str(item.relative_to(path))
                if item.is_symlink():
                    parts.append(f"{relative}\0symlink:{os.readlink(item)}")
                else:
                    parts.append(f"{relative}\0{hashlib.sha256(item.read_bytes()).hexdigest()}")
        return _digest("tree:" + "\n".join(parts))
    except OSError:
        return None


def newest_mtime_age_seconds(path: Path, clock: Callable[[], float] = time.time) -> float | None:
    """Seconds since the newest file mtime under a worktree, excluding this
    repo's own symlinked plumbing. This is the liveness signal bin/worktree.sh
    uses: a session interacts with its tree by editing files, and the shared
    gitdir does not live inside the tree, so fetches and commits do not move it.
    """
    path = Path(path)
    skip = {str(path / part) for part in PLUMBING_DIRS}
    newest = 0.0
    try:
        for current, dirnames, filenames in os.walk(path):
            if current in skip:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if str(Path(current) / d) not in skip]
            for name in filenames:
                try:
                    newest = max(newest, os.path.getmtime(Path(current) / name))
                except OSError:
                    continue
    except OSError:
        return None
    return None if newest == 0.0 else max(0.0, clock() - newest)


# ─────────────────────────────────────────────────────────────────────────────
# Ports. Everything the janitor cannot compute for itself arrives through one of
# these, so a test can supply a fake without the decision procedure knowing.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GitResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def out(self) -> str:
        return self.stdout.strip()


class GitPort:
    """Real git, against one explicit repository, with git's location variables
    scrubbed. `git -C` is not isolation: GIT_DIR outranks the directory, and
    every git hook exports it (see ops/git_env.py for the incident)."""

    def __init__(self, repository: Path, env: Mapping[str, str] | None = None, timeout: int = 120):
        self.repository = Path(repository)
        self.timeout = timeout
        self._env = dict(env) if env is not None else self._scrubbed()

    @staticmethod
    def _scrubbed() -> dict[str, str]:
        env = dict(os.environ)
        for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                    "GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES", "GIT_PREFIX",
                    "GIT_WORK_TREE_OVERRIDE"):
            env.pop(var, None)
        return env

    def run(self, argv: Sequence[str]) -> GitResult:
        full = ("git", "-C", str(self.repository), *argv)
        proc = subprocess.run(full, capture_output=True, text=True,
                              env=self._env, timeout=self.timeout, check=False)
        return GitResult(tuple(full), proc.returncode, proc.stdout, proc.stderr)


@dataclass(frozen=True)
class PullRequest:
    number: int
    state: str          # "open" | "closed" | "merged"
    base_ref: str
    head_oid: str
    evidence_id: str
    provider: str = "github"


class PullRequestProvider(Protocol):
    def pull_requests_for_branch(self, branch: str) -> Sequence[PullRequest]: ...


class NoPullRequestEvidence:
    """The default provider: no pull-request evidence at all.

    This is deliberately the shipped default. Squash deletion REQUIRES a merged
    pull request whose recorded head equals the tip, so with no evidence no
    branch can ever be squash-deleted, and every branch that needs a PR to be
    eligible is kept for a named reason. Real provider evidence arrives as a
    frozen, explicitly supplied file; this module performs no network call and
    no provider write.
    """

    def pull_requests_for_branch(self, branch: str) -> Sequence[PullRequest]:
        return ()


class FrozenPullRequestEvidence:
    """Read-only pull-request evidence loaded from an explicit local file.

    The file is a mapping of branch name to a list of {number, state, base_ref,
    head_oid, evidence_id}. Nothing here contacts a provider: the caller is
    responsible for how the evidence was gathered, and the receipt records the
    evidence id so a reviewer can trace it back.
    """

    def __init__(self, table: Mapping[str, Sequence[Mapping[str, Any]]]):
        self.table: dict[str, tuple[PullRequest, ...]] = {}
        for branch, rows in table.items():
            self.table[branch] = tuple(
                PullRequest(number=int(row["number"]), state=str(row["state"]),
                            base_ref=str(row["base_ref"]), head_oid=str(row["head_oid"]),
                            evidence_id=str(row["evidence_id"]),
                            provider=str(row.get("provider", "github")))
                for row in rows)

    @classmethod
    def from_file(cls, path: Path) -> "FrozenPullRequestEvidence":
        body = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(body, dict):
            raise JanitorRefusal(f"pull-request evidence must be an object: {path}")
        return cls(body)

    def pull_requests_for_branch(self, branch: str) -> Sequence[PullRequest]:
        return self.table.get(branch, ())


class EntrantReader(Protocol):
    """The R09 contract: read_lock_posture(root, owner_alive) -> {state, entrant}
    where state is one of none | active | stale_uncertain."""

    def __call__(self, root: Path) -> Mapping[str, Any]: ...


class ReceiptSink(Protocol):
    def available(self) -> bool: ...
    def emit(self, receipt: Mapping[str, Any]) -> None: ...


class JsonlReceiptSink:
    """Append-only local sink. `available()` is probed before the batch, and
    every individual emit is still checked, because availability at time T says
    nothing about a write at time T+1 (review finding R07-DOMAIN-003)."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def available(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8"):
                return True
        except OSError:
            return False

    def emit(self, receipt: Mapping[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


class MemoryReceiptSink:
    def __init__(self) -> None:
        self.receipts: list[dict[str, Any]] = []

    def available(self) -> bool:
        return True

    def emit(self, receipt: Mapping[str, Any]) -> None:
        self.receipts.append(dict(receipt))


# ─────────────────────────────────────────────────────────────────────────────
# The one repository-maintenance mutex, reusing hooks/worktree-self-plumb.py's
# protocol byte for byte rather than inventing a second one.
# ─────────────────────────────────────────────────────────────────────────────


class MaintenanceMutex:
    def __init__(self, canonical_root: Path, clock: Callable[[], float] = time.time):
        self.path = Path(canonical_root).joinpath(*MAINTENANCE_LOCK_RELPATH)
        self.clock = clock
        self.held = False

    def acquire(self) -> bool:
        """True when this process now owns the mutex. False on live contention —
        the caller stops; it does not wait and it does not steal."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if self.clock() - os.path.getmtime(self.path) < LOCK_STALE_SECONDS:
                    return False                       # a live reaper holds it
                os.unlink(self.path)                   # dead reaper's leftovers
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except OSError:
                return False
        except OSError:
            return False
        with os.fdopen(fd, "w") as handle:
            handle.write(str(os.getpid()))
        self.held = True
        return True

    def release(self) -> None:
        if not self.held:
            return
        try:
            os.unlink(self.path)
        except OSError:
            pass
        self.held = False

    def __enter__(self) -> "MaintenanceMutex":
        if not self.acquire():
            raise JanitorRefusal("repository maintenance mutex is held; refusing to plan")
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


# ─────────────────────────────────────────────────────────────────────────────
# Immutable inputs: R03's 265-branch tail and R01's never-cleanable register.
# Both are read-only. Neither is ever written, and neither becomes an allowlist.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TailRow:
    marker: str
    tip: str
    name: str
    reason: str
    in_tail: bool


def load_settlement_tail(path: Path) -> dict[str, TailRow]:
    rows: dict[str, TailRow] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        match = _MANIFEST_ROW.match(line)
        if not match:
            continue
        marker = match.group("marker")
        if marker in TAIL_MARKERS:
            reason, in_tail = TAIL_MARKERS[marker], True
        elif marker in NON_TAIL_MARKERS:
            reason, in_tail = NON_TAIL_MARKERS[marker], False
        else:
            continue
        rows[match.group("name")] = TailRow(marker, match.group("tip").lower(),
                                            match.group("name"), reason, in_tail)
    if not rows:
        raise JanitorRefusal(f"settlement tail manifest yielded no rows: {path}")
    return rows


def real_path(path: str | Path) -> str:
    """One normalization, used for every register decision on both sides.

    A lexical comparison is not an identity test: `<eligible-root>/../credentials`
    starts with an eligible root and IS a never-clean root, and a symlink named
    inside an eligible root can point anywhere at all. Everything the register
    judges is compared as a resolved real path, and the caller separately refuses
    a candidate whose written identity was not already normalized.
    """
    return os.path.realpath(str(Path(path)))


@dataclass(frozen=True)
class NeverCleanableRegister:
    never_clean: frozenset[str]
    exact_root_only: frozenset[str]

    def verdict(self, path: str | Path) -> str | None:
        """None when the path is eligible for exact-root receipted pruning.

        DENY WINS, and eligibility is EXACT. A never-clean root protects itself
        and everything beneath it; an eligible root grants only itself, because
        "exact-root receipted janitor only" in the register means that root, not
        an arbitrary descendant of it.
        """
        resolved = real_path(path)
        for root in self.never_clean:
            root = real_path(root)
            if resolved == root or resolved.startswith(root + os.sep):
                return "never_cleanable_register"
        if any(resolved == real_path(root) for root in self.exact_root_only):
            return None
        return "cache_root_not_registered"


def load_never_cleanable_register(path: Path) -> NeverCleanableRegister:
    never: set[str] = set()
    exact: set[str] = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith("|---"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if len(cells) < 5 or not cells[1].startswith("/"):
            continue
        realpath, protection = cells[1].rstrip("/"), cells[4]
        (never if "NEVER CLEAN" in protection else exact).add(realpath)
    if not never and not exact:
        raise JanitorRefusal(f"never-cleanable register yielded no rows: {path}")
    return NeverCleanableRegister(frozenset(never), frozenset(exact))


# ─────────────────────────────────────────────────────────────────────────────
# Candidates in, plan rows out.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BranchCandidate:
    name: str
    tip: str | None                     # None means the tip could not be read
    remote_ref_exists: bool = False
    worktree_held: bool = False
    adjudication: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class WorktreeCandidate:
    path: str
    registered: bool
    bare: bool = False
    locked: bool = False
    dirty: bool = True                  # the safe default is "someone's work"
    idle_seconds: float | None = None   # None means liveness is unknown
    isolation_root: str | None = None   # the R09 registry root, when one exists

    def posture(self) -> dict[str, Any]:
        return {"registered": self.registered, "bare": self.bare, "locked": self.locked,
                "dirty": self.dirty, "idle_seconds": self.idle_seconds,
                "isolation_root": self.isolation_root}


@dataclass(frozen=True)
class CacheCandidate:
    """Cache identity is COMPUTED, never asserted by the caller.

    `expected_digest` is the content-addressed identity the operator says this
    root should have; the observed digest is measured by the janitor at plan time
    and measured AGAIN at the mutation boundary. A caller cannot hand in a claim
    about the bytes (review finding R07-DOMAIN-002).
    """
    path: str
    expected_digest: str | None = None
    refetch_command: Sequence[str] | None = None


@dataclass(frozen=True)
class PlanRow:
    kind: str                           # branch | worktree | cache
    identity: str
    action: str                         # keep | delete_ancestry | delete_squash
                                        # | remove_worktree | prune_cache
    reason: str
    preimage: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    @property
    def mutating(self) -> bool:
        return self.action != "keep"


@dataclass(frozen=True)
class Plan:
    snapshot_id: str
    pinned_target: str
    pinned_ref: str
    taken_at: float
    rows: tuple[PlanRow, ...]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in self.rows:
            out[row.action] = out.get(row.action, 0) + 1
        return out


def _keep(kind: str, identity: str, reason: str, preimage: str | None = None,
          **evidence: Any) -> PlanRow:
    return PlanRow(kind, identity, "keep", reason, preimage, evidence)


def planned_adjudication(row: PlanRow) -> Mapping[str, Any] | None:
    record = row.evidence.get("adjudication")
    return record if isinstance(record, Mapping) else None


def _pr_evidence(pr: PullRequest) -> dict[str, Any]:
    return {"provider": pr.provider, "number": pr.number, "state": pr.state,
            "base_ref": pr.base_ref, "head_oid": pr.head_oid.lower(),
            "evidence_id": pr.evidence_id}


# ─────────────────────────────────────────────────────────────────────────────


class RepoHygieneJanitor:
    """Plan against one immutable snapshot; re-authenticate before every mutation."""

    def __init__(
        self,
        *,
        repository: Path,
        git: GitPort,
        receipt_sink: ReceiptSink,
        tail: Mapping[str, TailRow],
        register: NeverCleanableRegister,
        pull_requests: PullRequestProvider | None = None,
        entrant_reader: EntrantReader | None = None,
        mutex: MaintenanceMutex | None = None,
        clock: Callable[[], float] = time.time,
        liveness_probe: Callable[[Path], float | None] | None = None,
        cache_digest: Callable[[Path], str | None] = tree_digest,
        refetch_verifier: Callable[[CacheCandidate, str], bool] | None = None,
        target_ref: str = "refs/remotes/origin/main",
        target_base_branch: str = "main",
        backup_namespace: str = "refs/backup/r07-janitor",
        worktree_ttl_seconds: float = DEFAULT_WORKTREE_TTL_SECONDS,
        worktree_remove_command: Sequence[str] = ("zsh", "bin/worktree.sh", "--remove"),
        effect_packet: str | None = None,
    ):
        self.repository = Path(repository)
        self.git = git
        self.pull_requests: PullRequestProvider = pull_requests or NoPullRequestEvidence()
        self.receipt_sink = receipt_sink
        self.tail = dict(tail)
        self.register = register
        self.entrant_reader = entrant_reader
        self.mutex = mutex
        self.clock = clock
        self.liveness_probe = liveness_probe or (lambda p: newest_mtime_age_seconds(p, clock))
        self.cache_digest = cache_digest
        self.refetch_verifier = refetch_verifier
        self.target_ref = target_ref
        self.target_base_branch = target_base_branch
        self.backup_namespace = backup_namespace.rstrip("/")
        self.worktree_ttl_seconds = worktree_ttl_seconds
        self.worktree_remove_command = tuple(worktree_remove_command)
        self.effect_packet = effect_packet or ""
        self._seen_keys: dict[str, dict[str, Any]] = {}

    # ── mutex ownership spans the whole decision ────────────────────────────

    def _require_mutex(self, stage: str) -> None:
        # FAIL CLOSED ON ABSENCE, not just on non-ownership. An earlier version
        # treated `mutex=None` as "no mutex configured, carry on", which made the
        # public API mutate unlocked while only the command took the lock. A
        # caller with no mutex has not proven exclusivity, so it does not act.
        if self.mutex is None:
            raise JanitorRefusal(
                f"no repository maintenance mutex is bound; refusing to {stage}")
        if not self.mutex.held:
            raise JanitorRefusal(
                f"repository maintenance mutex is not held; refusing to {stage}")

    # ── snapshot ────────────────────────────────────────────────────────────

    def pin_target(self) -> str:
        """One fresh target OID. An unreadable target is missing evidence for
        every branch decision, so it refuses the whole plan rather than letting
        a stale pin decide a deletion."""
        result = self.git.run(("rev-parse", "--verify", self.target_ref))
        if result.returncode or not re.fullmatch(r"[0-9a-f]{40}", result.out):
            raise JanitorRefusal(f"fresh pinned target unavailable: {self.target_ref}")
        return result.out

    def _current_tip(self, branch: str) -> str | None:
        result = self.git.run(("rev-parse", "--verify", f"refs/heads/{branch}"))
        return result.out.lower() if not result.returncode else None

    def _is_ancestor(self, tip: str, pinned: str) -> bool:
        return self.git.run(("merge-base", "--is-ancestor", tip, pinned)).returncode == 0

    def backup_ref_for(self, branch: str) -> str:
        return f"{self.backup_namespace}/{branch}"

    # ── the branch decision procedure, in order ─────────────────────────────

    def classify_branch(self, candidate: BranchCandidate, pinned: str) -> PlanRow:
        name, tip = candidate.name, (candidate.tip or "").lower()
        row = self.tail.get(name)

        if tip == "":
            return _keep("branch", name, "missing_evidence_unreadable_tip")
        if name == self.target_base_branch:
            # The branch the deletion law measures everything else against is
            # never itself a candidate, whatever the ancestry check would say.
            return _keep("branch", name, "protected_target_branch", tip)
        if candidate.worktree_held:
            return _keep("branch", name, "worktree_held", tip)
        if row is not None and row.in_tail and not self._adjudicated(candidate, tip):
            # The load-bearing refusal. Being in R03's tail is a reason to look,
            # never a reason to delete.
            return _keep("branch", name, "tail_requires_human_adjudication", tip,
                         tail_marker=row.marker, tail_reason=row.reason)
        if row is not None and not row.in_tail and row.reason == "assurance_held":
            return _keep("branch", name, "assurance_held", tip)

        try:
            prs = list(self.pull_requests.pull_requests_for_branch(name))
        except Exception as exc:
            # Unavailable evidence is not absent evidence. Ancestry needs this
            # answer too, because an open pull request preserves a branch even
            # when the branch is genuinely merged into the pinned target.
            return _keep("branch", name, "missing_evidence_pull_request_provider_unavailable",
                         tip, provider_error=str(exc))
        if any(pr.state == "open" for pr in prs):
            open_pr = next(pr for pr in prs if pr.state == "open")
            return _keep("branch", name, "open_pull_request", tip,
                         pull_request=_pr_evidence(open_pr))

        if self._is_ancestor(tip, pinned):
            return PlanRow("branch", name, "delete_ancestry", "ancestry_merged_into_pinned_target",
                           tip, {"pinned_target": pinned,
                                 "backup_ref": self.backup_ref_for(name),
                                 "adjudication": dict(candidate.adjudication)
                                 if candidate.adjudication else None})

        squash = self._squash_evidence(prs, tip)
        if squash is not None:
            return PlanRow("branch", name, "delete_squash",
                           "squash_merged_pull_request_head_equals_current_tip", tip,
                           {"pinned_target": pinned, "pull_request": squash,
                            "backup_ref": self.backup_ref_for(name),
                            "adjudication": dict(candidate.adjudication)
                            if candidate.adjudication else None})

        if candidate.remote_ref_exists:
            return _keep("branch", name, "remote_unmerged", tip)
        if any(pr.state == "closed" for pr in prs):
            closed = next(pr for pr in prs if pr.state == "closed")
            return _keep("branch", name, "closed_unmerged_pull_request", tip,
                         pull_request=_pr_evidence(closed))
        if prs:
            # A merged PR exists but its recorded head is not this tip: the name
            # was reused and the current commits are unmerged work. This is the
            # exact FIX-1 shape that would have destroyed live commits.
            return _keep("branch", name, "reused_branch_name", tip,
                         pull_request=_pr_evidence(prs[0]))
        return _keep("branch", name, "unmerged_without_pull_request", tip)

    def _adjudicated(self, candidate: BranchCandidate, tip: str) -> bool:
        """A per-branch human adjudication that names THIS tip. An adjudication
        recorded against an older tip decided a different commit."""
        record = candidate.adjudication
        if not isinstance(record, Mapping):
            return False
        required = ("adjudicated_by", "adjudicated_at", "decision", "evidence_id", "tip")
        if any(not record.get(key) for key in required):
            return False
        return record["decision"] == "eligible" and str(record["tip"]).lower() == tip

    def _squash_evidence(self, prs: Iterable[PullRequest], tip: str) -> dict[str, Any] | None:
        for pr in prs:
            if (pr.state == "merged"
                    and pr.base_ref == self.target_base_branch
                    and pr.head_oid.lower() == tip):
                return _pr_evidence(pr)
        return None

    # ── the worktree decision procedure, in order ───────────────────────────

    def classify_worktree(self, candidate: WorktreeCandidate) -> PlanRow:
        path = candidate.path
        posture = self._entrant_posture(candidate)
        facts: dict[str, Any] = dict(candidate.posture())
        facts["entrant"] = posture

        def keep(reason: str) -> PlanRow:
            return _keep("worktree", path, reason, None, **facts)

        if Path(path).resolve() == CANONICAL_CHECKOUT.resolve():
            return keep("canonical_tree")
        if not candidate.registered:
            return keep("unregistered_worktree")
        if candidate.bare:
            return keep("bare_worktree")
        if candidate.locked:
            return keep("locked_worktree")
        if _is_volatile(path):
            return keep("volatile_private_tmp_path")
        if candidate.dirty:
            return keep("dirty_worktree")

        # Entrant posture is read BEFORE the TTL guard on purpose: an active or
        # uncertain R09 entrant preserves its worktree regardless of how idle
        # the tree looks, and the receipt should say so by name.
        if posture["state"] == "active":
            return keep("active_r09_entrant")
        if posture["state"] == "stale_uncertain":
            return keep("uncertain_r09_entrant_liveness")
        if posture["state"] == "unreadable":
            return keep("missing_evidence_entrant_unreadable")

        if candidate.idle_seconds is None:
            return keep("missing_evidence_unknown_liveness")
        if candidate.idle_seconds < self.worktree_ttl_seconds:
            return keep("recently_live_worktree")
        return PlanRow("worktree", path, "remove_worktree", "registered_stale_clean_no_entrant",
                       None, facts)

    def _entrant_posture(self, candidate: WorktreeCandidate) -> dict[str, Any]:
        # NOT CONSULTING R09 IS NOT THE SAME AS R09 SAYING "NOBODY IS HERE".
        # A caller with no reader, or a worktree with no bound registry root,
        # has no evidence about entrants, and missing evidence preserves. This
        # is why the shipped command cannot remove a worktree until an explicit
        # isolation-root map is supplied.
        if self.entrant_reader is None:
            return {"state": "unreadable", "entrant": None,
                    "error": "no R09 entrant reader configured"}
        if candidate.isolation_root is None:
            return {"state": "unreadable", "entrant": None,
                    "error": "no R09 isolation root bound for this worktree"}
        try:
            posture = self.entrant_reader(Path(candidate.isolation_root))
        except Exception as exc:                       # preserve on any refusal
            return {"state": "unreadable", "entrant": None, "error": str(exc)}
        state = posture.get("state")
        if state not in ("none", "active", "stale_uncertain"):
            return {"state": "unreadable", "entrant": None, "error": f"unknown state {state!r}"}
        return {"state": state, "entrant": posture.get("entrant")}

    # ── the cache decision procedure, in order ──────────────────────────────

    def classify_cache(self, candidate: CacheCandidate) -> PlanRow:
        path = candidate.path
        if self._under_canonical():
            return _keep("cache", path, "canonical_cache_cleanup_forbidden")

        # IDENTITY BEFORE ANYTHING ELSE. A written path that is relative, or that
        # walks through `..`, is not an identity — it is a sentence that resolves
        # to one, and `<eligible-root>/../credentials` resolves to a never-clean
        # root while reading like an eligible one.
        raw = Path(path)
        if not raw.is_absolute() or ".." in raw.parts:
            return _keep("cache", path, "cache_path_not_normalized")
        resolved = real_path(raw)
        # Volatility is judged on BOTH spellings, because /tmp is itself a
        # symlink to /private/tmp on this platform.
        if _is_volatile(raw) or _is_volatile(resolved):
            return _keep("cache", path, "volatile_private_tmp_path", None,
                         resolved_path=resolved)
        verdict = self.register.verdict(resolved)
        if verdict is not None:
            return _keep("cache", path, verdict, None, resolved_path=resolved)

        observed = self.cache_digest(Path(resolved))
        facts: dict[str, Any] = {"observed_digest": observed,
                                 "expected_digest": candidate.expected_digest,
                                 "resolved_path": resolved}
        if not candidate.expected_digest or not observed:
            return _keep("cache", path, "cache_reproducibility_missing", None, **facts)
        if observed.lower() != candidate.expected_digest.lower():
            return _keep("cache", path, "cache_reproducibility_mismatch", None, **facts)
        if not candidate.refetch_command or self.refetch_verifier is None:
            return _keep("cache", path, "cache_refetch_proof_missing", None, **facts)
        try:
            proven = bool(self.refetch_verifier(candidate, observed))
        except Exception as exc:
            facts["refetch_error"] = str(exc)
            return _keep("cache", path, "cache_refetch_proof_missing", None, **facts)
        if not proven:
            return _keep("cache", path, "cache_refetch_proof_missing", None, **facts)
        facts["refetch_command"] = list(candidate.refetch_command)
        facts["refetch_proof"] = True
        return PlanRow("cache", path, "prune_cache", "content_addressed_reproducible",
                       observed.lower(), facts)

    def _under_canonical(self) -> bool:
        try:
            resolved, canonical = self.repository.resolve(), CANONICAL_CHECKOUT.resolve()
        except OSError:
            return True                                # unresolvable: assume canonical
        return resolved == canonical or canonical in resolved.parents

    # ── planning ────────────────────────────────────────────────────────────

    def plan(
        self,
        *,
        branches: Sequence[BranchCandidate] = (),
        worktrees: Sequence[WorktreeCandidate] = (),
        caches: Sequence[CacheCandidate] = (),
    ) -> Plan:
        self._require_mutex("plan")
        pinned = self.pin_target()
        rows: list[PlanRow] = []
        for branch in branches:
            rows.append(self.classify_branch(branch, pinned))
        for worktree in worktrees:
            rows.append(self.classify_worktree(worktree))
        for cache in caches:
            rows.append(self.classify_cache(cache))
        return Plan(uuid.uuid4().hex, pinned, self.target_ref, self.clock(), tuple(rows))

    # ── applying ────────────────────────────────────────────────────────────

    def apply(self, plan: Plan, *, execute: bool = False,
              idempotency_prefix: str | None = None) -> list[dict[str, Any]]:
        """Re-authenticate each row against live state and record one receipt per
        row. `execute=False` is the default and the production posture: every
        fact is re-read, every receipt is written, and nothing is mutated."""
        self._require_mutex("apply")
        if execute and not self.effect_packet:
            raise JanitorRefusal(
                "execute requires a separately reviewed live-effect packet "
                "(CARR_REPO_HYGIENE_EFFECT_PACKET); refusing to mutate")
        if execute and self._under_canonical():
            raise JanitorRefusal(
                "execute against the canonical checkout requires a separate reviewed "
                "effect packet and is not authorized by this source; refusing to mutate")
        if execute and not self.receipt_sink.available():
            raise JanitorRefusal("receipt sink unavailable; refusing to mutate")

        prefix = idempotency_prefix or plan.snapshot_id
        receipts: list[dict[str, Any]] = []
        for index, row in enumerate(plan.rows):
            key = f"{prefix}:{row.kind}:{row.identity}:{index}"
            if key in self._seen_keys:
                receipts.append(self._seen_keys[key])   # replay, never re-mutate
                continue
            receipt = self._run_row(plan, row, key, execute)
            self._seen_keys[key] = receipt              # dedupe even on UNKNOWN
            receipts.append(receipt)
        return receipts

    def _emit(self, receipt: Mapping[str, Any]) -> tuple[bool, str | None]:
        try:
            self.receipt_sink.emit(receipt)
            return True, None
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

    def _run_row(self, plan: Plan, row: PlanRow, key: str, execute: bool) -> dict[str, Any]:
        """The receipt protocol across the mutation boundary.

        An effect that cannot be recorded before it happens does not happen; an
        effect whose result cannot be recorded after it happens is UNKNOWN, never
        success, and is never retried automatically.
        """
        if row.mutating and execute:
            intent = self._close(self._base(plan, row, key), INTENT,
                                 f"pre_effect_intent_{row.action}", receipt_durable=True)
            durable, error = self._emit(intent)
            if not durable:
                # Nothing has been touched yet, and nothing will be: an effect
                # whose intent cannot be recorded is not permitted to start.
                return self._close(self._base(plan, row, key), REFUSED,
                                   "pre_effect_intent_not_durable",
                                   receipt_durable=False, sink_error=error)

        receipt = self._apply_row(plan, row, key, execute)
        durable, error = self._emit(receipt)
        receipt["receipt_durable"] = durable
        if not durable:
            receipt["sink_error"] = error
            if receipt["result"] == DELETED:
                # The target is gone and the outcome could not be persisted.
                # That is precisely an unknown state, and the recorded
                # idempotency key stops a resubmit from repeating the effect.
                receipt["result"] = UNKNOWN
                receipt["reason"] = "result_receipt_not_durable_after_effect_no_automatic_retry"
        return receipt

    def _base(self, plan: Plan, row: PlanRow, key: str) -> dict[str, Any]:
        return {
            "operation_id": uuid.uuid4().hex,
            "idempotency_key": key,
            "snapshot_id": plan.snapshot_id,
            "target_kind": row.kind,
            "target_identity": row.identity,
            "observed_preimage": row.preimage,
            "pinned_target": plan.pinned_target,
            "planned_action": row.action,
            "planned_reason": row.reason,
            "evidence": dict(row.evidence),
            "argv": [],
            "started_at": self.clock(),
        }

    def _apply_row(self, plan: Plan, row: PlanRow, key: str, execute: bool) -> dict[str, Any]:
        base = self._base(plan, row, key)
        if not row.mutating:
            return self._close(base, KEPT, row.reason, current=row.preimage)
        if not execute:
            return self._close(base, KEPT, f"dry_run_would_{row.action}", current=row.preimage)
        if row.kind == "branch":
            return self._apply_branch(plan, row, base)
        if row.kind == "worktree":
            return self._apply_worktree(row, base)
        return self._apply_cache(row, base)

    def _close(self, base: dict[str, Any], result: str, reason: str, *,
               current: str | None = None, exit_code: int | None = None,
               stdout: str = "", stderr: str = "", **extra: Any) -> dict[str, Any]:
        base.update({
            "current_tip": current,
            "finished_at": self.clock(),
            "exit_code": exit_code,
            "stdout_digest": _digest(stdout),
            "stderr_digest": _digest(stderr),
            "result": result,
            "reason": reason,
            **extra,
        })
        return base

    def observe_branch(self, name: str, adjudication: Mapping[str, Any] | None,
                       base: dict[str, Any] | None = None) -> BranchCandidate:
        """Rebuild every bound branch fact from live state.

        The census calls the same function, so a planned decision and a
        re-authenticated one are derived identically rather than one being
        remembered. The human adjudication is carried forward from the plan
        because it is a recorded judgment, not an observation.
        """
        tip = self._current_tip(name)
        remotes = self.git.run(("for-each-ref", "--format=%(refname:short)", "refs/remotes"))
        if base is not None:
            base["argv"].append(list(remotes.argv))
        remote_names = ({line.split("/", 1)[1] for line in remotes.stdout.split() if "/" in line}
                        if not remotes.returncode else set())
        held = {entry["branch"].removeprefix("refs/heads/")
                for entry in self.read_worktree_inventory(base).values() if entry.get("branch")}
        return BranchCandidate(name=name, tip=tip, remote_ref_exists=name in remote_names,
                               worktree_held=name in held, adjudication=adjudication)

    # ── branch application: re-verify, back up, delete, confirm ─────────────

    def _apply_branch(self, plan: Plan, row: PlanRow, base: dict[str, Any]) -> dict[str, Any]:
        name = row.identity

        fresh_pin = self.git.run(("rev-parse", "--verify", plan.pinned_ref))
        base["argv"].append(list(fresh_pin.argv))
        if fresh_pin.returncode or fresh_pin.out != plan.pinned_target:
            return self._close(base, REFUSED, "pinned_target_drifted_since_plan",
                               exit_code=fresh_pin.returncode, stderr=fresh_pin.stderr)

        current = self._current_tip(name)
        if current is None:
            return self._close(base, REFUSED, "branch_disappeared_before_apply")
        if current != row.preimage:
            return self._close(base, REFUSED, "branch_tip_moved_since_plan", current=current)

        # ONE RE-AUTHENTICATION SEAM, the same shape worktrees and caches use.
        # The earlier version re-checked pull requests only on the squash path,
        # so an open pull request that appeared after an ancestry plan was
        # deleted anyway. Re-running the whole classifier means EVERY survivor
        # class is re-asked on EVERY branch mutation path, and a class added
        # later is covered without anyone remembering to add it here.
        fresh = self.observe_branch(name, planned_adjudication(row), base)
        reclassified = self.classify_branch(fresh, plan.pinned_target)
        base["fresh_evidence"] = dict(reclassified.evidence)
        base["fresh_reason"] = reclassified.reason
        if reclassified.action != row.action:
            return self._close(base, REFUSED,
                               f"branch_drift_at_apply:{reclassified.reason}", current=current)

        backup = self._verify_backup(name, current, base)
        if backup["result"] != "ok":
            return self._close(base, REFUSED, backup["reason"], current=current,
                               backup_ref=backup["ref"],
                               backup_local_readback=backup.get("local"),
                               backup_remote_readback=backup.get("remote"))

        flag = "-d" if row.action == "delete_ancestry" else "-D"
        deletion = self.git.run(("branch", flag, name))
        base["argv"].append(list(deletion.argv))
        after = self._current_tip(name)

        extra = {"backup_ref": backup["ref"], "backup_local_readback": backup.get("local"),
                 "backup_remote_readback": backup.get("remote")}
        if deletion.returncode == 0 and after is None:
            return self._close(base, DELETED, row.reason, current=current,
                               exit_code=0, stdout=deletion.stdout, stderr=deletion.stderr, **extra)
        if deletion.returncode != 0 and after == current:
            return self._close(base, REFUSED, "deletion_command_failed_branch_intact",
                               current=current, exit_code=deletion.returncode,
                               stdout=deletion.stdout, stderr=deletion.stderr, **extra)
        # Exit code and observed state disagree. That is a partial operation: it
        # is UNKNOWN, it is not retried, and a human reads the backup ref.
        return self._close(base, UNKNOWN, "partial_deletion_state_unknown_no_automatic_retry",
                           current=current, exit_code=deletion.returncode,
                           stdout=deletion.stdout, stderr=deletion.stderr, **extra)

    def _verify_backup(self, name: str, tip: str, base: dict[str, Any]) -> dict[str, Any]:
        """Local update-ref, local readback, atomic remote push, remote readback.
        All four, or there is no backup and no deletion."""
        ref = self.backup_ref_for(name)
        update = self.git.run(("update-ref", ref, tip))
        base["argv"].append(list(update.argv))
        if update.returncode:
            return {"result": "failed", "reason": "backup_update_ref_failed", "ref": ref}

        local = self.git.run(("rev-parse", "--verify", ref))
        base["argv"].append(list(local.argv))
        if local.returncode or local.out.lower() != tip:
            return {"result": "failed", "reason": "backup_local_readback_mismatch",
                    "ref": ref, "local": local.out or None}

        push = self.git.run(("push", "--atomic", "origin", f"refs/heads/{name}:{ref}"))
        base["argv"].append(list(push.argv))
        if push.returncode:
            return {"result": "failed", "reason": "backup_remote_push_failed",
                    "ref": ref, "local": local.out}

        remote = self.git.run(("ls-remote", "--refs", "origin", ref))
        base["argv"].append(list(remote.argv))
        fields = remote.out.split()
        if remote.returncode or len(fields) != 2 or fields[0].lower() != tip or fields[1] != ref:
            return {"result": "failed", "reason": "backup_remote_readback_mismatch",
                    "ref": ref, "local": local.out, "remote": remote.out or None}
        return {"result": "ok", "reason": "backup_verified", "ref": ref,
                "local": local.out, "remote": fields[0].lower()}

    # ── worktree application: rebuild every bound fact, then re-classify ────

    def read_worktree_inventory(self, base: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
        """git's own registered-worktree inventory, parsed into per-path facts."""
        listing = self.git.run(("worktree", "list", "--porcelain"))
        if base is not None:
            base["argv"].append(list(listing.argv))
        inventory: dict[str, dict[str, Any]] = {}
        current: dict[str, Any] | None = None
        for line in listing.stdout.splitlines():
            if line.startswith("worktree "):
                path = line.split(" ", 1)[1]
                current = {"path": path, "bare": False, "locked": False, "prunable": False}
                inventory[path] = current
            elif current is None:
                continue
            elif line == "bare" or line.startswith("bare "):
                current["bare"] = True
            elif line == "locked" or line.startswith("locked "):
                current["locked"] = True
            elif line == "prunable" or line.startswith("prunable "):
                current["prunable"] = True
            elif line.startswith("branch "):
                current["branch"] = line.split(" ", 1)[1]
        return inventory

    def observe_worktree(self, path: str, isolation_root: str | None,
                         base: dict[str, Any] | None = None) -> WorktreeCandidate:
        """Rebuild every bound worktree fact from live state.

        This is what the mutation boundary calls, and it is the same shape the
        census calls, so a planned decision and a re-authenticated decision are
        made from identically-derived facts rather than from a remembered one.
        """
        inventory = self.read_worktree_inventory(base)
        entry = inventory.get(path) or inventory.get(str(Path(path).resolve()))
        if entry is None:
            return WorktreeCandidate(path, registered=False, dirty=True,
                                     isolation_root=isolation_root)
        status = self.git.run(("-C", path, "status", "--porcelain"))
        if base is not None:
            base["argv"].append(list(status.argv))
        # An unreadable status is not a clean tree: dirty stays the safe default.
        dirty = bool(status.returncode) or bool(status.stdout.strip())
        try:
            idle = self.liveness_probe(Path(path))
        except Exception:
            idle = None
        return WorktreeCandidate(path, registered=True, bare=bool(entry["bare"]),
                                 locked=bool(entry["locked"]), dirty=dirty,
                                 idle_seconds=idle, isolation_root=isolation_root)

    def _apply_worktree(self, row: PlanRow, base: dict[str, Any]) -> dict[str, Any]:
        path = row.identity
        planned = dict(row.evidence)
        fresh = self.observe_worktree(path, planned.get("isolation_root"), base)
        reclassified = self.classify_worktree(fresh)
        base["fresh_posture"] = dict(reclassified.evidence)
        base["planned_posture"] = planned

        # The re-authenticated classification is the authority, not the plan. An
        # entrant that arrived after the census, a tree that became dirty or
        # locked or freshly touched, and any unreadable fact all land here.
        if reclassified.action != "remove_worktree":
            return self._close(base, REFUSED, f"worktree_drift_at_apply:{reclassified.reason}")

        argv = [*self.worktree_remove_command, path]
        base["argv"].append(list(argv))
        proc = subprocess.run(argv, cwd=self.repository, capture_output=True,
                              text=True, check=False)
        if proc.returncode == 0 and not Path(path).exists():
            return self._close(base, DELETED, row.reason, exit_code=0,
                               stdout=proc.stdout, stderr=proc.stderr)
        if proc.returncode != 0 and Path(path).exists():
            return self._close(base, REFUSED, "worktree_removal_refused_tree_intact",
                               exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
        return self._close(base, UNKNOWN, "partial_worktree_removal_unknown_no_automatic_retry",
                           exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

    # ── cache application: re-measure identity, then one real command ───────

    def _apply_cache(self, row: PlanRow, base: dict[str, Any]) -> dict[str, Any]:
        planned = dict(row.evidence)
        candidate = CacheCandidate(row.identity, planned.get("expected_digest"),
                                   planned.get("refetch_command"))
        reclassified = self.classify_cache(candidate)
        base["fresh_evidence"] = dict(reclassified.evidence)

        # The path this row is ABOUT is the resolved one, and it must still
        # resolve to the same place it did at census time. An alias that now
        # points somewhere else is a different target, not the same one moved.
        resolved = reclassified.evidence.get("resolved_path") or real_path(row.identity)
        base["resolved_path"] = resolved
        if planned.get("resolved_path") and planned["resolved_path"] != resolved:
            return self._close(base, REFUSED, "cache_path_identity_changed_since_plan")
        path = Path(resolved)
        if reclassified.action != "prune_cache":
            # Re-measured identity is the authority. Changed bytes, a new file, a
            # swapped symlink, and a vanished root all land here, because each of
            # them moves the digest away from the expected one.
            return self._close(base, REFUSED, f"cache_drift_at_apply:{reclassified.reason}")

        # The remaining two checks close the window BETWEEN the measurement above
        # and the removal below. They are not redundant with it: the digest was
        # read a moment ago, and these read the path as it is right now.
        if path.is_symlink():
            return self._close(base, REFUSED, "cache_path_became_symlink")
        if not path.exists():
            return self._close(base, REFUSED, "cache_path_disappeared_before_apply")
        if path.parent == path or str(path) == os.sep:
            return self._close(base, REFUSED, "cache_path_is_a_filesystem_root")
        # There is deliberately NO second register verdict here. The register was
        # already asked about this exact resolved string a few lines above, and
        # asking a pure function the same question twice cannot produce a new
        # answer — it would be a guard that looks protective and can never fire.
        # The two checks above are the real window closers: they re-read the
        # FILESYSTEM, which can change, rather than re-deriving a fixed string.

        # ONE actual bounded command, invoked as a real subprocess, so the argv
        # the receipt records is the argv that ran (review finding 002), naming
        # the resolved path the register just cleared.
        argv = [CACHE_REMOVE_PROGRAM, "-rf", "--", str(path)]
        base["argv"].append(list(argv))
        proc = subprocess.run(argv, capture_output=True, text=True, check=False)
        if proc.returncode == 0 and not path.exists():
            return self._close(base, DELETED, row.reason, current=row.preimage, exit_code=0,
                               stdout=proc.stdout, stderr=proc.stderr)
        if proc.returncode != 0 and path.exists():
            return self._close(base, REFUSED, "cache_removal_failed_path_intact",
                               current=row.preimage, exit_code=proc.returncode,
                               stdout=proc.stdout, stderr=proc.stderr)
        return self._close(base, UNKNOWN, "partial_cache_removal_unknown_no_automatic_retry",
                           current=row.preimage, exit_code=proc.returncode,
                           stdout=proc.stdout, stderr=proc.stderr)


# ─────────────────────────────────────────────────────────────────────────────
# Read-only census. Everything here is a git read; nothing contacts a provider.
# ─────────────────────────────────────────────────────────────────────────────


def census_branches(janitor: RepoHygieneJanitor,
                    adjudications: Mapping[str, Mapping[str, Any]] | None = None,
                    ) -> list[BranchCandidate]:
    """Every local branch, with the facts the classifier needs, read-only."""
    adjudications = adjudications or {}
    heads = janitor.git.run(("for-each-ref", "--format=%(refname:short) %(objectname)",
                             "refs/heads"))
    if heads.returncode:
        raise JanitorRefusal("could not read local branches")
    remotes = janitor.git.run(("for-each-ref", "--format=%(refname:short)", "refs/remotes"))
    remote_names = {line.split("/", 1)[1] for line in remotes.stdout.split()
                    if "/" in line} if not remotes.returncode else set()
    held = {entry["branch"].removeprefix("refs/heads/")
            for entry in janitor.read_worktree_inventory().values() if entry.get("branch")}

    candidates: list[BranchCandidate] = []
    for line in heads.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        name, tip = parts[0], parts[1].lower()
        candidates.append(BranchCandidate(
            name=name, tip=tip, remote_ref_exists=name in remote_names,
            worktree_held=name in held, adjudication=adjudications.get(name)))
    return candidates


def census_worktrees(janitor: RepoHygieneJanitor,
                     isolation_roots: Mapping[str, str] | None = None,
                     ) -> list[WorktreeCandidate]:
    """Every registered worktree, observed through the same path the mutation
    boundary uses, so the census and the re-authentication cannot disagree about
    how a fact is derived."""
    # Keyed on the RESOLVED path on both sides. git reports a worktree by its
    # real path, so a map written with the caller's spelling would silently miss
    # and read as "no entrant evidence" — which preserves, but for the wrong
    # reason, and would hide a genuinely bound registry.
    roots = {real_path(key): value for key, value in (isolation_roots or {}).items()}
    canonical = real_path(janitor.repository)
    candidates: list[WorktreeCandidate] = []
    for path in janitor.read_worktree_inventory():
        if real_path(path) == canonical:
            continue
        candidates.append(janitor.observe_worktree(path, roots.get(real_path(path))))
    return candidates


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint. Plans and reports by default; mutation needs three separate keys.
# ─────────────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="R07 repo-hygiene janitor (disabled by default; plans unless "
                    "explicitly executed against a non-canonical repository).")
    parser.add_argument("--repository", default=str(CANONICAL_CHECKOUT))
    parser.add_argument("--tail-manifest",
                        default=str(CANONICAL_CHECKOUT / "out" / "repo-hygiene-program"
                                    / "settlement-manifest-branches.txt"))
    parser.add_argument("--never-cleanable-register",
                        default=str(CANONICAL_CHECKOUT / "out" / "repo-hygiene-program"
                                    / "never-cleanable-register.md"))
    parser.add_argument("--lock-root", default=None,
                        help="Root holding out/worktree-reap.lock; defaults to --repository.")
    parser.add_argument("--receipts", default=None,
                        help="JSONL receipt path. Without it receipts are held in memory "
                             "and summarised on stdout.")
    parser.add_argument("--pr-evidence", default=None,
                        help="Frozen read-only pull-request evidence file. Absent means no "
                             "evidence, so no branch can be squash-deleted.")
    parser.add_argument("--adjudications", default=None,
                        help="Per-branch human adjudication records, keyed by branch name.")
    parser.add_argument("--isolation-roots", default=None,
                        help="Map of worktree path to its R09 registry root. Absent means "
                             "no entrant evidence, so no worktree is removable.")
    parser.add_argument("--cache-manifest", default=None,
                        help="Exact cache roots to consider, with expected digests. Absent "
                             "means no cache is a candidate.")
    parser.add_argument("--target-ref", default="refs/remotes/origin/main")
    parser.add_argument("--execute", action="store_true",
                        help="Refused unless CARR_REPO_HYGIENE_EFFECT_PACKET names a "
                             "separately reviewed live-effect packet AND the repository "
                             "is not the canonical checkout.")
    parser.add_argument("--json", action="store_true", help="Machine-readable report.")
    # WR-000040 AC-FRESH rides on this command rather than on scripts of its own.
    # It shares the program (R07 repo hygiene), it shares the canonical-checkout
    # subject, and — the deciding reason — a new executable script is a new SCAC
    # ingress row, while this command already holds one. See the module header of
    # lib/canonical_freshness.py for why a new row is not a small cost.
    #
    # It runs BEFORE and INSTEAD OF the census. The freshness modes take no
    # maintenance mutex because they remove nothing: the fast-forward's only
    # branch-moving command is `git merge --ff-only` against a tree with no
    # tracked modification, and the watchdog only reads. Waiting on the reaper's
    # lock would buy no safety and would turn a contended moment into a freshness
    # check that silently did not happen.
    parser.add_argument("--canonical-freshness", choices=("fast-forward", "watchdog"),
                        default=None,
                        help="WR-000040 AC-FRESH. Run the canonical fast-forward or the "
                             "independent dirty/staleness watchdog against --repository "
                             "and exit; the hygiene census is not run.")
    parser.add_argument("--max-age-hours", type=int, default=24,
                        help="Watchdog staleness bar. Exclusive: a HEAD exactly at the "
                             "limit is AT it, not past it, so a daily job that lands a "
                             "minute late does not page.")
    parser.add_argument("--no-page", action="store_true",
                        help="Watchdog only: skip the record-layer page. The nonzero exit "
                             "still stands, because that is the channel that cannot be "
                             "silenced.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fast-forward only: report what would move and move nothing.")
    return parser


def _load_module_by_path(name: str, module_path: Path, description: str):
    """Import a sibling module that is deliberately NOT an entrypoint.

    Two of this command's collaborators — R09's isolation module and WR-000040's
    freshness machinery — carry no shebang and no main guard on purpose, so that
    ops/scac-mutation-inventory.mjs does not read them as new SCAC ingress rows
    in a sealed inventory. The price of that choice is that neither has a package
    home and both must be loaded by path; this function is that loading, written
    once, so the two callers below differ only in WHICH module they name.

    Every path is resolved relative to THIS file rather than to the canonical
    checkout: a worktree must read its own copy rather than whatever a possibly
    stale canonical tree happens to hold. For the freshness module that is not a
    nicety — the staleness of that tree is the very thing it measures.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, module_path)
    if spec is None or spec.loader is None:
        raise JanitorRefusal(f"{description} unreadable: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_canonical_freshness(module_path: Path | None = None):
    """WR-000040's freshness machinery."""
    return _load_module_by_path(
        "canonical_freshness",
        module_path or (Path(__file__).resolve().parents[1] / "lib"
                        / "canonical_freshness.py"),
        "canonical freshness module")


def _load_r09_module(module_path: Path | None = None):
    """R09's worktree isolation module."""
    return _load_module_by_path(
        "worktree_runtime_isolation",
        module_path or (Path(__file__).resolve().parent / "room-bridge"
                        / "worktree_runtime_isolation.py"),
        "R09 isolation module")


class R09EntrantReader:
    """The real R09 posture read, bound to an operator-declared liveness reading.

    OWNER LIVENESS IS DECLARED, NEVER INFERRED. This command cannot tell whether
    the session behind an entrant key is alive, so it does not guess:

      registered (default)  an entrant that exists reads ACTIVE. The strongest
                            preserve, and true by construction — a registration
                            is present.
      unknown               owner liveness is explicitly unknowable in this
                            environment, so every entrant reads STALE_UNCERTAIN.

    Both preserve. Neither can ever report an entrant as gone, which is the only
    reading that could cost someone their worktree.
    """

    LIVENESS = ("registered", "unknown")

    def __init__(self, liveness: str = "registered", module_path: Path | None = None):
        if liveness not in self.LIVENESS:
            raise JanitorRefusal(f"unknown owner_liveness {liveness!r}; expected one of "
                                 f"{', '.join(self.LIVENESS)}")
        self.liveness = liveness
        self._module = _load_r09_module(module_path)

    def __call__(self, root: Path) -> Mapping[str, Any]:
        owner_alive = (lambda _owner: None) if self.liveness == "unknown" else None
        return self._module.read_lock_posture(Path(root), owner_alive=owner_alive)


class ReferenceCopyRefetchVerifier:
    """Reproducibility proved by an independently held reference copy.

    A cache is prunable only when its exact content can be recovered. This route
    proves that by measuring an operator-declared reference copy with the same
    content-addressed function and requiring an exact match. It EXECUTES NOTHING:
    the manifest's `refetch_command` is carried into the receipt as data so a
    human knows how to re-fetch, and is never run, never interpolated into a
    shell, and never treated as authority. No network is contacted.
    """

    def __init__(self, references: Mapping[str, str],
                 digest: Callable[[Path], str | None] = tree_digest):
        self.references = {real_path(key): real_path(value) for key, value in references.items()}
        self.digest = digest

    def __call__(self, candidate: CacheCandidate, observed: str) -> bool:
        reference = self.references.get(real_path(candidate.path))
        if not reference:
            return False
        cache_root = real_path(candidate.path)
        # A reference inside the cache would be destroyed with it, which proves
        # nothing about recoverability.
        if reference == cache_root or reference.startswith(cache_root + os.sep):
            return False
        return self.digest(Path(reference)) == observed


def _load_json(path: str | None, label: str) -> dict[str, Any]:
    if not path:
        return {}
    body = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise JanitorRefusal(f"{label} must be a JSON object: {path}")
    return body


def build_janitor(args: argparse.Namespace, *, mutex: MaintenanceMutex,
                  sink: ReceiptSink) -> RepoHygieneJanitor:
    repository = Path(args.repository)
    providers: PullRequestProvider = (
        FrozenPullRequestEvidence.from_file(Path(args.pr_evidence))
        if args.pr_evidence else NoPullRequestEvidence())

    isolation = _load_json(args.isolation_roots, "isolation roots")
    entrant_reader = (R09EntrantReader(isolation.get("owner_liveness", "registered"),
                                       Path(isolation["module_path"])
                                       if isolation.get("module_path") else None)
                      if isolation else None)

    # Reproducibility evidence is wired from the SAME manifest that names the
    # caches, so a candidate can actually reach the supported prunable case
    # instead of being permanently kept for missing proof.
    references = {str(entry["path"]): str(entry["reference_path"])
                  for entry in _load_json(args.cache_manifest, "cache manifest").get("caches", [])
                  if entry.get("reference_path")}

    return RepoHygieneJanitor(
        repository=repository,
        git=GitPort(repository),
        receipt_sink=sink,
        tail=load_settlement_tail(Path(args.tail_manifest)),
        register=load_never_cleanable_register(Path(args.never_cleanable_register)),
        pull_requests=providers,
        entrant_reader=entrant_reader,
        refetch_verifier=ReferenceCopyRefetchVerifier(references) if references else None,
        mutex=mutex,
        target_ref=args.target_ref,
        effect_packet=os.environ.get("CARR_REPO_HYGIENE_EFFECT_PACKET", ""),
    )


def run(args: argparse.Namespace, *, sink: ReceiptSink | None = None) -> tuple[int, dict[str, Any]]:
    """The whole command, as a callable, so a fixture can drive it end to end."""
    lock_root = Path(args.lock_root or args.repository)
    mutex = MaintenanceMutex(lock_root)
    if not mutex.acquire():
        return 3, {"mode": "refused", "reason": "maintenance_mutex_held_by_another_operation",
                   "lock": str(mutex.path), "actions": 0}
    try:
        receipts_path = Path(args.receipts) if args.receipts else None
        active_sink: ReceiptSink = sink or (JsonlReceiptSink(receipts_path)
                                            if receipts_path else MemoryReceiptSink())
        janitor = build_janitor(args, mutex=mutex, sink=active_sink)

        caches = [CacheCandidate(path=str(entry["path"]),
                                 expected_digest=entry.get("expected_digest"),
                                 refetch_command=entry.get("refetch_command"))
                  for entry in _load_json(args.cache_manifest, "cache manifest").get("caches", [])]
        plan = janitor.plan(
            branches=census_branches(janitor, _load_json(args.adjudications, "adjudications")),
            worktrees=census_worktrees(
                janitor,
                _load_json(args.isolation_roots, "isolation roots").get("worktrees", {})),
            caches=caches)
        receipts = janitor.apply(plan, execute=args.execute)

        by_result: dict[str, int] = {}
        for receipt in receipts:
            by_result[receipt["result"]] = by_result.get(receipt["result"], 0) + 1
        report = {
            "mode": "execute" if args.execute else "dry_run_default_off",
            "repository": str(Path(args.repository)),
            "snapshot_id": plan.snapshot_id,
            "pinned_target": plan.pinned_target,
            "planned": plan.counts(),
            "results": by_result,
            "reasons": sorted({row.reason for row in plan.rows}),
            "receipts": str(receipts_path) if receipts_path else "memory",
            "receipts_written": len(receipts),
            "non_durable_receipts": sum(1 for r in receipts if not r.get("receipt_durable", True)),
            "pull_request_evidence": args.pr_evidence or "none_supplied",
            "entrant_evidence": (args.isolation_roots or "none_supplied"),
            "cache_reproducibility_evidence": (args.cache_manifest or "none_supplied"),
        }
        return 0, report
    finally:
        mutex.release()


def run_canonical_freshness(args: argparse.Namespace, *, out=sys.stdout, err=sys.stderr) -> int:
    """AC-FRESH, kept whole: the library decides, this function only routes."""
    module = _load_canonical_freshness()
    return module.run(args.canonical_freshness, repository=args.repository,
                      max_age_hours=args.max_age_hours, page=not args.no_page,
                      dry_run=args.dry_run, out=out, err=err)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.canonical_freshness:
        try:
            return run_canonical_freshness(args)
        except JanitorRefusal as exc:
            print(json.dumps({"mode": "refused", "reason": str(exc)}, indent=2, sort_keys=True),
                  file=sys.stderr)
            return 2
    try:
        code, report = run(args)
    except JanitorRefusal as exc:
        print(json.dumps({"mode": "refused", "reason": str(exc)}, indent=2, sort_keys=True),
              file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
