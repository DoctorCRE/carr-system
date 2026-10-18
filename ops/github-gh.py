#!/usr/bin/env python3
"""Shared gh wrapper; the watchdog calls it directly from tracked source.

GET pagination uses one budgeted CLI request per page. Other gh subcommands
may make several hidden requests; invocation counts are a lower bound. Provider
holds still apply. Installing it on legacy PATHs is a separate action; the
wrapper neither installs itself nor changes credentials.
Native commands inherit stdin and preserve binary stdout. Mutations run once.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.github_reader import GitHubReader, GitHubUnreadable
from lib.github_rate_limit import GitHubReadPaused, resource_for, split_response_bytes


def native(reader, args):
    if args and args[0] in ('auth', 'config', 'completion', 'version', '--version', 'help', '--help', '-h'):
        return subprocess.call([reader.gh, *args])
    budget = reader.budget
    if budget is None:
        print('GitHub budget unavailable; no request made', file=sys.stderr)
        return 1
    resource = resource_for(args)
    try:
        delay = budget.reserve(resource)
        if delay:
            time.sleep(delay)
        budget.check(resource)
        observed_at = budget.clock()
        include = bool(args and args[0] == 'api' and '--include' not in args and '-i' not in args)
        invocation = [*args, '--include'] if include else args
        result = subprocess.run([reader.gh, *invocation], capture_output=True)
        headers, body = split_response_bytes(result.stdout) if args and args[0] == 'api' else ({}, result.stdout)
        diagnostic = reader._redact(result.stderr.decode('utf-8', errors='replace'))
        retry_at = budget.observe(resource, headers, diagnostic, observed_at)
    except GitHubReadPaused as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (RuntimeError, ValueError, OverflowError):
        print('GitHub budget unreadable; no retry attempted', file=sys.stderr)
        return 1
    except OSError:
        print('GitHub CLI could not start', file=sys.stderr)
        return 127
    sys.stdout.buffer.write(body if include else result.stdout)
    if diagnostic:
        print(diagnostic, file=sys.stderr)
    if result.returncode and retry_at is not None:
        print(f'CARR_GITHUB_PROVIDER_HOLD: retry at {retry_at:.3f}', file=sys.stderr)
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    real = os.environ.get("GH_LIMITER_REAL")
    if not real:
        candidates = [shutil.which("gh"), "/opt/homebrew/bin/gh", "/usr/local/bin/gh"]
        real = next((p for p in candidates if p and Path(p).resolve() != Path(__file__).resolve() and os.access(p, os.X_OK)), None)
    if not real:
        print("GitHub CLI unavailable", file=sys.stderr)
        return 127
    reader = GitHubReader(gh=real, retry_delays=())
    try:
        if args and args[0] == "api" and "--paginate" in args:
            parser_args = args[1:]
            path, query, slurp = None, None, False
            while parser_args:
                arg = parser_args.pop(0)
                if arg == "--paginate":
                    continue
                if arg == "--slurp":
                    slurp = True
                elif arg in ("--jq", "-q") and parser_args:
                    query = parser_args.pop(0)
                elif arg.startswith("-") or path is not None:
                    raise GitHubUnreadable("Bounded pagination supports GET paths, --slurp and --jq only", kind="invalid_response")
                else:
                    path = arg
            if not path:
                raise GitHubUnreadable("Bounded pagination requires a GET path", kind="invalid_response")
            if query and not shutil.which("jq"):
                raise GitHubUnreadable("jq unavailable; paginated read stopped", kind="invalid_response")
            pages = reader.api(path, paginate=True, slurp=True)
            outputs = []
            for payload in ([pages] if slurp else pages):
                output = json.dumps(payload) + "\n"
                if query:
                    result = subprocess.run(["jq", "-r", query], input=output, capture_output=True, text=True)
                    if result.returncode:
                        raise GitHubUnreadable("Invalid jq filter", kind="invalid_response")
                    output = result.stdout
                outputs.append(output)
            output = "".join(outputs)
        else:
            return native(reader, args)
        sys.stdout.write(output)
        return 0
    except GitHubUnreadable as exc:
        # Local refusals deliberately omit gh's provider-error prefixes.
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
