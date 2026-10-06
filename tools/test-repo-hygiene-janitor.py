#!/usr/bin/env python3
"""Hermetic disposable-fixture acceptance tests for repo-hygiene-janitor.py.

EVERY MUTATION IN THIS FILE HAPPENS INSIDE A TEMPORARY DIRECTORY THIS FILE
CREATED. No test touches the canonical checkout, a real remote, a real user
branch, a registered worktree, a real cache, a provider, or a scheduler. The
"remote" is a second bare repository in the same temporary directory; the
pull-request evidence, clock, receipt sink, refetch verifier, and
worktree-removal door are fakes; the R09 entrant reader is the REAL module read
against a fixture registry root, because that interop is the thing worth proving.

THREE KINDS OF TEST, and the last two carry the weight.

  Behaviour tests assert the janitor does the right thing on a fixture.

  Guard-mutation tests then DISABLE the specific guard that produced the answer
  and assert the outcome flips. Without them, a survivor test proves only that a
  fixture named "dirty" survived — a fixture name is not proof.

  Race tests change the world BETWEEN the plan and the apply. These are the ones
  the first version of this suite lacked: it only ever asserted against static
  planned state, so three real deletion races passed review-free. A snapshot is
  not authority; the mutation boundary is.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ops"))
from git_env import fixture_env                                    # noqa: E402

ENV = fixture_env()


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


J = _load("repo_hygiene_janitor", "tools/repo-hygiene-janitor.py")
R09 = _load("worktree_runtime_isolation", "tools/room-bridge/worktree_runtime_isolation.py")

# THE SUITE OWNS ITS OWN INPUTS. R03's settlement tail and the never-cleanable
# register are OPERATIONAL evidence: they live under out/, they are deliberately
# absent from a clean checkout, and they must never be committed. A test that
# read them passed here and failed on a hosted runner for the only reason it
# could -- the files were not there.
#
# What this suite actually needs is the PARSER CONTRACT, not the operational
# rows, so it writes its own small manifest and register in the same grammar and
# asserts against those. The real evidence keeps its separate, already-reviewed
# bindings; fixture data is test input and is not a replacement for it.
TAIL_MANIFEST: Path
REGISTER: Path


def write_synthetic_inputs(root: Path) -> tuple[Path, Path]:
    """A settlement tail and never-cleanable register in the exact grammar the
    production parser reads, with a deliberately small, known shape.

    The rows below are invented. The GRAMMAR is not: the marker vocabulary, the
    forty-hex tip, the whitespace shape and the register's five-cell table row
    are copied from what the parser accepts, which is what makes a pass here
    mean the parser still reads the real files the same way.
    """
    root.mkdir(parents=True, exist_ok=True)
    def tip(seed: int) -> str:
        return hashlib.sha256(f"synthetic-tip-{seed}".encode()).hexdigest()[:40]

    rows = []
    for index, name in enumerate(SYNTHETIC_TAIL["closed_unmerged_pull_request"]):
        rows.append(f"# KEEP-CLOSED-UNMERGED  {tip(index)}  {name}")
    for index, name in enumerate(SYNTHETIC_TAIL["unmerged_without_pull_request"]):
        rows.append(f"# KEEP-NO-PR  {tip(100 + index)}  {name}")
    for index, name in enumerate(SYNTHETIC_TAIL["reused_branch_name"]):
        rows.append(f"# RETAIN  {tip(200 + index)}  {name}  |  merged PR head differs")
    for index, name in enumerate(SYNTHETIC_NON_TAIL["worktree_held"]):
        rows.append(f"# KEEP-WORKTREE  {tip(300 + index)}  {name}")
    for index, name in enumerate(SYNTHETIC_NON_TAIL["open_pull_request"]):
        rows.append(f"# KEEP-OPEN-PR  {tip(400 + index)}  {name}")
    for index, name in enumerate(SYNTHETIC_NON_TAIL["assurance_held"]):
        rows.append(f"# HOLD  {tip(500 + index)}  {name}")

    # Lines the grammar must IGNORE, mixed in so a parser that got sloppy about
    # any of them would change the counts asserted below.
    noise = [
        "# Synthetic settlement manifest -- fixture input, not operational evidence.",
        "#",
        f"# KEEP-NO-PR  {tip(1)[:20]}  short-sha-must-be-ignored",
        f"# UNKNOWN-MARKER  {tip(2)}  unknown-marker-must-be-ignored",
        f"KEEP-NO-PR  {tip(3)}  missing-comment-prefix-must-be-ignored",
        "",
    ]
    manifest = root / "settlement-manifest-branches.txt"
    manifest.write_text("\n".join(noise + rows) + "\n", encoding="utf-8")

    register = root / "never-cleanable-register.md"
    register.write_text(
        "| root | canonical realpath | link | class | protection | n |\n"
        "|---|---|---|---|---|---:|\n"
        f"| `worktrees` | {SYNTHETIC_NEVER_CLEAN} | not-symlink | worktree | "
        "NEVER CLEAN; a session's uncommitted work lives here | 1 |\n"
        f"| `cache` | {SYNTHETIC_EXACT_ROOT} | not-symlink | cache | "
        "Exact-root receipted janitor only; never repo-wide clean | 1 |\n"
        "| not-a-row | relative/path | ignored | ignored | ignored | 0 |\n",
        encoding="utf-8")
    return manifest, register


SYNTHETIC_TAIL = {
    "closed_unmerged_pull_request": ["fixture-closed-a", "fixture-closed-b", "fixture-closed-c"],
    "unmerged_without_pull_request": ["fixture-nopr-a", "fixture-nopr-b",
                                      "fixture-nopr-c", "fixture-nopr-d"],
    "reused_branch_name": ["fixture-reused-a", "fixture-reused-b"],
}
SYNTHETIC_NON_TAIL = {
    "worktree_held": ["fixture-held-a", "fixture-held-b"],
    "open_pull_request": ["fixture-open-a"],
    "assurance_held": ["fixture-assurance-a"],
}
SYNTHETIC_NEVER_CLEAN = "/fixture-root/never-clean-worktrees"
SYNTHETIC_EXACT_ROOT = "/fixture-root/eligible-cache"

FAILURES: list[str] = []


def check(condition: bool, label: str) -> None:
    if not condition:
        FAILURES.append(label)


def expect_raises(exc_type, fn, label: str) -> None:
    try:
        fn()
    except exc_type:
        return
    except Exception as exc:                                       # pragma: no cover
        FAILURES.append(f"{label} (raised {type(exc).__name__}: {exc})")
        return
    FAILURES.append(f"{label} (no refusal raised)")


# ── fakes ────────────────────────────────────────────────────────────────────


class FakeClock:
    def __init__(self, start: float = 1_760_000_000.0):
        self.now = start

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


class FakePullRequests:
    def __init__(self, table: Mapping[str, Sequence[Any]] | None = None):
        self.table = dict(table or {})

    def pull_requests_for_branch(self, branch: str) -> Sequence[Any]:
        return self.table.get(branch, ())


class FakeSink:
    """A sink whose availability and per-emit behaviour are independently
    controllable, because the race the review found lives exactly in the gap
    between `available()` returning True and `emit()` succeeding."""

    def __init__(self, available: bool = True, fail_emit_after: int | None = None):
        self._available = available
        self.fail_emit_after = fail_emit_after
        self.receipts: list[dict[str, Any]] = []
        self.emit_calls = 0

    def available(self) -> bool:
        return self._available

    def emit(self, receipt: Mapping[str, Any]) -> None:
        self.emit_calls += 1
        if self.fail_emit_after is not None and self.emit_calls > self.fail_emit_after:
            raise OSError("fixture sink failed after availability")
        self.receipts.append(dict(receipt))


class FakeRefetch:
    """Reproducibility proof that is genuinely re-evaluated: it answers for the
    digest it is handed, so changed bytes lose their proof automatically."""

    def __init__(self, reproducible: set[str]):
        self.reproducible, self.calls = set(reproducible), 0

    def __call__(self, candidate: Any, observed: str) -> bool:
        self.calls += 1
        return observed in self.reproducible


# ── fixtures ─────────────────────────────────────────────────────────────────


def git(repo: Path, *args: str, check_rc: bool = True) -> str:
    proc = subprocess.run(("git", "-C", str(repo), *args), capture_output=True,
                          text=True, env=ENV, check=False)
    if check_rc and proc.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout.strip()


def commit(repo: Path, name: str, body: str) -> str:
    (repo / name).write_text(body, encoding="utf-8")
    git(repo, "add", "--", name)
    git(repo, "commit", "-m", f"add {name}")
    return git(repo, "rev-parse", "HEAD").lower()


def build_repo(root: Path) -> tuple[Path, Path]:
    """A disposable work repository plus its own bare 'origin'."""
    origin, work = root / "origin.git", root / "work"
    origin.mkdir(parents=True)
    work.mkdir(parents=True)
    subprocess.run(("git", "init", "--bare", "-b", "main", str(origin)),
                   capture_output=True, env=ENV, check=True)
    subprocess.run(("git", "init", "-b", "main", str(work)),
                   capture_output=True, env=ENV, check=True)
    git(work, "config", "user.email", "fixture@example.invalid")
    git(work, "config", "user.name", "R07 Fixture")
    git(work, "remote", "add", "origin", str(origin))
    commit(work, "seed.txt", "seed")
    git(work, "push", "-u", "origin", "main")
    git(work, "fetch", "origin", "main")
    return work, origin


def nonvolatile_fixture_base() -> Path:
    """A writable base for fixture roots that the production volatile-path law
    does not refuse.

    THE SUITE'S OWN TEMPORARY DIRECTORY IS AN INPUT TO THE CODE UNDER TEST.
    On Linux `tempfile.gettempdir()` is /tmp, which the janitor refuses by
    design, so a positive removal fixture placed there is classified
    `volatile_private_tmp_path` and kept -- correct production behaviour that
    silently turned every positive case into a false negative on a hosted
    runner. macOS never showed it because its default temp root is
    /var/folders, which is not a refused prefix.

    The fix belongs here, not in the law: the law is right, the fixture was in
    the wrong place. A cache directory under the user's home is writable on
    both platforms and is outside every refused prefix. If no candidate
    qualifies this refuses rather than quietly falling back to /tmp, because a
    silent fallback is exactly the failure being corrected.
    """
    candidates = []
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        candidates.append(Path(xdg))
    candidates.append(Path.home() / ".cache")
    candidates.append(Path.home())
    for candidate in candidates:
        base = candidate / "carr-r07-janitor-fixtures"
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        if not os.access(base, os.W_OK):
            continue
        if J._is_volatile(base):
            continue
        if base == J.CANONICAL_CHECKOUT or J.CANONICAL_CHECKOUT in base.parents:
            continue
        return base
    raise RuntimeError(
        "no writable non-volatile fixture base found; refusing to place positive "
        f"removal fixtures under a refused prefix (tried {candidates})")


def argv_entry(receipt: Mapping[str, Any], index: int, label: str) -> list | None:
    """Index a receipt's argv only when there is something to index.

    check() COLLECTS failures rather than raising, so a failed assertion above
    does not make the line below safe: the first version indexed argv[-1] on a
    receipt whose argv was legitimately empty, and the IndexError replaced the
    real reason with a traceback. The whole receipt goes into the failure text,
    because the reason is the thing a reader actually needs.
    """
    argv = receipt.get("argv") or []
    if len(argv) > abs(index) - (1 if index < 0 else 0):
        return argv[index]
    check(False, f"{label}: expected a command in the receipt but argv is {argv!r}; "
                 f"result={receipt.get('result')!r} reason={receipt.get('reason')!r}")
    return None


def receipt_field(receipt: Mapping[str, Any], key: str, label: str):
    """Read a field that only a mutation-boundary receipt carries.

    backup refs, posture blocks and the resolved cache path are absent by design
    on a receipt that was kept or refused. Reading them with [] turns a correct
    refusal into a KeyError and hides the reason, which is the same defect the
    argv guard exists for.
    """
    if key in receipt:
        return receipt[key]
    check(False, f"{label}: receipt carries no {key!r}; result={receipt.get('result')!r} "
                 f"reason={receipt.get('reason')!r}")
    return None


def expect_result(receipt: Mapping[str, Any], result: str, label: str) -> bool:
    """Assert a receipt's outcome and report the whole receipt when it differs."""
    if receipt.get("result") == result:
        return True
    check(False, f"{label}: expected result {result!r} but saw "
                 f"{receipt.get('result')!r} for reason {receipt.get('reason')!r} "
                 f"(argv={receipt.get('argv')!r})")
    return False


_LOCK_ROOTS = iter(range(1, 1_000_000))


def held_mutex(root: Path) -> Any:
    """A real mutex, really acquired, on a lock root private to this janitor.

    plan() and apply() refuse without a held mutex, so every fixture janitor
    needs one. The roots are private because a single test may build several
    janitors over one fixture repository and the real lock is exclusive — which
    is the point. The SHARED-root semantics that actually matter (contention
    refuses, ownership is cleaned up, the command takes no action) are proven
    against one shared root in test_mutex_spans_plan_and_apply and
    test_cli_refuses_under_contention_and_takes_no_action.
    """
    lock_root = Path(root).parent / f"lockroot-{next(_LOCK_ROOTS)}"
    lock_root.mkdir(parents=True, exist_ok=True)
    mutex = J.MaintenanceMutex(lock_root)
    if not mutex.acquire():
        raise RuntimeError(f"fixture mutex unexpectedly contended at {mutex.path}")
    return mutex


def janitor(work: Path, *, prs=None, sink=None, clock=None, entrant_reader=None,
            remove_command=None, effect_packet="EFFECT-PACKET-FIXTURE",
            ttl=J.DEFAULT_WORKTREE_TTL_SECONDS, mutex=None, liveness=None,
            refetch=None) -> Any:
    return J.RepoHygieneJanitor(
        repository=work,
        git=J.GitPort(work, env=ENV),
        pull_requests=prs or FakePullRequests(),
        receipt_sink=sink or FakeSink(),
        tail=J.load_settlement_tail(TAIL_MANIFEST),
        register=J.load_never_cleanable_register(REGISTER),
        entrant_reader=entrant_reader,
        mutex=mutex if mutex is not None else held_mutex(work),
        clock=clock or FakeClock(),
        liveness_probe=liveness,
        refetch_verifier=refetch,
        worktree_ttl_seconds=ttl,
        worktree_remove_command=remove_command or ("true",),
        effect_packet=effect_packet,
    )


def cache_janitor(work: Path, cache: Path, *, sink=None, refetch=None,
                  never: set[str] | None = None) -> Any:
    register = J.NeverCleanableRegister(
        never_clean=frozenset(never or set()), exact_root_only=frozenset({str(cache)}))
    return J.RepoHygieneJanitor(
        repository=work, git=J.GitPort(work, env=ENV), pull_requests=FakePullRequests(),
        receipt_sink=sink or FakeSink(), tail=J.load_settlement_tail(TAIL_MANIFEST),
        register=register, mutex=held_mutex(work), clock=FakeClock(),
        refetch_verifier=refetch, effect_packet="EFFECT-PACKET-FIXTURE")


def add_worktree(work: Path, trees: Path, name: str) -> Path:
    path = trees / name
    git(work, "worktree", "add", "-b", f"wt-{name}", str(path), "main")
    return path


def fixture_remove_door(work: Path, trees: Path) -> tuple[str, ...]:
    """A fixture-local stand-in for `zsh bin/worktree.sh --remove`, so no test
    can ever address a canonical worktree. Production still names the real door."""
    door = trees / "fixture-remove.sh"
    door.write_text(f'#!/bin/sh\nexec git -C "{work}" worktree remove "$1"\n', encoding="utf-8")
    door.chmod(0o755)
    return ("sh", str(door))


def r09_root(root: Path, entrant: dict | None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "r09-registry.lock").write_text("", encoding="utf-8")
    (root / "r09-state.json").write_text(
        json.dumps({"allocations": {}, "entrant": entrant, "receipts": []}), encoding="utf-8")
    return root


# ── immutable inputs ─────────────────────────────────────────────────────────


def test_fixture_root_is_not_a_refused_path(root: Path) -> None:
    """The positive fixtures must live somewhere the production law allows.

    Without this, an environment whose temporary directory moves under a refused
    prefix turns every positive removal and prune case into a silent keep, and
    the suite reports green for the wrong reason. That is precisely what a
    hosted Linux runner did.
    """
    for path in (root, J.real_path(root)):
        check(not J._is_volatile(path),
              f"the fixture root must not be a refused volatile path: {path} "
              f"(refused prefixes are {J.VOLATILE_PREFIXES})")
    resolved = Path(J.real_path(root))
    check(resolved != J.CANONICAL_CHECKOUT and J.CANONICAL_CHECKOUT not in resolved.parents,
          f"the fixture root must be outside the canonical checkout: {resolved}")
    check(os.access(root, os.W_OK), f"the fixture root must be writable: {root}")
    # ...and the refused prefixes are still refused, which is what makes the
    # placement above a fix rather than a way around the law.
    for prefix in J.VOLATILE_PREFIXES:
        check(J._is_volatile(f"{prefix}/some-worktree"),
              f"{prefix} must remain a refused path")


def test_immutable_inputs_parse_to_their_known_shape() -> None:
    """The parser contract, over rows this suite wrote.

    THE MARKER DECIDES WHICH SIDE OF THE TAIL A ROW IS ON, and that split is the
    load-bearing fact: three markers are the human-adjudication tail, three are
    kept for other reasons and are not. Reading the operational file to check
    this proved nothing the grammar does not already say, and it made the suite
    unrunnable anywhere the file is absent.
    """
    tail = J.load_settlement_tail(TAIL_MANIFEST)
    in_tail = [row for row in tail.values() if row.in_tail]
    expected_tail = sum(len(names) for names in SYNTHETIC_TAIL.values())
    check(len(in_tail) == expected_tail, f"tail rows (saw {len(in_tail)})")
    by_reason: dict[str, int] = {}
    for row in in_tail:
        by_reason[row.reason] = by_reason.get(row.reason, 0) + 1
    for reason, names in SYNTHETIC_TAIL.items():
        check(by_reason.get(reason) == len(names),
              f"{reason}: {len(names)} tail rows (saw {by_reason.get(reason)})")
    non_tail = [row for row in tail.values() if not row.in_tail]
    check(len(non_tail) == sum(len(n) for n in SYNTHETIC_NON_TAIL.values()),
          f"non-tail kept rows (saw {len(non_tail)})")
    for reason, names in SYNTHETIC_NON_TAIL.items():
        check(sorted(r.name for r in non_tail if r.reason == reason) == sorted(names), reason)
    # Every parsed tip is a full forty-hex object id, and the ignored lines above
    # really were ignored rather than parsed into something.
    check(all(len(row.tip) == 40 and all(c in "0123456789abcdef" for c in row.tip)
              for row in tail.values()), "every parsed tip is a 40-hex object id")
    for ignored in ("short-sha-must-be-ignored", "unknown-marker-must-be-ignored",
                    "missing-comment-prefix-must-be-ignored"):
        check(ignored not in tail, f"the grammar ignores {ignored}")

    # A manifest with no parsable row is missing evidence, never an empty tail.
    empty = TAIL_MANIFEST.parent / "empty-manifest.txt"
    empty.write_text("# nothing parsable here\n", encoding="utf-8")
    expect_raises(J.JanitorRefusal, lambda: J.load_settlement_tail(empty),
                  "a manifest with no parsable row refuses")

    register = J.load_never_cleanable_register(REGISTER)
    check(SYNTHETIC_NEVER_CLEAN in register.never_clean, "the NEVER CLEAN root parsed")
    check(SYNTHETIC_EXACT_ROOT in register.exact_root_only, "the eligible root parsed")
    check(register.verdict(SYNTHETIC_NEVER_CLEAN + "/anything") == "never_cleanable_register",
          "a path under a NEVER CLEAN root is denied")
    check(register.verdict(SYNTHETIC_EXACT_ROOT) is None, "the exact eligible root is allowed")
    check(register.verdict(SYNTHETIC_EXACT_ROOT + "/child") == "cache_root_not_registered",
          "a descendant of an eligible root is not itself eligible")
    check(register.verdict("/somewhere/unregistered") == "cache_root_not_registered",
          "an unregistered cache root is denied")


def test_tail_is_never_a_deletion_allowlist(root: Path) -> None:
    """The load-bearing refusal: a real R03 tail branch, genuinely ancestry-
    merged into the pin, still survives because no human adjudicated it."""
    work, _ = build_repo(root / "tail")
    tail = J.load_settlement_tail(TAIL_MANIFEST)
    name = next(n for n, row in tail.items()
                if row.in_tail and "/" not in n and row.reason == "unmerged_without_pull_request")
    check(name in SYNTHETIC_TAIL["unmerged_without_pull_request"],
          "the branch under test really is a tail row")

    git(work, "checkout", "-b", name)
    tip = commit(work, "tail.txt", "tail work")
    git(work, "checkout", "main")
    git(work, "merge", "--no-ff", "-m", "merge tail", name)
    git(work, "push", "origin", "main")
    git(work, "fetch", "origin", "main")

    jan = janitor(work)
    pinned = jan.pin_target()
    check(jan._is_ancestor(tip, pinned), "fixture tail branch really is ancestry-merged")

    row = jan.classify_branch(J.BranchCandidate(name=name, tip=tip), pinned)
    check(row.action == "keep", "an unadjudicated tail branch is kept")
    check(row.reason == "tail_requires_human_adjudication",
          f"kept for the tail reason (saw {row.reason})")

    # GUARD MUTATION: supply the missing human adjudication for this exact tip
    # and the same branch becomes deletable, proving the survival was caused by
    # the adjudication guard and not by the fixture.
    adjudicated = J.BranchCandidate(name=name, tip=tip, adjudication={
        "adjudicated_by": "joe", "adjudicated_at": "2026-09-09", "decision": "eligible",
        "evidence_id": "fixture-adjudication-1", "tip": tip})
    check(jan.classify_branch(adjudicated, pinned).action == "delete_ancestry",
          "adjudicated tail branch becomes deletable")

    stale = J.BranchCandidate(name=name, tip=tip, adjudication={
        "adjudicated_by": "joe", "adjudicated_at": "2026-09-09", "decision": "eligible",
        "evidence_id": "fixture-adjudication-2", "tip": "0" * 40})
    check(jan.classify_branch(stale, pinned).reason == "tail_requires_human_adjudication",
          "adjudication bound to a stale tip does not unlock the branch")


# ── the mutex spans the whole decision (review finding 004) ──────────────────


def test_mutex_spans_plan_and_apply(root: Path) -> None:
    work, _ = build_repo(root / "mutex-span")
    unheld = J.MaintenanceMutex(work)
    jan = janitor(work, mutex=unheld)
    expect_raises(J.JanitorRefusal, lambda: jan.plan(),
                  "plan refuses while the maintenance mutex is not held")

    check(unheld.acquire(), "the janitor's own mutex acquires")
    plan = jan.plan()
    check(plan.rows == (), "an empty census plans nothing")

    # A competing operation cannot take the lock while this one holds it.
    competitor = J.MaintenanceMutex(work)
    check(not competitor.acquire(), "a competing operation is refused the live mutex")

    unheld.release()
    expect_raises(J.JanitorRefusal, lambda: jan.apply(plan),
                  "apply refuses once mutex ownership has been released")
    check(not unheld.path.exists(), "releasing the mutex removes the lock file")

    # A lock older than the existing two-hour threshold belongs to a dead
    # reaper and is reclaimed — the same rule hooks/worktree-self-plumb.py uses.
    live = J.MaintenanceMutex(work)
    check(live.acquire(), "reacquire after release")
    stale = J.MaintenanceMutex(work, clock=lambda: time.time() + J.LOCK_STALE_SECONDS + 60)
    check(stale.acquire(), "a stale lock is reclaimed")
    stale.release()


def test_cli_refuses_under_contention_and_takes_no_action(root: Path) -> None:
    work, _ = build_repo(root / "cli-contention")
    blocker = J.MaintenanceMutex(work)
    check(blocker.acquire(), "an unrelated operation holds the maintenance lock")
    args = J.build_parser().parse_args(cli_args(work))
    sink = FakeSink()
    code, report = J.run(args, sink=sink)
    check(code == 3, f"the command refuses under contention (saw exit {code})")
    check(report["reason"] == "maintenance_mutex_held_by_another_operation",
          f"and names the contention (saw {report.get('reason')})")
    check(report["actions"] == 0 and sink.receipts == [],
          "a refused acquisition performs no action and writes no receipt")
    blocker.release()

    code2, report2 = J.run(args, sink=sink)
    check(code2 == 0, "once released the same command runs")
    check(not blocker.path.exists(), "the command released the lock it took (ownership cleanup)")


# ── allowed case 1: ancestry ─────────────────────────────────────────────────


def test_ancestry_delete_with_verified_backup(root: Path) -> None:
    work, origin = build_repo(root / "ancestry")
    git(work, "checkout", "-b", "feat-ancestry")
    tip = commit(work, "a.txt", "ancestry")
    git(work, "checkout", "main")
    git(work, "merge", "--no-ff", "-m", "merge ancestry", "feat-ancestry")
    git(work, "push", "origin", "main")
    git(work, "fetch", "origin", "main")

    sink = FakeSink()
    jan = janitor(work, sink=sink)
    plan = jan.plan(branches=[J.BranchCandidate(name="feat-ancestry", tip=tip)])
    check(plan.rows[0].action == "delete_ancestry", f"planned ancestry deletion")

    dry = jan.apply(plan)
    check(dry[0]["result"] == "kept", "dry run keeps")
    check(dry[0]["reason"] == "dry_run_would_delete_ancestry", "dry run names what it would do")
    check(dry[0]["argv"] == [], "dry run issued no command at all")
    check(git(work, "rev-parse", "--verify", "refs/heads/feat-ancestry") == tip,
          "dry run left the branch in place")

    receipts = jan.apply(plan, execute=True, idempotency_prefix="exec")
    receipt = receipts[0]
    check(receipt["result"] == "deleted",
          f"branch deleted (saw {receipt['result']}: {receipt['reason']})")
    check(git(work, "rev-parse", "--verify", "refs/heads/feat-ancestry", check_rc=False) == "",
          "the branch is really gone from the fixture")

    backup_ref = "refs/backup/r07-janitor/feat-ancestry"
    check(receipt_field(receipt, "backup_ref", "ancestry delete") == backup_ref,
          "receipt names the backup ref")
    check(receipt_field(receipt, "backup_local_readback", "ancestry delete") == tip,
          "local backup readback is the exact tip")
    check(receipt_field(receipt, "backup_remote_readback", "ancestry delete") == tip,
          "remote backup readback is the exact tip")
    check(git(origin, "rev-parse", "--verify", backup_ref) == tip,
          "the backup really exists on the fixture remote")

    # Exact argv, asserted by CONTENT and ORDER rather than by index: the
    # re-authentication seam legitimately adds its own read commands, and an
    # index-pinned assertion would break on a change that is not a defect.
    argv, prefix = receipt["argv"], ["git", "-C", str(work)]
    expected_in_order = [
        prefix + ["rev-parse", "--verify", "refs/remotes/origin/main"],
        prefix + ["update-ref", backup_ref, tip],
        prefix + ["rev-parse", "--verify", backup_ref],
        prefix + ["push", "--atomic", "origin", f"refs/heads/feat-ancestry:{backup_ref}"],
        prefix + ["ls-remote", "--refs", "origin", backup_ref],
        prefix + ["branch", "-d", "feat-ancestry"],
    ]
    positions = [argv.index(item) if item in argv else -1 for item in expected_in_order]
    for item, position in zip(expected_in_order, positions):
        check(position >= 0, f"argv contains the exact command {item[3:]}")
    check(positions == sorted(positions) and -1 not in positions,
          f"the exact commands appear in law order (positions {positions})")
    check(argv[-1] == prefix + ["branch", "-d", "feat-ancestry"],
          f"the LAST command is the deletion itself: {argv[-1]}")

    for key in ("operation_id", "idempotency_key", "target_kind", "target_identity",
                "observed_preimage", "pinned_target", "started_at", "finished_at",
                "exit_code", "stdout_digest", "stderr_digest", "result", "reason",
                "receipt_durable"):
        check(key in receipt, f"receipt carries {key}")
    check(receipt["receipt_durable"] is True, "the receipt records that it persisted")
    intents = [r for r in sink.receipts if r["result"] == "intent"]
    check(len(intents) == 1 and intents[0]["reason"] == "pre_effect_intent_delete_ancestry",
          "a durable intent was recorded before the effect")


def test_ancestry_guard_mutation_is_caught(root: Path) -> None:
    work, _ = build_repo(root / "ancestry-mutation")
    git(work, "checkout", "-b", "feat-unmerged")
    tip = commit(work, "u.txt", "unmerged")
    git(work, "checkout", "main")

    jan = janitor(work)
    pinned = jan.pin_target()
    check(jan.classify_branch(J.BranchCandidate("feat-unmerged", tip), pinned).reason
          == "unmerged_without_pull_request", "unmerged branch survives with its own reason")

    mutated = janitor(work, mutex=J.MaintenanceMutex(work))
    mutated._is_ancestor = lambda tip_, pin_: True            # the guard, removed
    check(mutated.classify_branch(J.BranchCandidate("feat-unmerged", tip), pinned).action
          == "delete_ancestry",
          "with the ancestry guard removed the unmerged branch would be deleted")


# ── allowed case 2: squash ───────────────────────────────────────────────────


def squash_fixture(root: Path, head_oid: str | None = None, state: str = "merged",
                   base: str = "main"):
    work, _ = build_repo(root)
    git(work, "checkout", "-b", "feat-squash")
    tip = commit(work, "s.txt", "squash work")
    git(work, "checkout", "main")
    commit(work, "other.txt", "unrelated main commit")
    git(work, "push", "origin", "main")
    git(work, "fetch", "origin", "main")
    pr = J.PullRequest(number=42, state=state, base_ref=base,
                       head_oid=head_oid or tip, evidence_id="gh-pr-42")
    return work, tip, FakePullRequests({"feat-squash": [pr]})


def test_squash_delete_requires_exact_recorded_head(root: Path) -> None:
    work, tip, prs = squash_fixture(root / "squash-ok")
    jan = janitor(work, prs=prs)
    plan = jan.plan(branches=[J.BranchCandidate(name="feat-squash", tip=tip)])
    check(plan.rows[0].action == "delete_squash", "squash-merged PR at the exact tip plans a delete")

    receipt = jan.apply(plan, execute=True)[0]
    check(receipt["result"] == "deleted", f"squash branch deleted (saw {receipt['reason']})")
    if expect_result(receipt, "deleted", "squash delete"):
        check(argv_entry(receipt, -1, "squash delete")
              == ["git", "-C", str(work), "branch", "-D", "feat-squash"],
              f"forced deletion argv is exact: {receipt['argv'][-1:]!r}")
    check((receipt.get("evidence") or {}).get("pull_request", {}).get("evidence_id") == "gh-pr-42",
          f"evidence id recorded (evidence={receipt.get('evidence')!r}, "
          f"result={receipt.get('result')!r} reason={receipt.get('reason')!r})")

    for label, kwargs, expected in [
        ("head is not this tip", {"head_oid": "a" * 40}, "reused_branch_name"),
        ("merged into another base", {"base": "release"}, None),
        ("PR closed rather than merged", {"state": "closed"}, "closed_unmerged_pull_request"),
    ]:
        work_n, tip_n, prs_n = squash_fixture(root / f"squash-{abs(hash(label))}", **kwargs)
        row = janitor(work_n, prs=prs_n).plan(
            branches=[J.BranchCandidate(name="feat-squash", tip=tip_n)]).rows[0]
        check(row.action == "keep", f"squash survivor: {label} (saw {row.action})")
        if expected:
            check(row.reason == expected, f"squash survivor reason {expected} (saw {row.reason})")


def test_backup_failure_refuses_before_any_deletion(root: Path) -> None:
    work, tip, prs = squash_fixture(root / "backup-fail")
    jan = janitor(work, prs=prs)
    plan = jan.plan(branches=[J.BranchCandidate(name="feat-squash", tip=tip)])
    git(work, "remote", "set-url", "origin", str(root / "backup-fail" / "no-such-remote.git"))
    receipt = jan.apply(plan, execute=True)[0]
    check(receipt["result"] == "refused", f"refused without a verified backup")
    check(receipt["reason"] == "backup_remote_push_failed",
          f"refusal names the backup failure (saw {receipt['reason']})")
    check(git(work, "rev-parse", "--verify", "refs/heads/feat-squash", check_rc=False) == tip,
          "the branch survived the failed backup")
    check(not any(a[3:5] == ["branch", "-D"] for a in receipt["argv"]),
          "no deletion command was ever issued")


def test_tip_movement_between_plan_and_apply_refuses(root: Path) -> None:
    work, tip, prs = squash_fixture(root / "drift")
    jan = janitor(work, prs=prs)
    plan = jan.plan(branches=[J.BranchCandidate(name="feat-squash", tip=tip)])
    git(work, "checkout", "feat-squash")
    moved = commit(work, "s2.txt", "work arrived after the snapshot")
    git(work, "checkout", "main")

    receipt = jan.apply(plan, execute=True)[0]
    check(receipt["result"] == "refused", "a tip that moved refuses")
    check(receipt["reason"] == "branch_tip_moved_since_plan", f"named (saw {receipt['reason']})")
    check(git(work, "rev-parse", "--verify", "refs/heads/feat-squash") == moved,
          "the newly arrived work is intact")


def test_pinned_target_drift_refuses(root: Path) -> None:
    work, tip, prs = squash_fixture(root / "pin-drift")
    jan = janitor(work, prs=prs)
    plan = jan.plan(branches=[J.BranchCandidate(name="feat-squash", tip=tip)])
    git(work, "checkout", "main")
    commit(work, "main-moved.txt", "main advanced")
    git(work, "push", "origin", "main")
    git(work, "fetch", "origin", "main")
    receipt = jan.apply(plan, execute=True)[0]
    check(receipt["result"] == "refused" and receipt["reason"] == "pinned_target_drifted_since_plan",
          f"a moved pin refuses (saw {receipt['result']}/{receipt['reason']})")


def test_unavailable_fresh_target_refuses_the_plan(root: Path) -> None:
    work, _ = build_repo(root / "no-pin")
    git(work, "update-ref", "-d", "refs/remotes/origin/main")
    expect_raises(J.JanitorRefusal, lambda: janitor(work).plan(),
                  "an unreadable pinned target refuses the whole plan")


def test_branch_evidence_changes_between_plan_and_apply(root: Path) -> None:
    """The pull-request provider is a port, so its answer can change between the
    census and the mutation exactly as the R09 entrant reader's can. A PR that is
    reopened, or corrected to a different head, must preserve the branch."""
    base = root / "pr-races"

    class MutablePullRequests:
        def __init__(self, rows):
            self.rows = list(rows)

        def pull_requests_for_branch(self, branch):
            return tuple(self.rows) if branch == "feat-squash" else ()

    def fixture(name: str):
        work, _ = build_repo(base / name)
        git(work, "checkout", "-b", "feat-squash")
        tip = commit(work, "s.txt", "squash work")
        git(work, "checkout", "main")
        commit(work, "other.txt", "unrelated")
        git(work, "push", "origin", "main")
        git(work, "fetch", "origin", "main")
        prs = MutablePullRequests([J.PullRequest(42, "merged", "main", tip, "gh-pr-42")])
        jan = janitor(work, prs=prs)
        plan = jan.plan(branches=[J.BranchCandidate("feat-squash", tip)])
        check(plan.rows[0].action == "delete_squash", f"{name}: a squash delete was planned")
        return jan, plan, prs, work, tip

    # (a) The recorded head no longer matches the tip.
    jan, plan, prs, work, tip = fixture("head-corrected")
    prs.rows = [J.PullRequest(42, "merged", "main", "c" * 40, "gh-pr-42")]
    receipt = jan.apply(plan, execute=True, idempotency_prefix="head")[0]
    check(receipt["result"] == "refused"
          and receipt["reason"] == "branch_drift_at_apply:reused_branch_name",
          f"corrected PR head refuses (saw {receipt['result']}/{receipt['reason']})")
    check(git(work, "rev-parse", "--verify", "refs/heads/feat-squash", check_rc=False) == tip,
          "the branch survives a corrected pull-request head")

    # (b) The pull request is reopened after the census.
    jan_b, plan_b, prs_b, work_b, tip_b = fixture("reopened")
    prs_b.rows = [J.PullRequest(42, "merged", "main", tip_b, "gh-pr-42"),
                  J.PullRequest(43, "open", "main", tip_b, "gh-pr-43")]
    receipt_b = jan_b.apply(plan_b, execute=True, idempotency_prefix="reopen")[0]
    check(receipt_b["result"] == "refused"
          and receipt_b["reason"] == "branch_drift_at_apply:open_pull_request",
          f"a reopened PR refuses (saw {receipt_b['result']}/{receipt_b['reason']})")
    check(git(work_b, "rev-parse", "--verify", "refs/heads/feat-squash", check_rc=False) == tip_b,
          "the branch survives a reopened pull request")


def test_backup_remote_readback_must_match_exactly(root: Path) -> None:
    """A remote that answers with the wrong OID is not a verified backup. The
    push succeeded, so only the readback equality can catch this."""
    work, tip, prs = squash_fixture(root / "readback-mismatch")
    jan = janitor(work, prs=prs)
    plan = jan.plan(branches=[J.BranchCandidate("feat-squash", tip)])
    real_run = jan.git.run

    def lying_run(argv):
        result = real_run(argv)
        if argv[0] == "ls-remote":
            wrong = "d" * 40
            return J.GitResult(result.argv, 0, f"{wrong}\t{jan.backup_ref_for('feat-squash')}\n", "")
        return result

    jan.git.run = lying_run
    receipt = jan.apply(plan, execute=True)[0]
    check(receipt["result"] == "refused", f"a mismatched readback refuses (saw {receipt['result']})")
    check(receipt["reason"] == "backup_remote_readback_mismatch",
          f"and names the readback (saw {receipt['reason']})")
    check(git(work, "rev-parse", "--verify", "refs/heads/feat-squash", check_rc=False) == tip,
          "the branch survives an unverifiable backup")
    check(not any(a[3:5] == ["branch", "-D"] for a in receipt["argv"]),
          "no deletion command was issued")


def test_branch_survivor_matrix(root: Path) -> None:
    work, _ = build_repo(root / "survivors")
    git(work, "checkout", "-b", "remote-unmerged")
    remote_tip = commit(work, "r.txt", "remote work")
    git(work, "push", "origin", "remote-unmerged")
    git(work, "checkout", "main")

    open_pr = J.PullRequest(1, "open", "main", remote_tip, "gh-pr-1")
    jan = janitor(work, prs=FakePullRequests({"open-pr": [open_pr]}))
    pinned = jan.pin_target()

    cases = [
        (J.BranchCandidate("remote-unmerged", remote_tip, remote_ref_exists=True),
         "remote_unmerged"),
        (J.BranchCandidate("open-pr", remote_tip), "open_pull_request"),
        (J.BranchCandidate("held", remote_tip, worktree_held=True), "worktree_held"),
        (J.BranchCandidate("gone", None), "missing_evidence_unreadable_tip"),
        (J.BranchCandidate("no-pr", remote_tip), "unmerged_without_pull_request"),
        (J.BranchCandidate("main", remote_tip), "protected_target_branch"),
    ]
    for candidate, expected in cases:
        row = jan.classify_branch(candidate, pinned)
        check(row.action == "keep" and row.reason == expected,
              f"{candidate.name} survives as {expected} (saw {row.action}/{row.reason})")

    plan = jan.plan(branches=[cases[0][0]])
    receipt = jan.apply(plan, execute=True)[0]
    check(receipt["argv"] == [], "remote-unmerged survivor receipt records an empty argv")
    check(receipt["result"] == "kept" and receipt["reason"] == "remote_unmerged",
          "remote-unmerged survivor is receipted as kept for its named reason")
    check(git(work, "rev-parse", "--verify", "refs/heads/remote-unmerged") == remote_tip,
          "the remote-unmerged branch is untouched")

    without = jan.classify_branch(
        J.BranchCandidate("remote-unmerged", remote_tip, remote_ref_exists=False), pinned)
    check(without.reason == "unmerged_without_pull_request",
          "the remote-unmerged reason is caused by the remote-ref fact")


# ── worktrees, including the race the review found (finding 001/006) ─────────


def test_worktree_survivor_matrix_and_removal(root: Path) -> None:
    work, _ = build_repo(root / "worktrees")
    trees = root / "worktrees" / "trees"
    trees.mkdir(parents=True)
    stale, dirty, live, locked = (add_worktree(work, trees, n)
                                  for n in ("stale", "dirty", "live", "locked"))
    entrant_tree = add_worktree(work, trees, "entrant")
    uncertain_tree = add_worktree(work, trees, "uncertain")
    (dirty / "scratch.txt").write_text("uncommitted work", encoding="utf-8")
    git(work, "worktree", "lock", str(locked))

    alive_root = r09_root(root / "r09-alive", {"owner": "session-a|wt|sha"})
    empty_root = r09_root(root / "r09-empty", None)
    reader_alive = lambda p: R09.read_lock_posture(p, owner_alive=lambda _o: True)
    reader_unknown = lambda p: R09.read_lock_posture(p, owner_alive=lambda _o: None)

    old = J.DEFAULT_WORKTREE_TTL_SECONDS * 2
    jan = janitor(work, entrant_reader=reader_alive)
    survivors = [
        (J.WorktreeCandidate(str(dirty), True, dirty=True, idle_seconds=old), "dirty_worktree"),
        (J.WorktreeCandidate(str(live), True, dirty=False, idle_seconds=60.0,
                             isolation_root=str(empty_root)), "recently_live_worktree"),
        (J.WorktreeCandidate(str(locked), True, locked=True, dirty=False, idle_seconds=old),
         "locked_worktree"),
        (J.WorktreeCandidate(str(trees / "bare"), True, bare=True, dirty=False, idle_seconds=old),
         "bare_worktree"),
        (J.WorktreeCandidate(str(trees / "ghost"), False, dirty=False, idle_seconds=old),
         "unregistered_worktree"),
        (J.WorktreeCandidate("/private/tmp/scratch-tree", True, dirty=False, idle_seconds=old),
         "volatile_private_tmp_path"),
        (J.WorktreeCandidate(str(J.CANONICAL_CHECKOUT), True, dirty=False, idle_seconds=old),
         "canonical_tree"),
        (J.WorktreeCandidate(str(stale), True, dirty=False, idle_seconds=None,
                             isolation_root=str(empty_root)), "missing_evidence_unknown_liveness"),
        (J.WorktreeCandidate(str(entrant_tree), True, dirty=False, idle_seconds=old,
                             isolation_root=str(alive_root)), "active_r09_entrant"),
    ]
    for candidate, expected in survivors:
        row = jan.classify_worktree(candidate)
        check(row.action == "keep" and row.reason == expected,
              f"worktree {Path(candidate.path).name} survives as {expected} "
              f"(saw {row.action}/{row.reason})")
        for fact in ("registered", "bare", "locked", "dirty", "idle_seconds", "entrant"):
            check(fact in row.evidence, f"{expected} row carries the {fact} posture fact")

    uncertain = janitor(work, entrant_reader=reader_unknown)
    row = uncertain.classify_worktree(J.WorktreeCandidate(
        str(uncertain_tree), True, dirty=False, idle_seconds=old, isolation_root=str(alive_root)))
    check(row.reason == "uncertain_r09_entrant_liveness",
          f"uncertain entrant liveness preserves regardless of TTL (saw {row.reason})")

    # NOT ASKING R09 IS NOT R09 SAYING NOBODY IS THERE. A janitor with no reader,
    # and a worktree with no bound registry root, both have no entrant evidence.
    no_reader = janitor(work, entrant_reader=None)
    check(no_reader.classify_worktree(J.WorktreeCandidate(
        str(stale), True, dirty=False, idle_seconds=old,
        isolation_root=str(alive_root))).reason == "missing_evidence_entrant_unreadable",
        "a janitor with no entrant reader cannot remove a worktree")
    check(jan.classify_worktree(J.WorktreeCandidate(
        str(stale), True, dirty=False, idle_seconds=old,
        isolation_root=None)).reason == "missing_evidence_entrant_unreadable",
        "a worktree with no bound R09 registry root cannot be removed")

    broken = root / "r09-broken"
    broken.mkdir()
    (broken / "r09-registry.lock").write_text("", encoding="utf-8")
    (broken / "r09-state.json").write_text("{not json", encoding="utf-8")
    row = jan.classify_worktree(J.WorktreeCandidate(
        str(stale), True, dirty=False, idle_seconds=old, isolation_root=str(broken)))
    check(row.reason == "missing_evidence_entrant_unreadable",
          f"an unreadable R09 registry preserves (saw {row.reason})")

    # ALLOWED CASE: registered, clean, stale, no entrant -> removal through the
    # governed door, with a full fresh posture on the receipt.
    removing = janitor(work, entrant_reader=reader_alive,
                       remove_command=fixture_remove_door(work, trees),
                       liveness=lambda _p: old)
    plan = removing.plan(worktrees=[J.WorktreeCandidate(
        str(stale), True, dirty=False, idle_seconds=old, isolation_root=str(empty_root))])
    check(plan.rows[0].action == "remove_worktree",
          f"a stale clean unentered tree plans removal (saw {plan.rows[0].reason})")
    check(removing.apply(plan)[0]["result"] == "kept" and stale.exists(),
          "dry run leaves the worktree in place")

    receipt = removing.apply(plan, execute=True, idempotency_prefix="exec")[0]
    check(receipt["result"] == "deleted", f"worktree removed (saw {receipt['reason']})")
    check(not stale.exists(), "the fixture worktree is really gone")
    if expect_result(receipt, "deleted", "worktree removal"):
        check(argv_entry(receipt, -1, "worktree removal")
              == [*fixture_remove_door(work, trees), str(stale)],
              f"the governed removal argv is exact: {receipt['argv'][-1:]!r}")
    for label in ("fresh_posture", "planned_posture"):
        posture = receipt.get(label)
        if not isinstance(posture, dict):
            check(False, f"removal receipt carries no {label}; result="
                         f"{receipt.get('result')!r} reason={receipt.get('reason')!r}")
            continue
        for fact in ("registered", "bare", "locked", "dirty", "idle_seconds", "entrant"):
            check(fact in posture, f"removal receipt carries {label} {fact}")

    check(jan.classify_worktree(J.WorktreeCandidate(
        str(entrant_tree), True, dirty=False, idle_seconds=old,
        isolation_root=str(empty_root))).action == "remove_worktree",
        "with no entrant the same tree is removable — the entrant clause is what saves it")


def test_worktree_races_between_plan_and_apply(root: Path) -> None:
    """The review's finding 001. Each case plans a legitimate removal, then
    changes the world, and requires the mutation boundary to preserve the tree."""
    base = root / "wt-races"
    work, _ = build_repo(base)
    trees = base / "trees"
    trees.mkdir(parents=True)
    door = fixture_remove_door(work, trees)
    old = J.DEFAULT_WORKTREE_TTL_SECONDS * 2
    registry = r09_root(base / "registry", None)

    def plan_removal(path: Path, *, reader=None, liveness=None):
        reader = reader or (lambda _r: {"state": "none", "entrant": None})
        jan = janitor(work, entrant_reader=reader, remove_command=door,
                      liveness=liveness or (lambda _p: old))
        plan = jan.plan(worktrees=[J.WorktreeCandidate(
            str(path), True, dirty=False, idle_seconds=old, isolation_root=str(registry))])
        check(plan.rows[0].action == "remove_worktree",
              f"{path.name}: a removal was genuinely planned")
        return jan, plan

    # (a) An R09 entrant ARRIVES after the census.
    victim = add_worktree(work, trees, "entrant-arrives")
    posture: dict[str, Any] = {"state": "none", "entrant": None}
    calls: list[dict[str, Any]] = []

    def reader(_root: Path) -> dict[str, Any]:
        calls.append(dict(posture))
        return dict(posture)

    jan, plan = plan_removal(victim, reader=reader)
    posture.update(state="active", entrant={"owner": "arrived-after-the-census"})
    receipt = jan.apply(plan, execute=True, idempotency_prefix="entrant")[0]
    check(receipt["result"] == "refused",
          f"an entrant arriving after the plan preserves the tree (saw {receipt['result']})")
    check(receipt["reason"] == "worktree_drift_at_apply:active_r09_entrant",
          f"and the refusal names the entrant (saw {receipt['reason']})")
    check(victim.exists(), "the newly entered worktree still exists")
    check(len(calls) >= 2, f"the entrant reader ran again at the boundary (calls={len(calls)})")
    fresh = receipt.get("fresh_posture")
    check(isinstance(fresh, dict) and fresh.get("entrant", {}).get("state") == "active",
          f"the receipt records the fresh active entrant (fresh_posture={fresh!r}, "
          f"result={receipt.get('result')!r} reason={receipt.get('reason')!r})")

    # (b) Liveness becomes uncertain after the census.
    victim_b = add_worktree(work, trees, "entrant-uncertain")
    live_posture: dict[str, Any] = {"state": "none", "entrant": None}
    jan_b, plan_b = plan_removal(victim_b, reader=lambda _r: dict(live_posture))
    live_posture.update(state="stale_uncertain", entrant={"owner": "unknown-liveness"})
    receipt_b = jan_b.apply(plan_b, execute=True, idempotency_prefix="uncertain")[0]
    check(receipt_b["reason"] == "worktree_drift_at_apply:uncertain_r09_entrant_liveness",
          f"uncertain liveness after the plan preserves (saw {receipt_b['reason']})")
    check(victim_b.exists(), "the uncertain worktree still exists")

    # (c) The tree becomes DIRTY after the census.
    victim_c = add_worktree(work, trees, "dirtied")
    jan_c, plan_c = plan_removal(victim_c)
    (victim_c / "late.txt").write_text("work arrived after the snapshot", encoding="utf-8")
    receipt_c = jan_c.apply(plan_c, execute=True, idempotency_prefix="dirty")[0]
    check(receipt_c["reason"] == "worktree_drift_at_apply:dirty_worktree",
          f"a tree dirtied after the plan preserves (saw {receipt_c['reason']})")
    check((victim_c / "late.txt").exists(), "the late work is intact")

    # (d) The tree becomes LOCKED after the census.
    victim_d = add_worktree(work, trees, "locked-late")
    jan_d, plan_d = plan_removal(victim_d)
    git(work, "worktree", "lock", str(victim_d))
    receipt_d = jan_d.apply(plan_d, execute=True, idempotency_prefix="locked")[0]
    check(receipt_d["reason"] == "worktree_drift_at_apply:locked_worktree",
          f"a tree locked after the plan preserves (saw {receipt_d['reason']})")
    check(victim_d.exists(), "the locked worktree still exists")

    # (e) The tree is TOUCHED after the census, so it is live again. The census
    # row carries the stale age explicitly; the probe is what the boundary asks,
    # and it now answers "touched five seconds ago".
    victim_e = add_worktree(work, trees, "touched")
    jan_e, plan_e = plan_removal(victim_e, liveness=lambda _p: 5.0)
    receipt_e = jan_e.apply(plan_e, execute=True, idempotency_prefix="touched")[0]
    check(receipt_e["reason"] == "worktree_drift_at_apply:recently_live_worktree",
          f"a freshly touched tree preserves (saw {receipt_e['reason']})")
    check(victim_e.exists(), "the freshly touched worktree still exists")

    # (f) Registration is lost after the census.
    victim_f = add_worktree(work, trees, "deregistered")
    jan_f, plan_f = plan_removal(victim_f)
    git(work, "worktree", "remove", str(victim_f))
    receipt_f = jan_f.apply(plan_f, execute=True, idempotency_prefix="dereg")[0]
    check(receipt_f["reason"] == "worktree_drift_at_apply:unregistered_worktree",
          f"a deregistered path preserves (saw {receipt_f['reason']})")


# ── caches, including the byte race and the truthful argv (finding 002) ──────


def test_cache_matrix(root: Path) -> None:
    base = root / "caches"
    work, _ = build_repo(base)
    cache = base / "cache-root"
    cache.mkdir()
    (cache / "blob").write_text("reproducible bytes", encoding="utf-8")
    digest = J.tree_digest(cache)
    refetch = FakeRefetch({digest})
    jan = cache_janitor(work, cache, refetch=refetch, never={str(base / "credentials")})

    survivors = [
        (J.CacheCandidate(str(base / "credentials" / "token"), digest, ["refetch"]),
         "never_cleanable_register"),
        (J.CacheCandidate(str(base / "elsewhere"), digest, ["refetch"]),
         "cache_root_not_registered"),
        (J.CacheCandidate("/private/tmp/whatever", digest, ["refetch"]),
         "volatile_private_tmp_path"),
        (J.CacheCandidate(str(cache), None, ["refetch"]), "cache_reproducibility_missing"),
        (J.CacheCandidate(str(cache), "b" * 64, ["refetch"]), "cache_reproducibility_mismatch"),
        (J.CacheCandidate(str(cache), digest, None), "cache_refetch_proof_missing"),
    ]
    for candidate, expected in survivors:
        row = jan.classify_cache(candidate)
        check(row.action == "keep" and row.reason == expected,
              f"cache survives as {expected} (saw {row.action}/{row.reason})")

    # A candidate that is reproducible but whose proof is REFUSED still survives.
    refusing = cache_janitor(work, cache, refetch=FakeRefetch(set()))
    check(refusing.classify_cache(J.CacheCandidate(str(cache), digest, ["refetch"])).reason
          == "cache_refetch_proof_missing", "an unproven refetch preserves the cache")

    good = J.CacheCandidate(str(cache), digest, ["fetch", "--exact", digest])
    plan = jan.plan(caches=[good])
    check(plan.rows[0].action == "prune_cache", "a reproducible registered cache plans a prune")
    check(jan.apply(plan)[0]["result"] == "kept" and cache.exists(), "dry run leaves the cache")

    before = refetch.calls
    receipt = jan.apply(plan, execute=True, idempotency_prefix="exec")[0]
    check(receipt["result"] == "deleted" and not cache.exists(), "the fixture cache is pruned")
    check(refetch.calls > before, "the refetch proof was re-evaluated at the mutation boundary")
    if expect_result(receipt, "deleted", "cache prune"):
        check(argv_entry(receipt, -1, "cache prune")
              == [J.CACHE_REMOVE_PROGRAM, "-rf", "--", J.real_path(cache)],
              f"cache removal argv names the RESOLVED path it acted on: {receipt['argv'][-1:]!r}")
    check(receipt_field(receipt, "resolved_path", "cache prune") == J.real_path(cache),
          "the receipt identity matches the actual invocation")
    check((receipt.get("evidence") or {}).get("refetch_command") == ["fetch", "--exact", digest],
          f"the receipt says how to re-fetch what it removed (evidence="
          f"{receipt.get('evidence')!r}, result={receipt.get('result')!r} "
          f"reason={receipt.get('reason')!r})")


def test_cache_races_between_plan_and_apply(root: Path) -> None:
    """The review's finding 002: changed bytes were deleted under stale proof."""
    base = root / "cache-races"
    work, _ = build_repo(base)

    def fixture(name: str) -> tuple[Any, Any, Path, str]:
        cache = base / name
        cache.mkdir(parents=True)
        (cache / "blob").write_text("approved bytes", encoding="utf-8")
        digest = J.tree_digest(cache)
        jan = cache_janitor(work, cache, refetch=FakeRefetch({digest}))
        plan = jan.plan(caches=[J.CacheCandidate(str(cache), digest, ["fetch", digest])])
        check(plan.rows[0].action == "prune_cache", f"{name}: a prune was genuinely planned")
        return jan, plan, cache, digest

    # (a) The bytes change after the census.
    jan, plan, cache, _ = fixture("bytes-change")
    (cache / "blob").write_text("changed after the snapshot", encoding="utf-8")
    receipt = jan.apply(plan, execute=True, idempotency_prefix="bytes")[0]
    check(receipt["result"] == "refused",
          f"changed cache bytes refuse (saw {receipt['result']}/{receipt['reason']})")
    check(receipt["reason"].startswith("cache_drift_at_apply:"),
          f"and the refusal names the drift (saw {receipt['reason']})")
    check(cache.exists() and (cache / "blob").read_text() == "changed after the snapshot",
          "the changed cache is intact")

    # (b) A new file appears in the cache after the census.
    jan_b, plan_b, cache_b, _ = fixture("file-appears")
    (cache_b / "late.bin").write_text("new content", encoding="utf-8")
    receipt_b = jan_b.apply(plan_b, execute=True, idempotency_prefix="appear")[0]
    check(receipt_b["result"] == "refused" and cache_b.exists(),
          f"a new file in the cache refuses (saw {receipt_b['result']})")

    # (c) The path is swapped for a symlink after the census.
    jan_c, plan_c, cache_c, _ = fixture("symlink-swap")
    decoy = base / "decoy"
    decoy.mkdir()
    for item in cache_c.iterdir():
        item.rename(decoy / item.name)
    cache_c.rmdir()
    cache_c.symlink_to(decoy)
    receipt_c = jan_c.apply(plan_c, execute=True, idempotency_prefix="symlink")[0]
    check(receipt_c["result"] == "refused",
          f"a swapped symlink refuses (saw {receipt_c['result']}/{receipt_c['reason']})")
    check(decoy.exists() and (decoy / "blob").exists(), "the symlink target is untouched")

    # (d) The cache disappears after the census.
    jan_d, plan_d, cache_d, _ = fixture("vanishes")
    subprocess.run(("/bin/rm", "-rf", "--", str(cache_d)), check=True)
    receipt_d = jan_d.apply(plan_d, execute=True, idempotency_prefix="vanish")[0]
    check(receipt_d["result"] == "refused",
          f"a vanished cache refuses rather than reporting success (saw {receipt_d['result']})")


def test_cache_path_swapped_after_the_digest_read(root: Path) -> None:
    """The window BETWEEN measuring the bytes and removing them.

    The drift check above re-measures identity, but a swap that lands after that
    measurement would still be removed by a naive applicator. These fixtures
    change the path as a side effect of the digest read itself — the digest
    returned is the honest pre-swap one — so the only thing that can save the
    target is a check that reads the path again immediately before removal.
    """
    base = root / "cache-toctou"
    work, _ = build_repo(base)

    class SwapOnApplyDigest:
        """Returns the true digest, then changes the world behind it."""

        def __init__(self, cache: Path, swap):
            self.cache, self.swap, self.calls = cache, swap, 0

        def __call__(self, path: Path) -> str | None:
            digest = J.tree_digest(path)
            self.calls += 1
            if self.calls == 2:                 # 1 = census, 2 = mutation boundary
                self.swap()
            return digest

    def fixture(name: str, swap):
        cache = base / name
        cache.mkdir(parents=True)
        (cache / "blob").write_text("approved bytes", encoding="utf-8")
        digest = J.tree_digest(cache)
        probe = SwapOnApplyDigest(cache, swap)
        register = J.NeverCleanableRegister(frozenset(), frozenset({str(cache)}))
        jan = J.RepoHygieneJanitor(
            repository=work, git=J.GitPort(work, env=ENV), pull_requests=FakePullRequests(),
            receipt_sink=FakeSink(), tail=J.load_settlement_tail(TAIL_MANIFEST),
            register=register, mutex=held_mutex(work), clock=FakeClock(),
            cache_digest=probe, refetch_verifier=FakeRefetch({digest}),
            effect_packet="EFFECT-PACKET-FIXTURE")
        plan = jan.plan(caches=[J.CacheCandidate(str(cache), digest, ["fetch", digest])])
        check(plan.rows[0].action == "prune_cache", f"{name}: a prune was genuinely planned")
        return jan, plan, cache

    # (a) The directory becomes a symlink to real content after the measurement.
    decoy = base / "decoy-target"
    decoy.mkdir()
    (decoy / "precious.txt").write_text("someone else's bytes", encoding="utf-8")

    def to_symlink() -> None:
        subprocess.run(("/bin/rm", "-rf", "--", str(base / "swap-symlink")), check=True)
        (base / "swap-symlink").symlink_to(decoy)

    jan, plan, cache = fixture("swap-symlink", to_symlink)
    receipt = jan.apply(plan, execute=True, idempotency_prefix="toctou-symlink")[0]
    check(receipt["result"] == "refused",
          f"a symlink swapped in after the measurement refuses (saw {receipt['result']})")
    check(receipt["reason"] == "cache_path_became_symlink",
          f"and names the swap (saw {receipt['reason']})")
    check((decoy / "precious.txt").exists(), "the symlink target's content is untouched")

    # (b) The path vanishes after the measurement.
    def to_absent() -> None:
        subprocess.run(("/bin/rm", "-rf", "--", str(base / "swap-absent")), check=True)

    jan_b, plan_b, _ = fixture("swap-absent", to_absent)
    receipt_b = jan_b.apply(plan_b, execute=True, idempotency_prefix="toctou-absent")[0]
    check(receipt_b["result"] == "refused"
          and receipt_b["reason"] == "cache_path_disappeared_before_apply",
          f"a path that vanished after the measurement refuses "
          f"(saw {receipt_b['result']}/{receipt_b['reason']})")


def test_canonical_cache_cleanup_is_structurally_forbidden(root: Path) -> None:
    """The register's own law: never run ignored-files cleanup against canonical."""
    jan = J.RepoHygieneJanitor(
        repository=J.CANONICAL_CHECKOUT, git=J.GitPort(root, env=ENV),
        pull_requests=FakePullRequests(), receipt_sink=FakeSink(),
        tail=J.load_settlement_tail(TAIL_MANIFEST),
        register=J.load_never_cleanable_register(REGISTER),
        clock=FakeClock(), effect_packet="EFFECT-PACKET-FIXTURE")
    row = jan.classify_cache(J.CacheCandidate(str(J.CANONICAL_CHECKOUT / ".DS_Store"), "a" * 64))
    check(row.action == "keep" and row.reason == "canonical_cache_cleanup_forbidden",
          f"canonical cache cleanup is refused (saw {row.reason})")
    expect_raises(J.JanitorRefusal,
                  lambda: jan.apply(J.Plan("s", "0" * 40, "refs/x", 0.0, ()), execute=True),
                  "executing against the canonical checkout refuses outright")


# ── the receipt protocol across the mutation boundary (finding 003) ──────────


def test_receipt_sink_availability_is_checked_before_mutation(root: Path) -> None:
    work, tip, prs = squash_fixture(root / "sink")
    sink = FakeSink(available=False)
    jan = janitor(work, prs=prs, sink=sink)
    plan = jan.plan(branches=[J.BranchCandidate(name="feat-squash", tip=tip)])
    expect_raises(J.JanitorRefusal, lambda: jan.apply(plan, execute=True),
                  "an unavailable receipt sink refuses before any mutation")
    check(git(work, "rev-parse", "--verify", "refs/heads/feat-squash", check_rc=False) == tip,
          "nothing was deleted into an unavailable sink")
    check(sink.receipts == [], "no receipt was emitted")


def test_pre_effect_intent_failure_preserves_the_target(root: Path) -> None:
    """available() passes, then the very first emit fails. Nothing may be
    touched, because an effect whose intent cannot be recorded never starts."""
    base = root / "intent-fail"
    work, _ = build_repo(base)
    cache = base / "cache-root"
    cache.mkdir()
    (cache / "blob").write_text("bytes", encoding="utf-8")
    digest = J.tree_digest(cache)
    sink = FakeSink(fail_emit_after=0)
    jan = cache_janitor(work, cache, sink=sink, refetch=FakeRefetch({digest}))
    plan = jan.plan(caches=[J.CacheCandidate(str(cache), digest, ["fetch", digest])])

    receipts = jan.apply(plan, execute=True, idempotency_prefix="intent")
    check(receipts[0]["result"] == "refused",
          f"a non-durable intent refuses (saw {receipts[0]['result']})")
    check(receipts[0]["reason"] == "pre_effect_intent_not_durable",
          f"and names why (saw {receipts[0]['reason']})")
    check(receipts[0]["receipt_durable"] is False, "the receipt admits it did not persist")
    check(cache.exists(), "the cache was never touched")
    check(receipts[0]["argv"] == [], "no command was issued")


def test_post_effect_receipt_failure_is_unknown_not_success(root: Path) -> None:
    """available() passes, the intent persists, the effect happens, and THEN the
    sink dies. The target is gone, so the honest result is UNKNOWN."""
    base = root / "result-fail"
    work, _ = build_repo(base)
    cache = base / "cache-root"
    cache.mkdir()
    (cache / "blob").write_text("bytes", encoding="utf-8")
    digest = J.tree_digest(cache)
    sink = FakeSink(fail_emit_after=1)          # intent lands; the result does not
    jan = cache_janitor(work, cache, sink=sink, refetch=FakeRefetch({digest}))
    plan = jan.plan(caches=[J.CacheCandidate(str(cache), digest, ["fetch", digest])])

    receipts = jan.apply(plan, execute=True, idempotency_prefix="result")
    receipt = receipts[0]
    check(not cache.exists(), "the effect really happened")
    check(receipt["result"] == "unknown",
          f"a lost result receipt is UNKNOWN, never deleted (saw {receipt['result']})")
    check(receipt["reason"] == "result_receipt_not_durable_after_effect_no_automatic_retry",
          f"and names no automatic retry (saw {receipt['reason']})")
    check(receipt["receipt_durable"] is False, "the receipt does not claim to have persisted")
    check("sink_error" in receipt, "the sink failure is carried as evidence")
    check(len(sink.receipts) == 1 and sink.receipts[0]["result"] == "intent",
          "only the pre-effect intent survived in the sink")

    replay = jan.apply(plan, execute=True, idempotency_prefix="result")
    check(replay[0]["operation_id"] == receipt["operation_id"],
          "a resubmit dedupes against the recorded unknown rather than repeating the effect")


def test_execute_without_effect_packet_refuses(root: Path) -> None:
    work, tip, prs = squash_fixture(root / "no-packet")
    jan = janitor(work, prs=prs, effect_packet=None)
    plan = jan.plan(branches=[J.BranchCandidate(name="feat-squash", tip=tip)])
    expect_raises(J.JanitorRefusal, lambda: jan.apply(plan, execute=True),
                  "execute without a reviewed live-effect packet refuses")
    check(git(work, "rev-parse", "--verify", "refs/heads/feat-squash", check_rc=False) == tip,
          "the branch survives an unauthorised execute")
    check(jan.apply(plan)[0]["result"] == "kept", "the same plan still dry-runs")


def test_idempotent_replay_does_not_remutate(root: Path) -> None:
    work, tip, prs = squash_fixture(root / "replay")
    jan = janitor(work, prs=prs)
    plan = jan.plan(branches=[J.BranchCandidate(name="feat-squash", tip=tip)])
    first = jan.apply(plan, execute=True, idempotency_prefix="run-1")
    check(first[0]["result"] == "deleted", "first apply deletes")

    replay = jan.apply(plan, execute=True, idempotency_prefix="run-1")
    check(replay[0]["operation_id"] == first[0]["operation_id"],
          "a replay under the same idempotency key returns the recorded receipt")

    fresh = jan.apply(plan, execute=True, idempotency_prefix="run-2")
    check(fresh[0]["result"] == "refused"
          and fresh[0]["reason"] == "branch_disappeared_before_apply",
          f"a new operation over deleted state refuses (saw {fresh[0]['reason']})")


def test_partial_operation_is_unknown_and_never_retried(root: Path) -> None:
    work, tip, prs = squash_fixture(root / "partial")
    jan = janitor(work, prs=prs)
    plan = jan.plan(branches=[J.BranchCandidate(name="feat-squash", tip=tip)])
    real_run = jan.git.run

    def lying_run(argv):
        if tuple(argv[:2]) == ("branch", "-D"):
            return J.GitResult(("git", "-C", str(work), *argv), 0, "", "")
        return real_run(argv)

    jan.git.run = lying_run
    receipt = jan.apply(plan, execute=True)[0]
    check(receipt["result"] == "unknown", f"the disagreement is UNKNOWN (saw {receipt['result']})")
    check(receipt["reason"] == "partial_deletion_state_unknown_no_automatic_retry",
          f"and it names no automatic retry (saw {receipt['reason']})")
    check(receipt_field(receipt, "backup_local_readback", "partial deletion") == tip,
          "the receipt still carries the backup a human needs to recover from")
    check(git(work, "rev-parse", "--verify", "refs/heads/feat-squash") == tip,
          "the branch is in fact still present")


# ── the command surface, end to end (finding 005) ───────────────────────────


def cli_args(work: Path, *extra: str) -> list[str]:
    return ["--repository", str(work), "--tail-manifest", str(TAIL_MANIFEST),
            "--never-cleanable-register", str(REGISTER), *extra]


def test_cli_plans_a_real_census_and_defaults_to_dry_run(root: Path) -> None:
    base = root / "cli-plan"
    work, _ = build_repo(base)
    git(work, "checkout", "-b", "feat-merged")
    merged_tip = commit(work, "m.txt", "merged work")
    git(work, "checkout", "main")
    git(work, "merge", "--no-ff", "-m", "merge", "feat-merged")
    git(work, "push", "origin", "main")
    git(work, "fetch", "origin", "main")
    git(work, "checkout", "-b", "feat-open")
    open_tip = commit(work, "o.txt", "open work")
    git(work, "checkout", "main")

    receipts_path = base / "receipts.jsonl"
    args = J.build_parser().parse_args(cli_args(work, "--receipts", str(receipts_path)))
    code, report = J.run(args)
    check(code == 0, f"the command completes (saw {code})")
    check(report["mode"] == "dry_run_default_off", f"default is dry run (saw {report['mode']})")
    check(report["planned"].get("delete_ancestry") == 1,
          f"the census really found the merged branch (saw {report['planned']})")
    check("protected_target_branch" in report["reasons"],
          f"main is protected by name (saw {report['reasons']})")
    check(git(work, "rev-parse", "--verify", "refs/heads/feat-merged") == merged_tip,
          "the dry run deleted nothing")
    check(git(work, "rev-parse", "--verify", "refs/heads/feat-open") == open_tip,
          "the unmerged branch is untouched")

    check(receipts_path.exists(), "the --receipts file was actually written")
    rows = [json.loads(line) for line in receipts_path.read_text().splitlines() if line]
    check(len(rows) == report["receipts_written"],
          f"every receipt is queryable on disk ({len(rows)} rows)")
    check(all("idempotency_key" in row and "result" in row for row in rows),
          "each persisted receipt carries its contract fields")
    check(any(row["target_identity"] == "feat-merged" for row in rows),
          "the merged branch has its own receipt")
    check(report["pull_request_evidence"] == "none_supplied",
          "with no evidence file the command says so rather than implying provider access")


def test_cli_squash_needs_frozen_evidence_and_executes_only_when_bound(root: Path) -> None:
    base = root / "cli-exec"
    work, _ = build_repo(base)
    git(work, "checkout", "-b", "feat-squash")
    tip = commit(work, "s.txt", "squash work")
    git(work, "checkout", "main")
    commit(work, "other.txt", "unrelated")
    git(work, "push", "origin", "main")
    git(work, "fetch", "origin", "main")

    # Without evidence, no squash branch can be planned for deletion at all.
    plain = J.build_parser().parse_args(cli_args(work))
    _, report = J.run(plain)
    check(report["planned"].get("delete_squash") is None,
          f"no provider evidence means no squash deletion (saw {report['planned']})")

    evidence = base / "pr-evidence.json"
    evidence.write_text(json.dumps({"feat-squash": [
        {"number": 7, "state": "merged", "base_ref": "main", "head_oid": tip,
         "evidence_id": "frozen-pr-7"}]}), encoding="utf-8")
    receipts_path = base / "receipts.jsonl"
    bound = cli_args(work, "--pr-evidence", str(evidence), "--receipts", str(receipts_path))

    args = J.build_parser().parse_args(bound)
    _, dry = J.run(args)
    check(dry["planned"].get("delete_squash") == 1, "frozen evidence makes the branch eligible")
    check(git(work, "rev-parse", "--verify", "refs/heads/feat-squash") == tip,
          "the dry run still deleted nothing")

    saved = os.environ.pop("CARR_REPO_HYGIENE_EFFECT_PACKET", None)
    try:
        check(J.main(bound + ["--execute"]) == 2,
              "--execute without a reviewed live-effect packet is refused")
        check(git(work, "rev-parse", "--verify", "refs/heads/feat-squash") == tip,
              "the refused execute deleted nothing")

        os.environ["CARR_REPO_HYGIENE_EFFECT_PACKET"] = "FIXTURE-EFFECT-PACKET"
        code, report = J.run(J.build_parser().parse_args(bound + ["--execute"]))
        check(code == 0 and report["mode"] == "execute", "a bound execute runs")
        check(report["results"].get("deleted") == 1,
              f"exactly the eligible branch was deleted (saw {report['results']})")
        check(git(work, "rev-parse", "--verify", "refs/heads/feat-squash",
                  check_rc=False) == "", "the branch is gone in the owned fixture")
        check(git(work, "rev-parse", "--verify",
                  "refs/backup/r07-janitor/feat-squash") == tip, "its backup ref exists")
        rows = [json.loads(line) for line in receipts_path.read_text().splitlines() if line]
        deleted = [row for row in rows if row["result"] == "deleted"]
        check(len(deleted) == 1, "one deletion receipt landed on disk")
        if deleted and expect_result(deleted[0], "deleted", "persisted CLI receipt"):
            last = argv_entry(deleted[0], -1, "persisted CLI receipt")
            check(last == ["git", "-C", str(work), "branch", "-D", "feat-squash"],
                  f"the persisted receipt carries the real argv: {last!r}")
    finally:
        os.environ.pop("CARR_REPO_HYGIENE_EFFECT_PACKET", None)
        if saved is not None:
            os.environ["CARR_REPO_HYGIENE_EFFECT_PACKET"] = saved


def test_public_api_refuses_without_a_bound_mutex(root: Path) -> None:
    """Finding A. An absent mutex is not "no mutex needed" — it is a caller that
    has not proven exclusivity, and it does not get to act."""
    base = root / "unlocked"
    work, _ = build_repo(base)
    cache = base / "cache-root"
    cache.mkdir()
    (cache / "blob").write_text("bytes", encoding="utf-8")
    digest = J.tree_digest(cache)

    unlocked = J.RepoHygieneJanitor(
        repository=work, git=J.GitPort(work, env=ENV), receipt_sink=J.MemoryReceiptSink(),
        tail=J.load_settlement_tail(TAIL_MANIFEST),
        register=J.NeverCleanableRegister(frozenset(), frozenset({str(cache)})),
        refetch_verifier=FakeRefetch({digest}), effect_packet="EFFECT-PACKET-FIXTURE",
        mutex=None)
    expect_raises(J.JanitorRefusal, lambda: unlocked.plan(),
                  "the public planner refuses with no mutex bound")
    check(cache.exists(), "nothing was touched by the unlocked planner")

    # And a plan built under a real lock cannot be applied by an unlocked caller.
    locked = cache_janitor(work, cache, refetch=FakeRefetch({digest}))
    plan = locked.plan(caches=[J.CacheCandidate(str(cache), digest, ["fetch", digest])])
    check(plan.rows[0].action == "prune_cache", "a genuine prune was planned under the lock")
    unlocked._seen_keys.clear()
    expect_raises(J.JanitorRefusal, lambda: unlocked.apply(plan, execute=True),
                  "the public applicator refuses with no mutex bound")
    check(cache.exists(), "the cache survives an unlocked apply")


def test_open_pull_request_after_an_ancestry_plan_preserves(root: Path) -> None:
    """Finding B. The branch really is merged into the pin, so the ancestry law
    is satisfied — and an open pull request that appeared after the census still
    preserves it, because every survivor class is re-asked on every path."""
    base = root / "ancestry-open-pr"
    work, _ = build_repo(base)
    git(work, "checkout", "-b", "feat-ancestry")
    tip = commit(work, "a.txt", "ancestry work")
    git(work, "checkout", "main")
    git(work, "merge", "--no-ff", "-m", "merge", "feat-ancestry")
    git(work, "push", "origin", "main")
    git(work, "fetch", "origin", "main")

    class MutablePR:
        def __init__(self):
            self.rows: list[Any] = []

        def pull_requests_for_branch(self, branch):
            return tuple(self.rows) if branch == "feat-ancestry" else ()

    prs = MutablePR()
    jan = janitor(work, prs=prs)
    plan = jan.plan(branches=[J.BranchCandidate("feat-ancestry", tip)])
    check(plan.rows[0].action == "delete_ancestry",
          f"an ancestry deletion was genuinely planned (saw {plan.rows[0].reason})")

    prs.rows = [J.PullRequest(99, "open", "main", tip, "opened-after-the-census")]
    receipt = jan.apply(plan, execute=True, idempotency_prefix="open-after")[0]
    check(receipt["result"] == "refused",
          f"an open PR after an ancestry plan preserves (saw {receipt['result']})")
    check(receipt["reason"] == "branch_drift_at_apply:open_pull_request",
          f"and names the open pull request (saw {receipt['reason']})")
    check(git(work, "rev-parse", "--verify", "refs/heads/feat-ancestry",
              check_rc=False) == tip, "the branch with the open pull request is intact")

    # Unavailable evidence preserves too: it is not the same as no pull request.
    class BrokenProvider:
        def pull_requests_for_branch(self, branch):
            raise OSError("provider unavailable")

    broken = janitor(work, prs=BrokenProvider())
    row = broken.classify_branch(J.BranchCandidate("feat-ancestry", tip), broken.pin_target())
    check(row.action == "keep"
          and row.reason == "missing_evidence_pull_request_provider_unavailable",
          f"an unavailable provider preserves (saw {row.action}/{row.reason})")

    # The genuine allowed case still works when the evidence really is clean.
    prs.rows = []
    jan2 = janitor(work, prs=prs)
    plan2 = jan2.plan(branches=[J.BranchCandidate("feat-ancestry", tip)])
    receipt2 = jan2.apply(plan2, execute=True, idempotency_prefix="clean-ancestry")[0]
    check(receipt2["result"] == "deleted",
          f"a genuinely clean ancestry deletion still succeeds (saw {receipt2['reason']})")


def test_never_clean_root_cannot_be_reached_through_an_alias(root: Path) -> None:
    """Finding C. `<eligible-root>/../credentials` reads like an eligible path and
    IS a never-clean root. Identity is the resolved real path, on both sides."""
    base = root / "alias"
    work, _ = build_repo(base)
    exact = base / "cache-root"
    exact.mkdir()
    (exact / "blob").write_text("cache bytes", encoding="utf-8")
    never = base / "credentials"                      # a FIXTURE directory, not real secrets
    never.mkdir()
    (never / "token").write_text("fixture-not-a-real-credential", encoding="utf-8")

    register = J.NeverCleanableRegister(frozenset({str(never)}), frozenset({str(exact)}))
    jan = J.RepoHygieneJanitor(
        repository=work, git=J.GitPort(work, env=ENV), receipt_sink=FakeSink(),
        tail=J.load_settlement_tail(TAIL_MANIFEST), register=register,
        mutex=held_mutex(work), clock=FakeClock(),
        refetch_verifier=lambda candidate, observed: True,
        effect_packet="EFFECT-PACKET-FIXTURE")

    alias = str(exact / ".." / "credentials")
    digest = J.tree_digest(Path(alias))
    row = jan.classify_cache(J.CacheCandidate(alias, digest, ["fetch"]))
    check(row.action == "keep",
          f"a dotdot alias of a never-clean root is kept (saw {row.action}/{row.reason})")
    check(row.reason == "cache_path_not_normalized",
          f"and the un-normalized identity is named (saw {row.reason})")

    # A fully-resolved spelling of the same never-clean root is denied by the
    # register itself, which is the check the alias was routing around.
    denied = jan.classify_cache(J.CacheCandidate(str(never), digest, ["fetch"]))
    check(denied.reason == "never_cleanable_register",
          f"the resolved never-clean root is denied (saw {denied.reason})")

    # A symlink sitting inside the eligible root, pointing at the never-clean
    # root, resolves to the denied path and is denied.
    link = exact / "sneaky-link"
    link.symlink_to(never)
    linked = jan.classify_cache(J.CacheCandidate(str(link), digest, ["fetch"]))
    check(linked.reason == "never_cleanable_register",
          f"a symlink into a never-clean root is denied (saw {linked.reason})")

    # Eligibility is the EXACT resolved root, never an arbitrary descendant.
    descendant = jan.classify_cache(J.CacheCandidate(str(exact / "blob"), digest, ["fetch"]))
    check(descendant.reason == "cache_root_not_registered",
          f"a descendant of an eligible root is not itself eligible (saw {descendant.reason})")

    # A trailing separator and a relative spelling are the same identity / not one.
    check(jan.classify_cache(J.CacheCandidate(str(exact) + "/", J.tree_digest(exact),
                                              ["fetch"])).action == "prune_cache",
          "a trailing separator is the same eligible identity")
    check(jan.classify_cache(J.CacheCandidate("cache-root", digest, ["fetch"])).reason
          == "cache_path_not_normalized", "a relative path is not an identity")

    check(never.exists() and (never / "token").exists(),
          "the never-clean fixture root was never touched")

    # PLAN/APPLY IDENTITY DRIFT: an eligible root that becomes a symlink to the
    # never-clean root between census and mutation must refuse.
    swap = base / "swapped-root"
    swap.mkdir()
    (swap / "blob").write_text("cache bytes", encoding="utf-8")
    register2 = J.NeverCleanableRegister(frozenset({str(never)}), frozenset({str(swap)}))
    jan2 = J.RepoHygieneJanitor(
        repository=work, git=J.GitPort(work, env=ENV), receipt_sink=FakeSink(),
        tail=J.load_settlement_tail(TAIL_MANIFEST), register=register2,
        mutex=held_mutex(work), clock=FakeClock(),
        refetch_verifier=lambda candidate, observed: True,
        effect_packet="EFFECT-PACKET-FIXTURE")
    plan = jan2.plan(caches=[J.CacheCandidate(str(swap), J.tree_digest(swap), ["fetch"])])
    check(plan.rows[0].action == "prune_cache", "a genuine prune was planned")
    subprocess.run(("/bin/rm", "-rf", "--", str(swap)), check=True)
    swap.symlink_to(never)
    receipt = jan2.apply(plan, execute=True, idempotency_prefix="alias-drift")[0]
    check(receipt["result"] == "refused",
          f"a root that became an alias of a never-clean root refuses (saw {receipt['result']})")
    check(never.exists() and (never / "token").exists(),
          "the never-clean fixture root survived the identity swap")


def fixture_register(path: Path, eligible: Path, never: Path) -> Path:
    """A register file in the real format, naming only fixture roots."""
    path.write_text(
        "| root | canonical realpath | link | class | protection | n |\n"
        "|---|---|---|---|---|---:|\n"
        f"| `cache` | {J.real_path(eligible)} | not-symlink | cache | "
        "Exact-root receipted janitor only; never repo-wide clean | 1 |\n"
        f"| `creds` | {J.real_path(never)} | not-symlink | credential-risk | "
        "NEVER CLEAN; contents not read | 1 |\n", encoding="utf-8")
    return path


def test_cli_entrant_evidence_end_to_end(root: Path) -> None:
    """Finding D. The command must consult the REAL R09 posture reader, and a
    missing mapping must refuse rather than read as 'nobody is there'."""
    base = root / "cli-entrant"
    work, _ = build_repo(base)
    trees = base / "trees"
    trees.mkdir(parents=True)
    tree = add_worktree(work, trees, "entered")
    registry = r09_root(base / "registry", {"owner": "live-session|wt|sha"})

    def reason_for(extra: Sequence[str]) -> str:
        args = J.build_parser().parse_args(cli_args(work, *extra))
        code, report = J.run(args, sink=FakeSink())
        check(code == 0, f"the command completed (saw {code})")
        return " ".join(report["reasons"])

    # (a) No mapping at all: no entrant evidence, so the tree is not removable.
    check("missing_evidence_entrant_unreadable" in reason_for([]),
          "with no isolation-root map the worktree is preserved for missing evidence")

    # (b) A real mapping, default liveness: the registered entrant reads active.
    mapping = base / "isolation.json"
    mapping.write_text(json.dumps({"worktrees": {str(tree): str(registry)}}), encoding="utf-8")
    check("active_r09_entrant" in reason_for(["--isolation-roots", str(mapping)]),
          "a mapped registry with an entrant preserves the worktree as active")

    # (c) The same mapping with owner liveness declared unknowable.
    unknown = base / "isolation-unknown.json"
    unknown.write_text(json.dumps({"worktrees": {str(tree): str(registry)},
                                   "owner_liveness": "unknown"}), encoding="utf-8")
    check("uncertain_r09_entrant_liveness" in reason_for(["--isolation-roots", str(unknown)]),
          "declared-unknown owner liveness preserves the worktree as uncertain")

    check(tree.exists(), "the entered worktree survived every command run")


def test_cli_cache_reproducibility_end_to_end(root: Path) -> None:
    """Finding E. The supported reproducible-cache case must be reachable from
    the real command, and its proof must be a real re-measurement."""
    base = root / "cli-cache"
    work, _ = build_repo(base)
    cache = base / "cache-root"
    cache.mkdir()
    (cache / "blob").write_text("recoverable bytes", encoding="utf-8")
    reference = base / "reference-copy"
    reference.mkdir()
    (reference / "blob").write_text("recoverable bytes", encoding="utf-8")
    never = base / "credentials"
    never.mkdir()
    (never / "token").write_text("fixture-not-a-real-credential", encoding="utf-8")
    register = fixture_register(base / "register.md", cache, never)

    digest = J.tree_digest(cache)
    manifest = base / "caches.json"
    # The refetch command names a binary that does not exist. If anything ever
    # executed it, this test would fail loudly rather than silently pass.
    manifest.write_text(json.dumps({"caches": [{
        "path": str(cache), "expected_digest": digest,
        "refetch_command": ["/nonexistent/refetch-binary", "--exact", digest],
        "reference_path": str(reference)}]}), encoding="utf-8")

    def run_cli(*extra: str):
        args = J.build_parser().parse_args(
            ["--repository", str(work), "--tail-manifest", str(TAIL_MANIFEST),
             "--never-cleanable-register", str(register),
             "--cache-manifest", str(manifest), *extra])
        return J.run(args, sink=FakeSink())

    _, report = run_cli()
    check(report["planned"].get("prune_cache") == 1,
          f"the supported reproducible case is reachable from the command "
          f"(saw {report['planned']} / {report['reasons']})")
    check(cache.exists(), "the dry run pruned nothing")

    # A reference copy that does not match is not proof.
    (reference / "blob").write_text("different bytes", encoding="utf-8")
    _, mismatched = run_cli()
    check(mismatched["planned"].get("prune_cache") is None
          and "cache_refetch_proof_missing" in mismatched["reasons"],
          f"a mismatched reference copy withholds the proof (saw {mismatched['reasons']})")
    (reference / "blob").write_text("recoverable bytes", encoding="utf-8")

    # A manifest with no reference at all cannot reach the prunable case.
    bare = base / "caches-bare.json"
    bare.write_text(json.dumps({"caches": [{"path": str(cache), "expected_digest": digest,
                                            "refetch_command": ["x"]}]}), encoding="utf-8")
    args = J.build_parser().parse_args(
        ["--repository", str(work), "--tail-manifest", str(TAIL_MANIFEST),
         "--never-cleanable-register", str(register), "--cache-manifest", str(bare)])
    _, unproven = J.run(args, sink=FakeSink())
    check("cache_refetch_proof_missing" in unproven["reasons"],
          f"no reference copy means no proof (saw {unproven['reasons']})")

    # A cache offered as its OWN reference is the dangerous circular case: the
    # digests match by definition, so without a containment check the cache
    # would authenticate its own destruction. A mere subdirectory would be
    # caught by the digest comparison anyway; only self-reference reaches here.
    circular = base / "caches-circular.json"
    circular.write_text(json.dumps({"caches": [{
        "path": str(cache), "expected_digest": digest,
        "refetch_command": ["x"], "reference_path": str(cache)}]}), encoding="utf-8")
    args = J.build_parser().parse_args(
        ["--repository", str(work), "--tail-manifest", str(TAIL_MANIFEST),
         "--never-cleanable-register", str(register), "--cache-manifest", str(circular)])
    _, circular_report = J.run(args, sink=FakeSink())
    check(circular_report["planned"].get("prune_cache") is None
          and "cache_refetch_proof_missing" in circular_report["reasons"],
          f"a cache is not its own proof (saw {circular_report['reasons']})")

    saved = os.environ.pop("CARR_REPO_HYGIENE_EFFECT_PACKET", None)
    try:
        os.environ["CARR_REPO_HYGIENE_EFFECT_PACKET"] = "FIXTURE-EFFECT-PACKET"
        code, executed = run_cli("--execute")
        check(code == 0 and executed["results"].get("deleted") == 1,
              f"a bound execute prunes exactly the proven cache (saw {executed['results']})")
        check(not cache.exists(), "the owned fixture cache is gone")
        check(reference.exists() and never.exists(),
              "the reference copy and the never-clean fixture root are untouched")
    finally:
        os.environ.pop("CARR_REPO_HYGIENE_EFFECT_PACKET", None)
        if saved is not None:
            os.environ["CARR_REPO_HYGIENE_EFFECT_PACKET"] = saved


def test_cli_defaults_and_parser_shape() -> None:
    parser = J.build_parser()
    args = parser.parse_args([])
    check(args.execute is False, "execute defaults to off")
    check(args.repository == str(J.CANONICAL_CHECKOUT), "the default repository is canonical")
    check(args.pr_evidence is None and args.cache_manifest is None,
          "no provider evidence and no cache manifest are supplied by default")


# ── runner ───────────────────────────────────────────────────────────────────


def main() -> int:
    global TAIL_MANIFEST, REGISTER
    base = nonvolatile_fixture_base()
    with tempfile.TemporaryDirectory(prefix="r07-repo-hygiene-janitor-", dir=base) as temporary:
        root = Path(temporary)
        test_fixture_root_is_not_a_refused_path(root)
        TAIL_MANIFEST, REGISTER = write_synthetic_inputs(root / "synthetic-inputs")
        test_immutable_inputs_parse_to_their_known_shape()
        test_tail_is_never_a_deletion_allowlist(root)
        test_mutex_spans_plan_and_apply(root)
        test_cli_refuses_under_contention_and_takes_no_action(root)
        test_ancestry_delete_with_verified_backup(root)
        test_ancestry_guard_mutation_is_caught(root)
        test_squash_delete_requires_exact_recorded_head(root)
        test_backup_failure_refuses_before_any_deletion(root)
        test_tip_movement_between_plan_and_apply_refuses(root)
        test_pinned_target_drift_refuses(root)
        test_unavailable_fresh_target_refuses_the_plan(root)
        test_branch_evidence_changes_between_plan_and_apply(root)
        test_backup_remote_readback_must_match_exactly(root)
        test_branch_survivor_matrix(root)
        test_worktree_survivor_matrix_and_removal(root)
        test_worktree_races_between_plan_and_apply(root)
        test_cache_matrix(root)
        test_cache_races_between_plan_and_apply(root)
        test_cache_path_swapped_after_the_digest_read(root)
        test_canonical_cache_cleanup_is_structurally_forbidden(root)
        test_receipt_sink_availability_is_checked_before_mutation(root)
        test_pre_effect_intent_failure_preserves_the_target(root)
        test_post_effect_receipt_failure_is_unknown_not_success(root)
        test_execute_without_effect_packet_refuses(root)
        test_idempotent_replay_does_not_remutate(root)
        test_partial_operation_is_unknown_and_never_retried(root)
        test_public_api_refuses_without_a_bound_mutex(root)
        test_open_pull_request_after_an_ancestry_plan_preserves(root)
        test_never_clean_root_cannot_be_reached_through_an_alias(root)
        test_cli_plans_a_real_census_and_defaults_to_dry_run(root)
        test_cli_entrant_evidence_end_to_end(root)
        test_cli_cache_reproducibility_end_to_end(root)
        test_cli_squash_needs_frozen_evidence_and_executes_only_when_bound(root)
        test_cli_defaults_and_parser_shape()

    if FAILURES:
        for failure in FAILURES:
            print(f"FAIL  {failure}", file=sys.stderr)
        print(f"repo-hygiene-janitor-selftest: {len(FAILURES)} FAILED", file=sys.stderr)
        return 1
    print("repo-hygiene-janitor-selftest: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
