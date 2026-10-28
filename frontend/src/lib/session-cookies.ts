// Cookies a login sets. Node.js API routes only (uses node:crypto).
//
// Who a session is lives in the signed rag_auth token (see lib/auth.ts). An LDAP or
// OIDC login is its directory id. A password login (Mode 2, and the admin escape in
// Mode 3) has no personal identity, so it is this browser: browser_id, a random id
// kept for a year. It is read only here, at login, and signed into the token, so a
// personal login and an admin login in the same browser never share a history.

import { randomUUID } from "crypto";

const BROWSER_ID_MAX_AGE = 60 * 60 * 24 * 365; // 1 year

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// This browser's id for password logins. Before browser_id, a password login's history
// was owned by a random UUID in session_id, so that one is adopted to keep it. An LDAP
// or OIDC login also wrote its directory id there, which is not a UUID and is not adopted.
export function browserIdFor(cookies: Partial<Record<string, string>>): string {
    const existing = cookies["browser_id"];
    if (existing && UUID.test(existing)) return existing;
    const legacy = cookies["session_id"];
    if (legacy && UUID.test(legacy)) return legacy;
    return randomUUID();
}

export function loginCookies(opts: {
    authToken: string;
    ttl: number;
    browserId: string;
    displayName: string | null;
    secure: string[];
}): string[] {
    const { authToken, ttl, browserId, displayName, secure } = opts;
    const cookies = [
        [`rag_auth=${authToken}`, "Path=/", `Max-Age=${ttl}`, "HttpOnly", "SameSite=Lax", ...secure].join("; "),
        [`browser_id=${browserId}`, "Path=/", `Max-Age=${BROWSER_ID_MAX_AGE}`, "HttpOnly", "SameSite=Lax", ...secure].join("; "),
        // Superseded by the token's subject; cleared so it cannot be mistaken for an identity.
        "session_id=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax",
    ];
    if (displayName !== null) {
        cookies.push(
            [`lancy_display_name=${encodeURIComponent(displayName)}`, "Path=/", `Max-Age=${ttl}`, "HttpOnly", "SameSite=Lax", ...secure].join("; "),
        );
    } else {
        // A password login has no display name; drop one left by an earlier personal login.
        cookies.push("lancy_display_name=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax");
    }
    return cookies;
}
