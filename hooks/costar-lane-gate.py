#!/usr/bin/env python3
"""costar-lane-gate.py — CoStar is driven in the Browser pane, never Chrome
(rule f5d97b4a).

THE RULE, in Joe's own words: "CoStar is driven ONLY in the Claude desktop
app's own Browser pane. NEVER Chrome, never the Chrome extension, which CoStar
blocks on the first click."

THE HOLE THIS CLOSES, from the 2026-08-14 enforceability audit — bucket U,
enforcement specified and not built — and re-probed still open the hour this
was written. Every route to CoStar through the Chrome extension was allowed by
the installed chain: navigate to the URL, type it into the address bar, set
location.href through javascript_tool, or bundle any of those into
browser_batch. One matcher already covers those tools,
mcp__claude-in-chrome__.*, and it belongs to model-floor-gate.py, which is
about model tiers and never looks at the destination.

WHY THE RULE IS WORTH A GATE RATHER THAN A REMINDER. The cost of breaking it is
not a style point: CoStar blocks the extension on the FIRST click, and what is
lost is Joe's own access to the platform the deal work runs on. The recitation
at boot has no bite at the moment the tool is called, and the moment the tool
is called is the only moment that matters.

WHY IT IS ENFORCEABLE WHERE MOST OF ITS NEIGHBOURS ARE NOT. The binding
condition is a HOST (rule 5e89c211: never spend a cognition token on a decision
a predicate can make). Most of the audit's partial rows are honestly advisory
because their condition needs judgment. This one does not.

THE LANE SPLIT IS WHAT MAKES A DENY SAFE HERE. The sanctioned lane and the
banned lane are different tool NAMESPACES:

    mcp__Claude_Browser__*    the desktop app's own Browser pane — sanctioned
    mcp__claude-in-chrome__*  the Chrome extension — what the rule forbids

A refusal therefore never wedges a session: the same work is still doable one
namespace over, on the same URL. That is why this ships with NO escape hatch
where its neighbours have one. An escape exists to rescue a session that cannot
otherwise finish, and there is no such session here — only a different tool
name. (Fourth ruling, 2026-08-14: a carve-out is for costs that are only a
rephrase; here the alternative is not even a rephrase.)

MATCHING IS ON THE HOST, NOT ON THE SUBSTRING. Two false DENYs shipped into
live gates on 2026-08-14, both from a gate ANSWERING a question it could not
answer instead of declining. A Google search for "costar comps" has host
google.com and passes; costar.com sitting in a path, a query string, a filename
or ordinary prose passes. Only a URL whose host IS costar.com, or a subdomain
of it, is the banned lane. A gate with a documented gap beats a gate that
guesses, and the error here is asymmetric in the ordinary direction: a false
DENY blocks real work every time it fires.

THE STATED GAP, so it is documented rather than papered over. This sees the
tool INPUT, so it catches every call that names where to go. It cannot see a
CoStar tab that is already open in Joe's Chrome and is then driven by
coordinate clicks alone, because no host appears anywhere in that payload.
Closing that would mean guessing at a tab's contents from a hook, which is the
2026-08-14 mistake. What it does cover is every way a session CHOOSES to go
there, which is every way a session gets there on its own.

FAILS OPEN on every error and on any input it cannot parse.

Fixtures: ops/costar-lane-gate-selftest.py.
"""

import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:                                    # telemetry only — never load-bearing
    import hook_meter
    DEBUG = hook_meter.guard_log_path(REPO)
except Exception:                       # a missing meter must not change a verdict
    DEBUG = os.path.join(REPO, "out", "hook-guard.log")

# The banned lane. The sanctioned Browser pane is mcp__Claude_Browser__* and is
# deliberately absent — matching it would eat the only route to CoStar there is.
CHROME_TOOL = re.compile(r"^mcp__claude-in-chrome__", re.I)

# Host-shaped tokens. Either after a scheme, or standing alone as a bare host
# at the start of a token — `costar.com/search` is how a person types it. The
# leading (?<![\w.@-]) keeps `notcostar.com` and `mail@costar.com.example` out.
HOST_TOKEN = re.compile(
    r"(?<![\w.@-])(?:https?://)?((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,})",
    re.I)

# The domain the rule names. A subdomain of it is the same platform; a domain
# that merely ENDS in these letters (notcostar.com) is not, which is why this
# compares whole labels rather than doing a suffix match on the string.
BANNED_DOMAIN = "costar.com"


def dlog(msg):
    try:
        os.makedirs(os.path.dirname(DEBUG), exist_ok=True)
        with open(DEBUG, "a") as fh:
            fh.write(f"costar-lane {msg}\n")
    except Exception:
        pass


def texts(value):
    """Every string anywhere in the tool input, however nested.

    browser_batch carries its real targets one level down in a list of action
    objects, so a scan of the top-level values only would miss exactly the
    call the rule calls out by name.
    """
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from texts(v)
    elif isinstance(value, list):
        for v in value:
            yield from texts(v)


def banned_host(text):
    """The host this text would send a browser to, if it is the banned one.

    Returns the offending host, or None. Host POSITION is the whole test: the
    same letters in a path, a query string or a sentence are not a destination.
    """
    for match in HOST_TOKEN.finditer(text or ""):
        # Only the host itself — everything from the first '/' on is a path.
        host = match.group(1).lower().rstrip(".")

        # Whatever this token sits inside, up to the preceding whitespace.
        # Position within that token is what separates a destination from a
        # mention, so both tests below read it rather than the whole string.
        before = text[:match.start()]
        token_head = before.split()[-1] if before.split() else ""

        # A host token found mid-path is a path segment, not a destination:
        # `example.com/costar.com/notes` must not read as CoStar. The `//`
        # exemption keeps the scheme's own slashes from matching.
        if token_head.endswith("/") and not token_head.endswith("//"):
            continue
        # Nor is a query string a destination: `?q=costar.com` is a search.
        if "?" in token_head:
            continue

        labels = host.split(".")
        if len(labels) >= 2 and ".".join(labels[-2:]) == BANNED_DOMAIN:
            return host
    return None


REASON = (
    "COSTAR LANE — refused. This is the Chrome extension, and CoStar is driven "
    "ONLY in the Claude desktop app's own Browser pane (rule f5d97b4a). The "
    "target host is {host}.\n\n"
    "This is not a style preference. CoStar blocks the extension on the FIRST "
    "click, and what that costs is Joe's own access to the platform the deal "
    "work runs on. Joe's words when he taught it: \"delete any workflows about "
    "accessing costar in google chrome\".\n\n"
    "DO THE SAME THING IN THE SANCTIONED LANE — same URL, one namespace over:\n"
    "    mcp__Claude_Browser__navigate      instead of mcp__claude-in-chrome__navigate\n"
    "    mcp__Claude_Browser__computer      instead of mcp__claude-in-chrome__computer\n"
    "    mcp__Claude_Browser__read_page     instead of mcp__claude-in-chrome__read_page\n"
    "If the Browser pane is not open yet, preview_start with the url opens it.\n\n"
    "AND ONCE YOU ARE IN IT, move slowly like a human: one action at a time, "
    "read the result before the next, and never browser_batch on CoStar even "
    "when the tooling suggests it. Prefer one market-level query over repeated "
    "city searches, and export through the saved \"Sale Export\" or \"Lease "
    "Export\" layout into the client's source-exports folder. If CoStar ever "
    "challenges or blocks, stop there and hand it back to Joe.\n\n"
    "There is no escape hatch here on purpose: the work is not blocked, only "
    "the lane is, and the other lane is always open."
)


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.hook_runtime import decision, run


def _parse_error(exc):
    dlog(f"ALLOW(parse-error) {exc}")
    return 0


def _decision_error(exc):
    dlog(f"ALLOW(internal-error) {exc}")
    return 0


@decision(on_error=_decision_error)
def decide(payload):
    tool = payload.get("tool_name") or payload.get("toolName") or ""
    if not CHROME_TOOL.match(tool or ""):
        sys.exit(0)

    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    for text in texts(tool_input):
        host = banned_host(text)
        if host:
            dlog(f"DENY {tool} -> {host}")
            print(REASON.format(host=host), file=sys.stderr)
            sys.exit(2)

    sys.exit(0)


def main():
    sys.exit(run(decide, parse_error=_parse_error))


if __name__ == "__main__":
    sys.exit(main())
