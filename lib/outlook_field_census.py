"""Generation-bound aggregate field accounting for the private Outlook intake copy."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone
import uuid
SCHEMA = 'outlook-field-census/v1'
CAUSES = ('legitimately_empty', 'protected', 'never_requested', 'acquisition_failed', 'decode_unsupported', 'parser_or_linkage', 'conflict_or_overflow', 'source_unavailable', 'unclassified')
FIELDS = {'subject': 'subject_decoded', 'body': 'body_complete', 'sender': 'sender_decoded', 'recipients': 'recipients_decoded'}
MESSAGE_COUNTERS = ('mail_account_unknown_not_stored', 'decoder_rejected',
                    'stored_not_admitted_account_ambiguous', 'stored_not_admitted_account_unresolved')
VERSIONS = {'admission_policy': 'intake-account-v1', 'completion_predicate': 'nonempty-full-v1'}

def _digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def binding(copy, files, mailbox, started_at, decoder=None):
    root = Path(__file__).resolve().parent
    revisions = dict(intake_revision=_digest(root / 'outlook_intake.py'), decoder_revision=_digest(decoder or copy), census_revision=_digest(__file__), **VERSIONS)
    sidecars = [(str(p.relative_to(files)), _digest(p)) for p in sorted(files.rglob('*')) if p.is_file()] if files.exists() else []
    private_manifest = [_digest(copy), sidecars, mailbox, revisions, started_at, SCHEMA]
    return dict(generation=str(uuid.uuid4()), commitment=hashlib.sha256(json.dumps(private_manifest, sort_keys=True).encode()).hexdigest(), started_at=started_at, finished_at=datetime.now(timezone.utc).isoformat(), **revisions)

def build(meta, mails, bound):
    from lib.outlook_intake import field_presence
    fields = {name: dict(captured=0, raw_missing=0, causes=dict.fromkeys(CAUSES, 0)) for name in FIELDS}
    for mail in mails:
        for name, present in field_presence(mail).items():
            row = fields[name]
            if present:
                row['captured'] += 1
            else:
                row['raw_missing'] += 1
                cause = 'unclassified'
                if ((name == 'body' and (mail.get('body_completeness') == 'conflict'
                                         or mail.get('body_outcome') in ('conflict', 'oversized')))
                        or (name == 'recipients' and mail.get('census_recipients_status') == 'conflict')):
                    cause = 'conflict_or_overflow'
                row['causes'][cause] += 1
    admitted = len(mails)
    unadmitted = sum((meta.get(key, 0) for key in ('mail_outside_selected_account', 'mail_account_ambiguous', 'mail_account_unresolved')))
    rejected = meta.get('decoder_rejected', 0)
    population = dict(decoded=admitted + unadmitted, admitted=admitted, unadmitted=unadmitted, rejected=rejected, observed=admitted + unadmitted + rejected)
    receipt = dict(generation=bound['generation'], counts={key: meta[key] for key in
                   ('admitted', 'stored', *FIELDS.values(), *MESSAGE_COUNTERS)})
    message_causes = dict(rejected=rejected, unknown_account=meta['mail_account_unknown_not_stored'],
                          ambiguous=meta['stored_not_admitted_account_ambiguous'],
                          unresolved=meta['stored_not_admitted_account_unresolved'])
    message_causes['other_stored_unadmitted'] = (meta['stored'] - admitted
                                                - message_causes['ambiguous'] - message_causes['unresolved'])
    result = dict(schema=SCHEMA, binding=bound, receipt=receipt, census=dict(generation=bound['generation'], population=population, fields=fields, message_causes=message_causes))
    validate(result)
    return result

def validate(bundle):
    """Reject unsupported or inconsistent public aggregates without echoing their input."""
    try:
        if not set(bundle) == {'schema', 'binding', 'receipt', 'census'}:
            raise ValueError('invalid_field_census')
        if not bundle['schema'] == SCHEMA:
            raise ValueError('invalid_field_census')
        bound, receipt, census = (bundle[k] for k in ('binding', 'receipt', 'census'))
        if not set(bound) == {'generation', 'commitment', 'started_at', 'finished_at', 'intake_revision', 'decoder_revision', 'census_revision', *VERSIONS}:
            raise ValueError('invalid_field_census')
        if not str(uuid.UUID(bound['generation'])) == bound['generation']:
            raise ValueError('invalid_field_census')
        if not all((bound[k] == v for k, v in VERSIONS.items())):
            raise ValueError('invalid_field_census')
        for key in ('commitment', 'intake_revision', 'decoder_revision', 'census_revision'):
            if not (len(bound[key]) == 64 and all((c in '0123456789abcdef' for c in bound[key]))):
                raise ValueError('invalid_field_census')
        times = [datetime.fromisoformat(bound[k]) for k in ('started_at', 'finished_at')]
        if not (all((t.tzinfo is not None for t in times)) and times[0] <= times[1]):
            raise ValueError('invalid_field_census')
        if not set(receipt) == {'generation', 'counts'}:
            raise ValueError('invalid_field_census')
        if not set(census) == {'generation', 'population', 'fields', 'message_causes'}:
            raise ValueError('invalid_field_census')
        if not receipt['generation'] == census['generation'] == bound['generation']:
            raise ValueError('invalid_field_census')
        counts, population = (receipt['counts'], census['population'])
        if not set(counts) == {'admitted', 'stored', *FIELDS.values(), *MESSAGE_COUNTERS}:
            raise ValueError('invalid_field_census')
        if not set(population) == {'decoded', 'admitted', 'unadmitted', 'rejected', 'observed'}:
            raise ValueError('invalid_field_census')

        def nonnegative(values):
            if not all((type(n) is int and 0 <= n < 2 ** 64 for n in values)):
                raise ValueError('invalid_field_census')
        nonnegative(counts.values())
        nonnegative(population.values())
        causes = census['message_causes']
        if set(causes) != {'rejected', 'unknown_account', 'ambiguous', 'unresolved', 'other_stored_unadmitted'}:
            raise ValueError('invalid_field_census')
        nonnegative(causes.values())
        if (causes['rejected'] != counts['decoder_rejected']
                or causes['unknown_account'] != counts['mail_account_unknown_not_stored']
                or causes['ambiguous'] != counts['stored_not_admitted_account_ambiguous']
                or causes['unresolved'] != counts['stored_not_admitted_account_unresolved']
                or sum(causes.values()) != counts['stored'] - counts['admitted']
                    + counts['mail_account_unknown_not_stored'] + counts['decoder_rejected']):
            raise ValueError('invalid_field_census')
        admitted = population['admitted']
        if not admitted == counts['admitted']:
            raise ValueError('invalid_field_census')
        if not population['decoded'] == admitted + population['unadmitted']:
            raise ValueError('invalid_field_census')
        if not population['observed'] == population['decoded'] + population['rejected']:
            raise ValueError('invalid_field_census')
        if not set(census['fields']) == set(FIELDS):
            raise ValueError('invalid_field_census')
        for name, counter in FIELDS.items():
            field = census['fields'][name]
            if not set(field) == {'captured', 'raw_missing', 'causes'}:
                raise ValueError('invalid_field_census')
            if not set(field['causes']) == set(CAUSES):
                raise ValueError('invalid_field_census')
            nonnegative([field['captured'], field['raw_missing'], *field['causes'].values()])
            if not field['captured'] == counts[counter]:
                raise ValueError('invalid_field_census')
            if not admitted == field['captured'] + field['raw_missing']:
                raise ValueError('invalid_field_census')
            if not field['raw_missing'] == sum(field['causes'].values()):
                raise ValueError('invalid_field_census')
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError('invalid_field_census') from None
    return bundle

def publish(path, bundle):
    validate(bundle)
    descriptor, temporary = tempfile.mkstemp(prefix='.field-census-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(bundle, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
