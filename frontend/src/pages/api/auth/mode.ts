import type { NextApiRequest, NextApiResponse } from "next";
import { isMode2Active, isMode3Active, getSSOConfig } from "@/lib/auth-config";

export type AuthMode = {
    mode: 1 | 2 | 3;
    provider: "oidc" | "ldap" | null;
};

export default function handler(req: NextApiRequest, res: NextApiResponse<AuthMode | { error: string }>) {
    if (req.method !== "GET") return res.status(405).end();

    const sso = getSSOConfig();
    if (sso) {
        const appPassword = process.env.APP_PASSWORD || "";
        if (!appPassword) {
            return res.status(500).json({
                error: "SSO is configured but APP_PASSWORD is not set. Set APP_PASSWORD to activate Mode 3.",
            } as any);
        }
        if (!isMode2Active()) {
            return res.status(500).json({
                error: "SSO is configured but ADMIN_PASSWORD is not set. Set ADMIN_PASSWORD so admins can log in via the escape hatch.",
            } as any);
        }
        return res.status(200).json({ mode: 3, provider: sso.provider });
    }
    if (isMode2Active()) {
        return res.status(200).json({ mode: 2, provider: null });
    }
    return res.status(200).json({ mode: 1, provider: null });
}
