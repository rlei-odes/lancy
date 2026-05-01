import type { NextApiRequest, NextApiResponse } from "next";
import { verifyToken } from "@/lib/auth";

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
    if (req.method !== "GET") return res.status(405).end();

    const appPassword = process.env.APP_PASSWORD || "";
    if (!appPassword) return res.status(200).json({ role: "admin" });

    const token = req.cookies.rag_auth ?? "";
    if (!token) return res.status(401).json({ error: "Not authenticated" });

    const role = await verifyToken(token, appPassword);
    if (!role) return res.status(401).json({ error: "Invalid session" });

    return res.status(200).json({ role });
}