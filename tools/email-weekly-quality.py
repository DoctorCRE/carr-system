"""Report bounded email filing cohorts without changing automation policy."""
import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys

POPULATIONS = ('automatic', 'partner_confirmed', 'bulk_import', 'unclassified', 'unrelated_actor')
REPORT_SQL = """
with scope as (
 select {start}::timestamptz as start_at, {end}::timestamptz as end_at,
        {cutoff}::timestamptz as cutoff, {partner}::text as partner, {account}::text as account
), effects as (
 select e.*, case
   when (who.slug=s.partner or (who.kind in ('automation','system') and e.sponsoring_human_slug=s.partner)) is not true
     then 'unrelated_actor'
   when e.cause in ('import_migration','import_salesforce') then 'bulk_import'
   when e.verb='inherit-email-thread' and e.cause='automation_job' and nullif(btrim(e.human_quote),'') is null
     then 'automatic'
   when e.verb='confirm-email-filing' and e.cause='human_stated' then 'partner_confirmed'
   else 'unclassified' end as population
 from public.v_email_thread_filing_event f
 join public.event e on e.id=f.id
 join public.actor who on who.id=e.actor_id
 join public.activity a on a.id::text=e.new_value->>'activity_id'
 left join public.deal d on d.id=e.subject_id
 cross join scope s
 where e.verb in ('inherit-email-thread','confirm-email-filing')
   and a.kind in ('email_in','email_out')
   and e.occurred_at>=s.start_at and e.occurred_at<s.end_at and e.recorded_at<s.cutoff
   and coalesce(a.email_identity->>'mailbox_owner',a.email_source->>'mailbox_owner',d.owner)=s.partner
   and (s.account is null or a.email_identity->>'mailbox_account'=s.account)
), undone as (
 select distinct e.id from effects e
 join public.v_email_thread_filing_event f on f.new_value->>'event_id'=e.id::text
 join public.event u on u.id=f.id
 join public.actor who on who.id=u.actor_id
 cross join scope s
 where u.verb='undo-email-thread-filing' and u.cause='human_correction'
   and who.slug=s.partner and who.kind='human'
   and u.new_value->>'activity_id'=e.new_value->>'activity_id' and u.subject_id=e.subject_id
   and u.mutation_order>e.mutation_order and u.occurred_at>=e.occurred_at
   and u.occurred_at<s.cutoff and u.recorded_at<s.cutoff
)
select p.population, count(e.id)::bigint as denominator, count(u.id)::bigint as numerator
from (values ('automatic'),('partner_confirmed'),('bulk_import'),('unclassified'),('unrelated_actor')) p(population)
left join effects e on e.population=p.population
left join undone u on u.id=e.id
group by p.population order by p.population
"""


@dataclass(frozen=True)
class Window:
    start: str
    end: str
    undo_before: str
    partner: str
    mailbox_account: str | None


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timestamps require an explicit UTC offset')
    return result.astimezone(timezone.utc)


def literal(value):
    if value is None:
        return 'NULL'
    if '\x00' in value or '\\' in value:
        raise ValueError('invalid filter')
    return "'" + value.replace("'", "''") + "'"


def report(window, read, clock):
    start, end, cutoff = map(timestamp, (window.start, window.end, window.undo_before))
    if not start < end <= cutoff <= clock() or end-start > timedelta(days=8) or cutoff-end > timedelta(days=90):
        raise ValueError('invalid or unbounded report window')
    if window.partner not in ('joe', 'dell'):
        raise ValueError('invalid partner')
    if window.mailbox_account is not None and not 3 <= len(window.mailbox_account) <= 254:
        raise ValueError('invalid mailbox account')
    sql = REPORT_SQL.format(start=literal(start.isoformat()), end=literal(end.isoformat()),
                            cutoff=literal(cutoff.isoformat()), partner=literal(window.partner),
                            account=literal(window.mailbox_account))
    rows = read(sql)
    if not isinstance(rows, list) or len(rows) != len(POPULATIONS):
        raise ValueError('incomplete aggregate population')
    counts = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'population', 'denominator', 'numerator'}:
            raise ValueError('invalid aggregate row')
        name, denominator, numerator = row['population'], row['denominator'], row['numerator']
        if (name not in POPULATIONS or name in counts or type(denominator) is not int or type(numerator) is not int
                or not 0 <= numerator <= denominator):
            raise ValueError('invalid aggregate counts')
        counts[name] = row
    automatic = counts['automatic']
    numerator, denominator = automatic['numerator'], automatic['denominator']
    comparison = None
    if denominator:
        comparison = 'above_threshold' if numerator*20 > denominator else 'at_threshold' if numerator*20 == denominator else 'below_threshold'
    return {
        'schema': 'email-weekly-quality.v1', 'window': asdict(window),
        'actions': [{'type': 'inherit-email-thread', 'denominator': denominator, 'numerator': numerator,
                     'observed_rate': numerator/denominator if denominator else None, 'threshold': .05,
                     'threshold_comparison': comparison, 'status': 'observed' if denominator else 'no_evidenced_population'}],
        'separate_populations': {name: counts[name]['denominator'] for name in POPULATIONS if name != 'automatic'},
        'unsupported_types': [{'type': name, 'status': 'unavailable', 'denominator': None, 'numerator': None,
                               'reason': 'No complete automatic effect and linked undo contract in this report.'}
                              for name in ('reply_send', 'next_step', 'deal_progress', 'lead_creation', 'other')],
        'corrected_misses': {'status': 'unavailable', 'count': None,
                            'reason': 'No complete linked missed-action correction population in filing history.'}}


def json_query(sql):
    return "select coalesce(json_agg(report_row),'[]'::json) from (" + sql + ') report_row;'


class ReportError(ValueError):
    def __init__(self, step, category):
        self.step, self.category = step, category
        super().__init__(f'step={step} category={category}')


def database_read(sql):
    try:
        import psycopg
    except ImportError:
        raise ReportError('database_connect', 'dependency') from None
    dsn = os.environ.get('DATABASE_URL')
    if not dsn:
        raise ReportError('database_connect', 'configuration')
    step = 'database_connect'
    try:
        with psycopg.connect(dsn, connect_timeout=5,
                              options='-c default_transaction_read_only=on') as conn:
            step = 'database_query'
            with conn.cursor() as cur:
                cur.execute('set transaction isolation level repeatable read read only')
                cur.execute("set local statement_timeout='30s'")
                cur.execute("set local lock_timeout='5s'")
                cur.execute(json_query(sql))
                rows = cur.fetchone()[0]
                if len(json.dumps(rows)) > 65536:
                    raise ReportError('database_result', 'size_limit')
                return rows
    except psycopg.Error as exc:
        category = ('read_only' if exc.sqlstate == '25006' else
                    'permission' if exc.sqlstate == '42501' else
                    'timeout' if exc.sqlstate in ('57014', '55P03') else
                    'connection' if step == 'database_connect' or (exc.sqlstate or '').startswith('08') else 'query')
        raise ReportError(step, category) from None


def write_private(path, result):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write('\n')


def summary(result):
    action = result['actions'][0]
    rate = f"{action['observed_rate']:.2%}" if action['observed_rate'] is not None else 'unavailable'
    return '\n'.join([
        f"Automatic thread filings {action['denominator']}; observed undos {action['numerator']}; rate {rate}.",
        f"Five percent comparison {action['threshold_comparison'] or action['status']}.",
        'Separate populations ' + ', '.join(f'{name} {count}' for name, count in result['separate_populations'].items()) + '.',
        'Other automatic types and corrected misses unavailable.'
    ])


def main(argv=None, clock=lambda: datetime.now(timezone.utc)):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('start', 'end', 'undo-before', 'partner', 'output'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--mailbox-account')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--database', action='store_true', help='Use DATABASE_URL supplied by tools/db-tap.py run')
    source.add_argument('--fixture', type=Path)
    args = parser.parse_args(argv)
    step = 'report_filters'
    try:
        def read(sql):
            nonlocal step
            if args.fixture:
                step = 'fixture_read'
                with args.fixture.open('rb') as stream:
                    payload = stream.read(65537)
                if len(payload) > 65536:
                    raise ValueError('oversized fixture')
                step = 'fixture_decode'
                rows = json.loads(payload)
            else:
                rows = database_read(sql)
            step = 'aggregate_validation'
            return rows
        result = report(Window(args.start, args.end, args.undo_before, args.partner, args.mailbox_account), read, clock)
        result['source'] = 'synthetic' if args.fixture else 'canonical_history'
        step = 'output_write'
        write_private(args.output, result)
        if args.fixture:
            print('Synthetic fixture report.')
        print(summary(result))
        return 0
    except ReportError as exc:
        print(f'Weekly quality report failed step={exc.step} category={exc.category}.', file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:
        category = 'filesystem' if isinstance(exc, OSError) else 'invalid_input'
        print(f'Weekly quality report failed step={step} category={category}.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
