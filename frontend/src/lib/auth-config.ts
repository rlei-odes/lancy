// Node.js only — do NOT import in middleware.ts (Edge Runtime).
// Reads and writes auth_config.json (gitignored) in the frontend working directory.
// Falls back to the ADMIN_PASSWORD env var for scripted/production deployments.

import fs from "fs";
import path from "path";

const CONFIG_PATH = path.join(process.cwd(), "auth_config.json");

// ── SSO config types ────────────────────────────────────────────────────────

export interface OIDCConfig {
    provider: "oidc";
    client_id: string;
    issuer_url: string;
    redirect_uri: string;
    allowed_groups?: string[];
    session_ttl_hours?: number;
}

export interface LDAPConfig {
    provider: "ldap";
    server: string;
    bind_dn_template: string;
    base_dn: string;
    user_id_attribute?: string;
    display_name_attribute?: string;
    allowed_groups?: string[];
    session_ttl_hours?: number;
    search_bind_dn?: string;
    search_bind_password?: string;
}

export type SSOConfig = OIDCConfig | LDAPConfig;

interface AuthConfig {
    admin_password?: string;
    sso?: SSOConfig;
}

// ── Internal read/write ─────────────────────────────────────────────────────

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

// ── Admin password (Mode 2) ─────────────────────────────────────────────────

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

// ── SSO config (Mode 3) ─────────────────────────────────────────────────────

export function getSSOConfig(): SSOConfig | null {
    return read().sso ?? null;
}

export function setSSOConfig(sso: SSOConfig | null): void {
    const config = read();
    if (sso === null) {
        delete config.sso;
    } else {
        config.sso = sso;
    }
    write(config);
}

export function isMode3Active(): boolean {
    return getSSOConfig() !== null;
}

// TTL in seconds for the session_ttl_hours configured in the SSO block.
// Falls back to 30 days for Modes 1/2.
export function getSessionTtlSeconds(): number {
    const sso = getSSOConfig();
    if (sso?.session_ttl_hours) {
        return sso.session_ttl_hours * 60 * 60;
    }
    return 60 * 60 * 24 * 30;
}
