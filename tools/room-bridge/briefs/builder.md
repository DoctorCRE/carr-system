Build <change> in <repository>. Size: <tiny / one session / larger>.
Read AGENTS.md and <source files>. Writes: <paths>.
Acceptance: <observable behavior and regression case>.
Before every push touching Python or Worker code, run `./ops/ci.sh --strict --only types && node --test mcp-server/test/worker-types.test.mjs` with pinned mypy on PATH.
Verify <targeted checks> and report PR number, head SHA, and test counts.
Open the PR with GitHub auto-merge turned on (squash, pinned to your head commit); it merges itself when CI is green. App screen changes open as drafts until the independent taste score passes.
If a decision is needed, put "QUESTION: ..." on the first line of your final report and end the turn.
Send this brief through dispatch.py send; its send-time picker appends the Required skills block with full SKILL.md texts, alongside brief-footer.md.
