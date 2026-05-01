import type { NextApiRequest, NextApiResponse } from "next";
import { verifyToken } from "@/lib/auth";
import { getAdminPassword, setAdminPassword, clearAdminPassword, isMode2Active } from "@/lib/auth-config";

async function currentRole(req: NextApiRequest): Promise<"admin" | "user" | null> {
    const appPassword = process.env.APP_PASSWORD || "";
    if (!appPassword) return "admin";
    const token = req.cookies.rag_auth ?? "";
    return verifyToken(token, appPassword);
}

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
    if (req.method === "GET") {
        return res.status(200).json({ mode: isMode2Active() ? "2" : "1" });
    }

    if (req.method === "POST") {
        const role = await currentRole(req);
        if (role !== "admin") {
            return res.status(403).json({ error: "Admin access required" });
        }

        const { admin_password } = req.body ?? {};

        // Passing null or omitting the field disables Mode 2
        if (admin_password === null || admin_password === undefined) {
            clearAdminPassword();
            return res.status(200).json({ ok: true, mode: "1" });
        }

        if (typeof admin_password !== "string" || admin_password.length < 8) {
            return res.status(400).json({ error: "Password must be at least 8 characters" });
        }

        const appPassword = process.env.APP_PASSWORD || "";
        if (admin_password === appPassword) {
            return res.status(400).json({ error: "Admin password must differ from the user password" });
        }

        setAdminPassword(admin_password);
        return res.status(200).json({ ok: true, mode: "2" });
    }

    return res.status(405).end();
}