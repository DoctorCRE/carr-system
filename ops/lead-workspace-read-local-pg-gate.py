#!/usr/bin/env python3
# ci: db-gate
# ci: db-gate-before-isolated
"""Compare complete Leads projections and plans on a synthetic disposable branch."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.disposable_pg_fixture import DisposablePostgres, postgres_fixture_group

bins = next((Path(p) for p in ('/opt/homebrew/opt/postgresql@18/bin', '/usr/lib/postgresql/18/bin') if (Path(p) / 'initdb').exists()), Path(shutil.which('initdb') or '/usr/bin/initdb').parent)
clean = {k: os.environ[k] for k in ('PATH', 'HOME', 'USER', 'TMPDIR') if k in os.environ}

def view(filename, name):
    sql = re.sub(r'--[^\n]*', '', (ROOT / 'migrations' / filename).read_text())
    return re.search(r'create (?:or replace )?view ' + name + r' as\n.*?;', sql, re.S | re.I)[0]

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--browser-app', type=Path)
parser.add_argument('--browser-base')
args = parser.parse_args()
if args.browser_app and not args.browser_base:
    parser.error('--browser-base must pin the application revision before the change')

with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
with postgres_fixture_group(), DisposablePostgres('lead-read-proof-', bins / 'pg_ctl', env=clean) as fixture:
    data = fixture.root / 'data'
    fixture.run([bins / 'initdb', '-D', data, '-U', 'postgres', '--auth=trust', '--no-locale'], capture_output=True, check=True, timeout=30)
    fixture.run([bins / 'pg_ctl', '-D', data, '-l', fixture.root / 'postgres.log', '-o',
                 f'-h 127.0.0.1 -p {port} -k {fixture.root}', '-w', 'start'], capture_output=True, check=True, timeout=30)
    def sql(source):
        result = subprocess.run([str(bins / 'psql'), '-X', '-v', 'ON_ERROR_STOP=1', '-h', '127.0.0.1',
            '-p', str(port), '-U', 'postgres', '-d', 'postgres', '-Atq'], input=source, text=True,
            capture_output=True, timeout=60, env={**clean, 'PGOPTIONS': '-c statement_timeout=30000 -c lock_timeout=2000'})
        if result.returncode:
            raise RuntimeError(result.stderr)
        return result.stdout.strip()
    sql((ROOT / 'test/fixtures/lead-workspace-read.sql').read_text())
    sql(view('0102_lead_client_link.sql', 'v_lead_client_link'))
    sql(view('0847_lead_score_owner_reader_repair.sql', 'v_lead_board'))
    lifecycle = view('0845_lead_archived_stage.sql', 'v_lead_workspace_lifecycle')
    workspace = view('0847_lead_score_owner_reader_repair.sql', 'v_lead_workspace')
    sql(lifecycle + workspace)
    sql('create role synthetic_reader; grant select on v_lead_workspace to synthetic_reader')
    sql('create view before_lifecycle as ' + lifecycle.split(' as\n', 1)[1])
    # Freeze both old definitions, so replacing lifecycle cannot change the control.
    sql('create view before_workspace as ' + workspace.split(' as\n', 1)[1].replace('v_lead_workspace_lifecycle', 'before_lifecycle'))
    def rows(name):
        return json.loads(sql(f"select coalesce(jsonb_agg(to_jsonb(w) order by id),'[]') from {name} w"))
    before = rows('before_workspace')
    migration = ROOT / 'migrations/0954_lead_workspace_set_reads.sql'
    if migration.exists():
        sql(migration.read_text())
    after = rows('v_lead_workspace')
    assert before == after, 'complete workspace output changed'
    assert json.loads(sql("set role synthetic_reader; select jsonb_agg(to_jsonb(w) order by id) from v_lead_workspace w")) == before, 'reader grant changed'
    assert rows('before_lifecycle') == rows('v_lead_workspace_lifecycle'), 'lifecycle output changed'
    by_ref = {row['registry_ref']: row for row in after}
    assert all(f'L-{i}' not in by_ref for i in (2, 3, 4)), 'suppressed or tombstoned lead leaked'
    assert sorted(by_ref['L-1']['possible_clients'], key=lambda row: row['name']) == [
        {'client_id': sql("select md5('client-501')::uuid"), 'name': ' SYNTHETIC AMBIGUOUS '},
        {'client_id': sql("select md5('client-502')::uuid"), 'name': 'synthetic ambiguous'},
    ], 'ambiguous clients changed or tombstones leaked'
    assert not by_ref['L-1']['linked_client'] and not by_ref['L-1']['is_client'], 'name ambiguity became an exact link'
    assert by_ref['L-5']['do_not_contact'] and not by_ref['L-5']['contact_eligible']
    assert by_ref['L-6']['is_client'] and by_ref['L-6']['linked_client']
    assert by_ref['L-7']['linked_client'] and by_ref['L-7']['is_deal']
    assert by_ref['L-8']['linked_client'], 'same-org link lost'
    assert not by_ref['L-9']['linked_client'], 'merged client linked'
    assert by_ref['L-10']['last_stage_move']['to'] == 'qualified', 'mutation order lost'
    assert by_ref['L-11']['contact_eligible'] is None, 'nullable contact policy changed'
    plans: dict[str, list[dict]] = {'before': [], 'after': []}
    for _ in range(5):
        for side, name in [('before', 'before_workspace'), ('after', 'v_lead_workspace')]:
            plans[side].append(json.loads(sql(f'''explain(analyze,buffers,format json)
                select jsonb_agg(to_jsonb(w) order by stage_sort,score desc nulls last,name,id) from {name} w'''))[0])
    def nodes(node):
        yield node
        for child in node.get('Plans', []):
            yield from nodes(child)
    repeated = [node for node in nodes(plans['after'][0]['Plan'])
        if node.get('Relation Name') in ('party', 'client') and node['Node Type'] == 'Seq Scan' and node['Actual Loops'] > 1]
    report = {'fixture': {'parties': 1480, 'leads': 375, 'visible': len(after)}, 'equal': before == after,
        'checks': 14, 'samples_ms': {k: [p['Execution Time'] for p in v] for k, v in plans.items()},
        'plans': plans, 'repeated_party_client_scans': repeated}
    out = ROOT / 'out/leads-speed'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'sql.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'plans'}, indent=2), flush=True)
    assert not repeated, 'party/client still sequentially scanned once per lead'
    print('PASS: 14 synthetic output, reader grant, lifecycle, eligibility, ambiguity and plan checks')
    if args.browser_app:
        class WorkspaceAPI(BaseHTTPRequestHandler):
            def do_GET(self):
                name = {'/before': 'before_workspace', '/after': 'v_lead_workspace'}.get(self.path)
                if name is None:
                    self.send_error(404)
                    return
                body = json.dumps({'schema_version': 'lead-workspace.v1',
                    'generated_at': '2026-01-01T00:00:00Z', 'leads': rows(name)}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), WorkspaceAPI)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            subprocess.run(['node', '--test', '--test-name-pattern=measure five', 'test/leads-session-browser.test.mjs'],
                cwd=args.browser_app, env={**clean, 'LEADS_BENCHMARK_BASE': args.browser_base,
                'LEADS_BENCHMARK_BACKEND': f'http://127.0.0.1:{server.server_port}'}, check=True, timeout=90)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
