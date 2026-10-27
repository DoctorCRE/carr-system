// Serve the workspace prototype and its fixtures.
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { extname, join, normalize } from "node:path";
import { createServer } from "node:http";
import { fileURLToPath } from "node:url";

const workspaceRoot = normalize(join(fileURLToPath(new URL(".", import.meta.url)), ".."));
const port = Number.parseInt(process.env.WORKSPACE_PROTOTYPE_PORT ?? "4174", 10);
const types = { ".css": "text/css", ".html": "text/html", ".js": "text/javascript", ".json": "application/json", ".mjs": "text/javascript" };
const publicFiles = new Map([
  ["/", "public/index.html"],
  ["/index.html", "public/index.html"],
  ["/css/app.css", "public/css/app.css"],
  ["/js/app.js", "public/js/app.js"],
  ["/js/client.js", "public/js/client.js"]
]);
const fixtureFiles = new Set([
  "call-review", "command-center", "deal-room", "doc-request", "lead-board",
  "market-map", "marketing", "more", "notifications", "tour"
]);

export function resolvePrototypePath(pathname) {
  const publicFile = publicFiles.get(pathname);
  if (publicFile) return normalize(join(workspaceRoot, publicFile));
  const match = pathname.match(/^\/fixtures\/([a-z-]+)\.v1\.json$/);
  if (match && fixtureFiles.has(match[1])) return normalize(join(workspaceRoot, `fixtures/${match[1]}.v1.json`));
  return null;
}

export const prototypeServer = createServer(async (request, response) => {
  const method = request.method ?? "GET";
  const allowedMethods = new Set(["GET", "HEAD", "OPTIONS"]);
  if (!allowedMethods.has(method)) {
    response.writeHead(405, { "Allow": "GET, HEAD, OPTIONS", "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" }).end("Method not allowed");
    return;
  }
  const pathname = new URL(request.url ?? "/", "http://localhost").pathname;
  const candidate = resolvePrototypePath(pathname);
  if (!candidate) {
    response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" }).end("Not found");
    return;
  }
  if (method === "OPTIONS") {
    response.writeHead(204, { "Allow": "GET, HEAD, OPTIONS", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" }).end();
    return;
  }
  try {
    const info = await stat(candidate);
    if (!info.isFile()) throw new Error("Not a file");
    response.writeHead(200, { "Content-Type": `${types[extname(candidate)] ?? "application/octet-stream"}; charset=utf-8`, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" });
    if (method === "HEAD") response.end();
    else createReadStream(candidate).pipe(response);
  } catch {
    response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" }).end("Not found");
  }
});

if (process.argv[1] && normalize(process.argv[1]) === normalize(fileURLToPath(import.meta.url))) {
  prototypeServer.listen(port, "127.0.0.1", () => {
    console.log(`CARR Workspace Phase 0 prototype: http://127.0.0.1:${port}`);
  });
}
