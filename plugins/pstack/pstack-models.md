---
description: pstack per-role model choices (overrides skill defaults)
alwaysApply: true
---
# pstack model configuration

Read this file before selecting a delegate. PORT.md defines the Model Room desk routes. Model slugs below are requested seat capabilities, not authorization to invoke model CLIs or to alter a live desk. Verify actual model and effort before dispatch and the returned result afterward.
The budget is large. Claude judgment and hardest-task roles retain Joe's explicit max override.
Code work uses Grok for fast tasks and Sol for the specified implementation roles.
`inherit-parent` and `auto` omit the model. Each panel entry still creates one seat.

```text
# budget: large (xhigh)
feature, refactoring: grok-4.7-xhigh-fast
bug-fix: gpt-6.1-sol-xhigh
perf-issue: gpt-6.1-sol-xhigh
hillclimb: grok-4.7-xhigh-fast
judgment and prose: claude-opus-5-5-max
hardest tasks: claude-opus-5-5-max
how explorer: grok-4.7-xhigh-fast
how explainer: claude-opus-5-5-max
why investigators: grok-4.7-xhigh-fast
why synthesizer: claude-opus-5-5-max
reflect tooling: gpt-6.1-sol-xhigh
reflect judgment, divergent, synthesizer: claude-opus-5-5-max
arena runners: claude-opus-5-5-max, gpt-6.1-sol-xhigh, grok-4.7-xhigh-fast
arena cross-judge pool: claude-opus-5-5-max, gpt-6.1-sol-xhigh, grok-4.7-xhigh-fast
swarm workers: grok-4.7-xhigh-fast
architect runners: claude-opus-5-5-max, gpt-6.1-sol-xhigh, grok-4.7-xhigh-fast
interrogate reviewers: claude-opus-5-5-max, gpt-6.1-sol-xhigh, grok-4.7-xhigh-fast
```
