"""Inventory scalar polymorphic pointers without connecting to any database."""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def scan(include_usages=True):
    entries = {}
    for path in sorted((ROOT / 'migrations').glob('*.sql')):
        raw = path.read_text()
        text = re.sub(r'/\*.*?\*/|--[^\n]*', lambda m: re.sub(r'[^\n]',' ',m[0]), raw, flags=re.S)
        for match in re.finditer(r'create\s+table\s+(?:if\s+not\s+exists\s+)?([\w.]+)\s*\(', text, re.I):
            start, end, depth = match.end(), match.end(), 1
            quoted = False
            while end < len(text) and depth:
                char = text[end]
                if char == "'":
                    quoted = not quoted
                if not quoted:
                    depth += (char == '(') - (char == ')')
                end += 1
            body = text[start:end - 1]
            columns = dict(re.findall(r'(?:^|,)\s*(\w+)\s+(uuid|text|bigint|integer|varchar(?:\(\d+\))?)\b', body, re.I))
            pairs = []
            for col in columns:
                if col.endswith(('_kind', '_type', '_table')) or col == 'kind':
                    stem = col.rsplit('_', 1)[0] if '_' in col else ''
                    refs = [stem + suffix for suffix in ('_id', '_ref')] if stem else ['ref']
                    for ref in refs:
                        if ref in columns:
                            vocab = re.search(r'\b' + col + r'\s+in\s*\(([^)]+)\)', body, re.I)
                            pairs.append({'kind_column': col, 'id_column': ref, 'id_type': columns[ref],
                                          'kinds': re.findall(r"'([^']+)'", vocab[1]) if vocab else []})
            if pairs:
                name = match[1] if '.' in match[1] else 'public.' + match[1]
                entries[name] = {'table': name, 'pairs': pairs, 'columns': columns,
                                 'definition': body.strip(), 'file': str(path.relative_to(ROOT)),
                                 'line': raw[:match.start()].count('\n') + 1,
                                 'sha256': hashlib.sha256(raw.encode()).hexdigest()}
    e = entries['ops.j102_evidence_subject_link']
    e['pairs'].append({'kind_column': 'evidence_source', 'id_column': 'evidence_ref', 'id_type': 'text',
                       'kinds': ['f01_document', 'f01_corporate_artifact']})
    entries['public.candidate_pool'] = entries.pop('public.prospect_pool')
    entries['public.candidate_pool']['table'] = 'public.candidate_pool'
    entries['public.candidate_pool']['amendments'] = ['migrations/0048_candidate_pool.sql:35 (rename; prospect_pool becomes a compatibility view)']
    for path in sorted((ROOT / 'migrations').glob('*.sql')):
        raw = path.read_text()
        text = re.sub(r'/\*.*?\*/|--[^\n]*', lambda m: re.sub(r'[^\n]',' ',m[0]), raw, flags=re.S)
        for match in re.finditer(r'alter\s+table\s+(?:only\s+)?([\w.]+)\s+[^;]*?add\s+constraint\s+\w+\s+check\s*\(\s*(\w+)\s+in\s*\(([^)]+)\)', text, re.I):
            table = match[1] if '.' in match[1] else 'public.' + match[1]
            if table not in entries:
                continue
            for pair in entries[table]['pairs']:
                if pair['kind_column'] == match[2]:
                    pair['kinds'] = re.findall(r"'([^']+)'",match[3])
                    entries[table].setdefault('amendments',[]).append(f'{path.relative_to(ROOT)}:{raw[:match.start()].count(chr(10))+1}')
    # A later ALTER creates a pointer that CREATE-only discovery cannot see.
    entries['ops.tour'] = {'table': 'ops.tour', 'pairs': [{'kind_column': 'subject_type', 'id_column': 'subject_id',
                           'id_type': 'text', 'kinds': ['client', 'work']}], 'columns': {'subject_type': 'text', 'subject_id': 'text'},
                           'file': 'migrations/0429_tour_domain_route_cheat_sheet.sql', 'line': 4,
                           'definition': 'ALTER-added subject_type / subject_id; MCP create-tour-domain allows client/work.'}
    for e in entries.values():
        usages = []
        for path in sorted((ROOT / 'mcp-server').rglob('*.js')) if include_usages else []:
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(r'\b' + re.escape(e['table'].split('.')[-1]) + r'\b', line):
                    usages.append(f'{path.relative_to(ROOT)}:{lineno}')
        e['mcp_usages'] = usages
    return list(entries.values())


if __name__ == '__main__':
    print(json.dumps(scan(), indent=2))
