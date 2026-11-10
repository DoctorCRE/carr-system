import { createServer as httpServer } from 'node:http';
import { readFileSync } from 'node:fs';

const marker = JSON.parse(readFileSync(new URL('../../../lib/release-reader-marker.json', import.meta.url), 'utf8'));

export function createServer(listener) {
  return httpServer((req, res) => {
    if (req.headers[marker.header] !== marker.value) {
      res.writeHead(403, { 'content-type': 'text/plain' });
      res.end(marker.refusal);
      return;
    }
    return listener(req, res);
  });
}
