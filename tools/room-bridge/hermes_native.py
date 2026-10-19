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
from nous_budget import CONFIG, dollars
from hermes_wire import CREDIT_ERROR

MAX_TURNS = 32


class SpendStop(ValueError):
    pass


def quote_model(model):
    try:
        from hermes_cli.models_pricing import get_pricing_for_provider
        prices = get_pricing_for_provider('nous', force_refresh=True)
    except Exception:
        prices = {}
    pinned = json.loads(CONFIG.read_text()).get('pinned_prices', {})
    for source, row in (('hermes_picker_v1_models', prices.get(model)),
                        ('pinned_conservative', pinned.get(model))):
        try:
            original = row.get('original') or {}
            incoming = max(dollars(row['prompt']), dollars(row.get('input_cache_read', 0)),
                           dollars(row.get('input_cache_write', 0)), dollars(original.get('prompt', 0)))
            outgoing = max(dollars(row['completion']), dollars(original.get('completion', 0)))
            return {'model': model, 'input_usd_per_token': str(incoming),
                    'output_usd_per_token': str(outgoing), 'source': source}
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    return None


class CallBudget:
    def __init__(self, allowance, price, model):
        self.allowance = dollars(allowance)
        self.input_price = dollars(price.get('input_usd_per_token'))
        self.output_price = dollars(price.get('output_usd_per_token'))
        self.model, self.origin = model, None
        self.reserved = Decimal(0)
        self.lock = threading.Lock()
        self.stopped = False
        self.credits_exhausted = False
        self.credits_path = None

    def stop(self):
        self.stopped = True
        raise SpendStop('Nous run spending limit reached')

    def output_limit(self, *, slots=1):
        with self.lock:
            if not self.output_price:
                return None
            return max(0, int((self.allowance - self.reserved) / self.output_price / slots))

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
        if ((not caps and self.output_price) or any(type(cap) is not int or cap <= 0 for cap in caps)
                or body.get('n', 1) != 1 or body.get('best_of', 1) != 1 or 'prediction' in body):
            self.stop()
        outputs = max(caps, default=0)
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
            response = sync(client, request, *args, **kwargs)
            status = getattr(response, 'status_code', 0)
            self.billing_response(status, response.read() if status >= 400 and status != 402 else b'')
            return response
        async def send_async(client, request, *args, **kwargs):
            if self.admit(request):
                kwargs['follow_redirects'] = False
            response = await asynchronous(client, request, *args, **kwargs)
            status = getattr(response, 'status_code', 0)
            self.billing_response(status, await response.aread() if status >= 400 and status != 402 else b'')
            return response
        httpx.Client.send, httpx.AsyncClient.send = send, send_async
        try:
            yield
        finally:
            httpx.Client.send, httpx.AsyncClient.send = sync, asynchronous

    def billing_response(self, status, body):
        if status == 402 or (status >= 400 and CREDIT_ERROR.search(body.decode('utf-8', errors='replace'))):
            self.credits_exhausted = self.stopped = True
            if self.credits_path is not None:
                self.credits_path.write_text('nous credits exhausted')


def run_native(prompt, model, reasoning, usage_file, budget):
    import run_agent
    from hermes_cli.oneshot import run_oneshot
    original = run_agent.AIAgent
    class BoundedAgent(original):
        def __init__(self, *args, **kwargs):
            budget.bind(kwargs.get('base_url'), kwargs.get('provider'), kwargs.get('model'))
            kwargs.update(max_iterations=MAX_TURNS, max_tokens=budget.output_limit(slots=MAX_TURNS),
                          fallback_model=None, skip_background_review=True)
            super().__init__(*args, **kwargs)

        def _max_tokens_param(self, value):
            limit = budget.output_limit()
            return super()._max_tokens_param(min(value, limit) if limit is not None else value)
    run_agent.AIAgent = BoundedAgent
    try:
        with budget.enforce():
            return run_oneshot(prompt, model=model, provider='nous', reasoning=reasoning,
                               usage_file=usage_file)
    finally:
        run_agent.AIAgent = original


def main():
    import argparse
    if sys.argv[1:2] == ['--quote-model'] and len(sys.argv) == 3:
        print(json.dumps(quote_model(sys.argv[2])))
        return 0
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
    budget.credits_path = Path(args.usage_file).with_suffix('.credits-exhausted')
    try:
        code = run_native(args.z, args.m, args.reasoning, args.usage_file, budget)
    except Exception:
        code = 1
    path = Path(args.usage_file)
    try:
        usage = json.loads(path.read_text())
    except (OSError, ValueError):
        usage = {}
    usage.update(spend_upper_bound_usd=str(budget.reserved), spend_limit_reached=budget.stopped,
                 nous_credits_exhausted=budget.credits_exhausted)
    path.write_text(json.dumps(usage))
    return 3 if budget.stopped else code


if __name__ == '__main__':
    sys.exit(main())
