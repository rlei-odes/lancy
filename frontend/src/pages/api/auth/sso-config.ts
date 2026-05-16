import type { NextApiRequest, NextApiResponse } from "next";
import { getSSOConfig } from "@/lib/auth-config";

// Public endpoint — returns only what the browser needs to initialise oidc-client-ts.
// Never exposes secrets or the full SSO config.
export default function handler(req: NextApiRequest, res: NextApiResponse) {
    if (req.method !== "GET") return res.status(405).end();

    const sso = getSSOConfig();
    if (!sso || sso.provider !== "oidc") {
        return res.status(404).json({ error: "OIDC not configured" });
    }

    return res.status(200).json({
        client_id: sso.client_id,
        issuer_url: sso.issuer_url,
        redirect_uri: sso.redirect_uri,
    });
}
