"""One cached, versioned semantic request after caller-owned exact checks.

This module owns no budget or credential. The existing transport still owns
admission. It rejects oversized requests rather than silently removing facts.
Answers are advisory until a caller independently validates its decision rule.
"""
import copy
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import math
import os
import time

MODEL = 'jev-1.13.0'
MAX_REQUEST_CHARS = 100000
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = os.path.join(REPO, 'out', 'jev-semantic-cache.json')

def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, 'ops', name+'.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def ordered(questions):
    """Stable Choice order; Score criteria retain their ordinal order."""
    result = copy.deepcopy(questions)
    for question in result.values():
        if question.get('type') == 'choice' and isinstance(question.get('criteria'), dict):
            question['criteria'] = dict(sorted(question['criteria'].items()))
    return dict(sorted(result.items()))

def cache_key(state, questions, caller, version):
    material = {'model': MODEL, 'state': state, 'questions': ordered(questions),
                'caller': caller, 'question_set_version': version}
    encoded = json.dumps(material, sort_keys=True, ensure_ascii=False, allow_nan=False)
    if len(encoded) > MAX_REQUEST_CHARS:
        raise ValueError('semantic request too large; narrow the candidates or excerpt before asking')
    return hashlib.sha256(encoded.encode()).hexdigest()

@contextmanager
def _claim(path, deadline):
    with open(path, 'a') as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('semantic cache claim unavailable')
                time.sleep(min(.01, remaining))
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)

def _cache_read(result, started):
    result = copy.deepcopy(result)
    result['cached_observation'] = result.get('cached_observation') or {
        'usage': result.get('usage'), 'elapsed_ms': result.get('elapsed_ms'),
        'latency_ms': result.get('latency_ms')}
    result.update(cache_hit=True, usage={'input_tokens': 0, 'output_tokens': 0},
                  elapsed_ms=0, latency_ms=0,
                  cache_read_elapsed_ms=(time.monotonic()-started)*1000)
    return result

def ask(state, questions, *, caller, version, client=None, transport=None,
        cache_path=None, **options):
    """Call once for the complete question set; cache only complete responses.

    A per-key file lock coalesces identical events across processes. The
    key covers every input, including options embedded in questions and the
    explicitly pinned model. Injected fakes cross the same seam as production.
    """
    started = time.monotonic()
    deadline = min(options.get('deadline') or float('inf'),
                   started + float(options.get('timeout', 20)))
    if not questions or not caller or not version:
        raise ValueError('semantic request needs caller, version and questions')
    if options.pop('model', MODEL) != MODEL:
        raise ValueError('semantic model must be pinned to '+MODEL)
    state = copy.deepcopy(state)
    qs = ordered(questions)
    key = cache_key(state, qs, caller, version)
    cache = _load('jev_verdict_cache')
    path = cache_path or os.environ.get('CARR_JEV_SEMANTIC_CACHE') or CACHE_PATH
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    cached = cache.get(path, key, ttl=86400)
    if cached is not None:
        return _cache_read(cached, started)
    with _claim(path+'.'+key+'.lock', deadline):
        cached = cache.get(path, key, ttl=86400)
        if cached is not None:
            return _cache_read(cached, started)
        send = transport or (client or _load('typesafe_client')).ask
        # Transport cache includes the same full payload; budget admission is
        # still enforced there on every miss. No retry fan-out at this seam.
        if transport is not None and client is not None:
            options['client'] = client
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('semantic request deadline exceeded')
        options.update(deadline=deadline, timeout=remaining)
        result = send(state, qs, model=MODEL, caller=caller, **options)
        if time.monotonic() > deadline:
            raise TimeoutError('semantic request deadline exceeded')
        answers = result.get('answers') if isinstance(result, dict) else None
        if not isinstance(answers, dict) or set(answers) != set(qs):
            raise ValueError('incomplete semantic answers')
        if result.get('model', MODEL) != MODEL:
            raise ValueError('resolved semantic model differs from pinned model')
        for name, question in qs.items():
            kind = question.get('type')
            answer = answers[name]
            if not isinstance(answer, dict) or answer.get('type', kind) != kind:
                raise ValueError('invalid semantic answer type')
            value = answer.get(kind)
            if kind == 'choice':
                valid = value in question.get('criteria', {})
            else:
                maximum = 1 if kind == 'noul' else len(question.get('criteria', []))-1
                valid = type(value) in (int, float) and math.isfinite(value) and 0 <= value <= maximum
            if not valid:
                raise ValueError('invalid semantic answer value')
        result = dict(result, advisory_only=True, question_set_version=version, cache_key=key)
        if result.get('cache_hit'):
            result = _cache_read(result, started)
        # Atomic reads need no lock; read/merge/write must not lose other keys.
        with _claim(path+'.lock', deadline):
            cache.put(path, key, result, ttl=86400)
        return result
