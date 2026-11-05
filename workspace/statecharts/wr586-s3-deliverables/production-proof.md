# Executable lifecycle observation

The partner performs the writes in a synthetic deal they own. The observer never performs writes or revokes a session. The unit stays unproven until both release smoke and this semantic observer pass against the deployed merge SHA.

Capture sanitized JSON responses for upload, private finalization, shared version 2 and private version 3. Store them in out/deliverables/partner-receipt.json under keys upload, finalize, share and unshare. Include partner as joe or dell and attestation with the partner's provenance statement. These responses are the actual byte and registered verb response bodies; preserve strings such as PostgreSQL bigint sizes. They are partner-attested evidence, not cryptographic audit records. Do not include cookies in this JSON.

After capturing the writes, revoke the upload session using the existing sign-out flow. Supply that session cookie in out/deliverables/revoked-cookie. Supply a separate current session for the same partner in out/deliverables/active-cookie. Keep both files local and do not commit or paste them. The observer reads these files at runtime and never prints their values.

From the deployed merge checkout, run the following command. The observer uses four GETs with redirects disabled, a 10-second timeout and a 36 MiB limit per response. It checks release identity, exact current private row/version, identical file bytes and a 401 for the revoked session. A network or contract failure exits 1.

```sh
python3 ops/deal-deliverables-proof.py --receipt out/deliverables/partner-receipt.json --sha "$(git rev-parse HEAD)" --active-cookie-file out/deliverables/active-cookie --revoked-cookie-file out/deliverables/revoked-cookie
```

Offline executable proof uses the same CLI with --fixture in the PostgreSQL suite. Actual registered handlers produce every lifecycle response and list; the real byte adapter produces upload/download/refusal. Only release identity is synthetic. The test invokes the parser successfully, then rejects revoked-session access, changed bytes and a wrong visibility version.

```sh
node --test mcp-server/test/deal-deliverables-postgres.test.mjs
```
