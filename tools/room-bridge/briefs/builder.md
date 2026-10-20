Build <change> in <repository>. Size: <tiny / one session / larger>.
Read AGENTS.md and <source files>. Writes: <paths>.
Acceptance: <observable behavior and regression case>.
Before every push touching Python or Worker code, run `./ops/ci.sh --strict --only types && node --test mcp-server/test/worker-types.test.mjs` with pinned mypy on PATH.
Verify <targeted checks>, open one PR, and report its number, head SHA, and test counts.
Send this brief through dispatch.py send; it appends brief-footer.md.
Run grill-with-docs autonomously in desk jobs: take your recommended answer at each frontier, record every question, answer, and rejected alternative in the PR's Decisions section, and continue until the frontier is empty. Stop for the orchestrator only on Joe-only decisions: money, outbound or client-facing items, credentials, identity edits, or rule retirement.
