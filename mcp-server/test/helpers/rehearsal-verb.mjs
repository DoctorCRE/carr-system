// Stand-in for tools/call-verb.py in the Call Mode rehearsal: same argv shape
// (<verb> '<json>'), same stdout (one JSON result), but answered by the
// rehearsal's disposable record layer instead of the production Worker.
const [url, verb, args = '{}'] = process.argv.slice(2);
const response = await fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ verb, args: JSON.parse(args) }) });
const body = await response.json();
process.stdout.write(`${JSON.stringify(body)}\n`);
process.exit(response.ok ? 0 : 1);
