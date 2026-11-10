// Emit historical source text without executing its JavaScript payload.
import { source as refAApp } from "./ref-a/app.ts";
import { source as refAData } from "./ref-a/data.ts";
import { source as refVApp } from "./ref-v/app.ts";
import { source as refVData } from "./ref-v/data.ts";

const sources = {
  "ref-a": { "app.js": refAApp, "data.js": refAData },
  "ref-v": { "app.js": refVApp, "data.js": refVData },
};
const site = process.argv[2];
if (site !== "ref-a" && site !== "ref-v") {
  process.stderr.write("Usage: node emit.ts ref-a|ref-v\n");
  process.exitCode = 2;
} else {
  process.stdout.write(JSON.stringify(sources[site]));
}
