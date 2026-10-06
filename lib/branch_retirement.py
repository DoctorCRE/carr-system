"""Evidence and recoverable actions for the existing worktree reaper."""
from contextlib import contextmanager, nullcontext
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "ops"))
from git_env import scrubbed_env
import job_watchdog
from job_watchdog import protected_branch, repository_roots

CLASSES = ("merged", "superseded", "abandoned", "live")


def command(argv, cwd=None, *, input=None, allowed=(0,), timeout=120, environment=None):
    env = scrubbed_env()
    env.update(environment or {})
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GH_PROMPT_DISABLED"] = "1"
    result = subprocess.run(argv, cwd=cwd, env=env, input=input, capture_output=True,
                            text=True, timeout=timeout)
    if result.returncode not in allowed:
        raise RuntimeError(f"{Path(argv[0]).name} failed (exit {result.returncode})")
    return result


def git(root, *args, **kwargs):
    return command(["git", "-C", str(root), *args], **kwargs).stdout.strip()


def branch_verdict(*, merged, successor, open_pr, idle):
    if successor:
        return "superseded"
    if open_pr:
        return "live"
    if merged:
        return "merged"
    return "abandoned" if idle else "live"


def append(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        stream.write(json.dumps(row, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def save(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(row, indent=2, sort_keys=True) + "\n")
    temp.replace(path)


class GitHub:
    fields = """number state body mergedAt url headRefName headRefOid
                headRepository { nameWithOwner } baseRefName baseRefOid
                baseRepository { nameWithOwner } mergeCommit { oid }"""

    def query(self, repo, query, **variables):
        owner, name = repo.split("/")
        argv = ["gh", "api", "graphql", "-f", "query=" + query,
                "-f", "owner=" + owner, "-f", "name=" + name]
        for key, value in variables.items():
            if value is not None:
                argv.extend(["-F" if isinstance(value, int) else "-f", f"{key}={value}"])
        body = json.loads(command(argv).stdout)
        if body.get("errors"):
            raise RuntimeError("GitHub GraphQL evidence refused")
        return body["data"]["repository"]

    def normalize(self, row):
        return {"number": row["number"], "state": "open" if row["state"] == "OPEN" else "closed",
                "body": row["body"], "merged_at": row["mergedAt"], "html_url": row["url"],
                "head": {"ref": row["headRefName"], "sha": row["headRefOid"],
                         "repo": {"full_name": row["headRepository"]["nameWithOwner"]} if row["headRepository"] else None},
                "base": {"ref": row["baseRefName"], "sha": row["baseRefOid"],
                         "repo": {"full_name": row["baseRepository"]["nameWithOwner"]}},
                "merge_commit_sha": (row["mergeCommit"] or {}).get("oid")}

    def verify_repository(self, repo, root):
        origin = git(root, "remote", "get-url", "origin").removesuffix(".git")
        if origin not in (f"https://github.com/{repo}", f"git@github.com:{repo}"):
            raise RuntimeError(f"authorized repository origin mismatch: {repo}")

    def pulls(self, repo):
        cursor, rows = None, []
        query = """query($owner:String!,$name:String!,$cursor:String) {
            repository(owner:$owner,name:$name) { pullRequests(first:100,after:$cursor) {
            nodes { """ + self.fields + """ } pageInfo { hasNextPage endCursor } } } }"""
        while True:
            connection = self.query(repo, query, cursor=cursor)["pullRequests"]
            rows.extend(self.normalize(row) for row in connection["nodes"])
            if not connection["pageInfo"]["hasNextPage"]:
                return rows
            cursor = connection["pageInfo"]["endCursor"]

    def pull(self, repo, number):
        query = """query($owner:String!,$name:String!,$number:Int!) {
            repository(owner:$owner,name:$name) { pullRequest(number:$number) { """ + self.fields + " } } }"
        return self.normalize(self.query(repo, query, number=number)["pullRequest"])

    def open_pulls(self, repo, branch):
        cursor, rows = None, []
        query = """query($owner:String!,$name:String!,$branch:String!,$cursor:String) {
            repository(owner:$owner,name:$name) { pullRequests(first:100,states:[OPEN],headRefName:$branch,after:$cursor) {
            nodes { """ + self.fields + " } pageInfo { hasNextPage endCursor } } } }"
        while True:
            connection = self.query(repo, query, branch=branch, cursor=cursor)["pullRequests"]
            rows.extend(self.normalize(row) for row in connection["nodes"])
            if not connection["pageInfo"]["hasNextPage"]:
                return [r for r in rows if (r["head"]["repo"] or {}).get("full_name") == repo]
            cursor = connection["pageInfo"]["endCursor"]

    def close(self, repo, number, successor):
        message = (f"Superseded by merged PR https://github.com/{repo}/pull/{successor}. "
                   "The branch janitor verified the successor before closing this PR.")
        command(["gh", "pr", "close", str(number), "--repo", repo, "--comment", message])


def process_paths(paths):
    """Observe cwd and open files, plus paths named by running commands."""
    paths = {str(Path(p).resolve()) for p in paths}
    result = command(["lsof", "-n", "-P", "-Fpn"], allowed=(0, 1), timeout=30)
    # A partial or denied collection cannot establish that any tree is unused.
    if result.stderr.strip() or not result.stdout.strip():
        return None
    names = [line[1:] for line in result.stdout.splitlines() if line.startswith("n")]
    commands = command(["ps", "-axo", "command="], timeout=30).stdout
    return {p for p in paths if any(n == p or n.startswith(p + "/") for n in names)
            or p in commands}


def ownership(root, path):
    """An absent owner is unknown; only an ended registered job proves orphaning."""
    directory = Path.home() / ".config/carr/session-directory.json"
    registry = root / "out/jobs/registry.jsonl"
    try:
        if directory.exists():
            rows = json.loads(directory.read_text())["sessions"].values()
            for row in rows:
                cwd = row.get("cwd")
                if cwd and Path(cwd).resolve().is_relative_to(path):
                    return "owned"
        latest = {}
        if registry.exists():
            for line in registry.read_text().splitlines():
                row = json.loads(line)
                latest.setdefault(row["id"], {}).update(row)
        matches = [r for r in latest.values() if r.get("cwd") and
                   Path(r["cwd"]).resolve().is_relative_to(path)]
        if matches and all("exit_code" in r for r in matches):
            return "orphaned"
        return "unknown"
    except (OSError, ValueError, KeyError, TypeError):
        return "unknown"


@contextmanager
def maintenance(root):
    lock = root / "out/worktree-reap.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    # Keep this inode permanently. Renaming or unlinking an acquired flock
    # would allow a second caller to lock a different inode at the same path.
    with lock.open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("repository maintenance lock held; preserve pending run") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


class FleetReaper:
    def __init__(self, root, hook, *, provider=None, process_probe=process_paths,
                 ownership_probe=None, clock=time.time, config=None):
        self.root, self.hook = Path(root), hook
        self.provider = provider or GitHub()
        self.process_probe = process_probe
        self.ownership_probe = ownership_probe or (lambda p: ownership(self.root, p))
        self.config = config or job_watchdog.load_config(SOURCE / "ops/config/job-watchdog.json")
        self.clock = clock
        self.branch_idle = self.config["thresholds"]["branch_idle_seconds"]
        self.tree_idle = hook.REAP_MIN_IDLE_S
        self.patches = {}
        self.ancestors = {}

    def ancestor(self, root, head, base):
        key = (str(root), head, base)
        if key not in self.ancestors:
            self.ancestors[key] = command(["git", "-C", str(root), "merge-base", "--is-ancestor", head, base],
                                          allowed=(0, 1, 128)).returncode == 0
        return self.ancestors[key]

    def patch(self, root, base, head):
        key = (str(root), base, head)
        if key not in self.patches:
            try:
                diff = git(root, "diff", "--binary", base, head)
                result = command(["git", "patch-id", "--stable"], input=diff + "\n").stdout.split()
                changes = []
                for line in git(root, "diff", "--raw", "--no-abbrev", base, head).splitlines():
                    fields = line.split(" ", 4)
                    changes.append(" ".join([fields[0], fields[1], fields[3], fields[4]]))
                content = hashlib.sha256("\n".join(changes).encode()).hexdigest()
                self.patches[key] = result[0] + ":" + content if result else None
            except RuntimeError:
                self.patches[key] = None
        return self.patches[key]

    def change_present(self, root, landed, target):
        names = git(root, "diff", "--no-renames", "--name-only", "-z", landed + "^", landed).split("\0")
        names = [n for n in names if n]
        return bool(names) and not git(root, "diff", "--no-renames", landed, target, "--", *names)

    def pr_identity(self, pr):
        return json.loads(json.dumps({"head": pr["head"], "base": pr["base"], "body": pr.get("body") or ""}))

    def target_matches(self, pr, repo):
        return pr["base"]["ref"] == "main" and pr["base"]["repo"]["full_name"] == repo

    def successor(self, root, repo, pr, merged, main):
        if not self.target_matches(pr, repo):
            return None
        merged = [p for p in merged if self.target_matches(p, repo)]
        for candidate in merged:
            n = candidate["number"]
            url = f"https://github.com/{repo}/pull/{n}"
            declared = re.search(r"superseded\s+by\s+(?:merged\s+)?(?:PR\s+)?(?:" +
                                 re.escape(url) + r"\b|#" + str(n) + r"\b)",
                                 pr.get("body") or "", re.I)
            reverse = re.search(r"supersedes\s+(?:PR\s+)?(?:#" + str(pr["number"]) +
                                r"\b|" + re.escape(pr.get("html_url", "NO-URL")) + r"\b)",
                                candidate.get("body") or "", re.I)
            landed = candidate.get("merge_commit_sha")
            if not landed or not self.ancestor(root, landed, main) or not self.change_present(root, landed, main):
                continue
            if declared or reverse:
                return {"number": n, "head": candidate["head"]["sha"],
                        "merge": landed, "proof": "PR body link", "url": url}
        try:
            base = git(root, "merge-base", pr["base"]["sha"], pr["head"]["sha"])
        except RuntimeError:
            return None
        patch = self.patch(root, base, pr["head"]["sha"])
        if patch:
            for candidate in merged:
                landed = candidate.get("merge_commit_sha")
                if not landed or not self.ancestor(root, landed, main) or not self.change_present(root, landed, main):
                    continue
                # Compare the complete PR diff with the entire landed change.
                if patch == self.patch(root, landed + "^", landed):
                    return {"number": candidate["number"], "head": candidate["head"]["sha"],
                            "merge": landed, "proof": "stable patch-id", "patch_id": patch,
                            "url": f"https://github.com/{repo}/pull/{candidate['number']}"}
        return None

    def snapshot(self, repo, root, skip=()):
        root = Path(root).resolve()
        self.provider.verify_repository(repo, root)
        git(root, "fetch", "--prune", "origin")
        main = git(root, "rev-parse", "refs/remotes/origin/main")
        pulls = self.provider.pulls(repo)
        merged = [p for p in pulls if p.get("merged_at") and p["base"]["ref"] == "main"
                  and p["base"]["repo"]["full_name"] == repo]
        opens = [p for p in pulls if p["state"] == "open"]
        successors = {p["number"]: self.successor(root, repo, p, merged, main) for p in opens}
        rows = []
        by_branch = {}
        for line in git(root, "for-each-ref", "--format=%(refname)%09%(objectname)%09%(committerdate:unix)",
                        "refs/heads", "refs/remotes/origin").splitlines():
            ref, head, updated = line.split("\t")
            if ref.endswith("/HEAD"):
                continue
            name = ref.removeprefix("refs/heads/").removeprefix("refs/remotes/origin/")
            related = [p for p in pulls if p["head"]["ref"] == name and
                       (p["head"].get("repo") or {}).get("full_name") == repo]
            active = [p for p in related if p["state"] == "open"]
            successor = next((successors[p["number"]] for p in active
                              if p["head"]["sha"] == head and successors[p["number"]]), None)
            merge_pr = next((p for p in related if
                p["head"]["sha"] == head and p in merged and p.get("merge_commit_sha")
                and self.ancestor(root, p["merge_commit_sha"], main)), None)
            landed = self.ancestor(root, head, main) or bool(merge_pr)
            classification = branch_verdict(merged=landed, successor=successor,
                open_pr=bool(active), idle=self.clock() - int(updated) >= self.branch_idle)
            protected = protected_branch(self.config, name)
            if protected:
                classification = "live"
            row = {"kind": "branch", "repo": repo, "name": name, "ref": ref, "head": head,
                   "class": classification, "main": main, "successor": successor,
                   "merge_pr": merge_pr["number"] if merge_pr else None,
                   "action": "delete_remote" if classification == "merged" and
                   ref.startswith("refs/remotes/origin/") and not protected else None}
            rows.append(row)
            by_branch.setdefault(name, []).append(row)
        for pr in opens:
            successor = successors[pr["number"]]
            rows.append({"kind": "pr", "repo": repo, "number": pr["number"],
                "head": pr["head"]["sha"], "identity": self.pr_identity(pr), "main": main, "successor": successor,
                "class": "superseded" if successor else "live",
                "action": "close_pr" if successor else None})
        entries = self.hook.worktree_entries(str(root))
        if not entries:
            raise RuntimeError("worktree collection unreadable")
        busy = self.process_probe([e["path"] for e in entries])
        if busy is None:
            raise RuntimeError("process evidence unavailable; preserve repository candidates")
        protected_paths = {str(root), *(str(Path(p).resolve()) for p in skip)}
        for entry in entries:
            rows.append(self.worktree_row(repo, root, entry, main, by_branch, busy, protected_paths))
        return rows

    def worktree_row(self, repo, root, entry, main, by_branch, busy, protected_paths):
        path = Path(entry["path"]).resolve()
        row = {"kind": "worktree", "repo": repo, "path": str(path), "head": entry.get("head"),
               "branch": entry.get("branch"), "detached": bool(entry.get("detached")), "main": main, "class": "live", "action": None}
        reasons = []
        if str(path) in protected_paths or "_to_delete" in path.parts:
            reasons.append("canonical, invoking or staged tree")
        elif entry.get("locked") or entry.get("bare") or not path.is_dir():
            reasons.append("locked, bare or missing tree")
        elif busy is None or str(path) in busy:
            reasons.append("process uses tree or process evidence unavailable")
        else:
            index_age, tree_age = self.hook.index_age_s(str(path)), self.hook.tree_age_s(str(path))
            status = git(path, "status", "--porcelain=v1", "--untracked-files=all")
            row["status"] = status
            row["ownership"] = self.ownership_probe(path)
            associated = by_branch.get(entry.get("branch"), [])
            classification = next((r["class"] for r in associated if r["head"] == entry.get("head")),
                                  "merged" if self.ancestor(root, entry.get("head", ""), main) else "abandoned")
            if index_age is None or tree_age is None or min(index_age, tree_age) < self.tree_idle:
                reasons.append("recent writes or unknown age")
            elif classification == "live":
                reasons.append("open PR or recent commits")
            elif row["ownership"] != "orphaned":
                reasons.append("session owns work or ownership unknown")
            elif entry.get("detached") and classification != "merged":
                reasons.append("detached work without a surviving branch")
            else:
                row["content"] = self.work_content(path)
                row.update({"class": classification, "action": "stage_worktree"})
        row["reason"] = "; ".join(reasons) or "idle with recoverable branch and no live owner"
        return row

    def work_content(self, path):
        digest = hashlib.sha256()
        def frame(value):
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
        frame(git(path, "-c", "diff.autoRefreshIndex=false", "diff", "HEAD", "--binary").encode())
        for name in git(path, "ls-files", "--others", "--exclude-standard", "-z").split("\0"):
            if not name:
                continue
            item = path / name
            frame(os.fsencode(name))
            if item.is_symlink():
                frame(b"symlink")
                frame(os.fsencode(os.readlink(item)))
            elif item.is_file():
                frame(b"file")
                content = hashlib.sha256()
                with item.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(65536), b""):
                        content.update(chunk)
                frame(content.digest())
            else:
                raise RuntimeError("orphan file type unreadable; preserve tree")
        return digest.hexdigest()

    def tree_identity(self, root, path):
        entry = next((e for e in self.hook.worktree_entries(str(root)) if
                      str(Path(e["path"]).resolve()) == str(path)), None)
        if not entry or entry.get("locked") or entry.get("bare"):
            return None
        return {"head": entry.get("head"), "branch": entry.get("branch"),
                "detached": bool(entry.get("detached"))}

    def tree_fresh(self, path):
        # Startup touches the index; read that signal after the longer tree scan.
        ages = (self.hook.tree_age_s(str(path)), self.hook.index_age_s(str(path)))
        return any(age is None or age < self.tree_idle for age in ages)

    def stage(self, root, row):
        path = Path(row["path"])
        token = hashlib.sha256((str(path) + row["head"]).encode()).hexdigest()[:16]
        target = root.parent / "_to_delete" / root.name / token
        if target.exists():
            saved = json.loads((target / "manifest.json").read_text())
            if saved.get("original_path") != str(path) or (target / "worktree").exists():
                raise RuntimeError("staging intent already has an effect; preserve for recovery")
        else:
            target.mkdir(parents=True, exist_ok=False)
        manifest = {**row, "original_path": str(path), "staged_path": str(target / "worktree"),
                    "at": self.clock(), "state": "intent"}
        save(target / "manifest.json", manifest)
        # Hashing large orphan files can take time. Probe again immediately
        # before the move so a session starting during verification wins.
        busy = self.process_probe([str(path)])
        if busy is None or str(path) in busy or self.ownership_probe(path) != row["ownership"] or \
                git(path, "status", "--porcelain=v1", "--untracked-files=all") != row["status"] or \
                self.tree_identity(root, path) != {k: row[k] for k in ("head", "branch", "detached")} or \
                self.tree_fresh(path):
            raise RuntimeError("worktree became live before move; preserve it")
        git(root, "worktree", "move", str(path), str(target / "worktree"))
        if self.tree_identity(root, target / "worktree") != {k: row[k] for k in ("head", "branch", "detached")} or \
                self.work_content(target / "worktree") != row["content"]:
            raise RuntimeError("staged worktree HEAD readback mismatch")
        manifest["state"] = "staged"
        save(target / "manifest.json", manifest)
        return {"staged_path": str(target / "worktree"), "manifest": str(target / "manifest.json")}

    def recover_pending(self, repo, root):
        ledger = self.root / "out/orch/branch-janitor-actions.jsonl"
        latest, recovered = {}, []
        if ledger.exists():
            for line in ledger.read_text().splitlines():
                event = json.loads(line)
                row = event["candidate"]
                key = (row["repo"], row["kind"], row.get("path"), row.get("ref"), row.get("number"), row["head"])
                latest[key] = event
        for event in latest.values():
            row = event["candidate"]
            if row["repo"] != repo or event["status"] != "intent":
                continue
            status = "verified_no_effect"
            if row["action"] == "stage_worktree":
                token = hashlib.sha256((row["path"] + row["head"]).encode()).hexdigest()[:16]
                target = root.parent / "_to_delete" / root.name / token
                staged = target / "worktree"
                if staged.exists() and not Path(row["path"]).exists():
                    if self.tree_identity(root, staged) != {k: row[k] for k in ("head", "branch", "detached")} or self.work_content(staged) != row["content"]:
                        raise RuntimeError("pending staged effect has changed; preserve it")
                    manifest = json.loads((target / "manifest.json").read_text())
                    manifest["state"] = "staged"
                    save(target / "manifest.json", manifest)
                    status = "observed_staged"
                elif staged.exists() or not Path(row["path"]).exists():
                    raise RuntimeError("pending staging identity is ambiguous; preserve it")
            elif row["action"] == "delete_remote":
                refs = git(root, "ls-remote", "--heads", "origin", "refs/heads/" + row["name"]).split()
                status = "observed_retired" if not refs else "verified_no_effect"
            elif row["action"] == "close_pr":
                observed = self.provider.pull(repo, row["number"])
                if self.pr_identity(observed) != row["identity"]:
                    raise RuntimeError("pending PR identity changed; preserve it")
                status = "observed_closed" if observed["state"] != "open" else "verified_no_effect"
            receipt = {**event, "status": status, "observed_at": self.clock()}
            append(ledger, receipt)
            recovered.append(receipt)
        return recovered

    def apply(self, repo, root, row, skip):
        self.provider.verify_repository(repo, root)
        advertised = git(root, "ls-remote", "--heads", "origin", "refs/heads/main").split()
        if not advertised or advertised[0] != row["main"]:
            return {"status": "preserved", "reason": "evidence changed before action"}
        if row["action"] == "stage_worktree":
            entry = next((e for e in self.hook.worktree_entries(str(root)) if
                          str(Path(e["path"]).resolve()) == row["path"]), None)
            if not entry or self.tree_identity(root, Path(row["path"])) != {k: row[k] for k in ("head", "branch", "detached")}:
                return {"status": "preserved", "reason": "worktree moved or HEAD changed"}
            active = self.provider.open_pulls(repo, entry["branch"]) if entry.get("branch") else []
            if active:
                return {"status": "preserved", "reason": "open PR protects tree"}
            # The previously proved class can only grant staging after the same
            # liveness, ownership and content tests pass at the mutation seam.
            by_branch = {row["branch"]: [{"head": row["head"], "class": row["class"]}]}
            current = self.worktree_row(repo, root, entry, row["main"], by_branch,
                self.process_probe([row["path"]]), {str(root), *(str(Path(p).resolve()) for p in skip)})
            if any(current.get(k) != row.get(k) for k in ("action", "status", "ownership", "content", "branch", "detached", "head")):
                return {"status": "preserved", "reason": "worktree became live or content changed"}
            return {"status": "staged", **self.stage(root, row)}
        if row["action"] == "close_pr":
            successor = row["successor"]
            landed = self.provider.pull(repo, successor["number"])
            live = self.provider.pull(repo, row["number"])
            if live["state"] != "open" or self.pr_identity(live) != row["identity"] or \
                    not landed.get("merged_at") or landed.get("merge_commit_sha") != successor["merge"]:
                return {"status": "preserved", "reason": "PR changed before close"}
            if self.successor(root, repo, live, [landed], row["main"]) != successor:
                return {"status": "preserved", "reason": "supersession proof changed before close"}
            advertised = git(root, "ls-remote", "--heads", "origin", "refs/heads/main").split()
            current = self.provider.pull(repo, row["number"])
            if not advertised or advertised[0] != row["main"] or current["state"] != "open" or \
                    self.pr_identity(current) != row["identity"]:
                return {"status": "preserved", "reason": "source identity changed at close"}
            self.provider.close(repo, row["number"], successor["number"])
            observed = self.provider.pull(repo, row["number"])
            if observed["state"] != "closed" or self.pr_identity(observed) != row["identity"]:
                raise RuntimeError("PR close readback failed")
            return {"status": "closed", "successor": successor["url"]}
        ref = "refs/heads/" + row["name"]
        observed = git(root, "ls-remote", "--heads", "origin", ref).split()
        if not observed or observed[0] != row["head"]:
            return {"status": "preserved", "reason": "remote tip changed"}
        if self.provider.open_pulls(repo, row["name"]):
            return {"status": "preserved", "reason": "open PR now protects branch"}
        if not self.ancestor(root, row["head"], row["main"]):
            proof = self.provider.pull(repo, row["merge_pr"])
            if not proof.get("merged_at") or proof["head"]["sha"] != row["head"] or \
                    proof["base"]["ref"] != "main" or not self.ancestor(root, proof["merge_commit_sha"], row["main"]):
                return {"status": "preserved", "reason": "merge proof changed"}
        backup = "refs/retired-branches/" + row["head"] + "-" + hashlib.sha256(ref.encode()).hexdigest()[:16]
        git(root, "update-ref", backup, row["head"])
        if git(root, "rev-parse", backup) != row["head"]:
            raise RuntimeError("branch backup readback failed")
        command(["git", "-C", str(root), "-c",
                 "core.hooksPath=" + str(SOURCE / "ops/branch-janitor-hooks"),
                 "push", "origin", ":" + ref],
                environment={"CARR_RETIRE_REF": ref, "CARR_RETIRE_HEAD": row["head"]})
        if git(root, "ls-remote", "--heads", "origin", ref):
            raise RuntimeError("remote branch deletion readback failed")
        return {"status": "retired", "backup_ref": backup}

    def run(self, roots, *, execute=False, skip=()):
        report = {"schema": "branch-retirement/v1", "at": self.clock(), "execute": execute,
                  "rows": [], "counts": {}, "errors": [], "actions": [], "recovered": []}
        try:
            with maintenance(self.root):
                for repo, path in roots.items():
                    root = Path(path).expanduser().resolve()
                    try:
                        with nullcontext() if root == self.root else maintenance(root):
                            if execute:
                                report["recovered"].extend(self.recover_pending(repo, root))
                            rows = self.snapshot(repo, root, skip)
                            report["rows"].extend(rows)
                            report["counts"][repo] = {kind: {c: sum(r["kind"] == kind and r["class"] == c
                                for r in rows) for c in CLASSES} for kind in ("branch", "worktree", "pr")}
                            if execute:
                                for row in rows:
                                    if not row["action"]:
                                        continue
                                    intent = {"at": self.clock(), "status": "intent", "candidate": row}
                                    ledger = self.root / "out/orch/branch-janitor-actions.jsonl"
                                    append(ledger, intent)
                                    result = self.apply(repo, root, row, skip)
                                    append(ledger, {**intent, **result})
                                    report["actions"].append({**intent, **result})
                    except Exception as exc:
                        report["errors"].append({"repo": repo, "error": str(exc)})
                save(self.root / "out/orch/branch-janitor-report.json", report)
                append(self.root / "out/orch/branch-janitor-runs.jsonl",
                       {k: v for k, v in report.items() if k != "rows"})
        except Exception as exc:
            report["errors"].append({"repo": "fleet", "error": str(exc)})
        return report


def health(root, now=None):
    action = ("on breach: owner orchestrator · run hooks/worktree-self-plumb.py --reap --fleet "
              "· verify out/orch/branch-janitor-report.json · auto-clear after a complete scheduled run")
    try:
        report = json.loads((Path(root) / "out/orch/branch-janitor-report.json").read_text())
        if not report.get("execute") or set(report.get("counts", {})) != set(repository_roots()) or \
                (time.time() if now is None else now) - report["at"] > 7200:
            raise ValueError("scheduled evidence absent or stale")
        failures = len(report["errors"])
        pending = sum(bool(r["action"]) for r in report["rows"]) - sum(
            r["status"] in {"retired", "closed", "staged"} for r in report["actions"])
        failed = failures > 0 or pending > 0
        return f"{'WARN' if failed else 'OK'} branch janitor: errors={failures}, preserved/pending={pending} · {action}", failed
    except (OSError, ValueError, KeyError, TypeError):
        return f"WARN branch janitor: scheduled evidence absent or stale · {action}", True
