"""Frozen natural-email intent and bounded, durable recovery."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from uuid import UUID, uuid4, uuid5

NAMESPACE = UUID('dcb4632a-eb32-4f52-b465-0ba9ef25e580')
ROLES = {'client': 'client', 'lead': 'prospect', 'vendor': 'vendor'}

class Refused(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise Refused(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def occurrence(unix):
    return datetime.fromtimestamp(unix, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def capture_key(mailbox_hash, producer_sha):
    return str(uuid5(NAMESPACE, canonical([mailbox_hash, producer_sha, 'selection_v1']).decode()))


def projection(owner, account, mail):
    scope = [owner, account.strip().lower()]
    source = 'outlook-intake:v1:' + digest([*scope, mail['ident']])
    value = dict(schema='outlook-source-projection.v1', store_keys=sorted(mail['local_keys']),
                 occurred_at=occurrence(mail['sent_unix']), sender=mail['sender'].strip().lower(),
                 recipients=mail.get('recipients'), cc=mail.get('cc'),
                 subject_digest=digest(mail.get('subject') or ''), body_digest=digest(mail['text']),
                 complete=mail['full'], outcome=mail.get('body_outcome'))
    return dict(source_id=source, source_version='sha256:'+digest(value), projection_digest=digest(value),
                mailbox_scope_hash=digest(scope), message_hash=digest(mail['ident']))


def verify_receipt(receipt, meta, proposals):
    raw = base64.b64decode(receipt['raw_base64'], validate=True)
    require(0 < len(raw) <= 1048576, 'journal_size')
    sha = hashlib.sha256(raw).hexdigest()
    require(sha == receipt['digest'] and receipt['reference'] == 'sha256:'+sha, 'digest_mismatch')
    j = json.loads(raw)
    require(set(j) == {'schema','principal_slug','actor_id','actor_slug','tenant','release_sha',
                       'start','end','expected_count','expected'}, 'journal_shape')
    require(j['schema']=='email-touch-expectations.v1' and j['tenant']=='carr-internal'
            and j['release_sha']==meta['producer_sha'] and j['principal_slug']==meta['principal_slug'], 'identity_mismatch')
    require(j['actor_id']==receipt['actor_id'] and j['actor_slug']==receipt['actor_slug'], 'actor_mismatch')
    UUID(j['actor_id'])
    expected = j['expected']
    require(j['expected_count']==len(expected)==len(proposals), 'count_mismatch')
    by_key = {p['idempotency_key']:p for p in proposals}
    require(len(by_key)==len(expected)==len({e['original_key'] for e in expected}), 'key_mismatch')
    require(len({(e['target_type'],e['target_id']) for e in expected})==len(expected), 'duplicate_effect')
    for e in expected:
        require(set(e)=={'source_id','source_version','original_key','role','target_type','target_id','occurred_at'}, 'effect_shape')
        p=by_key.get(e['original_key'])
        require(p and e['source_id']==meta['source_id'] and e['source_version']==meta['source_version']
                and e['occurred_at']==p['occurred_at'] and ROLES.get(e['target_type'])==e['role'], 'effect_mismatch')
        UUID(e['target_id'])
    start = proposals[0]['occurred_at']
    end = (datetime.fromisoformat(start.replace('Z','+00:00'))+timedelta(milliseconds=1)).isoformat(timespec='milliseconds').replace('+00:00','Z')
    require(j['start']==start and j['end']==end, 'window_mismatch')
    return j


class Cohort:
    def __init__(self, state, save):
        self.state, self.save = state, save

    def select(self, meta, proposals):
        require(1 <= len(proposals) <= 500, 'effect_limit')
        require(re.fullmatch('[0-9a-f]{40}',meta['producer_sha']), 'release_invalid')
        item = dict(meta=meta, proposals=proposals)
        old = self.state.get('email_cohort')
        if old:
            require(old['meta']==meta and old['proposals']==proposals, 'selection_conflict')
            return
        require(not any(p['idempotency_key'] in self.state.get('requests',{}) for p in proposals), 'legacy_request')
        self.state['email_cohort']={**item, 'phase':'pending', 'failures':0, 'observations':[], 'applied':[]}
        self.save()

    def fail(self, reason, retryable):
        item=self.state['email_cohort']
        item['failures'] += 1
        item['reason'] = reason
        if not retryable or item['failures'] >= 2:
            item['phase']='held'
        self.save()
        return False

    def resume_after_diagnosis(self, reason):
        item=self.state['email_cohort']
        require(item['phase']=='held' and isinstance(reason,str) and 1<=len(reason)<=200, 'diagnosis_required')
        if 'receipt' in item:
            verify_receipt(item['receipt'],item['meta'],item['proposals'])
        item.update(phase='frozen' if 'receipt' in item else 'pending',failures=0,diagnosis=reason)
        self.save()

    def _observe(self, item, key, status):
        item['observations'].append(dict(observation_id=str(uuid4()), original_key=key, status=status,
            activity_id=self.state.get('requests',{}).get(key,{}).get('activity_id'),
            error_code=self.state.get('requests',{}).get(key,{}).get('error_code')))
        item.setdefault('outcome_key',str(uuid4()))
        self.save()

    def _publish(self, item, call):
        if not item['observations']:
            return
        result=call('record-email-touch-journal-outcome',dict(idempotency_key=item['outcome_key'],
                    reference=item['receipt']['reference'], observations=item['observations']))
        require(result.get('ok') is True, 'outcome_refused')
        item['observations']=[]
        item.pop('outcome_key',None)
        self.save()

    def recover(self, call, apply, release):
        item=self.state.get('email_cohort')
        if not item:
            return True
        if item['phase']=='held':
            return False
        if release != item['meta']['producer_sha']:
            return self.fail('release_mismatch', False)
        if item['phase']=='available':
            try: verify_receipt(item['receipt'], item['meta'], item['proposals'])
            except (ValueError,KeyError,TypeError): return self.fail('integrity',False)
            return True
        try:
            if 'receipt' not in item:
                m=item['meta']
                args={**m, 'idempotency_key':capture_key(m['mailbox_scope_hash'],m['producer_sha']),
                      'proposals':[{k:p[k] for k in ('ref','kind','occurred_at')} |
                                   {'original_key':p['idempotency_key']} for p in item['proposals']]}
                receipt=call('capture-email-touch-journal',args)
                require(receipt.get('ok') is True, 'capture_refused')
                verify_receipt(receipt,m,item['proposals'])
                item.update(receipt=receipt, phase='frozen',failures=0)
                self.save()
            j=verify_receipt(item['receipt'],item['meta'],item['proposals'])
            if item['observations']:
                self._publish(item,call)
                if len(item['applied'])==len(item['proposals']):
                    item.update(phase='available',failures=0)
                    self.save()
                    return True
                return False
            effects={e['original_key']:e for e in j['expected']}
            for proposal in item['proposals']:
                key=proposal['idempotency_key']
                if key in item['applied']:
                    continue
                e=effects[key]
                payload={**proposal, 'ref':e['target_id'], 'email_touch':{k:e[k] for k in ('source_id','source_version','role')}}
                item['phase']='filing'
                self.save()
                if not apply(payload):
                    request=self.state.get('requests',{}).get(key,{})
                    refused=request.get('disposition')=='blocked'
                    item['phase']='held' if refused else 'uncertain'
                    self._observe(item,key,'refused' if refused else 'uncertain')
                    self._publish(item,call)
                    return self.fail('application_refused' if refused else 'application_uncertain',not refused)
                item['applied'].append(key)
                self._observe(item,key,'acknowledged')
                item['failures']=0
                self.save()
            item['phase']='acknowledged'
            self._publish(item,call)
            item.update(phase='available',failures=0)
            self.save()
            return True
        except (Refused,ValueError,KeyError,TypeError):
            return self.fail('integrity_or_refusal',False)
        except Exception as error:
            # CARR's typed refusal has a code; storage and transport faults are retryable.
            code=getattr(error,'code','transport_error')
            return self.fail(code,code in ('transport_error','timeout','rate_limited','internal_error'))


def runtime_identity(repo):
    from lib.local_principal import local_partner_principal
    from lib.outlook_runtime import stamp
    from urllib.request import urlopen
    with urlopen('https://api.doctorcre.com/release', timeout=5) as response:
        raw=response.read(65537)
    require(len(raw)<=65536, 'release_size')
    release=json.loads(raw)
    require(release.get('ok') is True and release['env']['value']=='production', 'release_invalid')
    sha=release['git_sha']['value']
    require(isinstance(sha,str) and re.fullmatch('[0-9a-f]{40}',sha), 'release_invalid')
    producer=stamp(repo)['runtime_sha']
    return dict(principal_slug=local_partner_principal(), producer_sha=producer, release_sha=sha)
