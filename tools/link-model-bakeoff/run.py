"""Run all measurements on an owned temporary socket-only PostgreSQL 18 cluster."""
import argparse
import json
import hashlib
import os
import platform
import subprocess
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from bench import BASE_SIZES, SEED, contexts, dataset, digest, drift, fk_probe_diagnostic, integrity, load, writes
from cluster import Cluster
from inventory import scan
from linkfork_migration import rehearsal
from model import FAMILIES, KINDS, domain_ddl, edge_ddl
from report import render


def run(bin_dir, output, small=False):
    output = Path(output).resolve()
    output.mkdir(parents=True,exist_ok=True)
    multiplier = 1 if small else 10
    nodes, edges = dataset(multiplier)
    degrees = Counter((e[4],e[5]) for e in edges)
    counts = sorted(degrees.values())
    data = {'schema':'linkfork-bakeoff/v1','timestamp':datetime.now(timezone.utc).isoformat(),
            'seed':SEED,'inventory':scan(),'families':[vars(f) for f in FAMILIES],
            'scale':{'multiplier':multiplier,'nodes':{k:len(v) for k,v in nodes.items()},
                     'assumed_current':{k:BASE_SIZES.get(k,100) for k in KINDS},'edges':len(edges),
                     'assumed_current_edges_per_family':3000,'edges_by_family':dict(Counter(e[1] for e in edges)),
                     'target_degree':{'max':max(counts),'median_nonzero':counts[len(counts)//2]},
                     'assumption':'Assumed planning baseline, not a production census. Core sizes model a growing brokerage/knowledge system; auxiliary kinds assume 100 rows each. Full run multiplies every kind and each 3,000-edge family by ten. Parties represent contacts/persons and companies/orgs. Synthetic UUIDs are globally distinct across kinds; text IDs and digests are normalized solely for this identity-layer experiment.'},
            'environment':{'os':platform.platform(),'cpu':platform.processor(),'cores':os.cpu_count(),
                           'load_average_before':os.getloadavg(),'python':platform.python_version(),
                           'source_revision':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()},
            'methodology':'Five repetitions per side, interleaved reference order and shuffled write-side order; one run only in selftest mode. End-to-end SQL fetch/commit timing includes local client overhead. Identical data, indexes in both directions, statistics refreshed, warm cache, fsync on. EXPLAIN separates server execution and planning and exposes index probes plus recursive result aggregation. Graph equality is checked for every reference and all successful write cycles are verified removed. Concurrent hotspot holds intentionally add 1ms; throughput is for the specified workload on this host, not a production capacity estimate. Baseline C preserves identity-relevant constraints from the migration-defined families, including fixed-source FKs, selected kind checks and duplicate checks; metadata, envelopes, per-owner uniqueness, source revision pins, and verb validation are excluded. A and B add explicit relationship uniqueness as a proposed contract. Open target vocabularies use the declared synthetic core set in A/B. Inventory includes broader pointer candidates and is distinct from the timed relationship families. No network database, production census, Neon command, credentials, or paid model call is used.'}
    executed_sources = ['inventory.py','model.py','cluster.py','bench.py','linkfork_migration.py','report.py','run.py']
    data['harness_sha256'] = {name:hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest() for name in executed_sources}
    cluster = Cluster(bin_dir)
    try:
        with cluster:
            data['environment'].update(postgres=cluster.version,port=cluster.port,listen_addresses='',fsync=True,synchronous_commit=True,shared_buffers='256MB')
            with cluster.connect() as c:
                for design in 'ABC':
                    s = design.lower()
                    (output / ('design-' + s + '.sql')).write_text('\n'.join(stmt+';' for stmt in domain_ddl(s,design)+edge_ddl(s,design))+'\n')
                    load(c,s,design,nodes,edges)
                    print('loaded '+design,flush=True)
                hashes = {d:digest(c.execute(f'SELECT * FROM {d.lower()}.relationships ORDER BY id').fetchall()) for d in 'ABC'}
                assert len(set(hashes.values())) == 1
                data['initial_graph_sha256'] = hashes
                data['integrity'] = {d:integrity(c,d.lower(),d,nodes,edges) for d in 'ABC'}
            data['context'] = contexts(cluster,nodes,32 if small else 1000,1 if small else 5)
            data['writes'] = writes(cluster,nodes,20 if small else 200,1 if small else 5)
            with cluster.connect() as c:
                for d in 'ABC':
                    count = c.execute(f'SELECT count(*) FROM {d.lower()}.d_doctrine_section').fetchone()[0]
                    assert count == len(nodes['doctrine_section']), 'Leaked write-cycle domain row'
                    assert c.execute(f'SELECT count(*) FROM {d.lower()}.relationships').fetchone()[0] == len(edges)
                    assert digest(c.execute(f'SELECT * FROM {d.lower()}.relationships ORDER BY id').fetchall()) == hashes[d]
            data['drift'] = drift(cluster)
            data['migration'] = [rehearsal(cluster,d,nodes,output) for d in 'AB']
            data['fk_index_diagnostic'] = fk_probe_diagnostic(cluster,edges)
            data['environment']['load_average_after'] = os.getloadavg()
    finally:
        data['cluster_stopped'] = not cluster.running
        data['environment']['retained_cluster_path'] = str(cluster.root)
        (output / 'results.json').write_text(json.dumps(data,indent=2,default=str)+'\n')
    a, b = data['context']['designs']['A'], data['context']['designs']['B']
    data['verdict'] = f"A enforces links to domain rows and rejects cross-kind UUID mixups. B accepts registry-only targets and domain deletion behind live edges. C accepts orphan targets and cross-kind UUID mixups. B's measured context p50 is {b['p50_ms']:.3f} ms versus A's {a['p50_ms']:.3f} ms; p95 is {b['p95_ms']:.3f} versus {a['p95_ms']:.3f} ms. The default is conditional on requiring domain-row existence for every database writer."
    previous = output / 'untuned-results.json'
    if previous.exists():
        old = json.loads(previous.read_text())
        if old.get('superseded_reason'):
            indexes = sum(stmt.startswith('CREATE INDEX') and ' WHERE dst_' in stmt for stmt in edge_ddl('a','A'))
            data['index_tuning'] = {'nullable_fk_indexes_added':indexes,
                                    'old_A_delete_transactions_per_second':statistics.median(r['delete']['transactions_per_second'] for r in old['writes']['A']['runs']),
                                    'new_A_delete_transactions_per_second':statistics.median(r['delete']['transactions_per_second'] for r in data['writes']['A']['runs']),
                                    'old_run_file':'untuned-results.json','old_run_superseded':True}
    (output / 'results.json').write_text(json.dumps(data,indent=2,default=str)+'\n')
    (output / 'bakeoff.html').write_text(render(data))
    print(json.dumps({'output':str(output),'cluster_stopped':data['cluster_stopped'],'nodes':sum(len(v) for v in nodes.values()),'edges':len(edges)},indent=2),flush=True)
    return data


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin',default='/opt/homebrew/opt/postgresql@18/bin')
    parser.add_argument('--output',default='out/orch/linkfork')
    parser.add_argument('--small',action='store_true',help='Reduced integration/selftest workload; not a decision-quality run')
    args = parser.parse_args()
    run(args.pg_bin,args.output,args.small)
