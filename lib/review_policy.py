"""Shared review verdicts and deployment path matching."""
from __future__ import annotations

import re


def _glob_regex(glob: str) -> "re.Pattern[str]":
    """GitHub Actions path-filter semantics, so a glob means the same thing
    here as it does in a workflow's paths/paths-ignore: `*` and `?` never
    cross `/`, `**` crosses any number of directories, and `**/` also matches
    zero directories (`**/*.md` covers a top-level `README.md`, `a/**/*.md`
    covers `a/README.md`). fnmatch is NOT safe here: its `*` crosses `/`, so
    `docs/*.md` would silently swallow `docs/deep/x.md` and diverge from the
    canary's own filter."""
    out, i, n = ["^"], 0, len(glob)
    while i < n:
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    out.append("$")
    return re.compile("".join(out))


def _glob_hit(path: str, glob: str) -> bool:
    return bool(_glob_regex(glob).fullmatch(path))


def verdict(body: str, cfg: dict) -> str | None:
    """'approve', 'block' or None, read from the comment's FIRST line only."""
    first = (body or "").strip().splitlines()[0].strip().lower() if (body or "").strip() else ""
    if any(first.startswith(m.lower()) for m in cfg.get("block_markers") or []):
        return "block"
    if any(first.startswith(m.lower()) for m in cfg.get("review_markers") or []):
        return "approve"
    return None


def trusted_commenter(comment: dict, cfg: dict) -> bool:
    """Both repositories are public: anyone can comment on a merged PR. Only a
    comment from the owner, a member, a collaborator, or a configured login can
    carry a verdict."""
    assoc = str(comment.get("author_association") or "").upper()
    login = str((comment.get("user") or {}).get("login") or "").lower()
    allowed = {str(x).upper() for x in cfg.get("review_author_associations") or []}
    logins = {str(x).lower() for x in cfg.get("review_logins") or []}
    return assoc in allowed or (bool(login) and login in logins)


REVIEWED_SHA_LINE_RE = re.compile(r"Reviewed-SHA: ([0-9a-f]{40})")


def reviewed_header_sha(body: str) -> str | None:
    """Parse the review header used by the independent review tools."""
    lines = [line.removesuffix("\r") for line in (body or "").split("\n")]
    if len(lines) < 2 or lines[0] != "APPROVE":
        return None
    second = REVIEWED_SHA_LINE_RE.fullmatch(lines[1])
    if second is None or any("reviewed-sha:" in line.lower() for line in lines[2:]):
        return None
    return second.group(1)


def deciding_verdict(comments: list[dict], cfg: dict) -> dict | None:
    """Latest trusted review comment for the optional review displays."""
    carrying = [c for c in comments if trusted_commenter(c, cfg) and verdict(c.get("body", ""), cfg)]
    if not carrying:
        return None
    return max(carrying, key=lambda c: (str(c.get("created_at") or ""), int(c.get("id") or 0)))
