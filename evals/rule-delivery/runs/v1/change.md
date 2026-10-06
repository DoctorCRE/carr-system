# v1: path_rule routes do not fire on read-only tools (Read/Grep/Glob/LS)

Goal: tokens. Decision: kept.

Gate: KEEP 

```
== train: baseline -> v1
  false_per_event       1.3188 ->    1.2337  delta -0.0851  [-0.1396, -0.0405]
  recall_micro          0.3342 ->    0.3342  delta +0.0000  [+0.0000, +0.0000]
  false_rules         666.0000 ->  623.0000  delta -43.0000  [-70.0000, -21.0000]
  sn_false_rules      114.0000 ->   79.0000  delta -35.0000  [-60.0000, -15.0000]
  tokens_per_event    602.4411 ->  567.9129  delta -34.5282  [-53.2220, -18.5660]
  precision             0.2155 ->    0.2270  delta +0.0115  [+0.0052, +0.0197]
== test: baseline -> v1
  false_per_event       1.4900 ->    1.2271  delta -0.2629  [-0.4008, -0.1472]
  recall_micro          0.4432 ->    0.4432  delta +0.0000  [+0.0000, +0.0000]
  false_rules         374.0000 ->  308.0000  delta -66.0000  [-98.0000, -39.0000]
  sn_false_rules      105.0000 ->   44.0000  delta -61.0000  [-91.0000, -34.0000]
  tokens_per_event    637.9612 ->  518.2500  delta -119.7112  [-174.3245, -72.7509]
  precision             0.2398 ->    0.2770  delta +0.0372  [+0.0203, +0.0592]
```
