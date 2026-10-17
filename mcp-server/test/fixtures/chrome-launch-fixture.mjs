import { createServer } from "node:http";
import { writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import path from "node:path";
import { spawn } from "node:child_process";

function websocketFrame(payload) {
  const body = Buffer.from(payload);
  return Buffer.concat([Buffer.from([0x81, body.length]), body]);
}

function websocketPayload(buffer) {
  const masked = (buffer[1] & 0x80) !== 0;
  const length = buffer[1] & 0x7f;
  const maskOffset = 2;
  const mask = masked ? buffer.subarray(2, 6) : null;
  const payloadOffset = maskOffset + (masked ? 4 : 0);
  const payload = Buffer.from(buffer.subarray(payloadOffset, payloadOffset + length));
  if (mask) {
    for (let index = 0; index < payload.length; index += 1) payload[index] ^= mask[index % 4];
  }
  return payload.toString();
}

const [profile, scenario] = process.argv.slice(2);
const portFile = path.join(profile, "DevToolsActivePort");
process.stderr.write(`fixture:${scenario}\n`);
if (scenario === "inherited-stderr") {
  const holder = spawn(process.execPath, ["-e", "setTimeout(() => {}, 1500)"], { detached: true, stdio: ["ignore", "ignore", "inherit"] });
  process.stderr.write(`holder-pid:${holder.pid}\n`);
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
  let pageReady = !["page-late", "page-hang"].includes(scenario);
  const server = createServer((req, res) => {
    if (scenario === "http-hang") return;
    if (!ready) { res.writeHead(503).end(); return; }
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify(pageReady ? [{ type: "page", webSocketDebuggerUrl: `ws://127.0.0.1:${server.address().port}/devtools/page/fixture` }] : []));
  });
  server.on("upgrade", (req, socket) => {
    const accept = createHash("sha1")
      .update(`${req.headers["sec-websocket-key"]}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`)
      .digest("base64");
    socket.write([
      "HTTP/1.1 101 Switching Protocols",
      "Upgrade: websocket",
      "Connection: Upgrade",
      `Sec-WebSocket-Accept: ${accept}`,
      "",
      "",
    ].join("\r\n"));
    socket.once("data", (data) => {
      const message = JSON.parse(websocketPayload(data));
      if (message.method !== "Browser.close") {
        socket.write(websocketFrame(JSON.stringify({ id: message.id, error: { message: "unknown method" } })));
        return;
      }
      process.stderr.write("fixture:Browser.close\n");
      socket.write(websocketFrame(JSON.stringify({ id: message.id, result: {} })), () => {
        socket.end();
        server.close(() => process.exit(0));
      });
    });
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
