import type { NextApiRequest, NextApiResponse } from "next";
import { randomUUID } from "crypto";
import { signToken } from "@/lib/auth";
import { getAdminPassword, isMode2Active } from "@/lib/auth-config";

const COOKIE_MAX_AGE = 60 * 60 * 24 * 30; // 30 days
const SESSION_MAX_AGE = 60 * 60 * 24 * 365; // 1 year

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

    const secure = process.env.NODE_ENV === "production" ? ["Secure"] : [];

    const authCookie = [
        `rag_auth=${token}`,
        "Path=/",
        `Max-Age=${COOKIE_MAX_AGE}`,
        "HttpOnly",
        "SameSite=Lax",
        ...secure,
    ].join("; ");

    const cookies: string[] = [authCookie];

    // Stable per-browser identity — issued once, survives re-logins.
    if (!req.cookies["session_id"]) {
        cookies.push([
            `session_id=${randomUUID()}`,
            "Path=/",
            `Max-Age=${SESSION_MAX_AGE}`,
            "HttpOnly",
            "SameSite=Lax",
            ...secure,
        ].join("; "));
    }

    res.setHeader("Set-Cookie", cookies);
    return res.status(200).json({ ok: true, role });
}