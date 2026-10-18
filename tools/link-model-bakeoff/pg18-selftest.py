"""Exercise actual SQL behavior and crash/catch-up boundaries on temporary PG18."""
import argparse
import tempfile
from pathlib import Path

from inventory import scan
from run import run
from regression_sql import verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin',default='/opt/homebrew/opt/postgresql@18/bin')
    args = parser.parse_args()
    verify(args.pg_bin)
    entries = {e['table']:e for e in scan()}
    expected = {'public.doctrine_link','ops.incident_link','ops.siep_evidence_link','ops.f01_derivative_link','ops.j102_evidence_subject_link'}
    assert expected <= entries.keys()
    assert len(entries['ops.j102_evidence_subject_link']['pairs']) == 2
    assert 'public.candidate_pool' in entries and 'public.prospect_pool' not in entries
    output = Path(tempfile.mkdtemp(prefix='linkfork-selftest-'))
    data = run(args.pg_bin,output,small=True)
    assert data['cluster_stopped']
    assert all(len(data['integrity'][d]) >= 15 for d in 'ABC')
    for d in 'ABC':
        assert data['context']['designs'][d]['errors'] == 0
        assert all(r['errors'] == 0 for r in data['writes'][d]['concurrent'])
    assert all(r['matched'] and r['post_cutover_dual_write_matched'] for r in data['migration'])
    assert data['drift'][-1]['drift'] is True
    assert '<table>' in (output / 'bakeoff.html').read_text()
    print('PASS local SQL integration, integrity, graph equality, concurrent writes, crash rollback, migration catch-up, shutdown')


if __name__ == '__main__':
    main()
