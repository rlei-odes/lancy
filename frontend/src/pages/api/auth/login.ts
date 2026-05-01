import type { NextApiRequest, NextApiResponse } from "next";
import { signToken } from "@/lib/auth";
import { getAdminPassword, isMode2Active } from "@/lib/auth-config";

const COOKIE_MAX_AGE = 60 * 60 * 24 * 30; // 30 days

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
    if (req.method !== "POST") return res.status(405).end();

    const appPassword = process.env.APP_PASSWORD || "";
    if (!appPassword) return res.status(500).json({ error: "APP_PASSWORD not configured" });

    const { password } = req.body ?? {};
    const adminPassword = getAdminPassword();
    const mode2 = isMode2Active();

    let role: "admin" | "user";
    if (mode2 && password === adminPassword) {
        role = "admin";
    } else if (password === appPassword) {
        // Mode 1: single password → admin. Mode 2: APP_PASSWORD → user.
        role = mode2 ? "user" : "admin";
    } else {
        return res.status(401).json({ error: "Falsches Passwort" });
    }

    const token = await signToken(role, appPassword);

    const cookieValue = [
        `rag_auth=${token}`,
        "Path=/",
        `Max-Age=${COOKIE_MAX_AGE}`,
        "HttpOnly",
        "SameSite=Lax",
        ...(process.env.NODE_ENV === "production" ? ["Secure"] : []),
    ].join("; ");

    res.setHeader("Set-Cookie", cookieValue);
    return res.status(200).json({ ok: true, role });
}