#!/usr/bin/env python3
"""bash-write-gate.py — the Bash door onto the write policies that already exist.

WHY THIS EXISTS, proven in one session on 2026-08-09 rather than argued: the
linkedin-engagement-daily run wrote a vault markdown file with a `python3 -c`
command from Bash and it SUCCEEDED. Minutes later the identical change through
Edit was BLOCKED with record-home-gate.py's full refusal text. Nothing about the
content differed. THE SAME CONTENT WAS REFUSED OR ALLOWED PURELY BY WHICH TOOL
CARRIED IT, because every write gate here matches Write|Edit|MultiEdit and Bash
is a door none of them watch. Rule 14181e60 (database first) is enforced by
record-home-gate.py, so it held only against sessions that were not going to
break it anyway.

THE THREAT MODEL IS CARELESSNESS, NOT EVASION, and that is not a guess. The run
that found this had no intent to bypass anything: it reached for Bash because the
content carried URLs another guard had just refused, discovered the write gate
only when Edit refused a follow-up correction, and then stopped rather than
repeating it. An unintentional bypass by a compliant session is stronger evidence
that a control is mis-scoped than a deliberate one would be. A gate written
against a determined adversary would need to be complete or worthless; a gate
written against a convenient tool needs to cover the shapes people reach for.

THIS GATE DECIDES NOTHING, WHICH IS THE WHOLE DESIGN. Two gates already hold the
judgment about where a write may land — record-home-gate.py owns the vault,
one-repo-gate.py owns code outside the repo. A third policy for Bash would be the
two-homes disease one layer down, and the copies would drift the first time
either changed. So this file EXTRACTS candidate write targets from a shell
command and hands each one to those policies unchanged. One judgment, three
doors: rule a8c55a47 (a manual path and an automated path that do the same job
must be the same code) applied to tool doors rather than to scripts.

WHAT IT EXTRACTS, and each shape is here because it is one somebody reaches for:
  · redirections — `> f`, `>> f`, `>| f`, `&> f`, `2> f`, with or without a space
  · heredocs written to a file — `cat > f <<EOF`, which is the redirect above
  · `tee f` and `tee -a f`
  · `sed -i` over a named file
  · `cp` / `mv` / `install` destinations
  · `dd of=f`, `truncate f`
  · inline interpreters — `python -c`, `python3 -c`, `node -e`, and heredocs fed
    to them — scanned for path literals ONLY when the code also carries a write
    indicator, so a read-only one-liner is not caught

TOKENISED, NOT REGEXED OVER RAW TEXT, and that difference is the gate's
survival. `git commit -m "throughput a > b now"` contains a `>` that is not a
redirect. A raw regex fires on it, on every commit whose message contains a
comparison, and the gate gets switched off within a day. shlex sees one quoted
token and correctly finds no redirection.

WHAT IT DELIBERATELY IGNORES: /dev/* targets, `2>&1` and its relatives, reads of
any kind, and every path both delegated policies allow.

THE RESIDUAL, NAMED RATHER THAN HIDDEN. Extraction cannot be complete against
arbitrary shell. A path assembled from variables at runtime, an interpreter this
file does not know, a script invoked by name that writes on its own, a base64
payload decoded and executed — none are seen here. This closes the shapes that
are actually used, not the space of things that are possible. Anyone reading this
should treat it as raising the cost of an accidental bypass, never as proof that
one cannot happen. The complete control for that is enforcement at the filesystem
or at the record layer, which is a larger build and a separate decision.

FAILS CLOSED ON DENY, OPEN ON ERROR — exit 2 plus stderr, matching
guard-unattended.py and record-home-gate.py. A gate that wedges a session costs
more than the marginal safety of failing closed on a single-operator machine, so
any internal error allows the call.
"""

import importlib.util
import json
import os
import re
import shlex
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cmd_text import shell_tokens, shell_operands, SHELL_BOUNDARIES
try:                                    # telemetry only — never load-bearing
    import hook_meter
    LOG = hook_meter.guard_log_path(REPO)
except Exception:                       # a missing meter must not change a verdict
    LOG = os.path.join(REPO, "out", "hook-guard.log")

REDIRECT = re.compile(r"^\d*&?>{1,2}\|?$")          # '>', '>>', '2>', '&>', '>|'
REDIRECT_ATTACHED = re.compile(r"^\d*&?>{1,2}\|?(?=\S)")
INTERPRETER_FLAG = {("python", "-c"), ("python3", "-c"), ("node", "-e"),
                    ("ruby", "-e"), ("perl", "-e")}
# A PATH INSIDE A WRITE CALL, not a path anywhere near one. The distinction cost
# a second false positive on the day this shipped.
#
# The first version paired two loose tests: "does this code contain any path
# literal" and, separately, "does this code contain any write indicator". Applied
# to a `python3 <<PY ... PY` heredoc it flagged a path the script only READ,
# because some other line in the same script wrote something else. A verification
# command of my own tripped it, which is how it was found — and it would fire on
# any script that reads one file and writes another, which is most scripts.
#
# Direction of error decides this, the same way it decided the unexpanded-variable
# case above: a false DENY blocks real work every time it fires, a false ALLOW
# only fails to catch something. So the path must sit INSIDE the write call
# itself. That still covers the shape this gate was built for — the 2026-08-09
# incident was `open("<vault file>","a").write(...)`, matched by the first pattern
# here — and it stops guessing about paths that merely appear nearby.
EMBEDDED_WRITE = [
    # open("PATH", "w"|"a"|"x"|"wb"…)
    re.compile(r"""open\s*\(\s*['"]([^'"\n]+)['"]\s*,\s*['"][awx]"""),
    # Path("PATH").write_text(…) / .write_bytes(…) / .open("w")
    re.compile(r"""Path\s*\(\s*['"]([^'"\n]+)['"]\s*\)\s*\.\s*(?:write_text|write_bytes|open\s*\(\s*['"][awx])"""),
    # fs.writeFileSync("PATH", …) / appendFileSync / createWriteStream
    re.compile(r"""(?:writeFileSync|appendFileSync|createWriteStream)\s*\(\s*['"]([^'"\n]+)['"]"""),
]


def log(line):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(f"bash-write-gate {line}\n")
    except Exception:
        pass


def load(module_name, filename):
    """Import a sibling gate so its POLICY is reused rather than reimplemented.

    Returns None on any failure, and the caller treats that as 'this policy has
    no opinion' — a missing sibling must not wedge Bash for the whole machine.
    """
    try:
        spec = importlib.util.spec_from_file_location(
            module_name, os.path.join(os.path.dirname(__file__), filename))
        if not spec or not spec.loader:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception as exc:
        log(f"policy {filename} unavailable: {exc}")
        return None


UNRESOLVED = re.compile(r"[$`*?]")


def is_real_target(token):
    """A path we should ask about, as opposed to a stream, a device, or a guess.

    THE UNRESOLVED CASE IS SKIPPED, and it is skipped because guessing produced a
    live false positive within a minute of this gate going in. `echo x > "$D/NOTES.md"`
    reaches this hook with `$D` UNEXPANDED — the shell has not run yet, and a
    PreToolUse hook never sees the expansion. The first version treated that text
    as a relative path, joined it to cwd, and because that session's cwd was the
    vault it produced `<vault>/$D/NOTES.md`, judged that vault markdown, and
    refused an ordinary write into a scratch directory.

    That is exactly the over-parsing the originating loop warned breeds the habit
    of switching a gate off, and it is worse than the gap it replaces: a false
    DENY blocks real work every time it fires, where a false ALLOW merely fails
    to catch something. A target whose value is not knowable at hook time is
    therefore not judged at all — it joins the residual already named in the
    docstring instead of being answered with a guess.

    Globs are skipped for the same reason: `> out/*.json` names no single file.
    """
    if not token or token.startswith("&"):
        return False
    if token.startswith("/dev/"):
        return False
    if UNRESOLVED.search(token):
        return False
    return True


def embedded_targets(code):
    """Paths that inline interpreter code opens FOR WRITING, and nothing else.

    Only what a write call names. A path the code merely reads, or mentions in a
    list, or prints, is not a write target and must not be judged as one — see
    EMBEDDED_WRITE above for what that cost when this was looser.
    """
    found = []
    for pattern in EMBEDDED_WRITE:
        for match in pattern.findall(code):
            if match not in found:
                found.append(match)
    return found


def extract_targets(command):
    """Every path this command plausibly WRITES. Order is not significant."""
    targets = []
    try:
        tokens = shell_tokens(command)
    except ValueError:
        # Unbalanced quotes — usually a heredoc body. Fall back to line-wise
        # parsing so `cat > f <<EOF` is still seen, and accept that a heredoc
        # body itself is not tokenised.
        tokens = []
        for line in command.splitlines():
            try:
                tokens.extend(shell_tokens(line))
            except ValueError:
                continue

    try:
        tokens, redirect_targets = shell_operands(tokens)
        targets.extend(redirect_targets)
    except ValueError:
        pass  # retain the conservative scan of malformed redirection tokens
    index = 0
    while index < len(tokens):
        token = tokens[index]

        if REDIRECT.match(token):
            if index + 1 < len(tokens):
                targets.append(tokens[index + 1])
                index += 2
                continue
        else:
            attached = REDIRECT_ATTACHED.match(token)
            if attached:
                targets.append(token[attached.end():])
                index += 1
                continue

        end = next((j for j in range(index + 1, len(tokens))
                    if tokens[j] in SHELL_BOUNDARIES), len(tokens))
        remainder = tokens[index + 1:end]
        base = os.path.basename(token)
        if base == "tee":
            for candidate in remainder:
                if candidate.startswith("-"):
                    continue
                if REDIRECT.match(candidate) or candidate in ("|", "&&", ";"):
                    break
                targets.append(candidate)
        elif base == "sed" and any(t == "-i" or t.startswith("-i") for t in
                                   tokens[index + 1:index + 4]):
            for candidate in remainder:
                if candidate.startswith("-") or REDIRECT.match(candidate):
                    continue
                targets.append(candidate)
        elif base in ("cp", "mv", "install", "rsync"):
            tail = [t for t in remainder
                    if not t.startswith("-") and not REDIRECT.match(t)]
            if len(tail) >= 2:
                targets.append(tail[-1])
        elif base in ("truncate", "touch"):
            for candidate in remainder:
                if not candidate.startswith("-"):
                    targets.append(candidate)
        elif token.startswith("of="):
            targets.append(token[3:])
        elif index + 1 < len(tokens) and (base, tokens[index + 1]) in INTERPRETER_FLAG:
            if index + 2 < len(tokens):
                targets.extend(embedded_targets(tokens[index + 2]))
        index += 1

    # A heredoc fed to an interpreter is not a token; scan the raw text for it.
    if "<<" in command:
        targets.extend(embedded_targets(command))

    seen, out = set(), []
    for target in targets:
        if is_real_target(target) and target not in seen:
            seen.add(target)
            out.append(target)
    return out


# FORGERY WARNING, NOT A BLOCK (round 3 of PR #1224, decision d47931da:
# "detectable, not prevented"). out/jev-calls.jsonl is the receipt ledger the
# completion-evidence Stop gate reads to decide whether a turn really called
# Jev, and ops/typesafe_client.py's ask() is its ONLY legitimate writer — from
# inside Python, so a real call's own shell command never names the file. A
# shell write naming it is therefore either a forged receipt or a mistake, and
# either way a human should see it. This gate WARNS loudly (a systemMessage to
# the operator plus additionalContext to the model, and a hook-guard.log line)
# and lets the call run; the Stop gate independently records a detection event
# for the same turn. A read (tail/grep/wc) is not warned on.
JEV_CALLS_BASENAME = "jev-calls.jsonl"
JEV_INLINE_WRITE = re.compile(
    r"""\.write\s*\(|write_text|write_bytes|writeFileSync|appendFileSync|"""
    r"""createWriteStream|open\s*\([^)]*['"][awx]""")


def jev_calls_write_targets(command, targets):
    """The write targets in `command` that are the Jev call ledger, or a
    one-item marker list when the command names the ledger AND carries an
    inline interpreter write call the extractor could not bind to a literal
    path (a variable path, say). [] when the command only reads it."""
    if JEV_CALLS_BASENAME not in command:
        return []
    hits = [t for t in targets if os.path.basename(t) == JEV_CALLS_BASENAME]
    if hits:
        return hits
    if JEV_INLINE_WRITE.search(command):
        return [f"<inline write in a command naming {JEV_CALLS_BASENAME}>"]
    return []


def warn_jev_calls_write(hits):
    note = (
        "JEV RECEIPT LEDGER WRITE DETECTED: this command writes "
        f"{', '.join(hits)}. out/{JEV_CALLS_BASENAME} is written ONLY by "
        "ops/typesafe_client.py's ask() on a real Jev response; a row written "
        "any other way is a forged Jev receipt. The command is allowed, but "
        "this warning is logged, and the completion-evidence Stop gate records "
        "a detection event for this turn (decision d47931da)."
    )
    log(f"WARN(jev-calls-ledger-write) {', '.join(hits)[:160]}")
    print(json.dumps({
        "systemMessage": note,
        "hookSpecificOutput": {"hookEventName": "PreToolUse",
                               "additionalContext": note},
    }))


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        log(f"ALLOW(parse-error) {exc}")
        sys.exit(0)
    try:
        tool = payload.get("tool_name") or payload.get("toolName") or ""
        if tool not in ("Bash", "functions.exec"):
            sys.exit(0)
        tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
        command = (tool_input or {}).get("command") or ""
        if not command.strip():
            sys.exit(0)

        targets = extract_targets(command)
        jev_hits = jev_calls_write_targets(command, targets)
        if not targets:
            if jev_hits:
                warn_jev_calls_write(jev_hits)
            sys.exit(0)

        cwd = payload.get("cwd") or os.getcwd()
        record_home = load("carr_record_home_policy", "record-home-gate.py")
        one_repo = load("carr_one_repo_policy", "one-repo-gate.py")

        for raw in targets:
            path = os.path.expanduser(raw)
            if not os.path.isabs(path):
                path = os.path.join(cwd, path)
            path = os.path.abspath(path)

            reason = None
            if record_home is not None:
                try:
                    reason = record_home.check("Write", {"file_path": path})
                except Exception as exc:
                    log(f"record-home policy errored on {path}: {exc}")
            if reason is None and one_repo is not None:
                try:
                    reason = one_repo.check({"file_path": path}, cwd)
                except Exception as exc:
                    log(f"one-repo policy errored on {path}: {exc}")

            if reason:
                text = (
                    f"BLOCKED by the CARR bash-write gate: {path}\n"
                    f"{reason}\n"
                    "This came through Bash rather than Write or Edit, and the "
                    "answer is the same either way — that is the point of this "
                    "gate. The same content must not be refused or allowed "
                    "purely by which tool carries it.\n"
                    "Re-route the content through the record verbs rather than "
                    "around the gate (rule 76a53dfe)."
                )
                log(f"DENY {path} :: {reason[:160]}")
                print(text, file=sys.stderr)
                sys.exit(2)
        if jev_hits:
            warn_jev_calls_write(jev_hits)
        sys.exit(0)
    except Exception as exc:
        log(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
