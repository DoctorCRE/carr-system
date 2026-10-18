"""Deterministic workload, integrity probes, and end-to-end write measurements."""
import concurrent.futures
import hashlib
import json
import random
import statistics
import threading
import time
from collections import Counter
from uuid import UUID

import psycopg
from model import CORE, FAMILIES, KINDS, CONTEXT, domain_ddl, edge_ddl, edge_columns, edge_table, insert_edge, values

SEED = 610106
BASE_SIZES = dict(party=5000,lead=2000,deal=500,rule=250,doctrine_section=500,loop=1000,decision=200)


def uid(n):
    return UUID(int=n)


def dataset(multiplier=10):
    rng, counter = random.Random(SEED), 1
    nodes = {}
    for kind in KINDS:
        size = BASE_SIZES.get(kind, 100) * multiplier
        nodes[kind] = [uid(n) for n in range(counter, counter + size)]
        counter += size
    edges, seen = [], set()
    for f in FAMILIES:
        count = 3000 * multiplier
        for _ in range(count):
            tk = rng.choice(f.targets)
            pool = nodes[tk]
            # Twenty percent of targets share a one-percent hot set.
            tid = rng.choice(pool[:max(1,len(pool)//100)] if rng.random() < .2 else pool)
            sid, rel = rng.choice(nodes[f.source]), rng.choice(f.relations)
            key = (f.name,tk,tid) if f.identity == 'target' else (f.name,sid,tk,tid) if f.identity == 'pair' else (f.name,sid,tk,tid,rel)
            if key in seen:
                continue
            seen.add(key)
            edges.append((uid(counter),f.name,f.source,sid,tk,tid,rel))
            counter += 1
    return nodes, edges


def load(c, s, design, nodes, edges):
    for stmt in domain_ddl(s,design) + edge_ddl(s,design):
        c.execute(stmt)
    if design == 'B':
        with c.cursor().copy(f'COPY {s}.entity(id,kind) FROM STDIN') as cp:
            for kind, ids in nodes.items():
                for id_ in ids:
                    cp.write_row((id_,kind))
    for kind, ids in nodes.items():
        with c.cursor().copy(f'COPY {s}.d_{kind}(id,payload) FROM STDIN') as cp:
            for id_ in ids:
                cp.write_row((id_,'synthetic-' + kind))
    by_family = {f.name:f for f in FAMILIES}
    groups = {}
    for e in edges:
        key = (e[1],e[4]) if design != 'B' else ('all','all')
        groups.setdefault(key,[]).append(e)
    for group in groups.values():
        f, target = by_family[group[0][1]], group[0][4]
        with c.cursor().copy(f'COPY {edge_table(s,design,f)}({",".join(edge_columns(design,target))}) FROM STDIN') as cp:
            for e in group:
                cp.write_row(values(design,e))
    c.commit()
    c.execute('ANALYZE')
    c.commit()
    actual = c.execute(f'SELECT count(*) FROM {s}.relationships').fetchone()[0]
    assert actual == len(edges), (s,actual,len(edges))


def digest(rows):
    return hashlib.sha256(json.dumps(rows,default=str,separators=(',',':')).encode()).hexdigest()


def percentile(xs, p):
    return sorted(xs)[min(len(xs)-1, int((len(xs)-1)*p))]


def summary(xs):
    return {'p50_ms':statistics.median(xs),'p95_ms':percentile(xs,.95),'min_ms':min(xs),'max_ms':max(xs)}


def contexts(cluster, nodes, samples=1000, repeats=5):
    refs = [(kind,id_) for kind,ids in nodes.items() for id_ in ids]
    rng = random.Random(SEED + 1)
    selected = rng.sample(refs,samples)
    connections = {d:cluster.connect() for d in 'ABC'}
    results = {d:{'runs':[],'errors':0} for d in 'ABC'}
    fingerprints, cardinalities = {}, []
    try:
        for c in connections.values():
            c.autocommit = True
        for d,c in connections.items():
            for ref in selected[:30]:
                c.execute(CONTEXT.format(s=d.lower()),ref).fetchall()
        for repeat in range(repeats):
            durations = {d:[] for d in 'ABC'}
            for idx,ref in enumerate(selected):
                order = list('ABC')
                rng.shuffle(order)
                for d in order:
                    start = time.perf_counter_ns()
                    rows = connections[d].execute(CONTEXT.format(s=d.lower()),ref).fetchall()
                    durations[d].append((time.perf_counter_ns()-start)/1e6)
                    fp = digest(rows)
                    if idx in fingerprints:
                        assert fp == fingerprints[idx], 'Graph result mismatch'
                    else:
                        fingerprints[idx] = fp
                        cardinalities.append(len(rows))
            for d in 'ABC':
                results[d]['runs'].append(summary(durations[d]))
            print(f'context repeat {repeat+1}/{repeats} complete',flush=True)
        for d,c in connections.items():
            plan = c.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) ' + CONTEXT.format(s=d.lower()),selected[0]).fetchone()[0]
            results[d].update(p50_ms=statistics.median(r['p50_ms'] for r in results[d]['runs']),
                              p95_ms=statistics.median(r['p95_ms'] for r in results[d]['runs']),plan=plan,
                              p50_range_ms=[min(r['p50_ms'] for r in results[d]['runs']),max(r['p50_ms'] for r in results[d]['runs'])])
    finally:
        for c in connections.values():
            c.close()
    return {'designs':results,'samples_per_run':samples,'repeats':repeats,'refs_sha256':digest(selected),
            'graph_fingerprints_sha256':digest(fingerprints),'cardinality':{'median':statistics.median(cardinalities),'max':max(cardinalities)},
            'prepared_statement_policy':'psycopg automatic prepare_threshold=5. Timed warm queries reuse prepared plans; the EXPLAIN query is planned separately.',
            'definition':'Bidirectional, distinct vertices at minimum depth, includes root, depth <= 2. Full fetch and sort timed over Unix socket; warm cache; no result cap.'}


def integrity(c, s, design, nodes, edges):
    f = FAMILIES[0]
    base = (uid(900000001),f.name,f.source,nodes[f.source][0],'party',nodes['party'][0],'citation')
    def add(tk='party',tid=None,sid=None,rel='citation',family=None):
        e = (base[0],family or f.name,f.source,sid or base[3],tk,tid or base[5],rel)
        insert_edge(c,s,design,f,e)
    tests = []
    def probe(name, action, expected):
        c.execute('SAVEPOINT probe')
        state = None
        try:
            action()
            c.execute('SET CONSTRAINTS ALL IMMEDIATE')
        except psycopg.Error as exc:
            state = exc.sqlstate
        finally:
            c.execute('ROLLBACK TO SAVEPOINT probe')
            c.execute('RELEASE SAVEPOINT probe')
        rejected = state is not None
        assert rejected == expected, (design,name,state,expected)
        tests.append({'test':name,'rejected':rejected,'sqlstate':state})
    for tk in ('party','deal','rule','doctrine_section','loop','decision'):
        probe('orphan target ' + tk,lambda tk=tk:add(tk,uid(800000001)),design != 'C')
    probe('orphan source',lambda:add(sid=uid(800000002)),True)
    probe('party label with deal UUID',lambda:add('party',nodes['deal'][0]),design != 'C')
    probe('rule label with loop UUID',lambda:add('rule',nodes['loop'][0]),design != 'C')
    probe('update link to orphan',lambda:(add(),c.execute(f'UPDATE {edge_table(s,design,f)} SET ' + ('dst_party' if design == 'A' else 'dst_id') + '=%s WHERE id=%s',(uid(800000003),base[0]))),design != 'C')
    def delete_target():
        add()
        c.execute(f'DELETE FROM {s}.d_party WHERE id=%s',(base[5],))
    probe('delete domain target leaving edge',delete_target,design == 'A')
    def deleted_target():
        c.execute(f'DELETE FROM {s}.d_party WHERE id=%s',(base[5],))
        add()
    probe('link to deleted domain target',deleted_target,design == 'A')
    def delete_source():
        add()
        c.execute(f'DELETE FROM {s}.d_doctrine_section WHERE id=%s',(base[3],))
    probe('delete source leaving edge',delete_source,design != 'B')
    original = next(e for e in edges if e[1] == f.name)
    duplicate = (base[0],*original[1:])
    probe('duplicate doctrine edge',lambda:insert_edge(c,s,design,f,duplicate),design != 'C')
    incident = next(e for e in edges if e[1] == 'incident_link')
    probe('duplicate incident edge',lambda:insert_edge(c,s,design,FAMILIES[1],(base[0],*incident[1:])),True)
    for name in ('f01_derivative_link','j102_document_link','siep_evidence_link'):
        family = next(f for f in FAMILIES if f.name == name)
        original_edge = next(e for e in edges if e[1] == name)
        probe('duplicate '+name,lambda family=family,e=original_edge:insert_edge(c,s,design,family,(base[0],*e[1:])),True)
    probe('unknown relation',lambda:add(rel='invented'),True)
    probe('unsupported target kind',lambda:add('lead',nodes['lead'][0]),True)
    if design == 'A':
        probe('two populated arc targets',lambda:c.execute(f'INSERT INTO {s}.l_doctrine_link(id,src_id,dst_party,dst_deal,relation) VALUES (%s,%s,%s,%s,%s)',(base[0],base[3],base[5],nodes['deal'][0],'citation')),True)
        probe('zero populated arc targets',lambda:c.execute(f'INSERT INTO {s}.l_doctrine_link(id,src_id,relation) VALUES (%s,%s,%s)',(base[0],base[3],'citation')),True)
    else:
        # Analogous requests at the raw storage seam, without an application validator.
        cols = 'id,src_id,dst_kind,dst_id,relation' if design == 'C' else 'id,family,src_kind,src_id,dst_kind,dst_id,relation'
        vals = (base[0],base[3],None,None,'citation') if design == 'C' else (base[0],f.name,f.source,base[3],None,None,'citation')
        probe('zero populated arc targets',lambda:c.execute(f'INSERT INTO {edge_table(s,design,f)}({cols}) VALUES ({",".join(["%s"]*len(vals))})',vals),True)
        probe('two populated arc targets',lambda:add('party,deal'),True)
    if design == 'B':
        probe('domain row without registry',lambda:c.execute(f'INSERT INTO {s}.d_party(id,payload) VALUES (%s,%s)',(uid(800000004),'bad')),True)
        def wrong_domain():
            c.execute(f'INSERT INTO {s}.entity VALUES (%s,%s)',(uid(800000004),'deal'))
            c.execute(f'INSERT INTO {s}.d_party(id,payload) VALUES (%s,%s)',(uid(800000004),'bad'))
        probe('wrong registry kind for domain',wrong_domain,True)
        def phantom():
            c.execute(f'INSERT INTO {s}.entity VALUES (%s,%s)',(uid(800000005),'party'))
            add('party',uid(800000005))
        probe('registry phantom target',phantom,False)
        probe('delete linked registry row',lambda:(add(),c.execute(f'DELETE FROM {s}.entity WHERE id=%s',(base[5],))),True)
    c.rollback()
    return tests


def write_cycle(c, s, design, nodes, serial):
    f, row_id = FAMILIES[0], uid(1000000000 + serial * 10)
    es = [(uid(row_id.int+i+1),f.name,f.source,row_id,k,nodes[k][serial % len(nodes[k])],'citation') for i,k in enumerate(('party','deal','rule'))]
    times = []
    start = time.perf_counter_ns()
    with c.transaction():
        if design == 'B':
            c.execute(f'INSERT INTO {s}.entity VALUES (%s,%s)',(row_id,f.source))
        c.execute(f'INSERT INTO {s}.d_doctrine_section(id,payload) VALUES (%s,%s)',(row_id,'write'))
        for e in es:
            insert_edge(c,s,design,f,e)
    times.append((time.perf_counter_ns()-start)/1e6)
    start = time.perf_counter_ns()
    with c.transaction():
        c.execute(f'UPDATE {s}.d_doctrine_section SET payload=%s WHERE id=%s',('updated',row_id))
        for e in es:
            column = 'dst_' + e[4] if design == 'A' else 'dst_id'
            c.execute(f'UPDATE {edge_table(s,design,f)} SET {column}=%s WHERE id=%s',(nodes[e[4]][(serial+1)%len(nodes[e[4]])],e[0]))
    times.append((time.perf_counter_ns()-start)/1e6)
    start = time.perf_counter_ns()
    with c.transaction():
        c.execute(f'DELETE FROM {edge_table(s,design,f)} WHERE src_id=%s',(row_id,))
        c.execute(f'DELETE FROM {s}.d_doctrine_section WHERE id=%s',(row_id,))
        if design == 'B':
            c.execute(f'DELETE FROM {s}.entity WHERE id=%s',(row_id,))
    times.append((time.perf_counter_ns()-start)/1e6)
    return times


def writes(cluster, nodes, count=200, repeats=5):
    output = {d:{'runs':[],'concurrent':[]} for d in 'ABC'}
    rng = random.Random(SEED)
    for repeat in range(repeats):
        order = list('ABC')
        rng.shuffle(order)
        for d in order:
            with cluster.connect() as c:
                c.autocommit = True
                timings = [write_cycle(c,d.lower(),d,nodes,repeat*count+i) for i in range(count)]
                run = {op:{**summary([row[j] for row in timings]),'transactions_per_second':count/(sum(row[j] for row in timings)/1000)} for j,op in enumerate(('insert','update','delete'))}
                output[d]['runs'].append(run)
            for hotspot in (False,True):
                stop, barrier = threading.Event(), threading.Barrier(8)
                lock_samples = []
                monitor_errors = []
                def monitor():
                    try:
                        with cluster.connect('linkfork-monitor') as c:
                            c.autocommit = True
                            while not stop.wait(.01):
                                n = c.execute("SELECT count(*) FROM pg_stat_activity WHERE application_name='linkfork-writer' AND wait_event_type='Lock'").fetchone()[0]
                                lock_samples.append(n)
                    except Exception as exc:
                        monitor_errors.append(exc)
                watcher = threading.Thread(target=monitor)
                watcher.start()
                def worker(w):
                    with cluster.connect('linkfork-writer') as c:
                        c.autocommit = True
                        barrier.wait(timeout=10)
                        ts = []
                        for i in range(max(10,count//4)):
                            if hotspot:
                                start = time.perf_counter_ns()
                                with c.transaction():
                                    c.execute(f'UPDATE {d.lower()}.d_doctrine_section SET payload=payload WHERE id=%s',(nodes['doctrine_section'][0],))
                                    c.execute(f'UPDATE {edge_table(d.lower(),d,FAMILIES[0])} SET relation=relation WHERE src_id=%s',(nodes['doctrine_section'][0],))
                                    c.execute('SELECT pg_sleep(0.001)')
                                ts.append((time.perf_counter_ns()-start)/1e6)
                            else:
                                ts.append(sum(write_cycle(c,d.lower(),d,nodes,500000+repeat*10000+w*1000+i)))
                        return ts
                start = time.perf_counter()
                try:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                        timings = list(pool.map(worker,range(8)))
                finally:
                    stop.set()
                    watcher.join()
                if monitor_errors:
                    raise RuntimeError('lock monitor failed; measurement unavailable') from monitor_errors[0]
                if not lock_samples:
                    raise RuntimeError('lock monitor produced no samples; measurement unavailable')
                elapsed = time.perf_counter()-start
                flat = [v for ts in timings for v in ts]
                output[d]['concurrent'].append({'repeat':repeat,'workload':'hotspot update + links (1ms intentional hold)' if hotspot else 'independent insert/update/delete cycles',
                                                'writers':8,'operations':len(flat),'errors':0,'elapsed_s':elapsed,
                                                'operations_per_second':len(flat)/elapsed,**summary(flat),
                                                'lock_samples':len(lock_samples),'samples_with_waiters':sum(n>0 for n in lock_samples),
                                                'max_waiters':max(lock_samples,default=0),'sampled_waiter_ms':sum(lock_samples)*10})
        print(f'write repeat {repeat+1}/{repeats} complete',flush=True)
    return output


def drift(cluster):
    results = []
    for d in 'ABC':
        for midpoint in ('after registry or first domain write','after domain before links'):
            id_ = uid(800000100)
            victim = cluster.connect()
            with cluster.connect() as killer:
                pid = victim.execute('SELECT pg_backend_pid()').fetchone()[0]
                if d == 'B':
                    victim.execute('INSERT INTO b.entity VALUES (%s,%s)',(id_,'party'))
                else:
                    victim.execute(f'INSERT INTO {d.lower()}.d_party(id,payload) VALUES (%s,%s)',(id_,'crash'))
                if midpoint.startswith('after domain') and d == 'B':
                    victim.execute('INSERT INTO b.d_party(id,payload) VALUES (%s,%s)',(id_,'crash'))
                assert killer.execute('SELECT pg_terminate_backend(%s)',(pid,)).fetchone()[0]
            victim.close()
            with cluster.connect() as c:
                domain = c.execute(f'SELECT count(*) FROM {d.lower()}.d_party WHERE id=%s',(id_,)).fetchone()[0]
                registry = c.execute('SELECT count(*) FROM b.entity WHERE id=%s',(id_,)).fetchone()[0] if d == 'B' else None
                assert domain == 0 and registry in (None,0)
                results.append({'design':d,'midpoint':midpoint,'domain_rows_after_kill':domain,'registry_rows_after_kill':registry,'drift':False})
    with cluster.connect() as c:
        c.execute('INSERT INTO b.entity VALUES (%s,%s)',(uid(800000101),'party'))
    with cluster.connect() as c:
        phantom = c.execute('SELECT count(*) FROM b.entity e LEFT JOIN b.d_party p ON p.id=e.id WHERE e.id=%s AND p.id IS NULL',(uid(800000101),)).fetchone()[0]
        assert phantom == 1
        c.execute('DELETE FROM b.entity WHERE id=%s',(uid(800000101),))
    results.append({'design':'B','midpoint':'committed registry-only bypass','registry_phantoms':phantom,'drift':True})
    return results


def fk_probe_diagnostic(cluster, edges):
    with cluster.connect() as c:
        c.execute('CREATE TABLE fk_probe(id uuid PRIMARY KEY,dst_doctrine_section uuid)')
        selected = [e for e in edges if e[1] == 'attachment']
        with c.cursor().copy('COPY fk_probe(id,dst_doctrine_section) FROM STDIN') as cp:
            for e in selected:
                cp.write_row((e[0],e[5] if e[4] == 'doctrine_section' else None))
        c.execute('ANALYZE fk_probe')
        query = 'EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) SELECT 1 FROM ONLY fk_probe p WHERE dst_doctrine_section=%s FOR KEY SHARE OF p'
        target = next(e[5] for e in selected if e[4] == 'doctrine_section')
        before = c.execute(query,(target,)).fetchone()[0]
        c.execute('CREATE INDEX ON fk_probe(dst_doctrine_section) WHERE dst_doctrine_section IS NOT NULL')
        c.execute('ANALYZE fk_probe')
        after = c.execute(query,(target,)).fetchone()[0]
        assert before[0]['Plan']['Actual Rows'] == after[0]['Plan']['Actual Rows']
    return {'purpose':'Profiling-only FK lookup shape, outside the timed graph/write workloads. Same attachment projection rows before/after a nullable-column index.',
            'rows':len(selected),'before':before,'after':after}
