import type { NextApiRequest, NextApiResponse } from "next";

const COOKIE_MAX_AGE = 60 * 60 * 24 * 30; // 30 days

export default function handler(req: NextApiRequest, res: NextApiResponse) {
    if (req.method !== "POST") return res.status(405).end();

    const API_KEY = process.env.API_KEY || "";
    if (!API_KEY) return res.status(500).json({ error: "API_KEY not configured" });

    const { password } = req.body ?? {};
    if (password !== API_KEY) {
        return res.status(401).json({ error: "Falsches Passwort" });
    }

    const cookieValue = [
        `rag_auth=${API_KEY}`,
        "Path=/",
        `Max-Age=${COOKIE_MAX_AGE}`,
        "HttpOnly",
        "SameSite=Lax",
        ...(process.env.NODE_ENV === "production" ? ["Secure"] : []),
    ].join("; ");

    res.setHeader("Set-Cookie", cookieValue);
    return res.status(200).json({ ok: true });
}
