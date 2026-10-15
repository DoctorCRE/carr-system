#!/usr/bin/env python3
"""Deterministic disk cleanup. Inspection is the default; --execute authorizes removal.

Extend branch_retirement's git, audit, persistence and maintenance primitives.
Repository/PR evidence is collected outside its maintenance lock; only worktree
removal takes that shared lock. The session hook calls this same run interface.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import fcntl
import glob
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import time

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(SOURCE / "lib"))
from lib.branch_retirement import append, command, git, maintenance, ownership, save


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


orphan = module("disk_reaper_orphan", SOURCE / "ops/orphan-reaper.py")
worktrees = module("disk_reaper_worktrees", SOURCE / "hooks/worktree-self-plumb.py")


def path(value):
    return Path(value).expanduser().resolve()


def overlaps(a, b):
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def load_config(file):
    config = json.loads(Path(file).read_text())
    if config["version"] != 1 or not config["repositories"]:
        raise ValueError("invalid disk-reaper config")
    for item in config["locations"]:
        if item["kind"] not in {"dispatch", "scratch", "worktree", "artifact", "staged"}:
            raise ValueError("invalid location kind")
        if not Path(item["pattern"].replace("{repo}", "/repo")).expanduser().is_absolute():
            raise ValueError("location must be absolute")
    if any(type(v) not in (int, float) or v < 0 for v in config["retention_seconds"].values()):
        raise ValueError("invalid retention")
    if type(config["artifact_keep"]) is not int or config["artifact_keep"] < 2:
        raise ValueError("keep at least two artifacts per series")
    pressure = config["pressure"]
    if not 0 < pressure["free_fraction"] < 1 or pressure["free_bytes"] <= 0:
        raise ValueError("invalid pressure threshold")
    return config


@dataclass(frozen=True)
class Candidate:
    path: Path
    kind: str
    series: str = ""


@dataclass
class Inventory:
    identity: tuple
    newest: float
    bytes: int
    repos: list[Path]


def inventory(root):
    """Walk all writes, including ignored build output, without following links."""
    initial = root.lstat()
    if not root.is_dir() or root.is_symlink():
        raise ValueError("not a real directory")
    newest, size, repos, seen = initial.st_mtime, 0, [], set()
    stack = [(root, True)]
    while stack:
        directory, watch = stack.pop()
        if watch and (directory / "HEAD").is_file() and (directory / "objects").is_dir() and (directory / "config").is_file():
            if git(directory, "rev-parse", "--is-bare-repository") == "true":
                repos.append(directory)
        with os.scandir(directory) as entries:
            for entry in entries:
                stat = entry.stat(follow_symlinks=False)
                if watch and entry.name != ".git":
                    newest = max(newest, stat.st_mtime)
                key = (stat.st_dev, stat.st_ino)
                if key not in seen:
                    size += stat.st_blocks * 512 if stat.st_nlink <= 1 else 0
                    seen.add(key)
                if entry.name == ".git":
                    if entry.is_symlink():
                        raise ValueError("symlinked git metadata")
                    repos.append(directory)
                if entry.is_dir(follow_symlinks=False):
                    stack.append((Path(entry.path), watch and entry.name != ".git"))
    for repo in repos:
        gitdir = worktrees.index_gitdir(str(repo))
        if gitdir and (Path(gitdir) / "index").exists():
            newest = max(newest, (Path(gitdir) / "index").stat().st_mtime)
    return Inventory((initial.st_dev, initial.st_ino), newest, size, sorted(set(repos)))


def jsonl(file):
    if not file.exists():
        return []
    return [json.loads(line) for line in file.read_text().splitlines() if line.strip()]


def dispatches(config):
    """Merge lifecycle events by job, never by desk or directory alone."""
    latest = {}
    for row in jsonl(path(config["dispatch_results"])):
        key = row.get("msg_id") or row.get("id")
        if key:
            latest.setdefault(key, {}).update(row)
        elif row.get("checkout_workspace") or row.get("checkout_path"):
            raise ValueError("dispatch row missing job identity")
    return list(latest.values())


def matches_dispatch(candidate, rows):
    return [r for r in rows if any(r.get(k) and overlaps(candidate, path(r[k]))
                                  for k in ("checkout_workspace", "checkout_path", "cwd"))]


def dispatch_active(row):
    # A released row can still report its prior running status in old ledgers.
    return row.get("ownership_state") == "held" or (
        row.get("status") in {"running", "queued", "starting", "stuck"}
        and row.get("ownership_state") != "released")


def sessions(config):
    file = path(config["session_directory"])
    if not file.exists():
        return []
    return [{"session_id": key, **row} for key, row in
            json.loads(file.read_text())["sessions"].items()]


class Pulls:
    """Live PR evidence. Failed queries preserve the affected candidate."""
    def __init__(self):
        self.cache = {}

    def repository(self, root):
        url = git(root, "config", "--get", "remote.origin.url").removesuffix(".git")
        match = re.fullmatch(r"(?:https://github.com/|git@github.com:)([^/]+/[^/]+)", url)
        if not match:
            raise RuntimeError("PR evidence unavailable for origin")
        return match[1]

    def get(self, repo, branch=None, fresh=False):
        key = (repo, branch)
        if fresh or key not in self.cache:
            endpoint = f"repos/{repo}/pulls?state={'all' if branch else 'open'}&per_page=100"
            if branch:
                from urllib.parse import quote
                endpoint += "&head=" + quote(repo.split('/')[0] + ':' + branch, safe='')
            result = command(["gh", "api", "--paginate", "--slurp", endpoint], timeout=60)
            rows = [r for page in json.loads(result.stdout) for r in page]
            if branch is None:
                for row in rows:
                    detail = json.loads(command(["gh", "pr", "view", str(row["number"]),
                        "--repo", repo, "--json", "body,title,comments,reviews,files"], timeout=30).stdout)
                    row["references"] = json.dumps(detail)
            self.cache[key] = rows
        return self.cache[key]


class Processes:
    def collect(self):
        table = orphan.parse_ps(command(["ps", "-ww", "-axo",
            "pid=,ppid=,uid=,pcpu=,lstart=,comm=,args="], timeout=30).stdout, time.time())
        managed = orphan.parse_launchctl(command(["launchctl", "list"], timeout=30).stdout)
        result = command(["lsof", "-n", "-P", "-Fpcfn"], allowed=(0, 1), timeout=60)
        if result.stderr.strip() or not result.stdout.strip():
            raise RuntimeError("incomplete cwd/open-file census")
        opened, cwd, pid, fd = {}, {}, None, None
        for line in result.stdout.splitlines():
            if line.startswith("p"):
                pid, fd = int(line[1:]), None
            elif line.startswith("f"):
                fd = line[1:]
            elif line.startswith("n") and pid is not None and line[1:].startswith("/"):
                name = path(line[1:].removesuffix(" (deleted)"))
                opened.setdefault(pid, set()).add(name)
                if fd == "cwd":
                    cwd[pid] = name
        return table, managed, opened, cwd

    def users(self, target, census):
        table, _, opened, _ = census
        known = {p.pid for p in table}
        # A process born after ps but before lsof still vetoes deletion.
        unknown = [orphan.Process(pid, 0, -1, "", 0, 0, "", "", "")
                   for pid, names in opened.items() if pid not in known
                   and any(n == target or n.is_relative_to(target) for n in names)]
        return unknown + [p for p in table if any(n == target or n.is_relative_to(target)
                                       for n in opened.get(p.pid, set()))
                or str(target) in p.args]

    def stop_orphans(self, target, config, ended, census):
        if not ended:
            return []
        table, managed, _, cwd = census
        service_ids = set(managed)
        while True:
            children = {p.pid for p in table if p.ppid in service_ids}
            if children <= service_ids:
                break
            service_ids |= children
        protected = orphan.protected_identities(table, service_ids, [])
        stopped = []
        for p in self.users(target, census):
            # The CPU reaper deliberately protects all node processes. Here an
            # ended job + reparented cwd proves ownership of abandoned servers.
            node_only = "node" in p.args and not re.search(
                r"com\.carr\.|dispatch\.py|codex|merge[-_]queue|dot[-_/ ]", p.args, re.I)
            if (p.ppid != 1 or p.uid != os.getuid() or p.age < config["orphan_min_age_seconds"]
                    or p.pid <= 1 or p.pid in service_ids
                    or (orphan.identity(p) in protected and not node_only)
                    or not cwd.get(p.pid, Path('/')).is_relative_to(target)):
                continue
            fresh = self.collect()
            current = next((q for q in self.users(target, fresh)
                            if orphan.identity(q) == orphan.identity(p)), None)
            if (current is None or current.ppid != 1 or current.args != p.args or current.comm != p.comm
                    or fresh[3].get(p.pid) != cwd[p.pid] or p.pid in fresh[1]):
                continue
            os.kill(p.pid, signal.SIGTERM)
            stopped.append({"pid": p.pid, "identity": orphan.identity(p), "signal": "TERM"})
        # A surviving process keeps the directory. No escalation to SIGKILL.
        if stopped:
            time.sleep(.2)
        return stopped


class Reaper:
    def __init__(self, root, config, *, pulls=None, processes=None, clock=time.time, usage=shutil.disk_usage):
        self.root, self.config = path(root), config
        self.pulls, self.processes = pulls or Pulls(), processes or Processes()
        self.clock, self.usage = clock, usage
        self.roots = [path(p) for p in config["repositories"] if path(p).is_dir()]
        self.fetched = set()
        self.registry_path = self.root / "out/disk-scratch-registry.json"

    def candidates(self, registry):
        found = {}
        for location in self.config["locations"]:
            patterns = [location["pattern"].replace("{repo}", str(r)) for r in self.roots]
            for pattern in sorted(set(patterns)):
                for name in sorted(glob.glob(os.path.expanduser(pattern))):
                    p = Path(name)
                    if p.is_symlink() or not p.is_dir() or ".git" in p.parts:
                        continue
                    p = p.resolve()
                    # Earlier specific policies win over /private/tmp/*.
                    found.setdefault(p, Candidate(p, "staged" if "_to_delete" in p.parts else location["kind"],
                        location.get("series", "").replace("{parent}", str(p.parent))))
        for r in self.roots:
            for item in worktrees.worktree_entries(str(r)):
                p = path(item["path"])
                if p != r and p.is_dir():
                    found.setdefault(p, Candidate(p, "staged" if "_to_delete" in p.parts else "worktree"))
        for name in registry:
            p = Path(name)
            if p.is_dir() and not p.is_symlink() and self.scratch_location(p):
                found.setdefault(p, Candidate(p, "scratch"))
        # A parent can never swallow a more specific candidate's keep decision.
        return [c for c in sorted(found.values(), key=lambda c: str(c.path))
                if not any(p != c.path and p.is_relative_to(c.path) for p in found)]

    def scratch_location(self, p):
        return p.parent == Path("/private/tmp") or any(
            p != r / "out" and p.is_relative_to(r / "out") for r in self.roots)

    def registry(self):
        return json.loads(self.registry_path.read_text()) if self.registry_path.exists() else {}

    def _register(self, p, session, registry):
        if Path(p).is_symlink() or not Path(p).is_dir():
            raise ValueError("scratch registration requires a real directory")
        p = path(p)
        if not session or not self.scratch_location(p) or p in self.roots:
            raise ValueError("registration requires session and a scratch directory")
        st = p.lstat()
        key = str(p)
        identity = [st.st_dev, st.st_ino]
        prior = registry.get(key)
        if prior and prior["identity"] == identity and prior.get("session") not in (None, session):
            raise ValueError("scratch directory belongs to another session")
        registry[key] = {"identity": identity, "session": session, "observed_at": self.clock()}

    def register(self, paths, session):
        out = self.root / "out"
        out.mkdir(parents=True, exist_ok=True)
        with (out / "disk-reaper.lock").open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            registry = self.registry()
            for p in paths:
                self._register(p, session, registry)
            save(self.registry_path, registry)

    def git_safety(self, repo, fresh=False):
        if git(repo, "rev-parse", "--is-bare-repository") == "true":
            return "bare repository"
        if git(repo, "status", "--porcelain=v1", "--untracked-files=normal", "--ignore-submodules=none"):
            return "uncommitted tracked or untracked work"
        linked = (repo / ".git").is_file()
        stashes = git(repo, "stash", "list", "--format=%P%x09%s").splitlines()
        if stashes:
            # An independent clone owns its stash store; linked worktrees share it.
            if not linked:
                return "local stash"
            branch = git(repo, "symbolic-ref", "--quiet", "HEAD", allowed=(0, 1)).removeprefix("refs/heads/")
            head = git(repo, "rev-parse", "HEAD")
            for stash in stashes:
                parents, subject = stash.split('\t', 1)
                origin = re.match(r"^(?:On |WIP on )([^:]+):", subject)
                if ((branch and origin and origin[1] == branch)
                        or (not branch and parents.split()[0] == head)):
                    return "local stash"
        common = path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        if common not in self.fetched or fresh:
            git(repo, "fetch", "--prune", "--all")
            self.fetched.add(common)
        remotes = git(repo, "for-each-ref", "--format=%(refname)", "refs/remotes").splitlines()
        if not remotes:
            return "no upstream or remote proof for local commits"
        # An independent clone loses every local ref when removed. A linked
        # worktree loses only HEAD; the common repository retains its branches.
        tips = ["HEAD"] if linked else ["HEAD", "--branches", "--tags"]
        if int(git(repo, "rev-list", "--count", *tips, "--not", *remotes)):
            # GitHub keeps pull refs after deleting a merged branch. Prove the
            # published head from the remote, rather than mistaking a squash
            # merge's deleted upstream for never-pushed work.
            published = []
            for pr in self.pr_rows(repo, fresh):
                if pr.get("head", {}).get("sha") and pr.get("number"):
                    git(repo, "fetch", "--no-tags", "origin", f"refs/pull/{int(pr['number'])}/head")
                    observed = git(repo, "rev-parse", "FETCH_HEAD")
                    if observed != pr["head"]["sha"]:
                        return "PR head changed during remote verification"
                    published.append(observed)
            if int(git(repo, "rev-list", "--count", *tips, "--not", *remotes, *published)):
                return "unpushed or no-upstream local-only commits"
        if not linked:
            entries = worktrees.worktree_entries(str(repo))
            if any(path(e["path"]) != repo and Path(e["path"]).exists() for e in entries):
                return "checkout owns other registered worktrees"
        return None

    def pr_rows(self, repo, fresh=False):
        name = self.pulls.repository(repo)
        branch = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", allowed=(0, 1))
        rows = self.pulls.get(name, branch, fresh=fresh) if branch else []
        return [r for r in rows if (r.get("head", {}).get("repo") or {}).get("full_name", name) == name]

    def artifact_referenced(self, candidate, fresh=False):
        for root in self.roots:
            name = self.pulls.repository(root)
            for pr in self.pulls.get(name, fresh=fresh):
                references = (pr.get("references", "") + " " + (pr.get("body") or "")).replace('\\/', '/')
                direct = {str(candidate.path)}
                parents = {str(candidate.path.parent)}
                if candidate.path.is_relative_to(root):
                    direct.add(str(candidate.path.relative_to(root)))
                    parents.add(str(candidate.path.parent.relative_to(root)))
                if (any(re.search(re.escape(n) + r"(?=$|[^\w.-])", references) for n in direct)
                        or any(re.search(re.escape(n) + r"(?=$|[^\w./-])", references) for n in parents)):
                    return True
        return False

    def safety(self, candidate, info, dispatch, *, fresh=False):
        p = candidate.path
        if p in {Path('/'), Path.home(), self.root, Path.cwd().resolve(), *self.roots} or any(
                r.is_relative_to(p) for r in [self.root, Path.cwd().resolve(), *self.roots]):
            return "canonical or invoking checkout"
        if any(overlaps(p, path(item)) for item in self.config["protected"]):
            return "protected path"
        rows = matches_dispatch(p, dispatch)
        if any(dispatch_active(r) for r in rows):
            return "dispatch held or running"
        if candidate.kind == "dispatch" and (not rows or any(r.get("ownership_state") != "released" for r in rows)):
            return "dispatch release unproved"
        if candidate.kind in {"scratch", "dispatch", "worktree"} and not info.repos:
            registered = self.registry().get(str(p))
            if not registered or registered["identity"] != list(info.identity) or not registered.get("session"):
                return "unattributed non-git scratch"
        # Refuse tracked artifacts in an enclosing repository as well.
        for root in self.roots:
            if p.is_relative_to(root) and not any(r == p for r in info.repos):
                if git(root, "ls-files", "--", str(p.relative_to(root))):
                    return "candidate contains canonical tracked files"
        for repo in info.repos:
            reason = self.git_safety(repo, fresh)
            if reason:
                return reason
            common = path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
            entries = worktrees.worktree_entries(str(repo))
            entry = next((e for e in entries if path(e["path"]) == repo), {})
            if entry.get("locked") or entry.get("bare"):
                return "locked or bare worktree"
            if not common.is_relative_to(p) and not (repo / ".git").is_file():
                return "external git metadata"
            for pr in self.pr_rows(repo, fresh):
                if pr["state"].lower() == "open":
                    updated = datetime.fromisoformat(pr["updated_at"].replace("Z", "+00:00")).timestamp()
                    if self.clock() - updated < 86400:
                        return "open PR updated within 24h"
        if candidate.kind == "artifact" and self.artifact_referenced(candidate, fresh):
            return "referenced by open PR"
        return None

    def retention(self, candidate, info, newest, *, session=False):
        if session:
            return None
        if candidate.kind == "dispatch":
            return None
        if candidate.kind == "artifact":
            return "newest artifacts in series" if candidate.path in newest else None
        if candidate.kind == "worktree":
            related = [pr for repo in info.repos for pr in self.pr_rows(repo)]
            if related and all(pr["state"].lower() == "closed" for pr in related):
                return None
        age = self.clock() - info.newest
        threshold = self.config["retention_seconds"][candidate.kind]
        return "retention: recent write" if age < threshold else None

    def remove(self, candidate, info):
        # Git's own non-force removal remains the final dirty-worktree refusal.
        linked = [r for r in info.repos if (r / ".git").is_file() and not
                  path(git(r, "rev-parse", "--path-format=absolute", "--git-common-dir")).is_relative_to(candidate.path)]
        commons = {path(git(r, "rev-parse", "--path-format=absolute", "--git-common-dir")).parent for r in linked}
        from contextlib import ExitStack
        with ExitStack() as stack:
            for common in sorted(commons):
                stack.enter_context(maintenance(common))
            current = inventory(candidate.path)
            if current.identity != info.identity or current.newest != info.newest:
                raise RuntimeError("directory changed at removal")
            if self.processes.users(candidate.path, self.processes.collect()):
                raise RuntimeError("process became live at removal")
            for repo in current.repos:
                if git(repo, "status", "--porcelain=v1", "--untracked-files=normal", "--ignore-submodules=none"):
                    raise RuntimeError("uncommitted work at removal")
            for repo in sorted(linked, key=lambda r: len(r.parts), reverse=True):
                git(repo, "worktree", "remove", str(repo))
            if candidate.path.exists():
                shutil.rmtree(candidate.path)

    def pressure(self):
        total, _, free = self.usage(self.root)
        return {"free_bytes": free, "free_fraction": free / total,
                "low": free / total < self.config["pressure"]["free_fraction"]
                       or free < self.config["pressure"]["free_bytes"]}

    def run(self, *, execute=False, session=None):
        out = self.root / "out"
        out.mkdir(parents=True, exist_ok=True)
        report = {"schema": "disk-reaper/v1", "at": self.clock(), "execute": execute,
                  "freed_bytes": 0, "would_free_bytes": 0, "kept_by_reason": {},
                  "rows": [], "errors": [], "processes": []}
        with (out / "disk-reaper.lock").open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                report["errors"].append("another disk reaper is running")
                append(out / "disk-reaper.jsonl", report)
                return report
            try:
                report["pressure_before"] = self.pressure()
                registry = self.registry()
                dispatch = dispatches(self.config)
                live_sessions = sessions(self.config)
                candidates = self.candidates(registry)
                if session:
                    selected = []
                    for c in candidates:
                        if registry.get(str(c.path), {}).get("session") == session:
                            selected.append(c)
                        else:
                            report["rows"].append({"path": str(c.path), "action": "keep",
                                                   "reason": "not this session's scratch"})
                    candidates = selected
                inventories, newest = {}, set()
                for candidate in candidates:
                    try:
                        inventories[candidate.path] = inventory(candidate.path)
                    except (OSError, ValueError) as exc:
                        report["rows"].append({"path": str(candidate.path), "action": "keep", "reason": str(exc)})
                series = {}
                for c in candidates:
                    if c.kind == "artifact" and c.path in inventories:
                        series.setdefault(c.series, []).append(c.path)
                for paths in series.values():
                    newest.update(sorted(paths, key=lambda p: (-inventories[p].newest, str(p)))[:self.config["artifact_keep"]])
                for c in candidates:
                    if c.path not in inventories:
                        continue
                    info = inventories[c.path]
                    row = {"path": str(c.path), "kind": c.kind, "bytes": info.bytes, "action": "keep"}
                    report["rows"].append(row)
                    try:
                        prior = registry.get(str(c.path))
                        bound = matches_dispatch(c.path, dispatch)
                        owner = next((r.get("thread_id") or r.get("session_id") for r in bound
                                      if r.get("thread_id") or r.get("session_id")), None)
                        owner = owner or next((r["session_id"] for r in live_sessions
                            if r.get("cwd") and path(r["cwd"]).is_relative_to(c.path)), None)
                        if not prior or prior["identity"] != list(info.identity):
                            registry[str(c.path)] = {"identity": list(info.identity), "session": owner,
                                                     "observed_at": self.clock()}
                        if session and (registry[str(c.path)].get("session") != session or c.kind in {"artifact", "staged"}):
                            row["reason"] = "not this session's scratch"
                            continue
                        reason = self.safety(c, info, dispatch) or self.retention(c, info, newest, session=bool(session))
                        if reason:
                            row["reason"] = reason
                            continue
                        census = self.processes.collect()
                        users = self.processes.users(c.path, census)
                        if execute and users:
                            bound = matches_dispatch(c.path, dispatches(self.config))
                            live_sessions = sessions(self.config)
                            reason = self.safety(c, info, dispatches(self.config), fresh=True)
                            if reason:
                                raise RuntimeError(reason)
                        ended = ((bool(bound) and all(r.get("ownership_state") == "released" for r in bound))
                                 or ownership(self.root, c.path) == "orphaned")
                        if any(r.get("cwd") and path(r["cwd"]).is_relative_to(c.path) for r in live_sessions):
                            ended = False
                        if execute and users:
                            report["processes"].extend(self.processes.stop_orphans(c.path, self.config, ended, census))
                            census = self.processes.collect()
                            users = self.processes.users(c.path, census)
                        if users:
                            row["reason"] = "live process cwd/open files or command"
                            row["pids"] = [p.pid for p in users]
                            continue
                        row.update(action="would_remove", reason="retention elapsed and safety checks passed")
                        if execute:
                            # Refresh all mutable evidence at the mutation seam.
                            current = inventory(c.path)
                            if current.identity != info.identity or current.newest != info.newest:
                                raise RuntimeError("directory changed before removal")
                            reason = self.safety(c, current, dispatches(self.config), fresh=True)
                            if reason or self.processes.users(c.path, self.processes.collect()):
                                raise RuntimeError(reason or "process became live before removal")
                            # Fetch writes Git metadata, excluded from write-age;
                            # user file/index changes still invalidate this scan.
                            self.remove(c, current)
                            row["action"] = "removed"
                            report["freed_bytes"] += current.bytes
                            registry.pop(str(c.path), None)
                    except Exception as exc:
                        row.update(action="keep", reason=str(exc))
                        if "maintenance lock held" in str(exc):
                            try:
                                row["lock_holders"] = command(["lsof", "-n", "-P", "--",
                                    *[str(r / 'out/worktree-reap.lock') for r in self.roots]], allowed=(0, 1)).stdout
                            except Exception:
                                row["lock_holders"] = "unavailable"
                save(self.registry_path, registry)
            except Exception as exc:
                report["errors"].append(str(exc))
            finally:
                for row in report["rows"]:
                    if row["action"] == "would_remove":
                        report["would_free_bytes"] += row["bytes"]
                    elif row["action"] == "keep":
                        group = report["kept_by_reason"].setdefault(row["reason"],
                            {"bytes": 0, "count": 0, "unmeasured_count": 0})
                        group["bytes"] += row.get("bytes", 0)
                        group["count"] += 1
                        group["unmeasured_count"] += int("bytes" not in row)
                report["pressure_after"] = self.pressure()
                if execute:
                    response = ("on breach: owner orchestrator; disk reaper runs immediately; "
                        "if still low, out/disk-pressure.flag requests dispatch admission hold; "
                        "inspect out/disk-reaper.jsonl, resolve kept candidates or expand storage, "
                        "rerun python3 ops/disk-reaper.py --execute; verify free space >=15% AND >=50 GB; "
                        "auto-clear flag after a run verifies both thresholds")
                    low = report["pressure_after"]["low"]
                    health = {"at": self.clock(), "status": "WARN" if low else "OK",
                              **report["pressure_after"], "bound_action": response,
                              "row": f"{'WARN' if low else 'OK'} disk pressure; {response}"}
                    save(out / "disk-pressure-health.json", health)
                    append(out / "disk-pressure-health.jsonl", health)
                    if low:
                        save(out / "disk-pressure.flag", health)
                    else:
                        (out / "disk-pressure.flag").unlink(missing_ok=True)
                append(out / "disk-reaper.jsonl", report)
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--config", type=Path, default=SOURCE / "ops/config/disk-reaper.v1.json")
    parser.add_argument("--root", type=Path, default=SOURCE)
    args = parser.parse_args(argv)
    try:
        report = Reaper(args.root, load_config(args.config)).run(execute=args.execute)
        print(json.dumps(report, sort_keys=True))
        return int(bool(report["errors"]))
    except Exception as exc:
        print(f"disk reaper stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
