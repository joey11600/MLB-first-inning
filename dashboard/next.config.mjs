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
  },
};

export default nextConfig;
