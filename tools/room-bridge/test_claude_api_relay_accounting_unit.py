"""Round-2 reviewer repro, inverted into a no-unmetered-spend regression.

Adapted from /private/tmp/claude-api-round2-review.py against f954cf2a.
Only local HTTP, a fake upstream and a fake Keychain are used.
"""
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_claude_api_unit as fixtures
import claude_api_wire as wire
from claude_api_transport import Transport


class Response(io.BytesIO):
    status = 200

    def getheader(self, name, default=None):
        return 'application/json'


class RelayAccountingTests(unittest.TestCase):
    def test_tool_calls_are_charged_before_another_job_can_be_admitted(self):
        fixture = fixtures.ApiDeskTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.entry['model'] = 'claude-sonnet-5-5'
        calls = []

        def upstream(path, body, headers):
            request = json.loads(body)
            sdk = request['messages'][0]['content'] == 'sdk fixture'
            if path.endswith('count_tokens'):
                return Response(json.dumps({'input_tokens': 1000 if sdk else 100000}).encode())
            calls.append(request)
            return Response(json.dumps({'content': [], 'usage': (
                {'input_tokens': 1000, 'output_tokens': 800} if sdk else
                {'cache_creation_input_tokens': 100000, 'output_tokens': 8192})}).encode())

        def transport(key, model, deadline):
            return Transport(key, model, deadline, upstream=upstream)

        tool = """import json, os, urllib.request, uuid
for _ in range(15):
    req=urllib.request.Request(os.environ['ANTHROPIC_BASE_URL']+'/v1/messages', data=json.dumps({'model':'claude-sonnet-5-5','messages':[{'role':'user','content':uuid.uuid4().hex+' fixture '*100000}],'max_tokens':8192,'cache_control':{'type':'ephemeral','ttl':'1h'}}).encode(),headers={'content-type':'application/json'})
    with urllib.request.urlopen(req,timeout=2) as response: response.read()
"""
        sdk_call = """    import json, urllib.request
    payload={'model':options.model,'messages':[{'role':'user','content':'sdk fixture'}],'max_tokens':800}
    req=urllib.request.Request(options.env['ANTHROPIC_BASE_URL']+'/v1/messages',data=json.dumps(payload).encode(),headers={'content-type':'application/json'})
    with urllib.request.urlopen(req,timeout=2) as response: response.read()
"""
        target = fixture.sdk / '__init__.py'
        target.write_text(target.read_text().replace('    await transport_fixture(options)\n', '')
            .replace('    yield SimpleNamespace(model=options.model)',
            sdk_call + '    import subprocess, sys\n    subprocess.run([sys.executable, "-c", '
            + repr(tool) + '], cwd=options.cwd, check=True)\n    yield SimpleNamespace(model=options.model)')
            .replace('total_cost_usd=1.25', 'total_cost_usd=0.01'))
        fixture.seed_spend('189.00')
        with patch.object(wire, 'Transport', transport):
            results = [fixture.run_task(), fixture.run_task()]
        self.assertEqual([r['status'] for r in results], ['completed', 'refused'], results)
        self.assertEqual(results[1]['detail'], 'monthly_budget_exceeded')
        self.assertEqual(len(calls), 16)
        self.assertEqual(wire.Decimal(fixture.ledger.read()['spent_usd']), wire.Decimal('196.23880'))
        self.assertEqual(wire.Decimal(str(results[0]['cost_usd'])), wire.Decimal('7.23880'))
        self.assertEqual(results[0]['sdk_cost_usd'], 0.01)
        self.assertEqual(fixture.ledger.read()['reserved_usd'], '0')
        self.assertIsNone(fixture.ledger.read()['stop'])


if __name__ == '__main__':
    unittest.main()
