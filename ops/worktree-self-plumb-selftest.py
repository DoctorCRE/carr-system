#!/usr/bin/env python3
"""Check the existing SessionStart policy and plumbing contract.

Fleet retirement behavior is covered by branch-janitor-selftest.py.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(os.path.dirname(HERE), "hooks", "worktree-self-plumb.py")

spec = importlib.util.spec_from_file_location("worktree_self_plumb", HOOK)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

failures: list[str] = []


# Fresh carr-system sessions receive the same product-first policy Codex reads
# from AGENTS.md. The hook extracts that exact block rather than maintaining a
# second prose copy.
repo = os.path.dirname(HERE)
agents = open(os.path.join(repo, "AGENTS.md"), encoding="utf-8").read()
if agents.count(mod.POLICY_START) != 1 or agents.count(mod.POLICY_END) != 1:
    failures.append("AGENTS.md must contain exactly one product-first policy block")
policy = mod.delivery_policy_brief(repo)
normalized_policy = " ".join(policy.split())
for required in (
        "1facbf00-60d9-4cde-bfab-9798f1b6e307",
        "019146bd-15fb-4f5e-8849-ed63911469e0",
        "52880de2-ab90-4673-b046-b74f900aa2de@6",
        "179be4b8-2fe0-418d-9503-52d1e33921d3@3",
        "80e6d24c-6b49-4765-80c3-e05c1025ba38",
        "fetch the current section by its stable section ID",
        "an ordinary pull request",
        "b729859d-be5d-4521-ba50-d4517bc57208",
        "was never his rule",
        "without CARR approval gates",
):
    if required not in normalized_policy:
        failures.append(f"product-first boot policy is missing: {required}")
if mod.delivery_policy_brief(os.path.join(repo, "missing-policy-root")):
    failures.append("missing AGENTS.md must fail soft with no policy text")
with tempfile.TemporaryDirectory() as malformed_root:
    malformed_agents = os.path.join(malformed_root, "AGENTS.md")
    with open(malformed_agents, "w", encoding="utf-8") as fh:
        fh.write(f"{mod.POLICY_END}\ntext\n{mod.POLICY_START}\n")
    if mod.delivery_policy_brief(malformed_root):
        failures.append("reversed policy markers must fail soft")
    with open(malformed_agents, "w", encoding="utf-8") as fh:
        fh.write(f"{mod.POLICY_START}\n{mod.POLICY_START}\n{mod.POLICY_END}\n")
    if mod.delivery_policy_brief(malformed_root):
        failures.append("duplicate policy markers must fail soft")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    emitted = mod.emit_delivery_policy(repo)
if not emitted or buf.getvalue().strip() != policy:
    failures.append("SessionStart policy emission must equal the AGENTS.md block")
claude = open(os.path.join(repo, "CLAUDE.md"), encoding="utf-8").read()
if "Production stops at 0454" in claude:
    failures.append("Claude boot instructions retain a stale migration frontier")
for required in (
        "current canonical migration/release state",
        "Dated incidents/WRs are history",
        "grants no live authority",
):
    if required not in " ".join(claude.split()):
        failures.append(f"Claude current-state boot guidance is missing: {required}")


if failures:
    print("worktree-self-plumb selftest FAILED")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print("worktree-self-plumb selftest passed")

# Independently reproduced Dot cases share the offline behavioral fixtures.
import runpy as _dot_runpy
_dot_runpy.run_path(str(__import__("pathlib").Path(__file__).with_name("dot-review-selftest.py")))["run_regressions"](['test_b24'])
