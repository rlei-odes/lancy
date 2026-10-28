// HMAC-SHA256 signed session tokens.
// Format: role.exp_unix.hex_subject.hex_signature
// The subject is the user id conversations and piles are owned by. It is hex-encoded
// because LDAP and OIDC ids can contain dots. Tokens from before the subject was added
// have no subject and are refused, so those sessions log in again.
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

export type Session = { role: "admin" | "user"; sub: string };

export async function signToken(
    role: "admin" | "user",
    sub: string,
    secret: string,
    ttlSeconds?: number,
): Promise<string> {
    if (!sub) throw new Error("A session token needs a subject");
    const exp = Math.floor(Date.now() / 1000) + (ttlSeconds ?? DEFAULT_TOKEN_TTL);
    const payload = `${role}.${exp}.${toHex(new TextEncoder().encode(sub).buffer as ArrayBuffer)}`;
    const key = await hmacKey(secret);
    const sig = await crypto.subtle.sign(ALGO, key, new TextEncoder().encode(payload));
    return `${payload}.${toHex(sig)}`;
}

// Returns the role and subject if the token is valid and unexpired, otherwise null.
export async function verifySession(token: string, secret: string): Promise<Session | null> {
    const lastDot = token.lastIndexOf(".");
    if (lastDot < 0) return null;
    const payload = token.slice(0, lastDot);
    const sig = token.slice(lastDot + 1);
    const parts = payload.split(".");
    if (parts.length !== 3) return null;
    const [role, expStr, subHex] = parts;
    if (role !== "admin" && role !== "user") return null;
    if (parseInt(expStr) < Math.floor(Date.now() / 1000)) return null;
    if (!/^(?:[0-9a-f]{2})+$/.test(subHex)) return null;
    try {
        const key = await hmacKey(secret);
        const valid = await crypto.subtle.verify(ALGO, key, fromHex(sig), new TextEncoder().encode(payload));
        if (!valid) return null;
        return { role, sub: new TextDecoder().decode(fromHex(subHex)) };
    } catch {
        return null;
    }
}

// Returns the role if the token is valid and unexpired, otherwise null.
export async function verifyToken(token: string, secret: string): Promise<"admin" | "user" | null> {
    return (await verifySession(token, secret))?.role ?? null;
}