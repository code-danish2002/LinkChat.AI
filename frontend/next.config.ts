import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  images: {
    remotePatterns: [{ protocol: "https", hostname: "i.ytimg.com" }],
  },
  webpack: (config) => {
    // onnxruntime-web (used by the browser voice detector) loads its wasm
    // through a dynamic require; the files come from a CDN at runtime, so the
    // bundler's "cannot statically extract" warning is noise.
    config.ignoreWarnings = [...(config.ignoreWarnings ?? []), { module: /onnxruntime-web/ }];
    return config;
  },
};

export default nextConfig;
