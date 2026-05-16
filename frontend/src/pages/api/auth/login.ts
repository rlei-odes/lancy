import type { NextApiRequest, NextApiResponse } from "next";
import { randomUUID } from "crypto";
import { signToken } from "@/lib/auth";
import { getAdminPassword, getSSOConfig, isMode2Active, isMode3Active, getSessionTtlSeconds } from "@/lib/auth-config";

const SESSION_MAX_AGE = 60 * 60 * 24 * 365; // 1 year

function buildCookies(
    authToken: string,
    ttl: number,
    sessionId: string,
    displayName: string | null,
    existingSessionId: string | undefined,
    secure: string[],
): string[] {
    const cookies: string[] = [
        [`rag_auth=${authToken}`, "Path=/", `Max-Age=${ttl}`, "HttpOnly", "SameSite=Lax", ...secure].join("; "),
    ];

    // Only set session_id when not already present (Mode 1/2) or always override (Mode 3).
    if (!existingSessionId || isMode3Active()) {
        cookies.push(
            [`session_id=${sessionId}`, "Path=/", `Max-Age=${SESSION_MAX_AGE}`, "HttpOnly", "SameSite=Lax", ...secure].join("; "),
        );
    }

    if (displayName !== null) {
        cookies.push(
            [`lancy_display_name=${encodeURIComponent(displayName)}`, "Path=/", `Max-Age=${ttl}`, "HttpOnly", "SameSite=Lax", ...secure].join("; "),
        );
    }

    return cookies;
}

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
    if (req.method !== "POST") return res.status(405).end();

    const appPassword = process.env.APP_PASSWORD || "";
    if (!appPassword) return res.status(500).json({ error: "APP_PASSWORD not configured" });
    const signingKey = process.env.SESSION_SECRET || appPassword;

    const secure = process.env.NODE_ENV === "production" ? ["Secure"] : [];

    // ── Mode 3 LDAP ──────────────────────────────────────────────────────────
    const sso = getSSOConfig();
    if (sso?.provider === "ldap") {
        const { username, password } = req.body ?? {};
        if (!username || !password) {
            return res.status(400).json({ error: "Username and password required" });
        }

        const backendUrl = process.env.BACKEND_URL || "http://localhost:8080";
        let ldapRes: Response;
        try {
            ldapRes = await fetch(`${backendUrl}/api/v1/auth/ldap-verify`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    username,
                    password,
                    server: sso.server,
                    bind_dn_template: sso.bind_dn_template,
                    base_dn: sso.base_dn,
                    user_id_attribute: sso.user_id_attribute ?? "uid",
                    display_name_attribute: sso.display_name_attribute ?? "cn",
                    allowed_groups: sso.allowed_groups ?? [],
                    search_bind_dn: sso.search_bind_dn ?? null,
                    search_bind_password: sso.search_bind_password ?? null,
                }),
            });
        } catch {
            return res.status(503).json({ error: "Login service unavailable — try the admin password login" });
        }

        if (!ldapRes.ok) {
            const data = await ldapRes.json().catch(() => ({}));
            return res.status(401).json({ error: data.detail ?? "Invalid credentials" });
        }

        const { session_id, display_name } = await ldapRes.json();
        const ttl = getSessionTtlSeconds();
        const token = await signToken("user", signingKey, ttl);

        res.setHeader(
            "Set-Cookie",
            buildCookies(token, ttl, session_id, display_name, req.cookies["session_id"], secure),
        );
        return res.status(200).json({ ok: true, role: "user" });
    }

    // ── Mode 1 / Mode 2 password login ───────────────────────────────────────
    const { password } = req.body ?? {};
    const adminPassword = getAdminPassword();
    const mode2 = isMode2Active();

    let role: "admin" | "user";
    if (mode2 && password === adminPassword) {
        role = "admin";
    } else if (password === appPassword) {
        role = mode2 ? "user" : "admin";
    } else {
        return res.status(401).json({ error: "Incorrect password" });
    }

    const ttl = getSessionTtlSeconds();
    const token = await signToken(role, signingKey, ttl);
    const sessionId = req.cookies["session_id"] ?? randomUUID();

    res.setHeader(
        "Set-Cookie",
        buildCookies(token, ttl, sessionId, null, req.cookies["session_id"], secure),
    );
    return res.status(200).json({ ok: true, role });
}
