"""Process-owned Hermes one-shot with a shared, conservative inference allowance."""
from contextlib import contextmanager, nullcontext
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import threading
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nous_budget import CONFIG, Ledger, dollars
from hermes_wire import billing_error

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
    def __init__(self, allowance, price, model, *, ledger=None):
        self.allowance = dollars(allowance)
        self.input_price = dollars(price.get('input_usd_per_token'))
        self.output_price = dollars(price.get('output_usd_per_token'))
        self.model, self.origin = model, None
        self.reserved = Decimal(0)
        self.lock = threading.Lock()
        self.stopped = False
        self.credits_exhausted = False
        self.credits_path = None
        self.ledger = ledger

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
        if hasattr(self, 'record_tools'):
            tools = body.get('tools', [])
            if (not isinstance(tools, list) or any(tool.get('type') != 'function'
                    or tool.get('function', {}).get('name') not in self.record_tools for tool in tools)):
                raise ValueError('record-layer refused non-CARR inference tools')
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
            if self.ledger is not None:
                try:
                    shared_stop = self.ledger.read()['stop']
                except (OSError, ValueError, KeyError, TypeError):
                    self.stop()
                if shared_stop:
                    self.stop()
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
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            payload = None
        if status == 402 or (status >= 400 and billing_error(payload)):
            self.credits_exhausted = self.stopped = True
            if self.ledger is not None:
                self.ledger.exhaust_credits()
            if self.credits_path is not None:
                self.credits_path.write_text('nous credits exhausted')


@contextmanager
def record_configuration():
    from importlib import import_module
    config = import_module('hermes_cli.config')
    mcp_config = import_module('tools.mcp_tool_config')
    from hermes_cli.mcp_startup import set_mcp_server_filter
    original = config.load_config
    servers = {'carr': {'url': 'https://api.doctorcre.com/mcp', 'auth': 'oauth'}}
    def load(*args, **kwargs):
        return {**original(*args, **kwargs), 'mcp_servers': servers, 'plugins': {}}
    set_mcp_server_filter(['carr'])
    original_mcp = mcp_config._load_mcp_config
    config.load_config = load
    mcp_config._load_mcp_config = lambda: servers
    try:
        yield
    finally:
        config.load_config = original
        mcp_config._load_mcp_config = original_mcp


def restrict_tools(agent):
    from tools.mcp_tool import _mcp_tool_server_names
    tools = [tool for tool in agent.tools
             if _mcp_tool_server_names.get(tool['function']['name']) == 'carr']
    if not tools:
        raise ValueError('record-layer requires live CARR tools')
    agent.tools = tools
    agent.valid_tool_names = {tool['function']['name'] for tool in tools}
    return frozenset(agent.valid_tool_names)


def run_native(prompt, model, reasoning, usage_file, budget, *, posture=None):
    if posture == 'record-layer':
        budget.record_tools = frozenset()
    with record_configuration() if posture == 'record-layer' else nullcontext():
        import run_agent
        from hermes_cli.oneshot import run_oneshot
        original = run_agent.AIAgent
        class BoundedAgent(original):
            def __init__(self, *args, **kwargs):
                budget.bind(kwargs.get('base_url'), kwargs.get('provider'), kwargs.get('model'))
                kwargs.update(max_iterations=MAX_TURNS, max_tokens=budget.output_limit(slots=MAX_TURNS),
                              fallback_model=None, skip_background_review=True)
                if posture == 'record-layer':
                    kwargs.update(enabled_toolsets=['carr'], skip_memory=True, skip_context_files=True)
                super().__init__(*args, **kwargs)
                if posture == 'record-layer':
                    self.record_tools = restrict_tools(self)
                    budget.record_tools = self.record_tools

            def _execute_tool_calls(self, assistant_message, *args, **kwargs):
                if posture == 'record-layer':
                    if any(call.function.name not in self.record_tools for call in assistant_message.tool_calls):
                        raise ValueError('record-layer refused a non-CARR tool call')
                return super()._execute_tool_calls(assistant_message, *args, **kwargs)

            def _max_tokens_param(self, value):
                limit = budget.output_limit()
                return super()._max_tokens_param(min(value, limit) if limit is not None else value)
        run_agent.AIAgent = BoundedAgent
        try:
            with budget.enforce():
                return run_oneshot(prompt, model=model, provider='nous', reasoning=reasoning,
                                   usage_file=usage_file, **({'toolsets': ['carr']} if posture == 'record-layer' else {}))
        finally:
            run_agent.AIAgent = original


def main():
    import argparse
    if sys.argv[1:2] == ['--quote-model'] and len(sys.argv) == 3:
        print(json.dumps(quote_model(sys.argv[2])))
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument('--posture', choices=['record-layer'])
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
    budget = CallBudget(args.allowance_usd, json.loads(args.price_json), args.m,
                        ledger=Ledger(os.environ['CARR_NOUS_BUDGET_RESULTS']))
    budget.credits_path = Path(args.usage_file).with_suffix('.credits-exhausted')
    try:
        code = run_native(args.z, args.m, args.reasoning, args.usage_file, budget, **({'posture': args.posture} if args.posture else {}))
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
