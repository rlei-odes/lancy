import type { NextApiRequest, NextApiResponse } from "next";
import { createRemoteJWKSet, jwtVerify, JWTPayload } from "jose";
import { signToken } from "@/lib/auth";
import { getSSOConfig, getSessionTtlSeconds } from "@/lib/auth-config";
import { logAuth, clientIp } from "@/lib/log-auth";

// Module-level JWKS cache keyed by issuer URL.
// On key rotation or verification failure the cache entry is evicted and refetched.
const jwksCache = new Map<string, ReturnType<typeof createRemoteJWKSet>>();

async function getJWKS(issuerUrl: string) {
    if (!jwksCache.has(issuerUrl)) {
        const discoveryUrl = `${issuerUrl.replace(/\/$/, "")}/.well-known/openid-configuration`;
        const res = await fetch(discoveryUrl);
        if (!res.ok) throw new Error(`OIDC discovery failed: ${res.status}`);
        const discovery = await res.json();
        jwksCache.set(issuerUrl, createRemoteJWKSet(new URL(discovery.jwks_uri)));
    }
    return jwksCache.get(issuerUrl)!;
}

function extractDisplayName(payload: JWTPayload): string {
    return (
        (payload["name"] as string | undefined) ||
        (payload["preferred_username"] as string | undefined) ||
        (payload["email"] as string | undefined) ||
        String(payload.sub ?? "")
    );
}

function checkGroupMembership(payload: JWTPayload, allowedGroups: string[]): boolean {
    const groups: string[] = (payload["groups"] as string[] | undefined) || [];
    const roles: string[] = (payload["roles"] as string[] | undefined) || [];
    // Keycloak prefixes group names with "/" (e.g. "/allowed") — strip it for comparison
    const normalize = (g: string) => g.replace(/^\/+/, "").toLowerCase();
    const combined = [...groups, ...roles].map(normalize);
    return allowedGroups.some((g) => combined.includes(normalize(g)));
}

const SESSION_MAX_AGE = 60 * 60 * 24 * 365; // 1 year — stable IdP identity

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
    if (req.method !== "POST") return res.status(405).end();

    const appPassword = process.env.APP_PASSWORD || "";
    if (!appPassword) {
        return res.status(500).json({ error: "APP_PASSWORD is not set — cannot issue session cookies" });
    }
    const signingKey = process.env.SESSION_SECRET || appPassword;

    const sso = getSSOConfig();
    if (!sso || sso.provider !== "oidc") {
        return res.status(400).json({ error: "OIDC not configured" });
    }

    const { id_token } = req.body ?? {};
    if (!id_token || typeof id_token !== "string") {
        return res.status(400).json({ error: "id_token required" });
    }

    let payload: JWTPayload;
    try {
        let jwks = await getJWKS(sso.issuer_url);
        try {
            ({ payload } = await jwtVerify(id_token, jwks, {
                issuer: sso.issuer_url,
                audience: sso.client_id,
            }));
        } catch {
            // Evict cache and retry once in case keys were rotated.
            jwksCache.delete(sso.issuer_url);
            jwks = await getJWKS(sso.issuer_url);
            ({ payload } = await jwtVerify(id_token, jwks, {
                issuer: sso.issuer_url,
                audience: sso.client_id,
            }));
        }
    } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : "Token verification failed";
        logAuth("OIDC_TOKEN_INVALID", { ip: clientIp(req), error: msg });
        return res.status(401).json({ error: msg });
    }

    if (sso.allowed_groups?.length) {
        if (!checkGroupMembership(payload, sso.allowed_groups)) {
            logAuth("OIDC_LOGIN_DENIED", {
                ip: clientIp(req),
                username: (payload["preferred_username"] as string | undefined) ?? null,
                user: extractDisplayName(payload),
                sub: String(payload.sub ?? "-"),
                required: sso.allowed_groups.join(","),
            });
            return res.status(401).json({ error: "Your account is not authorised to use this application" });
        }
    }

    const sessionId = String(payload.sub ?? "");
    const displayName = extractDisplayName(payload);
    const username = (payload["preferred_username"] as string | undefined) ?? null;
    const ttl = getSessionTtlSeconds();

    const authToken = await signToken("user", signingKey, ttl);

    const secure = process.env.NODE_ENV === "production" ? ["Secure"] : [];
    const cookies = [
        [`rag_auth=${authToken}`, "Path=/", `Max-Age=${ttl}`, "HttpOnly", "SameSite=Lax", ...secure].join("; "),
        [`session_id=${sessionId}`, "Path=/", `Max-Age=${SESSION_MAX_AGE}`, "HttpOnly", "SameSite=Lax", ...secure].join("; "),
        [`lancy_display_name=${encodeURIComponent(displayName)}`, "Path=/", `Max-Age=${ttl}`, "HttpOnly", "SameSite=Lax", ...secure].join("; "),
    ];

    logAuth("OIDC_LOGIN_SUCCESS", { ip: clientIp(req), username, user: displayName, sub: sessionId });
    res.setHeader("Set-Cookie", cookies);
    return res.status(200).json({ ok: true });
}
