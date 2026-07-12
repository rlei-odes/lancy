import type { NextApiRequest, NextApiResponse } from "next";
import { verifyToken } from "@/lib/auth";
import { getAppPasswordSecret } from "@/lib/password-hash";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
    if (req.method !== "GET") return res.status(405).end();

    const appSecret = getAppPasswordSecret();
    if (!appSecret) return res.status(200).json({ role: "admin", display_name: null });

    const token = req.cookies.rag_auth ?? "";
    if (!token) return res.status(401).json({ error: "Not authenticated" });

    const signingKey = process.env.SESSION_SECRET || appSecret;
    const role = await verifyToken(token, signingKey);
    if (!role) return res.status(401).json({ error: "Invalid session" });

    const rawName = req.cookies.lancy_display_name ?? "";
    const display_name = rawName ? decodeURIComponent(rawName) : null;

    return res.status(200).json({ role, display_name });
}
