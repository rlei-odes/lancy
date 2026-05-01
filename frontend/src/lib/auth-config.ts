// Node.js only — do NOT import in middleware.ts (Edge Runtime).
// Reads and writes auth_config.json (gitignored) in the frontend working directory.
// Falls back to the ADMIN_PASSWORD env var for scripted/production deployments.

import fs from "fs";
import path from "path";

const CONFIG_PATH = path.join(process.cwd(), "auth_config.json");

interface AuthConfig {
    admin_password?: string;
}

function read(): AuthConfig {
    try {
        return JSON.parse(fs.readFileSync(CONFIG_PATH, "utf-8"));
    } catch {
        return {};
    }
}

function write(config: AuthConfig): void {
    fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2));
}

export function getAdminPassword(): string | null {
    return read().admin_password ?? process.env.ADMIN_PASSWORD ?? null;
}

export function setAdminPassword(password: string): void {
    const config = read();
    config.admin_password = password;
    write(config);
}

export function clearAdminPassword(): void {
    const config = read();
    delete config.admin_password;
    write(config);
}

export function isMode2Active(): boolean {
    return getAdminPassword() !== null;
}