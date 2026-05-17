import type { NextApiRequest, NextApiResponse } from "next";
import { logAuth, clientIp } from "@/lib/log-auth";

export default function handler(req: NextApiRequest, res: NextApiResponse) {
    logAuth("LOGOUT", { ip: clientIp(req) });
    res.setHeader("Set-Cookie", [
        "rag_auth=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax",
        "lancy_display_name=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax",
    ]);
    return res.status(200).json({ ok: true });
}
