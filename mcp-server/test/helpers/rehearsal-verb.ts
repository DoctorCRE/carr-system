// Stand-in for tools/call-verb.py in the Call Mode rehearsal: same argv shape
// (<verb> '<json>'), same stdout (one JSON result), but answered by the
// rehearsal's disposable record layer instead of the production Worker.
const [url, verb, args = "{}"] = process.argv.slice(2);
if (url === undefined) throw new Error("rehearsal URL is required");
const requestArgs: unknown = JSON.parse(args);
const response = await fetch(url, {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({ verb, args: requestArgs }),
});
const body: unknown = await response.json();
process.stdout.write(`${JSON.stringify(body)}\n`);
process.exit(response.ok ? 0 : 1);
