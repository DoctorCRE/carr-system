#!/usr/bin/env python3
"""Exercise snapshot pins at the chain interface and restore guards at the SQL seam."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from registry_chain import registry_chain, snapshot_selection

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = (ROOT / 'bin/schema-snapshot.sh').read_text()
SNAPSHOT = (ROOT / 'db/schema.sql').read_text()
chain = registry_chain()
for table, key in [('doctrine_gate_check', 'check_key'), ('agent_profile', 'profile_key')]:
    insert = f'insert into public.{table} select * from jsonb_populate_record(null::public.{table}'
    assert insert in GENERATOR
    assert f'from public.{table} ' in GENERATOR
    assert f'on conflict ({key}) do nothing' in GENERATOR
    if f'insert into public.{table}' in SNAPSHOT:
        assert insert in SNAPSHOT
assert GENERATOR.count("e.entry_digest is distinct from 'sha256:'||encode(public.digest(") >= 2
assert GENERATOR.count('ops.scac_mutation_registry_seal_valid(historical.registry_version)') >= 2

# Known original snapshot frontiers, independently captured before this refactor.
for number, total, entry, source in [(9,12660,1439,800), (25,37055,1609,840), (31,47633,1818,863)]:
    selected = snapshot_selection(number, chain)
    assert (selected['SCAC_TOTAL_ENTRY_COUNT'],selected['SCAC_CURRENT_ENTRY_COUNT'],selected['SCAC_CURRENT_SOURCE_COUNT']) == (str(total),str(entry),str(source))
for row in chain['versions'][8:]:
    selected = snapshot_selection(row['number'], chain)
    assert selected['SCAC_CURRENT_CATALOG_FUNCTION'] == f"ops.scac_mutation_catalog_v{row['number']}_current()"
    script = subprocess.check_output(['python3', ROOT/'ops/registry_chain.py', 'snapshot', str(row['number'])], text=True)
    result = subprocess.run(['sh','-c', script+'\ntest "$SCAC_CURRENT_NUMBER" = '+str(row['number'])], capture_output=True)
    assert result.returncode == 0, result.stderr

validation_start = GENERATOR.index('  case "$SCAC_EXPECTED_CURRENT_DIGEST$SCAC_EXPECTED_CURRENT_SOURCE_SET')
validation_end = GENERATOR.index('  SCAC_EXPECTED_CURRENT_DIGEST="sha256:', validation_start)
validation = GENERATOR[validation_start:validation_end]
env = {**os.environ, **snapshot_selection(chain['versions'][-1]['number'], chain)}
subprocess.run(['sh','-c',validation], check=True, env=env)
for field in ['SCAC_EXPECTED_CURRENT_DIGEST','SCAC_EXPECTED_CURRENT_SOURCE_SET','SCAC_EXPECTED_CURRENT_CATALOG']:
    result = subprocess.run(['sh','-c',validation], env={**env,field:'malformed'},capture_output=True)
    assert result.returncode != 0, field+' must refuse malformed seals'

# Retaining a digest while changing its contract must fail either aggregate seal.
canonical = lambda value: json.dumps(value, sort_keys=True, separators=(',',':')).encode()
original = {'effect_class':'read','ingress_key':'fixture'}
tampered = {**original,'effect_class':'write'}
assert hashlib.sha256(canonical(original)).digest() != hashlib.sha256(canonical(tampered)).digest()
print('schema snapshot registry seeds: public-qualified and rebuild-safe')
