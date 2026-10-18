"""Run smoke probes with a fake curl; never touch production or print identities."""
import json
import os
import pathlib
import subprocess
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]


class SmokeFixtureTests(unittest.TestCase):
    def test_requests_use_private_configuration_and_frozen_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            curl = root / "curl"
            curl.write_text('''#!/usr/bin/env python3
import json, os, sys
if "--config" in sys.argv: sys.stdin.read()
args=sys.argv
payload=json.loads(args[args.index("-d")+1])
with open(os.environ["SMOKE_REQUEST_LOG"],"a") as f: f.write(json.dumps(payload)+"\\n")
content={"ok":True,"existing":True,"replayed":True,"organizations":[],"matching_records":[],"live_rows":1,"retired_refs":["P-TEST-OLD"],"name":"Synthetic Supplier","role_refs":["P-TEST-LIVE"],"hops":2}
print(json.dumps({"result":{"content":[{"type":"text","text":json.dumps(content,separators=(",",":"))}]}},separators=(",",":")))
''')
            curl.chmod(0o755)
            frozen = {"idempotency_key": "smoke-links-probe-permanent", "ref": "V-BNK-013",
                      "kind": "note", "summary": "smoke links probe — edge already exists, replayed for ever after",
                      "links": [{"from_ref": "V-BNK-013", "to_ref": "C-TEST", "kind": "intro"}]}
            fixtures = root / "fixtures.env"
            fixtures.write_text("\n".join([
                "SMOKE_GRAPH_CLIENT_SURNAME='Synthetic'",
                "SMOKE_GRAPH_VENDOR_QUERY='Synthetic Vendor'",
                "SMOKE_GRAPH_TWO_HOP_NAME='Synthetic'",
                "SMOKE_GRAPH_CLIENT_REF='C-TEST'",
                "SMOKE_SUPPLIER_NAME='Synthetic Supplier'",
                "SMOKE_SUPPLIER_SURVIVOR_REF='P-TEST-LIVE'",
                "SMOKE_SUPPLIER_TOMBSTONE_REF='P-TEST-OLD'",
                "SMOKE_SUPPLIER_ZERO_ALIAS_NAME='Synthetic Other'",
                "SMOKE_LINKS_PROBE_ARGS='" + json.dumps(frozen) + "'",
            ]) + "\n")
            log = root / "requests.jsonl"
            env = dict(os.environ, PATH=str(root)+os.pathsep+os.environ["PATH"],
                       CARR_MCP_TOKEN_JOE="synthetic-test", CARR_MCP_PROBE_TOKEN="",
                       CARR_MCP_ENV=str(root / "absent.env"), SMOKE_LOCAL_FIXTURES=str(fixtures), SMOKE_REQUEST_LOG=str(log),
                       SMOKE_REPS="2", SMOKE_REP_SLEEP="0", SMOKE_CALL_RETRY_SLEEP="0")
            result = subprocess.run(["bash", str(REPO / "mcp-server/smoke-reads.sh")],
                                    env=env, capture_output=True, text=True, timeout=45)
            requests = [json.loads(line)["params"] for line in log.read_text().splitlines()
                        if json.loads(line).get("method") == "tools/call"]
            self.assertTrue(any(r["name"] == "who-do-we-know" and
                                r["arguments"] == {"target": "C-TEST"} for r in requests))
            self.assertTrue(any(r["name"] == "find" and
                                r["arguments"] == {"query": "Synthetic Supplier"} for r in requests))
            replay = [r["arguments"] for r in requests if r["name"] == "log-activity" and
                      r["arguments"].get("idempotency_key") == frozen["idempotency_key"]]
            self.assertEqual(replay, [frozen, frozen])
            # Without private fixtures no permanent links request may be invented.
            fixtures.write_text("")
            log.write_text("")
            subprocess.run(["bash", str(REPO / "mcp-server/smoke-reads.sh")],
                           env=env, capture_output=True, text=True, timeout=45)
            self.assertFalse(any(json.loads(line).get("params", {}).get("arguments", {}).get(
                "idempotency_key") == frozen["idempotency_key"] for line in log.read_text().splitlines()))


if __name__ == "__main__":
    unittest.main()
