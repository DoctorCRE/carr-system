Build <change> in <repository>. Size: <tiny / one session / larger>.
Read AGENTS.md and <source files>. Writes: <paths>.
Acceptance: <observable behavior and regression case>.
Before every push touching Python or Worker code, run `./ops/ci.sh --strict --only types && node --test mcp-server/test/worker-types.test.mjs` with pinned mypy on PATH.
Verify <targeted checks>, open one PR, and report its number, head SHA, and test counts.
Send this brief through dispatch.py send; its send-time picker appends the Required skills block with full SKILL.md texts, alongside brief-footer.md.
