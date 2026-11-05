"""Closed, content-free diagnostics shared by intake and the scheduler receipt."""
import re


DATABASE_HINT = 'The operation could not be completed. Quote the correlation_id when reporting it.'


def refusal_diagnostic(reply: dict) -> dict:
    result = {}
    code = reply.get('error')
    if isinstance(code, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,63}', code):
        result['server_error_code'] = code
    sqlstate = reply.get('sqlstate')
    if isinstance(sqlstate, str) and re.fullmatch(r'[0-9A-Z]{5}', sqlstate):
        result['sqlstate'] = sqlstate
    correlation = reply.get('correlation_id')
    if isinstance(correlation, str) and re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', correlation):
        result['correlation_id'] = correlation.lower()
    # Only the closed public database hint is retained, never a driver hint.
    if reply.get('hint') == DATABASE_HINT:
        result['hint'] = DATABASE_HINT
    return result


def public_write_diagnostics(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if isinstance(item, dict):
            safe = refusal_diagnostic({**item, 'error': item.get('server_error_code')})
            if safe.get('server_error_code'):
                result.append(safe)
    return result
