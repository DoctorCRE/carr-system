"""Process-owned Hermes one-shot with a shared, conservative inference allowance."""
from contextlib import contextmanager
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import threading
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nous_budget import dollars

MAX_TURNS = 8
MAX_OUTPUT_TOKENS = 4096


class SpendStop(ValueError):
    pass


class CallBudget:
    def __init__(self, allowance, price, model):
        self.allowance = dollars(allowance)
        self.input_price = dollars(price.get('input_usd_per_token'))
        self.output_price = dollars(price.get('output_usd_per_token'))
        self.model, self.origin = model, None
        self.reserved = Decimal(0)
        self.lock = threading.Lock()
        self.stopped = False

    def stop(self):
        self.stopped = True
        raise SpendStop('Nous run spending limit reached')

    def bind(self, base_url, provider, model):
        origin = urlsplit(str(base_url))
        if provider != 'nous' or model != self.model or not origin.hostname:
            self.stop()
        with self.lock:
            route = (origin.scheme, origin.netloc)
            if self.origin is not None and self.origin != route:
                self.stop()
            self.origin = route

    def admit(self, request):
        if request.method != 'POST':
            return
        url = urlsplit(str(request.url))
        try:
            body = json.loads(request.content)
        except (ValueError, UnicodeError):
            body = {}
        inference = any(part in url.path for part in ('/completions', '/responses', '/messages'))
        if not inference and not (isinstance(body, dict) and 'model' in body):
            return
        if (not isinstance(body, dict) or not url.path.endswith('/chat/completions')
                or (url.scheme, url.netloc) != self.origin or body.get('model') != self.model):
            self.stop()
        caps = [body[key] for key in ('max_tokens', 'max_completion_tokens', 'max_output_tokens') if key in body]
        if (not caps or any(type(cap) is not int or not 0 < cap <= MAX_OUTPUT_TOKENS for cap in caps)
                or body.get('n', 1) != 1 or body.get('best_of', 1) != 1 or 'prediction' in body):
            self.stop()
        outputs = max(caps)
        messages = body.get('messages')
        if not isinstance(messages, list):
            self.stop()
        for message in messages:
            content = message.get('content') if isinstance(message, dict) else False
            if isinstance(content, list):
                if any(not isinstance(part, dict) or part.get('type') != 'text' for part in content):
                    self.stop()
            elif content is not None and not isinstance(content, str):
                self.stop()
        # Byte-level text tokens cannot outnumber serialized UTF-8 bytes. Include schemas,
        # framing and every other request field; reserve the entire allowed output, even on
        # failures/retries. Never refund: auxiliary/client retries may already have been billed.
        ceiling = len(request.content) * self.input_price + outputs * self.output_price
        with self.lock:
            if self.stopped or self.reserved + ceiling > self.allowance:
                self.stop()
            self.reserved += ceiling
        return True

    @contextmanager
    def enforce(self):
        import httpx
        sync, asynchronous = httpx.Client.send, httpx.AsyncClient.send
        def send(client, request, *args, **kwargs):
            if self.admit(request):
                kwargs['follow_redirects'] = False
            return sync(client, request, *args, **kwargs)
        async def send_async(client, request, *args, **kwargs):
            if self.admit(request):
                kwargs['follow_redirects'] = False
            return await asynchronous(client, request, *args, **kwargs)
        httpx.Client.send, httpx.AsyncClient.send = send, send_async
        try:
            yield
        finally:
            httpx.Client.send, httpx.AsyncClient.send = sync, asynchronous


def run_native(prompt, model, reasoning, usage_file, budget):
    import run_agent
    from hermes_cli.oneshot import run_oneshot
    original = run_agent.AIAgent
    class BoundedAgent(original):
        def __init__(self, *args, **kwargs):
            budget.bind(kwargs.get('base_url'), kwargs.get('provider'), kwargs.get('model'))
            kwargs.update(max_iterations=MAX_TURNS, max_tokens=MAX_OUTPUT_TOKENS,
                          fallback_model=None, skip_background_review=True)
            super().__init__(*args, **kwargs)
    run_agent.AIAgent = BoundedAgent
    try:
        with budget.enforce():
            return run_oneshot(prompt, model=model, provider='nous', reasoning=reasoning,
                               usage_file=usage_file)
    finally:
        run_agent.AIAgent = original


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('-z', required=True)
    parser.add_argument('-m', required=True)
    parser.add_argument('--reasoning', required=True)
    parser.add_argument('--usage-file', required=True)
    parser.add_argument('--price-json', required=True)
    parser.add_argument('--allowance-usd', required=True)
    args = parser.parse_args()
    os.environ['HERMES_IGNORE_USER_CONFIG'] = '1'
    os.environ['HERMES_SINGLE_QUERY_SESSION'] = '1'
    os.environ['HERMES_YOLO_MODE'] = '1'
    budget = CallBudget(args.allowance_usd, json.loads(args.price_json), args.m)
    try:
        code = run_native(args.z, args.m, args.reasoning, args.usage_file, budget)
    except Exception:
        code = 1
    path = Path(args.usage_file)
    try:
        usage = json.loads(path.read_text())
    except (OSError, ValueError):
        usage = {}
    usage.update(spend_upper_bound_usd=str(budget.reserved), spend_limit_reached=budget.stopped)
    path.write_text(json.dumps(usage))
    return 3 if budget.stopped else code


if __name__ == '__main__':
    sys.exit(main())
