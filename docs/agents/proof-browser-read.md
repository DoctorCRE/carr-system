# Read the signed-in proof browser

The designated proof browser listens at `127.0.0.1:9333`. The guard admits
configuration-free curl discovery at `/json/list` and `/json/version`.
Redirects, headers, request bodies, other ports and browser control endpoints
remain refused. This does not add a host to `KNOWN_HOSTS`.

For signed-in data, use `python3 tools/proof-browser-read.py config subscriptions`.
The reader uses an existing DoctorCRE app tab and performs only the two named
read verbs with empty arguments. The optional `text` operation reads visible
page text. The reader has no endpoint, expression, navigation or mutation option.
It disconnects its own socket and leaves the borrowed browser and tabs open.

If no DoctorCRE tab exists, collection exits 78 with insufficient evidence. Open the app in the designated
proof browser through the available browser tools. Follow the current sign-in
convention if the account chooser appears. The reader does not change profiles
or obtain credentials.

Each invocation makes one discovery request and one bounded browser evaluation.
There are no retries, queues or cached results. Discovery never follows a
redirect; the evaluation checks the current app origin before each collection.
The MCP requests also refuse redirects. Authentication refusals remain errors
in the returned MCP response and must not be reported as successful proofs.

Run `python3 ops/proof-browser-read-selftest.py` for the emitted browser-code
checks and `python3 ops/guard-selftest.py` for real hook-process admission and
refusal checks. The gates class discovers both suites.
