import { createServer } from "node:http";
import { writeFile } from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";

const [profile, scenario] = process.argv.slice(2);
const portFile = path.join(profile, "DevToolsActivePort");
process.stderr.write(`fixture:${scenario}\n`);
if (scenario === "inherited-stderr") {
  const holder = spawn(process.execPath, ["-e", "setTimeout(() => {}, 4000)"], { detached: true, stdio: ["ignore", "ignore", "inherit"] });
  holder.unref();
}
if (scenario === "hang-ignore-term") process.on("SIGTERM", () => {});
if (scenario === "exit") process.exit(17);
if (scenario.endsWith("helper")) {
  // Inherit the browser's owned group, but resist graceful shutdown.
  const helper = spawn(process.execPath, ["-e", "process.on('SIGTERM', () => {}); process.stdout.write('ready'); setInterval(() => {}, 1000)"], { stdio: ["ignore", "pipe", "inherit"] });
  await new Promise((resolve) => helper.stdout.once("data", resolve));
  await writeFile(path.join(profile, "helper.pid"), String(helper.pid));
  process.stderr.write(`helper-pid:${helper.pid}\n`);
}
if (scenario.startsWith("hang")) setInterval(() => {}, 1000);
else {
  let ready = scenario !== "http-late";
  let pageReady = scenario !== "page-late";
  const server = createServer((req, res) => {
    if (scenario === "http-hang") return;
    if (!ready) { res.writeHead(503).end(); return; }
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify(pageReady ? [{ type: "page", webSocketDebuggerUrl: `ws://127.0.0.1:${server.address().port}/devtools/page/fixture` }] : []));
  });
  server.listen(0, "127.0.0.1", async () => {
    const contents = `${server.address().port}\n/devtools/browser/fixture\n`;
    if (scenario === "partial") {
      await writeFile(portFile, "");
      setTimeout(() => writeFile(portFile, contents), 100);
    } else await writeFile(portFile, contents);
    if (scenario === "http-late") setTimeout(() => { ready = true; }, 100);
    if (scenario === "page-late") setTimeout(() => { pageReady = true; }, 300);
  });
}
