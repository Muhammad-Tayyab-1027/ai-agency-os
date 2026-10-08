import type { NextConfig } from "next";

const backend = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  // The browser only ever talks to this origin; API calls are proxied to FastAPI so the
  // session cookie stays same-site (SameSite=Strict) and no CORS is needed.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
  poweredByHeader: false,
};

export default nextConfig;
