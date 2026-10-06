# Rule-delivery eval results

| variant | split | recall (micro) | recall (macro) | false deliveries / event | quiet cases with a false delivery | tokens / event | precision |
|---|---|---|---|---|---|---|---|
| baseline | train | 0.334 | 0.303 | 1.319 | 40/83 | 602 | 0.216 |
| baseline | test | 0.443 | 0.378 | 1.490 | 40/63 | 638 | 0.240 |
| v1 | train | 0.334 | 0.303 | 1.234 | 24/83 | 568 | 0.227 |
| v1 | test | 0.443 | 0.378 | 1.227 | 15/63 | 518 | 0.277 |
| v2 | train | 0.357 | 0.355 | 1.230 | 24/83 | 572 | 0.236 |
| v2 | test | 0.459 | 0.397 | 1.215 | 15/63 | 518 | 0.284 |
| v3 | train | 0.362 | 0.337 | 1.222 | 24/83 | 570 | 0.239 |
| v3 | test | 0.465 | 0.402 | 1.227 | 15/63 | 531 | 0.284 |

## Rounds

* v1 (kept, goal tokens): path_rule routes do not fire on read-only tools (Read/Grep/Glob/LS). KEEP 
* v2 (reverted, goal recall): prompt cue: a brief that opens with an Executor: line delivers c20dc3d5. REVERT test recall_micro delta +0.0162 [+0.0000, +0.0375] not clear of zero
* v3 (kept, goal recall): prompt cue: a request for an independent/adversarial review or a Codex review delivers 2b66211d. KEEP 
