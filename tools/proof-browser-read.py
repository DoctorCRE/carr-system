#!/usr/bin/env python3
"""Read signed-in proof data without arbitrary browser code or navigation.

Usage: python3 tools/proof-browser-read.py config subscriptions
Uses the existing app tab at the designated CDP endpoint. Never closes tabs.
"""
import argparse
import json
import re
import subprocess
import http.client
import signal
import time
from contextlib import contextmanager


ORIGIN = 'https://app.doctorcre.com'
TOTAL_SECONDS = 20
MAX_BYTES = 128 * 1024
TRUNCATED = '[TRUNCATED: proof output exceeds byte limit]'
READS = {
    'config': 'read-web-push-config-v1',
    'subscriptions': 'read-web-push-subscriptions-v1',
}

# Node supplies only its standard WebSocket transport. Policy and requests live
# in Python; neither a script path nor browser code is accepted from the caller.
TRANSPORT = r'''
const input = JSON.parse(await new Response(process.stdin).text());
const socket = new WebSocket(input.url);
let finished = false;
function unavailable(message) {
  if (finished) return;
  finished = true;
  clearTimeout(timer);
  socket.close();
  process.stderr.write(message);
  process.exitCode = 78;
}
const timer = setTimeout(() => unavailable('Proof deadline expired'), input.timeout ?? 15000);
socket.addEventListener('error', () => unavailable('Proof socket unavailable'));
socket.addEventListener('close', () => {
  if (!finished) unavailable('Proof socket closed before reply');
});
socket.addEventListener('open', () => {
  if (finished) { socket.close(); return; }
  socket.send(JSON.stringify(input.request));
});
socket.addEventListener('message', event => {
  if (finished) return;
  if (typeof event.data !== 'string' || Buffer.byteLength(event.data) > (input.maxBytes ?? 135168)) {
    unavailable('[TRUNCATED: proof output exceeds byte limit]');
    return;
  }
  let reply;
  try { reply = JSON.parse(event.data); }
  catch { unavailable('Invalid proof reply'); return; }
  if (reply.id !== input.request.id) return;
  finished = true;
  clearTimeout(timer);
  socket.close();
  process.stdout.write(JSON.stringify(reply));
});
'''


def target(tabs):
    for tab in tabs:
        if (tab.get('type') == 'page'
                and tab.get('url', '').startswith(ORIGIN + '/')):
            url = tab.get('webSocketDebuggerUrl', '')
            if re.fullmatch(r'ws://127\.0\.0\.1:9333/devtools/page/[A-Za-z0-9-]+', url):
                return url
    raise ValueError('No existing DoctorCRE proof tab. Sign in through the designated proof browser.')


def request(reads, deadline_ms=None):
    if not reads or any(name not in {*READS, 'text'} for name in reads):
        raise ValueError('Only config, subscriptions and text reads are supported')
    expressions = {}
    for name in dict.fromkeys(reads):
        if name == 'text':
            expressions[name] = "boundedText(document.body.innerText)"
            continue
        body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                           'params': {'name': READS[name], 'arguments': {}}})
        expressions[name] = (
            "await (async () => { const response = await fetch('/mcp', {"
            "method:'POST',credentials:'same-origin',redirect:'error',"
            "signal:controller.signal,"
            "headers:{'Content-Type':'application/json'},body:"
            + json.dumps(body) + "}); if (!response.ok) throw new Error('Read refused');"
            "const reader = response.body.getReader(); const chunks = []; let size = 0;"
            "try { while (true) { const {done,value} = await reader.read(); if(done) break;"
            "size += value.byteLength; if(size > limit) throw new Error(marker); chunks.push(value); }"
            "} finally { await reader.cancel(); reader.releaseLock(); }"
            "return JSON.parse(await new Blob(chunks).text()); })()")
    fields = ','.join(json.dumps(name) + ':(' + expr + ')'
                      for name, expr in expressions.items())
    expression = ("(async () => { if(location.origin !== " + json.dumps(ORIGIN)
                  + ") throw new Error('Wrong origin');"
                  + f"const limit = {MAX_BYTES}; const marker = {json.dumps(TRUNCATED)};"
                  + "const controller = new AbortController();"
                  + (f"const timeout = Math.min(12000, {deadline_ms} - Date.now());" if deadline_ms is not None
                     else "const timeout = 12000;")
                  + "if(timeout <= 0) throw new Error('Proof deadline expired');"
                  "const timer = setTimeout(() => controller.abort(), timeout); try {"
                  "const bytes = value => new TextEncoder().encode(value).byteLength;"
                  "const boundedText = value => { if(value.length > limit || bytes(value) > limit)"
                  "throw new Error(marker); return value; };"
                  "const result = {" + fields + "};"
                  "if(bytes(JSON.stringify(result)) > limit) throw new Error(marker); return result;"
                  "} finally { clearTimeout(timer); } })()")
    return {'id': 1, 'method': 'Runtime.evaluate', 'params': {
        'expression': expression, 'awaitPromise': True, 'returnByValue': True,
        'timeout': 12000,
    }}


def remaining(deadline):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise TimeoutError('Proof deadline expired')
    return seconds


@contextmanager
def discovery_deadline(deadline):
    def expired(_signum, _frame):
        raise TimeoutError('Proof deadline expired')

    seconds = remaining(deadline)
    previous_handler = signal.signal(signal.SIGALRM, expired)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    started = time.monotonic()
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0]:
            signal.setitimer(signal.ITIMER_REAL,
                             max(.001, previous_timer[0] - (time.monotonic() - started)),
                             previous_timer[1])


def collect(reads):
    deadline = time.monotonic() + TOTAL_SECONDS
    request(reads)
    connection = http.client.HTTPConnection('127.0.0.1', 9333, timeout=remaining(deadline))
    try:
        with discovery_deadline(deadline):
            connection.request('GET', '/json/list')
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError('Proof discovery refused; redirects are not followed')
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError(TRUNCATED)
            tabs = json.loads(raw)
    finally:
        connection.close()
    command = request(reads, (time.time() + remaining(deadline)) * 1000)
    command['params']['timeout'] = min(12000, remaining(deadline) * 1000)
    result = subprocess.run(
        ['node', '--input-type=module', '-e', TRANSPORT],
        input=json.dumps({'url': target(tabs), 'request': command,
                          'timeout': remaining(deadline) * 1000, 'maxBytes': MAX_BYTES + 4096}),
        text=True, capture_output=True, timeout=remaining(deadline), check=False)
    remaining(deadline)
    if result.returncode:
        raise ValueError(TRUNCATED if 'TRUNCATED' in result.stderr else 'Proof transport unavailable')
    if len(result.stdout.encode('utf-8')) > MAX_BYTES + 4096:
        raise ValueError(TRUNCATED)
    reply = json.loads(result.stdout)
    if reply.get('error') or reply.get('result', {}).get('exceptionDetails'):
        raise ValueError(TRUNCATED if 'TRUNCATED' in result.stdout else 'Proof read refused by the browser')
    value = reply['result']['result']['value']
    if len(json.dumps(value, ensure_ascii=False).encode('utf-8')) + 1 > MAX_BYTES:
        raise ValueError(TRUNCATED)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reads', nargs='+', choices=[*READS, 'text'])
    args = parser.parse_args()
    try:
        print(json.dumps(collect(args.reads), ensure_ascii=False))
    except (ValueError, OSError, subprocess.SubprocessError, KeyError) as exc:
        marker = f' {TRUNCATED}' if TRUNCATED in str(exc) else ''
        parser.exit(78, f'Proof browser read unavailable ({type(exc).__name__}).{marker} No retry or fallback.\n')


if __name__ == '__main__':
    main()
