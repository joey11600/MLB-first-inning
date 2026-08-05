/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  experimental: {
    // scripts/copy-data.mjs mirrors ../data into ./data before build; include
    // it in the function trace so the CSVs ship with the serverless bundle.
    outputFileTracingIncludes: {
      "/api/**/*": ["./data/**/*"],
      "/": ["./data/**/*"],
    },
    // Route handlers probe ../data (the repo data dir) at request time via
    // dataDir(), so Next's file tracer pulls the ENTIRE tracked data/ tree
    // into every serverless function.  That is mostly wanted (picks CSVs,
    // cluster_demotions.json live there) -- but data/backups/ is 100+ MB of
    // git-history snapshots the dashboard never reads, and on 2026-08-05 it
    // pushed the bundle past Vercel's 250MB uncompressed limit (251.15MB),
    // erroring every production deploy.  Both glob spellings on purpose:
    // trace keys can be relative to the repo root or prefixed with ../.
    outputFileTracingExcludes: {
      "*": ["../data/backups/**", "**/data/backups/**"],
    },
  },
};

export default nextConfig;
