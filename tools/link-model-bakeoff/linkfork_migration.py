"""Shadow-copy migration with transactional CDC, replay, and bounded cutover."""
import threading
import time
from pathlib import Path

from bench import digest, write_cycle
from model import FAMILIES, KINDS, domain_ddl, edge_ddl, edge_columns, edge_table, source_fk_actions


def plan(s, design):
    queue = s + '_cdc'
    stmts = domain_ddl(s,design) + edge_ddl(s,design)
    stmts += [f'CREATE SCHEMA {queue}',
              f'CREATE TABLE {queue}.changes(seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,table_name text NOT NULL,operation text NOT NULL,row_data jsonb NOT NULL,old_id uuid)',
              f'''CREATE FUNCTION {queue}.capture() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO {queue}.changes(table_name,operation,row_data,old_id)
 VALUES (TG_TABLE_NAME,TG_OP,CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END,
         CASE WHEN TG_OP='INSERT' THEN NULL ELSE OLD.id END);
 RETURN NULL;
END $$''']
    tables = ['d_' + k for k in KINDS] + ['l_' + f.name for f in FAMILIES]
    stmts += [f'CREATE TRIGGER {queue}_capture AFTER INSERT OR UPDATE OR DELETE ON c.{table} FOR EACH ROW EXECUTE FUNCTION {queue}.capture()' for table in tables]
    return stmts, tables


def backfill(s, design):
    stmts = []
    if design == 'B':
        stmts.append(f'INSERT INTO {s}.entity(id,kind) ' + ' UNION ALL '.join(f"SELECT id,'{kind}' FROM c.d_{kind}" for kind in KINDS))
    stmts += [f'INSERT INTO {s}.d_{kind}(id,payload) SELECT id,payload FROM c.d_{kind}' for kind in KINDS]
    if design == 'B':
        stmts.append(f'INSERT INTO {s}.edge SELECT * FROM c.relationships')
    else:
        for f in FAMILIES:
            cols = ['id','src_id'] + ['dst_' + k for k in f.targets] + ['relation']
            expressions = ['id','src_id'] + [f"CASE WHEN dst_kind='{k}' THEN dst_id END" for k in f.targets] + ['relation']
            stmts.append(f'INSERT INTO {s}.l_{f.name}({",".join(cols)}) SELECT {",".join(expressions)} FROM c.l_{f.name}')
    return stmts


def apply_branches(s, design):
    branches = []
    for kind in KINDS:
        registry_insert = f"INSERT INTO {s}.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'{kind}') ON CONFLICT DO NOTHING;" if design == 'B' else ''
        registry_delete = f"DELETE FROM {s}.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);" if design == 'B' else ''
        cascade_delete = '\n'.join(
            f"DELETE FROM {s}.edge WHERE family='{f.name}' AND src_kind='{kind}' AND src_id=(r.row_data->>'id')::uuid;"
            for f in FAMILIES if design == 'B' and f.source == kind and source_fk_actions().get(f.name) == 'CASCADE')
        if cascade_delete:
            cascade_delete = f"IF r.operation='DELETE' THEN {cascade_delete} END IF;\n "
        branches.append(f'''WHEN 'd_{kind}' THEN
 {cascade_delete}IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM {s}.d_{kind} WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  {registry_delete}
 END IF;
 IF r.operation<>'DELETE' THEN
  {registry_insert}
  INSERT INTO {s}.d_{kind}(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF;''')
    for f in FAMILIES:
        table = edge_table(s,design,f)
        if design == 'B':
            cols = ['id','family','src_kind','src_id','dst_kind','dst_id','relation']
            vals = ["(r.row_data->>'id')::uuid",f"'{f.name}'",f"'{f.source}'","(r.row_data->>'src_id')::uuid","r.row_data->>'dst_kind'","(r.row_data->>'dst_id')::uuid","r.row_data->>'relation'"]
        else:
            cols = ['id','src_id'] + ['dst_' + k for k in f.targets] + ['relation']
            vals = ["(r.row_data->>'id')::uuid","(r.row_data->>'src_id')::uuid"] + [f"CASE WHEN r.row_data->>'dst_kind'='{k}' THEN (r.row_data->>'dst_id')::uuid END" for k in f.targets] + ["r.row_data->>'relation'"]
        update = ','.join(f'{col}=EXCLUDED.{col}' for col in cols[1:])
        branches.append(f'''WHEN 'l_{f.name}' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM {table} WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO {table}({','.join(cols)}) VALUES ({','.join(vals)}) ON CONFLICT(id) DO UPDATE SET {update};
 END IF;''')
    return ' '.join(branches)


def replay_sql(s, design):
    def touched(table):
        return f"SELECT (row_data->>'id')::uuid FROM {s}_cdc.changes WHERE table_name='{table}' UNION SELECT old_id FROM {s}_cdc.changes WHERE table_name='{table}' AND old_id IS NOT NULL"

    stmts = []
    tables = ['d_' + k for k in KINDS] + ['l_' + f.name for f in FAMILIES]
    # Locks give all final-state reads one drained source boundary. Old edge
    # references must disappear before removing obsolete domain identities.
    stmts.append('LOCK TABLE ' + ','.join('c.' + t for t in tables) + ' IN SHARE MODE')
    for f in FAMILIES:
        stmts.append(f'DELETE FROM {edge_table(s,design,f)} WHERE id IN ({touched("l_"+f.name)})')
    for kind in KINDS:
        ids = touched('d_' + kind)
        missing = f'SELECT id FROM {s}.d_{kind} WHERE id IN ({ids}) AND NOT EXISTS (SELECT 1 FROM c.d_{kind} source WHERE source.id={s}.d_{kind}.id)'
        stmts.append(f'DELETE FROM {s}.d_{kind} WHERE id IN ({missing})')
        if design == 'B':
            stmts.append(f"DELETE FROM {s}.entity WHERE kind='{kind}' AND id IN ({ids}) AND NOT EXISTS (SELECT 1 FROM c.d_{kind} source WHERE source.id={s}.entity.id)")
    rows = ' UNION ALL '.join(f"SELECT '{table}'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,{int(table.startswith('l_'))} stage FROM c.{table} source WHERE id IN ({touched(table)})" for table in tables)
    stmts.append(f'''FOR r IN {rows} ORDER BY stage LOOP
 CASE r.table_name {apply_branches(s,design)} ELSE RAISE EXCEPTION 'unknown CDC table'; END CASE;
 END LOOP''')
    return f'''CREATE FUNCTION {s}_cdc.replay() RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE r record; applied bigint := 0;
BEGIN
 {(';'+chr(10)).join(stmts)};
 SELECT count(*) INTO applied FROM {s}_cdc.changes;
 DELETE FROM {s}_cdc.changes;
 RETURN applied;
END $$'''


def sync_sql(s, design):
    return f'''CREATE OR REPLACE FUNCTION {s}_cdc.capture() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r record;
BEGIN
 SELECT TG_TABLE_NAME table_name,TG_OP operation,
  CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END row_data,
  CASE WHEN TG_OP='INSERT' THEN NULL ELSE OLD.id END old_id INTO r;
 CASE r.table_name {apply_branches(s,design)} ELSE RAISE EXCEPTION 'unknown CDC table'; END CASE;
 RETURN NULL;
END $$'''


def rehearsal(cluster, design, nodes, output):
    s = 'm' + design.lower()
    statements, tables = plan(s,design)
    copy = backfill(s,design)
    replay = replay_sql(s,design)
    locked_tables = ','.join('c.' + t for t in tables)
    sync = sync_sql(s,design)
    acquire = f'''DO $$
DECLARE acquired boolean := false;
BEGIN
 FOR attempt IN 1..1000 LOOP
  BEGIN
   LOCK TABLE {locked_tables} IN SHARE MODE NOWAIT;
   acquired := true;
   EXIT;
  EXCEPTION WHEN lock_not_available THEN
   PERFORM pg_sleep(0.005);
  END;
 END LOOP;
 IF NOT acquired THEN RAISE EXCEPTION 'cutover lock budget exhausted'; END IF;
END $$'''
    cutover = ["SET LOCAL lock_timeout='1s'", acquire,
               f'SELECT {s}_cdc.replay()',sync,f'CREATE OR REPLACE VIEW {s}_cdc.current_relationships AS SELECT * FROM {s}.relationships']
    sql = '\n'.join(stmt + ';' for stmt in statements + [replay] + ["BEGIN ISOLATION LEVEL REPEATABLE READ"] + copy + ['COMMIT','BEGIN'] + cutover + ['COMMIT'])
    (output / f'c-to-{design.lower()}.sql').write_text('\n'.join(line.rstrip() for line in sql.splitlines()) + '\n')
    with cluster.connect() as c:
        for stmt in statements + [replay]:
            c.execute(stmt)
    errors, writer_latencies = [], []
    stop, ready = threading.Event(), threading.Event()
    def writer():
        try:
            with cluster.connect('linkfork-migration-writer') as c:
                c.autocommit = True
                serial = 700000
                while not stop.is_set():
                    start = time.perf_counter()
                    write_cycle(c,'c','C',nodes,serial)
                    writer_latencies.append((time.perf_counter()-start)*1000)
                    serial += 1
                    ready.set()
                    # Fixed modest arrival rate; writer stays active through cutover.
                    stop.wait(.005)
        except Exception as exc:
            errors.append(type(exc).__name__)
            ready.set()
    thread = threading.Thread(target=writer)
    thread.start()
    assert ready.wait(10)
    start = time.perf_counter()
    try:
        with cluster.connect() as c:
            c.execute('BEGIN ISOLATION LEVEL REPEATABLE READ')
            for stmt in copy:
                c.execute(stmt)
        backfill_s = time.perf_counter()-start
        with cluster.connect() as c:
            lock_start = time.perf_counter()
            for stmt in cutover:
                row = c.execute(stmt)
                if stmt.startswith('SELECT'):
                    replayed = row.fetchone()[0]
            lock_ms = (time.perf_counter()-lock_start)*1000
        elapsed = time.perf_counter()-start
        time.sleep(.05)
    finally:
        stop.set()
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert not errors and writer_latencies
    with cluster.connect() as c:
        source = c.execute('SELECT * FROM c.relationships ORDER BY id').fetchall()
        migrated = c.execute(f'SELECT * FROM {s}.relationships ORDER BY id').fetchall()
        assert source == migrated, 'Post-cutover dual-write mismatch'
        for kind in KINDS:
            assert c.execute(f'SELECT id,payload FROM c.d_{kind} EXCEPT SELECT id,payload FROM {s}.d_{kind}').fetchall() == []
            assert c.execute(f'SELECT id,payload FROM {s}.d_{kind} EXCEPT SELECT id,payload FROM c.d_{kind}').fetchall() == []
    return {'design':design,'statements':len(statements)+1+len(copy)+len(cutover)+4,
            'backfill_statements':len(copy),'backfill_passes':len(KINDS)+len(FAMILIES) if design == 'A' else 2*len(KINDS)+1,
            'registry_passes':len(KINDS) if design == 'B' else 0,
            'backfill_s':backfill_s,'total_s':elapsed,'cutover_attempt_and_lock_ms':lock_ms,
            'cdc_rows_replayed':replayed,'writer_cycles':len(writer_latencies),'writer_errors':errors,
            'writer_max_cycle_ms':max(writer_latencies),'source_edges':len(source),'edge_sha256':digest(source),
            'matched':True,'sql_file':f'c-to-{design.lower()}.sql',
            'post_cutover_dual_write_matched':True,
            'limits':'Shadow domain copies are rehearsal isolation overhead. A can reuse existing domain tables in deployment. Cutover duration includes bounded NOWAIT acquisition retries, final-state reconciliation of touched old/new identities, synchronous capture installation and view switch; equality validation runs after the writer stops. Reconciliation reads drained source tables under SHARE locks, avoiding obsolete pre-snapshot intermediate references. No original table is dropped. Existing orphans/duplicates and unmapped external text/digest references must be resolved before strict backfill.'}
