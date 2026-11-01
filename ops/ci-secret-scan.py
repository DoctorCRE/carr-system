#!/usr/bin/env python3
"""Detect secrets with pinned Gitleaks; retain shared redaction and identity checks.

Scopes are index blobs (--staged), commits (--range A..B or --history), and
tracked working files (default). --redact remains a stdin filter for CI and Dream.
"""
import json
import os
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

PRIVATE_KEY_KINDS = ('PRIVATE KEY', 'RSA PRIVATE KEY', 'EC PRIVATE KEY', 'DSA PRIVATE KEY',
                     'ENCRYPTED PRIVATE KEY', 'OPENSSH PRIVATE KEY', 'PGP PRIVATE KEY',
                     'PGP PRIVATE KEY BLOCK')
PRIVATE_KEY_STARTS = tuple('-----BEGIN ' + kind + '-----' for kind in PRIVATE_KEY_KINDS)
PRIVATE_KEY_BLOCK = re.compile(
    r'-----BEGIN (' + '|'.join(map(re.escape, PRIVATE_KEY_KINDS))
    + r')-----[\s\S]*?(?:-----END \1-----|\Z)')


# Redaction/evidence patterns retained for current importers. Detection lives in .gitleaks.toml.
# (name, regex, rationale, legacy metadata)
PATTERNS = [
    ("private-key-block",
     PRIVATE_KEY_BLOCK,
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


def identity_changed_files():
    """Read changed working files against the event's base, or local main.

    Historical applied SQL remains checksum immutable. It is not an exemption:
    any edit to such a file enters this scope and must pass the identity check.
    """
    base = None
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_path:
        event = json.loads(pathlib.Path(event_path).read_text())
        base = event.get("pull_request", {}).get("base", {}).get("sha") or event.get("before")
        if base == "0" * 40:
            base = None
    if base is None:
        try:
            base = _git("merge-base", "origin/main", "HEAD").stdout.decode().strip()
            # On a main push the remote ref can already name the current tip.
            if base == _git("rev-parse", "HEAD").stdout.decode().strip():
                # Keep unstaged branch edits in scope; fall back one commit only
                # on a hosted run where main may already have advanced.
                if event_path:
                    base = _git("rev-parse", "HEAD^").stdout.decode().strip()
        except subprocess.CalledProcessError:
            yield from tracked_files()
            return
    for name in _names("diff", "--name-only", "--diff-filter=ACMR", "-z", base):
        path = REPO / name
        if path.exists():
            text = _decode(path.read_bytes())
            if text is not None:
                yield name, text


class Redactor:
    """Redact ordered text chunks; private blocks remain hidden across chunks."""

    def __init__(self):
        self.end_marker: str | None = None
        self.pending = ''

    def redact(self, text, *, final=False):
        text = self.pending + text
        self.pending = ''
        if self.end_marker:
            end = text.find(self.end_marker)
            if end < 0:
                self.pending = text[-(len(self.end_marker) - 1):]
                return ''
            text = text[end + len(self.end_marker):]
            self.end_marker = None

        def private_block(match):
            end_marker = '-----END ' + match[1] + '-----'
            if not match[0].endswith(end_marker):
                self.end_marker = end_marker
                self.pending = match[0][-(len(end_marker) - 1):]
            return '<redacted:private-key-block>'

        for name, pattern, _, _ in PATTERNS:
            if name == 'private-key-block':
                text = pattern.sub(private_block, text)
            else:
                text = pattern.sub(lambda m, n=name: f'<redacted:{n}:{len(m[0])} chars>', text)
        if not final and not self.end_marker:
            for size in range(min(len(text), max(map(len, PRIVATE_KEY_STARTS)) - 1), 0, -1):
                if any(marker.startswith(text[-size:]) for marker in PRIVATE_KEY_STARTS):
                    self.pending, text = text[-size:], text[:-size]
                    break
        return text


def redact(text):
    """Mask credential shapes in logs without applying detection allowlists."""
    return Redactor().redact(text, final=True)


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

    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument('--staged', action='store_true')
    scope.add_argument('--range')
    scope.add_argument('--history', action='store_true')
    args = parser.parse_args()
    argv = sys.argv[1:]
    rev_range = args.range

    import pii_guard
    identity_source = (staged_files() if "--staged" in argv else
                       range_files(rev_range) if "--range" in argv else
                       identity_changed_files())
    try:
        identity_status = pii_guard.check(
            identity_source, REPO / "ops/config/public-source-identities.v1.json",
            output=sys.stderr)
    except (OSError, ValueError, subprocess.CalledProcessError):
        print("ops/ci-secret-scan.py:1", file=sys.stderr)
        return 2
    if identity_status:
        return identity_status

    from gitleaks import detect
    try:
        return detect(REPO, redactor=redact, rev_range=args.range, history=args.history,
                      files=staged_files() if args.staged else tracked_files()
                      if not (args.range or args.history) else None)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(redact(str(exc)), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
