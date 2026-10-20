// Bundles the browser adapter and sandbox proxy into the Python package.
import { build } from "esbuild";
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const outdir = join(here, "..", "src", "shiny_mcp_host", "static");
mkdirSync(outdir, { recursive: true });

const common = {
  bundle: true,
  format: "iife",
  target: ["es2020"],
  minify: true,
  sourcemap: false,
  legalComments: "eof",
  logLevel: "info",
};

await build({ ...common, entryPoints: [join(here, "src/host.ts")], outfile: join(outdir, "shiny-mcp-host.js") });
await build({ ...common, entryPoints: [join(here, "src/sandbox-proxy.ts")], outfile: join(outdir, "sandbox.js") });
copyFileSync(join(here, "src/styles.css"), join(outdir, "shiny-mcp-host.css"));

