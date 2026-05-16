// HMAC-SHA256 signed session tokens.
// Format: role.exp_unix.hex_signature
// Works in both Next.js Edge Runtime (middleware) and Node.js API routes.

const ALGO = { name: "HMAC", hash: "SHA-256" };
const DEFAULT_TOKEN_TTL = 60 * 60 * 24 * 30; // 30 days (Mode 1 / Mode 2 default)

function toHex(buf: ArrayBuffer): string {
    return Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, "0")).join("");
}

function fromHex(hex: string): Uint8Array {
    const pairs = hex.match(/.{2}/g) ?? [];
    return new Uint8Array(pairs.map((h) => parseInt(h, 16)));
}

async function hmacKey(secret: string): Promise<CryptoKey> {
    return crypto.subtle.importKey("raw", new TextEncoder().encode(secret), ALGO, false, ["sign", "verify"]);
}

export async function signToken(role: "admin" | "user", secret: string, ttlSeconds?: number): Promise<string> {
    const exp = Math.floor(Date.now() / 1000) + (ttlSeconds ?? DEFAULT_TOKEN_TTL);
    const payload = `${role}.${exp}`;
    const key = await hmacKey(secret);
    const sig = await crypto.subtle.sign(ALGO, key, new TextEncoder().encode(payload));
    return `${payload}.${toHex(sig)}`;
}

// Returns the role if the token is valid and unexpired, otherwise null.
export async function verifyToken(token: string, secret: string): Promise<"admin" | "user" | null> {
    const lastDot = token.lastIndexOf(".");
    if (lastDot < 0) return null;
    const payload = token.slice(0, lastDot);
    const sig = token.slice(lastDot + 1);
    const parts = payload.split(".");
    if (parts.length !== 2) return null;
    const [role, expStr] = parts;
    if (role !== "admin" && role !== "user") return null;
    if (parseInt(expStr) < Math.floor(Date.now() / 1000)) return null;
    try {
        const key = await hmacKey(secret);
        const valid = await crypto.subtle.verify(ALGO, key, fromHex(sig), new TextEncoder().encode(payload));
        return valid ? role : null;
    } catch {
        return null;
    }
}