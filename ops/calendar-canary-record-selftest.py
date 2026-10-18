#!/usr/bin/env python3
"""Hermetic contract checks for lease-bound Calendar canary evidence."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from migration_source import read_migration

import importlib.util
ROOT=Path(__file__).resolve().parents[1]; failed: list[str]=[]
def check(label,ok):
 print(('  ok  ' if ok else '  FAIL ')+label)
 if not ok: failed.append(label)
child=(ROOT/'tools/calendar-canary-result.py').read_text(); runner=(ROOT/'tools/control-plane.py').read_text(); capture=(ROOT/'bin/calendar-eventkit-capture.sh').read_text(); sql=read_migration(ROOT, '0226_calendar_canary_record_layer.sql')
check('child emits only typed aggregate and has no database seam','psycopg' not in child and 'CARR_DB_' not in child and 'canary-result' in child)
check('parent alone parses and mints exact receipt','_calendar_canary_aggregate' in runner and 'record_calendar_canary_receipt' in runner and 'resolve_calendar_canary_receipt' in runner)
check('receipt binds exact job-definition lease, Calendar v5 deterministic canary and attempt','p_lease uuid' in sql and 'from ops.job_definition where key=j.definition_key and version=j.definition_version' in sql and "k<>'deterministic'" in sql and "j.mode<>'canary'" in sql and 'unique(job_id,attempt)' in sql)
check('receipt is append-only and cannot touch live activities','calendar_canary_receipt_append_only' in sql and 'log-activity' not in sql and 'activity' not in sql)
check('strict child marker occurs before intake/live writes',capture.index('calendar-canary-result.py') < capture.index('calendar-intake-gate.py'))
check('parent recomputes both digests and rejects bool counts','type(counts.get(k)) is int' in child and 'hashlib.sha256' in runner and 'set(value)' in runner and 'type(count) is int' in runner)
spec=importlib.util.spec_from_file_location('calendar_matcher',ROOT/'tools/calendar-touch-matcher.py')
assert spec is not None and spec.loader is not None
matcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(matcher)
emails,domains=matcher.load_record_contacts([{'email':'z@same.example','ref':'C-1','name':'Client','org':'Client Org'},{'email':'a@same.example','ref':'L-1','name':'Lead','org':'Lead Org'}])
check('global snapshot order preserves client domain precedence','jsonb_agg(x order by source_rank' in sql and domains['same.example'].startswith('C-1'))
check('matcher envelope requires exact key set','set(envelope)!={"source_snapshot_id","snapshot_digest","contact_count","snapshot_text"}' in (ROOT/'tools/calendar-touch-matcher.py').read_text())
print(f'calendar canary record selftest — {8-len(failed)}/8 passed'); raise SystemExit(bool(failed))
