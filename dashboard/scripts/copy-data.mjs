// Bundle ../data into ./data before `next build` so the CSVs ship with the
// Vercel deploy (the upload tarball starts at `dashboard/`, so sibling dirs
// are otherwise invisible to the build).
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(here, "..");
const src = path.resolve(projectRoot, "..", "data");
const dest = path.resolve(projectRoot, "data");

if (!fs.existsSync(src)) {
  console.log(`[copy-data] no ${src} — skipping (running outside repo?)`);
  process.exit(0);
}

fs.rmSync(dest, { recursive: true, force: true });
fs.cpSync(src, dest, { recursive: true });
console.log(`[copy-data] copied ${src} → ${dest}`);
