import type { NextApiRequest, NextApiResponse } from "next";
import { signToken } from "@/lib/auth";
import { getAdminPassword, getSSOConfig, isMode2Active, getSessionTtlSeconds } from "@/lib/auth-config";
import { getAppPasswordSecret, verifyPassword } from "@/lib/password-hash";
import { logAuth, clientIp } from "@/lib/log-auth";
import { browserIdFor, loginCookies } from "@/lib/session-cookies";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
    if (req.method !== "POST") return res.status(405).end();

    const appSecret = getAppPasswordSecret();
    if (!appSecret) return res.status(500).json({ error: "APP_PASSWORD or APP_PASSWORD_HASH not configured" });
    const signingKey = process.env.SESSION_SECRET || appSecret;

    // "Secure" must reflect whether this request actually arrived over HTTPS, not whether
    // this is a production build — a production `next start` is very often served plain
    // HTTP on an internal network (no public exposure), and a Secure cookie there would be
    // silently dropped by the browser, breaking login with no visible error.
    const secure = req.headers["x-forwarded-proto"] === "https" ? ["Secure"] : [];

    // ── Mode 3 LDAP ──────────────────────────────────────────────────────────
    // Skip this branch for the admin-escape submission (login.tsx sends adminEscape: true) —
    // that form has no username field and must fall through to the admin password check below.
    const sso = getSSOConfig();
    const { adminEscape } = req.body ?? {};
    if (sso?.provider === "ldap" && !adminEscape) {
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
            logAuth("LDAP_LOGIN_FAILED", { ip: clientIp(req), username });
            return res.status(401).json({ error: data.detail ?? "Invalid credentials" });
        }

        const { session_id, display_name } = await ldapRes.json();
        const ttl = getSessionTtlSeconds();
        const token = await signToken("user", session_id, signingKey, ttl);

        logAuth("LDAP_LOGIN_SUCCESS", { ip: clientIp(req), user: display_name });
        res.setHeader(
            "Set-Cookie",
            loginCookies({ authToken: token, ttl, browserId: browserIdFor(req.cookies), displayName: display_name, secure }),
        );
        return res.status(200).json({ ok: true, role: "user" });
    }

    // ── Mode 1 / Mode 2 password login ───────────────────────────────────────
    const { password } = req.body ?? {};
    const adminPassword = getAdminPassword();
    const mode2 = isMode2Active();

    let role: "admin" | "user";
    if (mode2 && adminPassword && (await verifyPassword(password, adminPassword))) {
        role = "admin";
    } else if (await verifyPassword(password, appSecret)) {
        role = mode2 ? "user" : "admin";
    } else {
        logAuth("LOGIN_FAILED", { ip: clientIp(req) });
        return res.status(401).json({ error: "Incorrect password" });
    }

    const ttl = getSessionTtlSeconds();
    // No personal identity behind a shared password: the history belongs to this browser.
    const browserId = browserIdFor(req.cookies);
    const token = await signToken(role, browserId, signingKey, ttl);

    logAuth("LOGIN_SUCCESS", { ip: clientIp(req), role, mode: mode2 ? "2" : "1" });
    res.setHeader("Set-Cookie", loginCookies({ authToken: token, ttl, browserId, displayName: null, secure }));
    return res.status(200).json({ ok: true, role });
}
