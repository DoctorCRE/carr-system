import { readFileSync } from "node:fs";
import { createServer as httpServer, type RequestListener } from "node:http";

const marker: unknown = JSON.parse(
  readFileSync(new URL("../../../lib/release-reader-marker.json", import.meta.url), "utf8"),
);

if (
  typeof marker !== "object" ||
  marker === null ||
  !("header" in marker) ||
  typeof marker.header !== "string" ||
  !("value" in marker) ||
  typeof marker.value !== "string" ||
  !("refusal" in marker) ||
  typeof marker.refusal !== "string"
)
  throw new Error("invalid release reader marker");
const { header, value, refusal } = marker;

export function createServer(listener: RequestListener) {
  return httpServer((req, res) => {
    if (new URL(req.url ?? "/", "http://fixture.invalid").pathname === "/release" && req.headers[header] !== value) {
      res.writeHead(403, { "content-type": "text/plain" });
      res.end(refusal);
      return;
    }
    return listener(req, res);
  });
}
