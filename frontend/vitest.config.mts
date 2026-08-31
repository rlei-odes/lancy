import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

export default defineConfig({
    resolve: {
        // Same `@/` alias tsconfig.json gives the app code.
        alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
    },
    // tsconfig.json sets jsx: "preserve" because Next does its own transform.
    // Vite has to actually compile it, so override that here only.
    oxc: { jsx: "automatic" },
    test: {
        include: ["tests/**/*.test.ts"],
        environment: "node",
    },
});
