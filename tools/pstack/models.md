# pstack model configuration. One line per role. Delete a line to fall back to the skill default.
# inherit-parent and auto omit the model. Panel aliases still count toward fan-out.
# budget: large (xhigh)
# Routine builders use gpt-6.1-terra at the same effort. Coordinators use claude-opus-5-5.
# Verifiers use the other family from their builder. No Grok until balance is restored.
feature, refactoring: gpt-6.1-sol-xhigh
bug-fix: gpt-6.1-sol-xhigh
perf-issue: gpt-6.1-sol-xhigh
hillclimb: gpt-6.1-sol-xhigh
judgment and prose: claude-opus-5-5-xhigh
hardest tasks: gpt-6.1-sol-xhigh
how explorer: gpt-6.1-sol-xhigh
how explainer: claude-opus-5-5-xhigh
why investigators: gpt-6.1-sol-xhigh
why synthesizer: claude-opus-5-5-xhigh
reflect tooling: gpt-6.1-sol-xhigh
reflect judgment, divergent, synthesizer: claude-opus-5-5-xhigh
arena runners: claude-opus-5-5-xhigh, gpt-6.1-sol-xhigh
arena cross-judge pool: claude-opus-5-5-xhigh, gpt-6.1-sol-xhigh
swarm workers: claude-opus-5-5-xhigh
architect runners: claude-opus-5-5-xhigh, gpt-6.1-sol-xhigh
interrogate reviewers: claude-opus-5-5-xhigh, gpt-6.1-sol-xhigh
