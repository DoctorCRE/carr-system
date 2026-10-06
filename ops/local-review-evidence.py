#!/usr/bin/env python3
"""Exact-source local review floor; data-only evidence for the orchestrator.

ops/ci.sh --review-floor --body-file <file> --receipt <file>
ops/ci.sh --review-admit --pr <number> --receipt <file>

Collect runs relevant canonical checks in the foreground on clean committed
source. Admit authenticates the live PR's head/base/body using gh, then verifies
the receipt and reruns the existing eval/body oracle. It grants only local
review readiness: hosted PR CI and independent review remain separate gates.
Receipts are local trusted executor evidence, never executable or a merge token.
Only hashes and fixed check identities persist; child output/body stay private.
"""
# doctrine: engineering-workflow-sop
from __future__ import annotations

import argparse
from fnmatch import fnmatch
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile

from git_env import scrubbed_env

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from migration_number_contract import validate_migration_names, MigrationNumberError  # noqa: E402

FLOOR = ["pushfloor", "secret", "freshness"]
SCHEMA = "carr-local-review-evidence/v1"
CLOSURE_LIMIT = 50  # consumer closures wider than this are not narrowed


class Refusal(ValueError):
    """Evidence is missing, stale, failed or incompatible; no admission."""


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def json_digest(value) -> str:
    return digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def child_env() -> dict:
    """Environment every check runs under; everything left in it is bound.

    CARR_CI_RANGE would narrow the scans below the full depth this floor is
    judged at, and the PR body reaches checks only as the explicit, hashed file.
    """
    env = scrubbed_env()
    for key in ["CARR_CI_RANGE", "CARR_PR_BODY_FILE", "GITHUB_EVENT_PATH"]:
        env.pop(key, None)
    return env


def run(root: Path, argv: list[str], *, timeout: int = 120, accept: tuple[int, ...] = (0,)) -> str:
    try:
        out = subprocess.run([sys.executable, str(ROOT / "bin/with-timeout.py"), str(timeout), *argv],
                             cwd=root, env=child_env(), capture_output=True,
                             text=True, timeout=timeout + 30)
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        # Error strings and argv can carry credentials. Persist neither.
        raise Refusal(f"check unavailable ({type(exc).__name__}); retry after repair") from None
    if out.returncode not in accept:
        raise Refusal(f"check refused (exit {out.returncode}); repair the failing check")
    return out.stdout


def git(root: Path, *args: str) -> str:
    return run(root, ["git", *args]).strip()


def class_order(root: Path) -> list[str]:
    # ci.sh owns the inventory. Fail rather than silently guessing if its
    # declaration changes shape; this is not another list to maintain.
    found = re.findall(r'^CLASS_ORDER="([a-z ]+)"$', (root / "ops/ci.sh").read_text(), re.M)
    if len(found) != 1:
        raise Refusal("CI class inventory unreadable; repair selection before admission")
    classes = found[0].split()
    if len(classes) != len(set(classes)) or not set(FLOOR).issubset(classes):
        raise Refusal("CI class inventory incomplete; repair selection before admission")
    return classes


def class_consumers(root: Path) -> dict[str, list[str]]:
    """Path patterns each check_<class> body in ci.sh executes or reads.

    Read from the canonical runner itself, so a class gains coverage by naming
    a path and nothing here has to be kept in step. Comments are not consumers.
    """
    text = (root / "ops/ci.sh").read_text()
    top = {p.split("/", 1)[0] for p in git(root, "ls-files", "-z").split("\0") if "/" in p}
    consumers = {}
    for name, body in re.findall(r"^check_([a-z]+)\(\) \{\n(.*?)^\}", text, re.M | re.S):
        code = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))
        patterns = set(re.findall(r"[\w.*-]+(?:/[\w.*-]+)+", code))
        # A package loop (`for pkg in mcp-server workspace`), a `cd`, or a bare
        # `migrations/` argument consumes the whole tree. Other bare words (a
        # SQL schema called ops) are not paths.
        trees = re.findall(r"\bcd\s+\"?([\w-]+)", code) + re.findall(r"(?<![\w./-])([\w-]+)/(?![\w.*-])", code)
        for words in re.findall(r"\bfor\s+\w+\s+in\s+([^;\n]*)", code):
            trees += words.split()
        patterns.update(f"{word}/*" for word in trees if word in top)
        consumers[name] = sorted(patterns)
    return consumers


def referencers(root: Path, words: set[str], *excluded: str) -> list[str]:
    found = run(root, ["git", "grep", "-l", "-z", "-w", "-F", *[a for w in sorted(words) for a in ("-e", w)],
                       "--", ".", ":(exclude)*.md", ":(exclude)*.txt",
                       *[f":(exclude){p}" for p in excluded]], accept=(0, 1))
    return [name for name in found.split("\0") if name]


def consumer_closure(root: Path, path: str) -> set[str] | None:
    """Every tracked file that names `path`, transitively; None past the bound.

    A module is reached by its stem (`import release_manifest`, `tools/release-
    manifest.py`); a data file or plug-in by a scan of its directory, so the
    directory name is searched too. ci.sh is left out of that second search
    only because its own directory use is already read as class patterns.
    Prose names things it never executes and is excluded.
    """
    closure, queue = {path}, [path]
    while queue:
        member = Path(queue.pop())
        found = referencers(root, {member.stem, member.stem.replace("-", "_")})
        if member.parent.name:
            found += referencers(root, {member.parent.name}, "ops/ci.sh")
        for name in found:
            if name not in closure:
                closure.add(name)
                queue.append(name)
        if len(closure) > CLOSURE_LIMIT:
            return None
    return closure


def select_classes(root: Path, paths: list[str]) -> list[str]:
    """Narrow only to classes whose ci.sh body verifiably reaches the change.

    Anything ci.sh names directly, anything it cannot be shown to execute, and
    any closure too wide to bound selects the whole inventory.
    """
    order = class_order(root)
    if len(paths) > 30:
        return order.copy()
    selected = set(FLOOR)
    consumers = class_consumers(root)
    for path in paths:
        if path in {"README.md", "LICENSE"}:
            continue
        if path.endswith(("lock.json", ".lock")) or path.startswith(".github/") or path in {
                "ops/ci.sh", "evals/surfaces.json"}:
            return order.copy()
        closure = consumer_closure(root, path)
        if closure is None or "ops/ci.sh" in closure:
            return order.copy()
        reached = {name for name, patterns in consumers.items()
                   for member in closure if any(fnmatch(member, p) for p in patterns)}
        if not reached:
            return order.copy()  # nothing verified executes it
        selected |= reached
        if path.endswith(".py"):
            selected.add("types")  # bin/type-check.sh reads every Python file
    if not selected.issubset(order):
        raise Refusal("required class absent from CI inventory; repair selection")
    return [name for name in order if name in selected]


RUNTIME_PROBE = ("import importlib.metadata as m, json, platform, sys; print(json.dumps("
                 "[sys.executable, platform.python_version(), "
                 "sorted((str(d.metadata['Name']), d.version) for d in m.distributions())]))")


def ci_python(root: Path) -> list:
    """The interpreter and packages ci.sh at `root` selects, with its bytes."""
    venv = root / ".venv/bin/python"  # ci.sh: [ -x "$PY" ] || PY=python3
    chosen = str(venv) if os.access(venv, os.X_OK) else shutil.which("python3", path=child_env().get("PATH"))
    if not chosen:
        raise Refusal("no Python for canonical CI; install one before collecting")
    resolved = Path(chosen).resolve()
    return [chosen, str(resolved), digest(resolved.read_bytes()), json.loads(run(root, [chosen, "-c", RUNTIME_PROBE]))]


def environment(root: Path) -> dict:
    # Installed dependency state is not necessarily the committed lockfile.
    distributions = sorted((str(d.metadata["Name"]), d.version) for d in importlib.metadata.distributions())
    lockfiles = [p for p in git(root, "ls-files", "-z", "*package-lock.json").split("\0") if p]
    installed = [str(Path(p).parent / "node_modules/.package-lock.json") for p in lockfiles]
    ignored_locks = {p: digest((root / p).read_bytes()) if (root / p).is_file() else None for p in installed}
    env = child_env()
    for key in ["PWD", "OLDPWD", "SHLVL", "_"]:  # shell bookkeeping, never a check input
        env.pop(key, None)
    return {"system": platform.system(), "machine": platform.machine(),
            "python": platform.python_version(), "node": run(root, ["node", "--version"]).strip(),
            "runtime_sha256": json_digest([distributions, sys.executable, ci_python(root), ignored_locks]),
            "env_sha256": json_digest(env)}


def file_bytes(path: Path) -> bytes:
    return os.readlink(path).encode() if path.is_symlink() else path.read_bytes()


def physical_source(root: Path) -> str:
    """Prove the bytes on disk are HEAD's; return a digest of untracked inputs.

    Hashes each tracked file the way git does and compares it with the commit
    tree, so assume-unchanged and skip-worktree cannot hide an edit. Untracked,
    unignored files can be loaded by the checks, so they are bound instead.
    """
    hasher = hashlib.sha256 if git(root, "rev-parse", "--show-object-format") == "sha256" else hashlib.sha1
    for entry in run(root, ["git", "ls-tree", "-r", "-z", "--full-tree", "HEAD"]).split("\0"):
        if not entry:
            continue
        meta, name = entry.split("\t", 1)
        mode, _, oid = meta.split()
        if mode == "160000":
            continue  # a submodule is its own repository, not bytes here
        path = root / name
        try:
            data = file_bytes(path)
        except OSError:
            raise Refusal("tracked source missing on disk; restore the committed candidate") from None
        # Content and link-ness only: core.fileMode is false here, so the
        # executable bit on disk legitimately differs from the tree.
        if path.is_symlink() != (mode == "120000") or hasher(b"blob %d\0" % len(data) + data).hexdigest() != oid:
            raise Refusal("source on disk differs from the commit; commit or restore it")
    untracked = [p for p in git(root, "ls-files", "-z", "--others", "--exclude-standard").split("\0") if p]
    return json_digest({p: digest(file_bytes(root / p)) for p in sorted(untracked)})


def binding(root: Path, base: str, body: str) -> tuple[dict, list[str]]:
    if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root.resolve():
        raise Refusal("wrong repository root; use the assigned worktree")
    if git(root, "status", "--porcelain", "--untracked-files=no"):
        raise Refusal("source/index differs from HEAD; commit the tested candidate")
    untracked = physical_source(root)
    base_sha = git(root, "rev-parse", f"{base}^{{commit}}")
    if base_sha != git(root, "rev-parse", "origin/main^{commit}"):
        raise Refusal("wrong current base; fetch origin/main and recollect")
    if git(root, "merge-base", base_sha, "HEAD") != base_sha:
        raise Refusal("current main is not integrated; update source before collecting the floor")
    paths = git(root, "diff", "--name-only", "-z", f"{base_sha}..HEAD").split("\0")
    paths = [p for p in paths if p]
    revision = {p: digest((ROOT / p).read_bytes()) for p in [
        "ops/ci.sh", "ops/local-review-evidence.py", "ops/check-eval-receipt.py",
        "ops/migration-order-gate.py", "tools/migration_number_contract.py", "ops/git_env.py",
        "bin/with-timeout.py", "ops/ai_eval.py", "tools/room-bridge/evaluation_kernel.py"]}
    return {"head": git(root, "rev-parse", "HEAD"), "tree": git(root, "rev-parse", "HEAD^{tree}"),
            "untracked_sha256": untracked, "base": base_sha, "environment": environment(root),
            "checker_revision": json_digest(revision), "body_sha256": digest(body.encode())}, paths


def checked(root: Path, name: str, argv: list[str], acknowledgement: str) -> dict:
    out = run(root, argv, timeout=3600)
    if acknowledgement not in out.splitlines():
        raise Refusal(f"{name}: missing acknowledgement; rerun the check")
    return {"name": name, "status": "passed", "output_sha256": digest(out.encode())}


def eval_check(root: Path, base: str, body: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="review-eval-") as td:
        path = Path(td) / "body"
        path.write_text(body)
        out = run(root, [sys.executable, str(ROOT / "ops/check-eval-receipt.py"),
                         "--root", str(root), "--base", base, "--pr-body-file", str(path)])
    if not any(line.startswith("check-eval-receipt: OK (") for line in out.splitlines()):
        raise Refusal("eval/body: missing acknowledgement; repair the checker")
    return {"name": "eval-body", "status": "passed", "output_sha256": digest(out.encode())}


def migration_union(root: Path, base: str) -> dict:
    names = set()
    for ref in [base, "HEAD"]:
        names.update(git(root, "ls-tree", "--name-only", f"{ref}:migrations").splitlines())
    try:
        validate_migration_names(names)
    except MigrationNumberError:
        raise Refusal("migration-name union refused; allocate a valid successor") from None
    out = run(root, [sys.executable, str(ROOT / "ops/migration-order-gate.py"),
                     "--repo", str(root), "--base", base])
    if not any(line.startswith("migration-order-gate: OK") for line in out.splitlines()):
        raise Refusal("migration-order: missing acknowledgement")
    return {"name": "migration-union", "status": "passed", "output_sha256": digest(out.encode())}


def validate_ci(data, classes: list[str]) -> None:
    if not isinstance(data, dict) or set(data) != {"schema", "strict", "classes"} or data.get("schema") != "carr-ci-result/v1" or data.get("strict") is not True:
        raise Refusal("CI result missing or not strict; rerun the local floor")
    rows = data.get("classes")
    if not isinstance(rows, list) or any(not isinstance(r, dict) or set(r) != {
            "name", "status", "checks"} for r in rows) or [r["name"] for r in rows] != classes:
        raise Refusal("CI result is incomplete; run every selected class")
    if any(r.get("status") != "passed" or type(r.get("checks")) is not int or r["checks"] < 1 for r in rows):
        raise Refusal("CI result red, partial or empty; repair or provide the still-required hosted check")


def collect(root: Path, base: str, body: str) -> dict:
    source, paths = binding(root, base, body)
    classes = select_classes(root, paths)
    checks = [eval_check(root, base, body), migration_union(root, base)]
    checks.append(checked(root, "source-seal", ["node", "--input-type=module", "-e",
        "import {assertCurrentSourceInventoryMatchesFixture} from './ops/scac-mutation-inventory.mjs';"
        "import {TOOLS} from './mcp-server/src/tools.js';"
        "assertCurrentSourceInventoryMatchesFixture(TOOLS); console.log('source-seal: OK');"], "source-seal: OK"))
    with tempfile.TemporaryDirectory(prefix="review-ci-") as td:
        result = Path(td) / "result.json"
        body_file = Path(td) / "body"
        body_file.write_text(body)
        run(root, ["bash", str(root / "ops/ci.sh"), "--strict", "--only", ",".join(classes),
                   "--pr-body-file", str(body_file), "--result-file", str(result)], timeout=3600)
        try:
            ci = json.loads(result.read_text())
        except (OSError, ValueError):
            raise Refusal("CI result absent/unreadable; rerun the local floor") from None
    validate_ci(ci, classes)
    after, _ = binding(root, base, body)
    if after != source:
        raise Refusal("source/environment moved during checks; recollect")
    return {"schema": SCHEMA, "binding": source, "classes": classes, "ci": ci, "checks": checks}


def verify(root: Path, base: str, body: str, receipt: dict) -> dict:
    source, paths = binding(root, base, body)
    if not isinstance(receipt, dict) or set(receipt) != {"schema", "binding", "classes", "ci", "checks"}:
        raise Refusal("invalid receipt schema; recollect")
    if receipt["schema"] != SCHEMA or receipt["binding"] != source:
        raise Refusal("stale source/base/environment/checker/body evidence; recollect")
    classes = select_classes(root, paths)
    if receipt["classes"] != classes:
        raise Refusal("selected coverage changed; recollect")
    validate_ci(receipt["ci"], classes)
    checks = receipt["checks"]
    if not isinstance(checks, list) or [c.get("name") for c in checks if isinstance(c, dict)] != [
            "eval-body", "migration-union", "source-seal"]:
        raise Refusal("missing mandatory local checks; recollect")
    if any(set(c) != {"name", "status", "output_sha256"} or c["status"] != "passed" or
           not isinstance(c["output_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", c["output_sha256"]) for c in checks):
        raise Refusal("invalid local check evidence; recollect")
    eval_check(root, base, body)  # body-only edits use the hosted oracle too
    after, _ = binding(root, base, body)
    if after != source:
        raise Refusal("source/environment moved during admission; recollect")
    return {"schema": "carr-local-review-admission/v1", "head": source["head"],
            "local_floor": "passed", "preflight": "partial", "merge_ready": False,
            "required_hosted_checks": ["ops/ci.sh --strict"],
            "next_action": "independent review and exact-source hosted PR checks"}


def read_pr(root: Path, pr: int) -> dict:
    raw = run(root, ["gh", "api", f"repos/jbookout/carr-system/pulls/{pr}"])
    try:
        data = json.loads(raw)
        if data["state"] != "open" or data["number"] != pr or data["base"]["ref"] != "main":
            raise ValueError()
        for part in ["head", "base"]:
            if data[part]["repo"]["full_name"] != "jbookout/carr-system" or not re.fullmatch(r"[0-9a-f]{40}", data[part]["sha"]):
                raise ValueError()
        if data["body"] is not None and not isinstance(data["body"], str):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise Refusal("provider PR response incomplete/unknown; retry the authenticated read") from None
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["collect", "admit"])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--body-file", type=Path)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--pr", type=int)
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        if args.command == "collect":
            if not args.body_file:
                raise Refusal("collect requires --body-file; branch-only eval evidence is partial")
            receipt = collect(root, args.base, args.body_file.read_text())
            target = args.receipt.resolve()
            # Private, atomic publication; a failed collect cannot overwrite good evidence.
            fd, temp = tempfile.mkstemp(dir=target.parent, prefix=".review-evidence-")
            with os.fdopen(fd, "w") as out:
                json.dump(receipt, out, sort_keys=True)
            os.replace(temp, target)
            print(json.dumps({"local_floor": "passed", "preflight": "partial", "merge_ready": False,
                              "required_hosted_checks": ["ops/ci.sh --strict"]}))
        else:
            if args.pr is None or args.pr < 1:
                raise Refusal("admit requires a live --pr number")
            before = read_pr(root, args.pr)
            if git(root, "rev-parse", "HEAD") != before["head"]["sha"] or git(root, "rev-parse", args.base) != before["base"]["sha"]:
                raise Refusal("live PR head/base differs; fetch and recollect")
            result = verify(root, args.base, before["body"] or "", json.loads(args.receipt.read_text()))
            after = read_pr(root, args.pr)
            if [before[k] for k in ["head", "base", "body"]] != [after[k] for k in ["head", "base", "body"]]:
                raise Refusal("live PR moved during admission; retry with current evidence")
            print(json.dumps(result))
        return 0
    except (Refusal, OSError, ValueError, TypeError):
        # Do not expose provider payloads, paths, body or subprocess errors.
        print(json.dumps({"local_floor": "refused", "merge_ready": False,
                          "next_action": "repair checks or recollect current source/base/body evidence"}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
