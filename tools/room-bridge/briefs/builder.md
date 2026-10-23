Build <change> in <repository>. Size: <tiny / one session / larger>.
Read AGENTS.md and <source files>. Writes: <paths>.
Acceptance: <observable behavior and regression case>.
Before every push touching Python or Worker code, run `./ops/ci.sh --strict --only types && node --test mcp-server/test/worker-types.test.mjs` with pinned mypy on PATH.
Verify <targeted checks>, open one PR, and report its number, head SHA, and test counts.
Run `bin/codex-job <owner/repo> <new-branch> "<brief>"` as a tracked background task and read its printed result. The launcher selects skills once; model and effort use Codex config defaults.
