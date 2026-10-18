import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dispatch } from "./src/mcp.js";
import { authenticatedIdentity } from "./src/identity.js";

const before = JSON.parse(fs.readFileSync(new URL("../ops/fixtures/mcp-catalog/before-2026-10-05.json", import.meta.url)));
async function rpc(method, actor) {
  const request = new Request("https://synthetic.example/mcp", {
    method: "POST", body: JSON.stringify({ jsonrpc: "2.0", id: 1, method }),
  });
  const response = await dispatch(request, {}, {}, actor);
  const body = await response.json();
  if (!response.ok || body.error) throw new Error("advertisement measurement failed");
  return body.result;
}
const actor = () => authenticatedIdentity.connectionForGrant({ slug: "joe" });
const afterTools = (await rpc("tools/list", actor())).tools;
const afterInstructions = (await rpc("initialize", actor())).instructions;
function summary(tools, instructions) {
  return { tool_count: tools.length, tool_json_chars: JSON.stringify(tools).length,
    tool_name_json_chars: JSON.stringify(tools.map(t => t.name)).length,
    instruction_chars: instructions.length };
}
const after = summary(afterTools, afterInstructions);
function cost(stats) {
  return { tools: stats.tool_count, estimated_name_tokens: Math.ceil(stats.tool_name_json_chars / 4),
    estimated_schema_tokens: Math.ceil(stats.tool_json_chars / 4),
    estimated_instruction_tokens: Math.ceil(stats.instruction_chars / 4) };
}
const codexNames = ["codex-checkpoint", "codex-read-recovery"];
async function continuityCost(codex) {
  const fixtureDir = fileURLToPath(new URL("../out/_to_delete/advertisement-measurement/", import.meta.url));
  fs.mkdirSync(fixtureDir, { recursive: true });
  const tokenFile = path.join(fixtureDir, `${codex ? "codex" : "claude"}.env`);
  fs.writeFileSync(tokenFile, `CARR_${codex ? "CODEX" : "CLAUDE"}_CONTINUITY_MCP_TOKEN=synthetic-fixture\n`, { mode: 0o600 });
  const server = http.createServer(async (request, response) => {
    let body = "";
    for await (const chunk of request) body += chunk;
    const actor = { slug: "synthetic", via: `${codex ? "codex" : "claude"}-continuity-token`,
      continuity_surface: codex ? "codex" : "claude" };
    const result = await dispatch(new Request("https://synthetic.example/mcp", { method: "POST", body }), {}, {}, actor);
    response.writeHead(result.status, { "content-type": "application/json" });
    response.end(await result.text());
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  try {
    const env = { ...process.env, CARR_MCP_ENV: tokenFile,
      CARR_MCP_URL: `http://127.0.0.1:${server.address().port}/mcp` };
    delete env.CARR_CODEX_CONTINUITY_MCP_TOKEN;
    delete env.CARR_CLAUDE_CONTINUITY_MCP_TOKEN;
    const child = spawn(process.execPath, [fileURLToPath(new URL("./continuity-stdio-proxy.mjs", import.meta.url)),
      ...(codex ? ["--codex"] : [])], { env, stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "";
    child.stdout.on("data", chunk => { stdout += chunk; });
    child.stderr.resume();
    const timeout = setTimeout(() => child.kill(), 10000);
    const closed = new Promise(resolve => child.on("close", resolve));
    child.stdin.end(["initialize", "tools/list"].map((method, id) => JSON.stringify({ jsonrpc: "2.0", id, method })).join("\n") + "\n");
    const code = await closed;
    clearTimeout(timeout);
    if (code !== 0) throw new Error("continuity advertisement measurement failed");
    const [initialized, listed] = stdout.trim().split("\n").map(JSON.parse);
    if (!initialized.result || !listed.result) throw new Error("continuity advertisement measurement refused");
    const expected = codex ? codexNames : ["claude-checkpoint", "claude-read-recovery", "claude-record-event"];
    if (JSON.stringify(listed.result.tools.map(t => t.name).sort()) !== JSON.stringify([...expected].sort()))
      throw new Error("continuity advertisement changed; inspect the measured surface before comparing costs");
    return summary(listed.result.tools, initialized.result.instructions);
  } finally {
    server.close();
  }
}
const claudeContinuity = await continuityCost(false);
const codexContinuity = await continuityCost(true);
const beforeCodex = { ...before, tool_count: before.tool_count - codexNames.length,
  tool_json_chars: before.tool_json_chars - before.tools.filter(t => codexNames.includes(t.name)).reduce((n, t) => n + t.chars + 1, 0),
  tool_name_json_chars: JSON.stringify(before.tools.filter(t => !codexNames.includes(t.name)).map(t => t.name)).length };
const rows = [];
for (const client of ["Claude desktop", "Claude CLI", "Codex"]) {
  for (const server of client === "Codex" ? ["carr", "carr-records"] : ["carr", "CARR Record Layer"])
    rows.push({ client, server, before: cost(client === "Codex" ? beforeCodex : before), after: cost(after) });
  const continuity = client === "Codex" ? codexContinuity : claudeContinuity;
  rows.push({ client, server: client === "Codex" ? "carr-codex-continuity" : "carr-continuity",
    before: cost(continuity), after: cost(continuity) });
}
console.log(JSON.stringify({ schema: "carr-tool-advertisement-measurement/v1", baseline_source: before.source_sha,
  method: "Compact JSON characters / 4, rounded up per registration. Names and schemas are alternatives, not additive. Instructions are separate. These are transport estimates, not billed tokens or measured native startup context.",
  topology_evidence: "Local Claude and Codex registrations read on 2026-10-05; Claude account connector supplied by the task and the prior staged duplication finding. Account connector inheritance is assumed for desktop and CLI; native clients were not launched by this harness.",
  runs: 1, failures: 0, before: cost(before), after: cost(after), rows }, null, 2));
