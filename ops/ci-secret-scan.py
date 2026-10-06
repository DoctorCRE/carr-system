#!/usr/bin/env python3
"""ci-secret-scan.py — refuse a tree that carries a live credential.

WHY THIS IS HAND-WRITTEN RATHER THAN gitleaks/trufflehog. The scan has to run
identically in two places: a GitHub runner and Joe's Mac at push time. rule
a8c55a47 says a manual path and an automated path doing the same job must be the
same code, and the cheapest way to guarantee that is a scanner with no install
step, no network fetch and no version to drift. It is stdlib only, so it cannot
be the reason a check is skipped.

WHAT IT LOOKS FOR. High-confidence, shaped credentials only — the ones whose
format is distinctive enough that a match is almost never a false positive. It
deliberately does NOT do generic entropy scoring. An entropy scanner in a repo
holding 947 markdown files, migration SQL and doctrine prose produces noise, and
a noisy gate is one people learn to bypass, which is worse than no gate.

THREE BIND-MOMENTS, ONE IMPLEMENTATION. Rule a8c55a47 again: the scan that runs
at commit, at push and in hosted CI must be the same code, so the scope is a
FLAG rather than a second scanner.

  * --staged      the blobs entering the index, INCLUDING newly added files.
                  This is the cheap moment, and it is the one that was missing.
                  The default scope reads files git already TRACKS, so a brand-new
                  file carrying a key passed every local check until `git add`
                  made it tracked -- and then failed at pre-push, the most
                  expensive boundary a session can hit. Sessions hit it often
                  enough to be written down as a standing gotcha. Reading the
                  INDEX rather than the working tree also means a partially
                  staged file is judged by the bytes actually being committed.
  * --range A..B  the blobs introduced by a range of commits. This is the push
                  scope: it costs O(what is being pushed) rather than O(repo),
                  and it still catches content that reached the branch without
                  passing this file at commit time -- a rebase, a cherry-pick,
                  an amend, or a plain `git commit --no-verify`.
  * (no flag)     every tracked file in the working tree. Unchanged, and this is
                  what hosted CI runs: the depth that does not depend on which
                  commits happen to be in front of it.

WHAT IT DOES NOT PROTECT AGAINST. The default scope reads the working tree, not
git history. A credential already committed and later removed is still in the
history and this will not find it. That is a separate, one-time job (history
rewrite), not a per-push check, and saying so here is the point: a gate that
quietly implies coverage it does not have is how loop #276 happened in another
form. --range narrows that gap for the commits actually leaving the machine; it
does not close it for history already on the remote.

ALLOWLISTING, because a repo about credentials must be able to write about them.
Two mechanisms, both deliberately narrow:
  * an inline `ci-secret-scan: allow` marker on the same line, which forces the
    author to make the claim at the exact site rather than in a distant config;
  * ops/config/secret-scan-allow.json, for whole paths that are documentation or
    fixtures by construction.
Neither can allowlist a real private-key block — that pattern is unconditional,
because there is no legitimate reason for one to be in this tree at all.

Usage:
    ops/ci-secret-scan.py                 # scan tracked files, exit 1 on a hit
    ops/ci-secret-scan.py --staged        # scan the index, new files included
    ops/ci-secret-scan.py --range A..B    # scan blobs introduced by a range
    ops/ci-secret-scan.py --list-patterns # what it looks for, and why
    ops/ci-secret-scan.py --redact        # stdin -> stdout, every match masked
"""

import json
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
ALLOW_FILE = REPO / "ops" / "config" / "secret-scan-allow.json"
INLINE_ALLOW = "ci-secret-scan: allow"

# (name, regex, why it is high-confidence, allowlistable)
PATTERNS = [
    ("private-key-block",
     re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
     "A private key block has no legitimate reason to be in this tree.",
     False),
    ("aws-access-key",
     re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
     "AWS access key IDs have a fixed, unmistakable prefix and length.",
     True),
    ("google-api-key",
     re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"),
     "Google API keys are fixed-length with an AIza prefix.",
     True),
    ("google-oauth-client-secret",
     re.compile(r"\bGOCSPX-[0-9A-Za-z\-_]{28}\b"),
     "The Worker's Google Sign-In secret has this exact shape.",
     True),
    ("github-token",
     re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36}\b"),
     "GitHub personal/OAuth/app tokens are prefixed and fixed-length.",
     True),
    ("slack-token",
     re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
     "Slack tokens carry an xox<type>- prefix.",
     True),
    ("anthropic-key",
     re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{20,}\b"),
     "Anthropic API keys are prefixed sk-ant-.",
     True),
    ("openai-key",
     re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b"),
     "OpenAI API keys are prefixed sk- or sk-proj-.",
     True),
    ("dsn-with-password",
     re.compile(r"\bpostgres(?:ql)?://[^\s:/@]+:[^\s@]{6,}@"),
     "A Postgres DSN carrying an inline password is the Neon credential shape.",
     True),
]

# Binary and vendored trees are skipped by extension/path rather than sniffed:
# a scanner that reads a 1.6GB model file to find no secrets in it is a scanner
# nobody leaves enabled.
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".xlsx", ".docx", ".pptx",
    ".zip", ".gz", ".tgz", ".bin", ".ico", ".woff", ".woff2", ".ttf",
    ".mp3", ".mp4", ".wav", ".m4a", ".webp", ".heic",
}
SKIP_PATH_PARTS = {"node_modules", ".venv", "vendor", ".git"}


def _interesting(name):
    """The skip rules, applied to a path NAME so every scope shares them."""
    if pathlib.PurePath(name).suffix.lower() in SKIP_SUFFIXES:
        return False
    if SKIP_PATH_PARTS & set(pathlib.PurePath(name).parts):
        return False
    return True


def _git(*args):
    return subprocess.run(["git", *args], cwd=REPO,
                          capture_output=True, check=True)


def _names(*args):
    """NUL-separated path names from a git plumbing command."""
    out = _git(*args).stdout.decode("utf-8", errors="surrogateescape")
    return [n for n in out.split("\0") if n and _interesting(n)]


def _decode(raw):
    """Text of a blob, or None when it is not text a regex can match."""
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None


def tracked_files():
    """Only files git tracks. An untracked scratch file is not shipping."""
    for name in _names("ls-files", "-z"):
        path = REPO / name
        try:
            yield name, path.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue


def staged_files():
    """The blobs entering the index, read FROM the index.

    --diff-filter=ACMR keeps added, copied, modified and renamed paths and drops
    deletions: a path being removed cannot carry a credential into the commit.
    The A is the whole point -- a brand-new file is exactly what the tracked-file
    default cannot see.

    Content comes from `git show :path`, the staged blob, NOT from the working
    tree. `git add` then edit is a real sequence, and the bytes being committed
    are the ones this must judge.
    """
    for name in _names("diff", "--cached", "--name-only", "--diff-filter=ACMR",
                       "-z"):
        try:
            raw = _git("show", f":{name}").stdout
        except subprocess.CalledProcessError:
            continue  # racing stage; the next moment catches it
        text = _decode(raw)
        if text is not None:
            yield name, text


def range_files(rev_range):
    """The blobs a range of commits introduces, read at its tip.

    Reading at the tip rather than per-commit is deliberate. A key added in one
    commit and removed in the next is not shipping in the tree being pushed, and
    failing the push for it would teach --no-verify. What IS still true is that
    the key is in the history now; the pre-push refusal text says so.
    """
    tip = rev_range.split("..")[-1] or "HEAD"
    for name in _names("diff", "--name-only", "--diff-filter=ACMR", "-z",
                       rev_range):
        try:
            raw = _git("show", f"{tip}:{name}").stdout
        except subprocess.CalledProcessError:
            continue  # deleted again by the tip; nothing is shipping
        text = _decode(raw)
        if text is not None:
            yield name, text


def load_allowed_paths():
    if not ALLOW_FILE.exists():
        return set()
    try:
        data = json.loads(ALLOW_FILE.read_text())
    except json.JSONDecodeError as exc:
        print(f"secret-scan: {ALLOW_FILE} is not valid JSON — {exc}", file=sys.stderr)
        sys.exit(2)
    return set(data.get("paths", []))


def scan(source=None):
    """Findings from a (name, text) source. Every scope shares this body, so a
    pattern or allowlist rule cannot hold at one bind-moment and not another."""
    allowed_paths = load_allowed_paths()
    findings = []

    for name, text in (source if source is not None else tracked_files()):
        if name in allowed_paths:
            continue

        for lineno, line in enumerate(text.splitlines(), 1):
            for pname, rx, _why, allowlistable in PATTERNS:
                m = rx.search(line)
                if not m:
                    continue
                if allowlistable and INLINE_ALLOW in line:
                    continue
                # Never print the value. The finding names the location and the
                # shape; a scanner that echoes the credential into a CI log has
                # published it to a second place.
                findings.append((name, lineno, pname, len(m.group(0))))

    return findings


def redact(text):
    """Mask every shaped credential in ARBITRARY TEXT, reusing this one list.

    WHY IT LIVES HERE AND NOT WHERE IT IS CALLED. ops/ci.sh prints a failing
    gate's captured child output — up to a whole log — so that output is a
    second place a credential could be published, and CI logs outlive the run.
    The obvious fix is a handful of regexes at the call site, and that is the
    fix that rots: a second pattern set drifts from this one, and the drift is
    invisible because both sides look fine on their own. A pattern added to
    PATTERNS must protect every place this repository prints untrusted text, so
    the redactor is the scanner's own function over the scanner's own list.

    NO ALLOWLIST APPLIES HERE, deliberately. The inline marker and the path
    allowlist exist so this repository can WRITE about credentials in its own
    tracked files; neither has any meaning for a child process's stdout, which
    is not a file anyone reviewed. Every match is masked, unconditionally.

    The mask keeps the finding and drops the value: the pattern name and the
    length, which is what a reader diagnosing a log actually needs, and exactly
    what scan() already reports for the same reason.
    """
    for pname, rx, _why, _allowlistable in PATTERNS:
        text = rx.sub(lambda m, n=pname: f"<redacted:{n}:{len(m.group(0))} chars>",
                      text)
    return text


def main():
    if "--redact" in sys.argv:
        # A FILTER, not a gate: stdin to stdout, exit 0 on clean and on masked
        # alike. The caller is printing a diagnosis it already decided to print;
        # a nonzero here would turn "this log mentioned a token shape" into a
        # second failure on top of the one being diagnosed. errors="replace"
        # because a child's log is arbitrary bytes and a decode crash would
        # suppress the diagnosis entirely — the one outcome worse than printing.
        raw = sys.stdin.buffer.read()
        sys.stdout.write(redact(raw.decode("utf-8", errors="replace")))
        return 0

    if "--list-patterns" in sys.argv:
        print("ci-secret-scan patterns:\n")
        for pname, _rx, why, allowlistable in PATTERNS:
            flag = "" if allowlistable else "  [NOT allowlistable]"
            print(f"  {pname}{flag}\n      {why}")
        print(f"\nInline allow marker: {INLINE_ALLOW!r}")
        print(f"Path allowlist:      {ALLOW_FILE.relative_to(REPO)}")
        return 0

    argv = sys.argv[1:]
    scope, source = "tracked files", None
    if "--staged" in argv:
        scope, source = "staged blobs (new files included)", staged_files()
    elif "--range" in argv:
        i = argv.index("--range")
        if i + 1 >= len(argv):
            print("ci-secret-scan: --range needs a revision range, e.g. "
                  "origin/main..HEAD", file=sys.stderr)
            return 64
        rev_range = argv[i + 1]
        scope, source = f"blobs introduced by {rev_range}", range_files(rev_range)

    try:
        findings = scan(source)
    except subprocess.CalledProcessError as exc:
        # A scope that cannot be READ is not a scope that is clean. Rule
        # 88e9b5eb: "not possible" and "not authorized" are not "no findings".
        cmd = " ".join(exc.cmd) if isinstance(exc.cmd, list) else str(exc.cmd)
        print(f"ci-secret-scan: could not read {scope} -- `{cmd}` failed. "
              "Reporting failure rather than a clean scan.", file=sys.stderr)
        return 2

    if not findings:
        print(f"secret scan: clean ({scope})")
        return 0

    print(f"secret scan: {len(findings)} finding(s) in {scope}\n",
          file=sys.stderr)
    for name, lineno, pname, length in findings:
        print(f"  {name}:{lineno}  {pname}  ({length} chars, value not printed)",
              file=sys.stderr)
    print(
        "\nIf a finding is documentation or a fixture, mark it at the site with"
        f"\n  {INLINE_ALLOW}"
        f"\non the same line, or add the path to {ALLOW_FILE.relative_to(REPO)}."
        "\nThe marker must be on the SAME LINE as the match."
        "\nIf it is a real credential: rotate it first, then remove it. Removing"
        "\nit from the working tree does NOT remove it from git history.",
        file=sys.stderr,
    )
    if source is not None and scope.startswith("blobs introduced"):
        # Reached only at push time, and the distinction is the whole reason the
        # remedy differs: the credential is already in a COMMIT. Removing it from
        # the tree and committing again leaves it reachable in the history this
        # push would publish.
        print(
            "\nThis scope reads commits, not the working tree. Deleting the line"
            "\nand committing again does NOT unpublish it -- the earlier commit"
            "\nstill carries it. Rotate the credential, then rewrite the branch"
            "\n(git rebase -i / git commit --amend) before pushing.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
