import fs from "fs";
import path from "path";
import { randomBytes } from "crypto";
import type { NextApiRequest, NextApiResponse } from "next";
import { verifyToken } from "@/lib/auth";
import {
    setAdminPassword,
    clearAdminPassword,
    isMode2Active,
    getSSOConfig,
    setSSOConfig,
    type SSOConfig,
} from "@/lib/auth-config";

async function currentRole(req: NextApiRequest): Promise<"admin" | "user" | null> {
    const appPassword = process.env.APP_PASSWORD || "";
    if (!appPassword) return "admin";
    const signingKey = process.env.SESSION_SECRET || appPassword;
    const token = req.cookies.rag_auth ?? "";
    return verifyToken(token, signingKey);
}

function sanitiseSSOForResponse(sso: SSOConfig | null): object | null {
    if (!sso) return null;
    if (sso.provider === "ldap") {
        // Mask the service account password — never send it to the browser.
        const { search_bind_password: _, ...rest } = sso;
        return { ...rest, search_bind_password: rest.search_bind_dn ? "••••••••" : undefined };
    }
    return sso;
}

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
    if (req.method === "GET") {
        return res.status(200).json({
            mode: isMode2Active() ? "2" : "1",
            sso: sanitiseSSOForResponse(getSSOConfig()),
            session_secret_set: !!process.env.SESSION_SECRET,
        });
    }

    if (req.method === "POST") {
        const role = await currentRole(req);
        if (role !== "admin") {
            return res.status(403).json({ error: "Admin access required" });
        }

        // ── Admin password management ─────────────────────────────────────
        if ("admin_password" in (req.body ?? {})) {
            const { admin_password } = req.body;

            if (admin_password === null || admin_password === undefined) {
                clearAdminPassword();
                return res.status(200).json({ ok: true, mode: "1" });
            }

            if (typeof admin_password !== "string" || admin_password.length < 8) {
                return res.status(400).json({ error: "Password must be at least 8 characters" });
            }

            const appPassword = process.env.APP_PASSWORD || "";
            if (admin_password === appPassword) {
                return res.status(400).json({ error: "Admin password must differ from the user password" });
            }

            setAdminPassword(admin_password);
            return res.status(200).json({ ok: true, mode: "2" });
        }

        // ── SSO config management ─────────────────────────────────────────
        if ("sso" in (req.body ?? {})) {
            const { sso } = req.body as { sso: SSOConfig | null };

            if (sso === null) {
                setSSOConfig(null);
                return res.status(200).json({ ok: true, sso: null });
            }

            if (!sso.provider || !["oidc", "ldap"].includes(sso.provider)) {
                return res.status(400).json({ error: "sso.provider must be 'oidc' or 'ldap'" });
            }

            if (sso.provider === "oidc") {
                if (!sso.client_id || !sso.issuer_url || !sso.redirect_uri) {
                    return res.status(400).json({ error: "OIDC requires client_id, issuer_url, and redirect_uri" });
                }
            } else {
                if (!sso.server || !sso.bind_dn_template || !sso.base_dn) {
                    return res.status(400).json({ error: "LDAP requires server, bind_dn_template, and base_dn" });
                }
                // Preserve existing search_bind_password if the placeholder was sent back.
                const existing = getSSOConfig();
                if (
                    sso.search_bind_password === "••••••••" &&
                    existing?.provider === "ldap" &&
                    existing.search_bind_password
                ) {
                    sso.search_bind_password = existing.search_bind_password;
                }
            }

            setSSOConfig(sso);

            // Auto-generate SESSION_SECRET if not already present in the environment.
            // Appends to .env so it takes effect after the next frontend restart.
            let sessionSecretGenerated = false;
            if (!process.env.SESSION_SECRET) {
                const envPath = path.join(process.cwd(), ".env");
                let existing = "";
                try { existing = fs.readFileSync(envPath, "utf-8"); } catch { /* .env may not exist */ }
                if (!existing.includes("SESSION_SECRET=")) {
                    const secret = randomBytes(32).toString("base64url");
                    fs.appendFileSync(envPath, `\nSESSION_SECRET=${secret}\n`);
                    sessionSecretGenerated = true;
                }
            }

            return res.status(200).json({ ok: true, sso: sanitiseSSOForResponse(sso), session_secret_generated: sessionSecretGenerated });
        }

        return res.status(400).json({ error: "Request body must contain 'admin_password' or 'sso'" });
    }

    return res.status(405).end();
}
