"""Three concrete graph storage designs behind the same relationship read shape."""
from dataclasses import dataclass
from functools import lru_cache
import re

from inventory import scan

CORE = ['party', 'lead', 'deal', 'rule', 'doctrine_section', 'loop', 'decision']


@dataclass(frozen=True)
class Family:
    name: str
    source: str
    targets: tuple
    relations: tuple = ('reference',)
    source_fk: bool = False
    kind_check: bool = True
    unique: bool = False
    identity: str = 'edge'


FAMILIES = [
    Family('doctrine_link', 'doctrine_section', ('doctrine_document','doctrine_section','party','deal','decision','rule','loop','capture'), ('citation','related','example','source'), True),
    Family('incident_link', 'incident', ('run','deployment','work_request','defect','decision'), source_fk=True, unique=True),
    Family('siep_evidence_link', 'siep_package', ('job_receipt','decision_event'), ('source','tests','migration','deploy','readback','live_readback','rollback','independent_review','joe_approval','joe_go_no_go','zero_unresolved_findings','zero_blockers','two_clean_audit_cycles','material_fix'), True, unique=True, identity='pair'),
    Family('f01_derivative_link', 'f01_corporate_artifact', tuple(CORE), source_fk=True, kind_check=False,unique=True,identity='target'),
    Family('j102_document_link', 'f01_document', ('relationship','engagement','assignment','property_negotiation','deal'),unique=True),
    Family('j102_artifact_link', 'f01_corporate_artifact', ('relationship','engagement','assignment','property_negotiation','deal'),unique=True),
    Family('event', 'event', tuple(CORE), kind_check=False),
    Family('record_source', 'record_source', tuple(CORE + ['client','building']), kind_check=False),
    Family('next_action', 'next_action', ('deal','client','lead','vendor')),
    Family('record_flag', 'record_flag', ('lead','client','vendor','party','deal','campaign','platform','pillar','format','repo','commit')),
    Family('attachment', 'attachment', tuple(CORE), kind_check=False),
]
KINDS = sorted(set(CORE + [f.source for f in FAMILIES] + [k for f in FAMILIES for k in f.targets]))


@lru_cache(maxsize=1)
def source_fk_actions():
    definitions = {e['table']:e['definition'] for e in scan(include_usages=False)}
    contracts = {
        'doctrine_link': ('public.doctrine_link', 'source_section_id', 'doctrine_section'),
        'incident_link': ('ops.incident_link', 'incident_id', 'ops.incident'),
        'siep_evidence_link': ('ops.siep_evidence_link', 'package_key', 'ops.siep_package_contract'),
        'f01_derivative_link': ('ops.f01_derivative_link', 'source_artifact_digest', 'ops.f01_corporate_artifact'),
    }
    assert {f.name for f in FAMILIES if f.source_fk} == contracts.keys()
    actions = {}
    for family, (table, column, target) in contracts.items():
        match = re.search(r'\b' + column + r'\s+[^,]*?references\s+([\w.]+)\s*\([^)]*\)(?:\s+on\s+delete\s+(cascade|restrict|no\s+action|set\s+null|set\s+default))?', definitions[table], re.I)
        if not match or match[1] != target:
            raise ValueError(f'Source FK contract changed for {table}.{column}')
        actions[family] = ' '.join((match[2] or 'NO ACTION').upper().split())
    return actions


def literals(values):
    return ','.join("'" + v + "'" for v in values)


def domain_ddl(s, design):
    statements = [f'CREATE SCHEMA {s}']
    if design == 'B':
        statements.append(f'CREATE TABLE {s}.entity(id uuid PRIMARY KEY, kind text NOT NULL CHECK(kind IN ({literals(KINDS)})), UNIQUE(id,kind))')
    for kind in KINDS:
        extra = (f",kind text NOT NULL DEFAULT '{kind}' CHECK(kind='{kind}'),FOREIGN KEY(id,kind) REFERENCES {s}.entity(id,kind)" if design == 'B' else '')
        statements.append(f'CREATE TABLE {s}.d_{kind}(id uuid PRIMARY KEY,payload text NOT NULL{extra})')
    return statements


def edge_ddl(s, design):
    statements, arms = [], []
    if design == 'B':
        check = ' OR '.join(f"(family='{f.name}' AND src_kind='{f.source}' AND dst_kind IN ({literals(f.targets)}) AND relation IN ({literals(f.relations)}))" for f in FAMILIES)
        statements = [f'CREATE TABLE {s}.edge(id uuid PRIMARY KEY,family text NOT NULL,src_kind text NOT NULL,src_id uuid NOT NULL,dst_kind text NOT NULL,dst_id uuid NOT NULL,relation text NOT NULL,CHECK({check}),FOREIGN KEY(src_id,src_kind) REFERENCES {s}.entity(id,kind),FOREIGN KEY(dst_id,dst_kind) REFERENCES {s}.entity(id,kind),UNIQUE(family,src_kind,src_id,dst_kind,dst_id,relation))',
                f'CREATE INDEX ON {s}.edge(src_kind,src_id)', f'CREATE INDEX ON {s}.edge(dst_kind,dst_id)',
                f'CREATE VIEW {s}.relationships AS SELECT * FROM {s}.edge']
        for f in FAMILIES:
            if f.identity != 'edge':
                cols = 'dst_kind,dst_id' if f.identity == 'target' else 'src_id,dst_kind,dst_id'
                statements.append(f"CREATE UNIQUE INDEX ON {s}.edge({cols}) WHERE family='{f.name}'")
        return statements
    for f in FAMILIES:
        action = source_fk_actions().get(f.name, 'NO ACTION')
        source = f'src_id uuid NOT NULL' + (f' REFERENCES {s}.d_{f.source}(id) ON DELETE {action}' if design == 'A' or f.source_fk else '')
        if design == 'A':
            arc = ','.join(f'dst_{k} uuid REFERENCES {s}.d_{k}(id)' for k in f.targets)
            case = 'CASE ' + ' '.join(f"WHEN dst_{k} IS NOT NULL THEN '{k}'" for k in f.targets) + ' END'
            coalesce = 'coalesce(' + ','.join('dst_' + k for k in f.targets) + ')'
            target = f'{arc},CHECK(num_nonnulls({",".join("dst_" + k for k in f.targets)})=1),dst_kind text GENERATED ALWAYS AS ({case}) STORED,dst_id uuid GENERATED ALWAYS AS ({coalesce}) STORED'
        else:
            check = f' CHECK(dst_kind IN ({literals(f.targets)}))' if f.kind_check else ''
            target = f'dst_kind text NOT NULL{check},dst_id uuid NOT NULL'
        cols = 'dst_kind,dst_id' if f.identity == 'target' else 'src_id,dst_kind,dst_id' if f.identity == 'pair' else 'src_id,dst_kind,dst_id,relation'
        unique = ',UNIQUE(' + cols + ')' if design == 'A' or f.unique else ''
        statements += [f'CREATE TABLE {s}.l_{f.name}(id uuid PRIMARY KEY,{source},{target},relation text NOT NULL CHECK(relation IN ({literals(f.relations)})){unique})',
                       f'CREATE INDEX ON {s}.l_{f.name}(src_id)', f'CREATE INDEX ON {s}.l_{f.name}(dst_kind,dst_id)']
        if design == 'A':
            statements += [f'CREATE INDEX ON {s}.l_{f.name}(dst_{k}) WHERE dst_{k} IS NOT NULL' for k in f.targets]
        arms.append(f"SELECT id,'{f.name}'::text family,'{f.source}'::text src_kind,src_id,dst_kind,dst_id,relation FROM {s}.l_{f.name}")
    statements.append(f'CREATE VIEW {s}.relationships AS ' + ' UNION ALL '.join(arms))
    return statements


def edge_columns(design, target):
    return ['id','family','src_kind','src_id','dst_kind','dst_id','relation'] if design == 'B' else ['id','src_id','dst_' + target,'relation'] if design == 'A' else ['id','src_id','dst_kind','dst_id','relation']


def edge_table(s, design, f):
    return s + ('.edge' if design == 'B' else '.l_' + f.name)


def values(design, edge):
    eid, family, sk, sid, tk, tid, relation = edge
    return edge if design == 'B' else (eid,sid,tid,relation) if design == 'A' else (eid,sid,tk,tid,relation)


def insert_edge(c, s, design, f, edge):
    cols = edge_columns(design, edge[4])
    c.execute(f'INSERT INTO {edge_table(s,design,f)}({",".join(cols)}) VALUES ({",".join(["%s"] * len(cols))})', values(design, edge))


CONTEXT = '''WITH RECURSIVE reach(kind,id,depth) AS (
 SELECT %s::text,%s::uuid,0
 UNION
 SELECT a.kind,a.id,r.depth+1 FROM reach r CROSS JOIN LATERAL (
  SELECT dst_kind kind,dst_id id FROM {s}.relationships WHERE src_kind=r.kind AND src_id=r.id
  UNION
  SELECT src_kind kind,src_id id FROM {s}.relationships WHERE dst_kind=r.kind AND dst_id=r.id
 ) a WHERE r.depth < 2
) SELECT kind,id,min(depth) FROM reach GROUP BY kind,id ORDER BY kind,id'''
