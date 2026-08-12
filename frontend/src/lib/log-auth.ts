// Server-side only — do NOT import in middleware.ts (Edge Runtime).
import fs from "fs";
import path from "path";
import type { NextApiRequest } from "next";

const LOG_PATH = path.join(process.cwd(), "..", "logs", "auth.log");

// Matches the backend's rotation (see backend/src/lancy/logging_config.py) and the
// start scripts' (scripts/lib/log-rotate.sh), so every log the project writes is
// bounded by the same numbers. Nothing else truncates this file — the app appends
// to it for the life of the deployment — so without this it grows forever.
const MAX_BYTES = Number(process.env.AUTH_LOG_MAX_BYTES) || 10 * 1024 * 1024;
const BACKUP_COUNT = Number(process.env.AUTH_LOG_BACKUP_COUNT) || 5;

/** Cascade auth.log -> .1 -> … -> .BACKUP_COUNT once it outgrows MAX_BYTES. */
function rotateIfOversized(): void {
    let size: number;
    try {
        size = fs.statSync(LOG_PATH).size;
    } catch {
        return; // no file yet, or unreadable — the append below reports it
    }
    if (size < MAX_BYTES) return;

    // One stat per event is affordable because auth events are rare (logins and
    // failures, not requests). An in-memory byte counter would be cheaper but
    // wrong: Next.js may run several worker processes against this one file.
    try {
        fs.rmSync(`${LOG_PATH}.${BACKUP_COUNT}`, { force: true });
        for (let i = BACKUP_COUNT - 1; i >= 1; i--) {
            if (fs.existsSync(`${LOG_PATH}.${i}`)) {
                fs.renameSync(`${LOG_PATH}.${i}`, `${LOG_PATH}.${i + 1}`);
            }
        }
        fs.renameSync(LOG_PATH, `${LOG_PATH}.1`);
    } catch {
        // Two workers rotating at once can lose a race here. Appending simply
        // continues against whichever file now holds the name; an audit line is
        // never worth throwing out of an auth path.
    }
}

export function logAuth(event: string, fields: Record<string, string | null | undefined> = {}): void {
    const parts = [new Date().toISOString(), event];
    for (const [k, v] of Object.entries(fields)) {
        if (v != null && v !== "") parts.push(`${k}="${v}"`);
    }
    try {
        rotateIfOversized();
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
