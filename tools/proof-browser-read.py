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


ORIGIN = 'https://app.doctorcre.com'
READS = {
    'config': 'read-web-push-config-v1',
    'subscriptions': 'read-web-push-subscriptions-v1',
}

# Node supplies only its standard WebSocket transport. Policy and requests live
# in Python; neither a script path nor browser code is accepted from the caller.
TRANSPORT = r'''
const input = JSON.parse(await new Response(process.stdin).text());
const socket = new WebSocket(input.url);
const timer = setTimeout(() => { socket.close(); process.exit(1); }, 15000);
socket.addEventListener('error', () => process.exit(1));
socket.addEventListener('open', () => socket.send(JSON.stringify(input.request)));
socket.addEventListener('message', event => {
  const reply = JSON.parse(event.data);
  if (reply.id !== input.request.id) return;
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


def request(reads):
    if not reads or any(name not in {*READS, 'text'} for name in reads):
        raise ValueError('Only config, subscriptions and text reads are supported')
    expressions = {}
    for name in dict.fromkeys(reads):
        if name == 'text':
            expressions[name] = 'document.body.innerText'
            continue
        body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                           'params': {'name': READS[name], 'arguments': {}}})
        expressions[name] = (
            "await (async () => { const response = await fetch('/mcp', {"
            "method:'POST',credentials:'same-origin',redirect:'error',"
            "headers:{'Content-Type':'application/json'},body:"
            + json.dumps(body) + "}); if (!response.ok) throw new Error('Read refused');"
            "return await response.json(); })()")
    fields = ','.join(json.dumps(name) + ':(' + expr + ')'
                      for name, expr in expressions.items())
    expression = ("(async () => { if(location.origin !== " + json.dumps(ORIGIN)
                  + ") throw new Error('Wrong origin'); return {" + fields + "}; })()")
    return {'id': 1, 'method': 'Runtime.evaluate', 'params': {
        'expression': expression, 'awaitPromise': True, 'returnByValue': True,
        'timeout': 12000,
    }}


def collect(reads):
    command = request(reads)
    connection = http.client.HTTPConnection('127.0.0.1', 9333, timeout=5)
    try:
        connection.request('GET', '/json/list')
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError('Proof discovery refused; redirects are not followed')
        tabs = json.loads(response.read(1024 * 1024))
    finally:
        connection.close()
    result = subprocess.run(
        ['node', '--input-type=module', '-e', TRANSPORT],
        input=json.dumps({'url': target(tabs), 'request': command}),
        text=True, capture_output=True, timeout=20, check=True)
    reply = json.loads(result.stdout)
    if reply.get('error') or reply.get('result', {}).get('exceptionDetails'):
        raise ValueError('Proof read refused by the browser')
    return reply['result']['result']['value']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reads', nargs='+', choices=[*READS, 'text'])
    args = parser.parse_args()
    try:
        print(json.dumps(collect(args.reads)))
    except (ValueError, OSError, subprocess.SubprocessError, KeyError) as exc:
        parser.exit(78, f'Proof browser read unavailable ({type(exc).__name__}). No retry or fallback.\n')


if __name__ == '__main__':
    main()
