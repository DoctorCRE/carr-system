// Generate Cloudflare Worker type declarations.
import { execFileSync } from "node:child_process";
import { rmSync } from "node:fs";

const output = ".generated/worker-configuration.d.ts";
rmSync(output, { force: true });
execFileSync("wrangler", ["types", output, "--strict-vars=false", "--env-file", "/dev/null"], {
  stdio: "inherit",
});
