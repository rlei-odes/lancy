import type { NextApiRequest, NextApiResponse } from "next";
import { isMode2Active, isMode3Active, getSSOConfig } from "@/lib/auth-config";
import { getAppPasswordSecret } from "@/lib/password-hash";

export type AuthMode = {
    mode: 1 | 2 | 3;
    provider: "oidc" | "ldap" | null;
};

export default function handler(req: NextApiRequest, res: NextApiResponse<AuthMode | { error: string }>) {
    if (req.method !== "GET") return res.status(405).end();

    const sso = getSSOConfig();
    if (sso) {
        if (!getAppPasswordSecret()) {
            return res.status(500).json({
                error: "SSO is configured but neither APP_PASSWORD nor APP_PASSWORD_HASH is set. Set one to activate Mode 3.",
            } as any);
        }
        if (!isMode2Active()) {
            return res.status(500).json({
                error: "SSO is configured but no admin password is set. Set ADMIN_PASSWORD or ADMIN_PASSWORD_HASH so admins can log in via the escape hatch.",
            } as any);
        }
        return res.status(200).json({ mode: 3, provider: sso.provider });
    }
    if (isMode2Active()) {
        return res.status(200).json({ mode: 2, provider: null });
    }
    return res.status(200).json({ mode: 1, provider: null });
}
