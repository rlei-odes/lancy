import type { NextApiRequest, NextApiResponse } from "next";
import { verifyToken } from "@/lib/auth";

interface TestStep {
    label: string;
    ok: boolean;
    detail: string;
}

async function currentRole(req: NextApiRequest): Promise<"admin" | "user" | null> {
    const appPassword = process.env.APP_PASSWORD || "";
    if (!appPassword) return "admin";
    const signingKey = process.env.SESSION_SECRET || appPassword;
    return verifyToken(req.cookies.rag_auth ?? "", signingKey);
}

async function testLDAP(body: Record<string, unknown>): Promise<TestStep[]> {
    const backendUrl = process.env.BACKEND_URL || "http://localhost:8080";
    const res = await fetch(`${backendUrl}/api/v1/auth/ldap-test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            server: body.server,
            base_dn: body.base_dn,
            search_bind_dn: body.search_bind_dn ?? null,
            search_bind_password: body.search_bind_password ?? null,
        }),
        signal: AbortSignal.timeout(15_000),
    });
    const data = await res.json();
    return data.steps ?? [];
}

async function testOIDC(body: Record<string, unknown>): Promise<TestStep[]> {
    const steps: TestStep[] = [];
    const issuerUrl = String(body.issuer_url ?? "").replace(/\/$/, "");

    if (!issuerUrl) {
        return [{ label: "OIDC discovery", ok: false, detail: "issuer_url is required" }];
    }

    const discoveryUrl = `${issuerUrl}/.well-known/openid-configuration`;
    let discovery: Record<string, unknown> = {};
    try {
        const res = await fetch(discoveryUrl, { signal: AbortSignal.timeout(10_000) });
        if (!res.ok) {
            steps.push({ label: "OIDC discovery", ok: false, detail: `HTTP ${res.status} from ${discoveryUrl}` });
            return steps;
        }
        discovery = await res.json();
        steps.push({ label: "OIDC discovery", ok: true, detail: `Found at ${discoveryUrl}` });
    } catch (e) {
        steps.push({ label: "OIDC discovery", ok: false, detail: e instanceof Error ? e.message : String(e) });
        return steps;
    }

    const jwksUri = String(discovery.jwks_uri ?? "");
    if (!jwksUri) {
        steps.push({ label: "JWKS endpoint", ok: false, detail: "jwks_uri missing from discovery document" });
        return steps;
    }
    try {
        const res = await fetch(jwksUri, { signal: AbortSignal.timeout(10_000) });
        steps.push({
            label: "JWKS endpoint",
            ok: res.ok,
            detail: res.ok ? `Reachable at ${jwksUri}` : `HTTP ${res.status} from ${jwksUri}`,
        });
    } catch (e) {
        steps.push({ label: "JWKS endpoint", ok: false, detail: e instanceof Error ? e.message : String(e) });
    }

    return steps;
}

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
    if (req.method !== "POST") return res.status(405).end();

    const role = await currentRole(req);
    if (role !== "admin") return res.status(403).json({ error: "Admin access required" });

    const { provider, ...body } = req.body ?? {};

    try {
        let steps: TestStep[];
        if (provider === "ldap") {
            steps = await testLDAP(body);
        } else if (provider === "oidc") {
            steps = await testOIDC(body);
        } else {
            return res.status(400).json({ error: "provider must be 'ldap' or 'oidc'" });
        }
        return res.status(200).json({ steps });
    } catch (e) {
        return res.status(500).json({ error: e instanceof Error ? e.message : "Test failed" });
    }
}
