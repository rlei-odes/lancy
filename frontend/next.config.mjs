import webpack from "webpack";
import { config as dotenvConfig } from "dotenv";

dotenvConfig({ override: true });

const mode = process.env.BUILD_MODE ?? "standalone";
console.log("[Next] build mode", mode);

const disableChunk = !!process.env.DISABLE_CHUNK || mode === "export";
console.log("[Next] build with chunk: ", !disableChunk);

/** @type {import('next').NextConfig} */
const nextConfig = {
    async rewrites() {
        // Backend läuft immer lokal auf Port 8080.
        // Next.js proxied diese Pfade server-seitig — der Browser sieht nur eine Domain.
        const backend = process.env.BACKEND_URL || "http://localhost:8080";
        return [
            { source: "/api/:path*",   destination: `${backend}/api/:path*` },
            { source: "/auth/:path*",  destination: `${backend}/auth/:path*` },
            { source: "/v1/:path*",    destination: `${backend}/v1/:path*` },
            { source: "/files/:path*", destination: `${backend}/files/:path*` },
            { source: "/c/:conversationId", destination: "/" },
        ];
    },
    env: {
        SERVER_URL: "",
        NEXT_PUBLIC_SERVER_URL: "",
        USE_MOCK_SERVICES: process.env.NEXT_USE_MOCK_SERVICES,
    },
    webpack(config) {
        config.module.rules.push({
            test: /\.svg$/,
            use: ["@svgr/webpack"],
        });

        if (disableChunk) {
            config.plugins.push(new webpack.optimize.LimitChunkCountPlugin({ maxChunks: 1 }));
        }

        config.resolve.fallback = {
            child_process: false,
        };

        return config;
    },
    eslint: { ignoreDuringBuilds: true },
    output: mode,
    distDir: "dist",
    images: {
        unoptimized: mode === "export",
    },
    devIndicators: false,
    experimental: {
        forceSwcTransforms: true,
    },
};

const CorsHeaders = [
    { key: "Access-Control-Allow-Credentials", value: "true" },
    { key: "Access-Control-Allow-Origin", value: "*" },
    {
        key: "Access-Control-Allow-Methods",
        value: "*",
    },
    {
        key: "Access-Control-Allow-Headers",
        value: "*",
    },
    {
        key: "Access-Control-Max-Age",
        value: "86400",
    },
];

if (mode !== "export") {
    nextConfig.headers = async () => [
        { source: "/",           headers: CorsHeaders },
        { source: "/v1/:path*",  headers: CorsHeaders },
        { source: "/api/:path*", headers: CorsHeaders },
        { source: "/auth/:path*",headers: CorsHeaders },
    ];
}

export default nextConfig;
