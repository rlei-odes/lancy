// Server-side only — do NOT import in middleware.ts (Edge Runtime).
import fs from "fs";
import path from "path";
import type { NextApiRequest } from "next";

const LOG_PATH = path.join(process.cwd(), "..", "logs", "auth.log");

export function logAuth(event: string, fields: Record<string, string | null | undefined> = {}): void {
    const parts = [new Date().toISOString(), event];
    for (const [k, v] of Object.entries(fields)) {
        if (v != null && v !== "") parts.push(`${k}="${v}"`);
    }
    try {
        fs.appendFileSync(LOG_PATH, parts.join(" ") + "\n");
    } catch {
        // Non-critical — never let logging break auth
    }
}

export function clientIp(req: NextApiRequest): string {
    const fwd = req.headers["x-forwarded-for"];
    if (typeof fwd === "string") return fwd.split(",")[0].trim();
    return req.socket?.remoteAddress ?? "-";
}
