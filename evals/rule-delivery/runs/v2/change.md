# v2: prompt cue: a brief that opens with an Executor: line delivers c20dc3d5

Goal: recall. Decision: reverted.

Gate: REVERT test recall_micro delta +0.0162 [+0.0000, +0.0375] not clear of zero

```
== train: v1 -> v2
  false_per_event       1.2337 ->    1.2297  delta -0.0040  [-0.0139, +0.0059]
  recall_micro          0.3342 ->    0.3568  delta +0.0226  [+0.0091, +0.0392]
  false_rules         623.0000 ->  621.0000  delta -2.0000  [-7.0000, +3.0000]
  sn_false_rules       79.0000 ->   79.0000  delta +0.0000  [+0.0000, +0.0000]
  tokens_per_event    567.9129 ->  572.4134  delta +4.5005  [+1.1609, +8.4855]
  precision             0.2270 ->    0.2362  delta +0.0091  [+0.0031, +0.0163]
== test: v1 -> v2
  false_per_event       1.2271 ->    1.2151  delta -0.0120  [-0.0272, +0.0000]
  recall_micro          0.4432 ->    0.4595  delta +0.0162  [+0.0000, +0.0375]
  false_rules         308.0000 ->  305.0000  delta -3.0000  [-7.0000, +0.0000]
  sn_false_rules       44.0000 ->   44.0000  delta +0.0000  [+0.0000, +0.0000]
  tokens_per_event    518.2500 ->  517.6614  delta -0.5886  [-2.0020, +0.4271]
  precision             0.2770 ->    0.2840  delta +0.0070  [+0.0000, +0.0164]
```
