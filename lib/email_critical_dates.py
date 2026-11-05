"""Bounded obligation quotes. The server decides application from stored evidence."""
import re

LABELS = {
    'loi_expiry': r'LOI (?:expiration|expiry)',
    'lease_commencement': r'lease commencement',
    'lease_expiration': r'lease (?:expiration|expiry)',
    'contingency_deadline': r'contingency deadline',
}


def extract(source):
    if not source.get('deal'):
        return []
    rows = []
    for kind, label in LABELS.items():
        match = re.search(r'[^\n!?]*\b' + label + r'\b[^\n!?]*', (source.get('body') or '')[:12000], re.I)
        if match:
            rows.append(dict(mode='critical_date', activity_id=source['activity_id'], deal=source['deal'],
                             obligation_kind=kind, evidence_quote=match.group().strip()[:2000]))
    return rows
