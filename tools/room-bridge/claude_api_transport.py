"""Per-job model transport. Credentials never enter the coding process."""
from __future__ import annotations

import http.client
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
import uuid
import claude_api_config as config
from claude_api_worker import billing_error


class Transport:
    def __init__(self, key, model, deadline, upstream=None):
        self.key = key
        self.model = model
        self.deadline = deadline
        self.upstream = upstream or self._upstream
        self.calls = 0
        self.billing_error = False
        self.lock = threading.Lock()
        self.prefix = '/' + uuid.uuid4().hex
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                path = self.path.partition('?')[0]
                if path not in (owner.prefix + '/v1/messages', owner.prefix + '/v1/messages/count_tokens'):
                    self.send_error(403)
                    return
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= 8 * 1024 * 1024:
                        raise ValueError('request size')
                    body = self.rfile.read(size)
                    headers = {k: v for k, v in self.headers.items()
                               if k.lower() in ('anthropic-version', 'content-type', 'user-agent')}
                    headers['x-api-key'] = owner.key
                    status, response = owner.forward(path[len(owner.prefix):], body, headers)
                    if response is None:
                        self.send_response(status)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(b'{"type":"error","error":{"type":"invalid_request_error","message":"Model Room request bound refused"}}')
                        return
                    self.send_response(response.status)
                    self.send_header('Content-Type', response.getheader('Content-Type', 'application/json'))
                    self.end_headers()
                    try:
                        carry = b''
                        secret = owner.key.encode()
                        while chunk := response.read1(65536):
                            carry += chunk
                            # Keep a possible partial secret across network chunks.
                            end = max(0, len(carry) - len(secret) + 1)
                            found = carry.find(secret)
                            while 0 <= found < end:
                                self.wfile.write(carry[:found] + b'[redacted]')
                                carry = carry[found + len(secret):]
                                end = max(0, len(carry) - len(secret) + 1)
                                found = carry.find(secret)
                            self.wfile.write(carry[:end])
                            carry = carry[end:]
                            self.wfile.flush()
                        self.wfile.write(carry)
                    finally:
                        response.close()
                except (OSError, ValueError, TypeError, AttributeError, http.client.HTTPException):
                    self.close_connection = True

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.url = f'http://127.0.0.1:{self.server.server_port}{self.prefix}'

    def forward(self, path, body, headers):
        request = json.loads(body)
        allowed = {'model', 'messages', 'system', 'tools', 'tool_choice', 'thinking',
                   'output_config', 'stream', 'max_tokens', 'temperature', 'top_p', 'top_k',
                   'stop_sequences', 'metadata', 'cache_control', 'service_tier', 'speed'}
        if (not isinstance(request, dict) or set(request) - allowed
                or request.get('model') != self.model or self.model not in config.MODEL_REQUESTS
                or request.get('speed', 'standard') != 'standard'
                or request.get('service_tier', 'auto') not in ('auto', 'standard')
                or any(t.get('type', 'custom') != 'custom' for t in request.get('tools', []))):
            return 400, None
        if path.endswith('count_tokens'):
            return self.send(path, body, headers)
        with self.lock:
            if (self.billing_error or self.calls >= config.MODEL_REQUESTS[self.model]
                    or time.monotonic() >= self.deadline):
                return 429, None
            count_request = {k: v for k, v in request.items()
                             if k in ('model', 'messages', 'system', 'tools', 'tool_choice', 'thinking')}
            status, counted = self.send('/v1/messages/count_tokens', json.dumps(count_request).encode(), headers)
            if counted is None:
                return status, None
            try:
                tokens = json.loads(counted.read()).get('input_tokens')
                if counted.status != 200 or type(tokens) is not int or not 0 <= tokens <= config.MAX_INPUT_TOKENS:
                    return 400, None
            finally:
                counted.close()
            self.calls += 1
        request['max_tokens'] = min(request.get('max_tokens', config.MAX_OUTPUT_TOKENS), config.MAX_OUTPUT_TOKENS)
        return self.send(path, json.dumps(request).encode(), headers)

    def send(self, path, body, headers):
        response = self.upstream(path, body, headers)
        if response.status >= 400:
            try:
                message = response.read(65536).decode(errors='replace')
                if response.status == 402 or billing_error(message):
                    self.billing_error = True
                return response.status, None
            finally:
                response.close()
        return response.status, response

    def _upstream(self, path, body, headers):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('deadline')
        connection = http.client.HTTPSConnection('api.anthropic.com', timeout=min(60, remaining))
        connection.request('POST', path, body, headers)
        return connection.getresponse()

    def __enter__(self):
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': 0.01}, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
