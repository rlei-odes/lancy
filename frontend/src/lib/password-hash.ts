// Password hashing and verification.
// Works in both Node.js and Edge Runtime — uses Web Crypto (crypto.subtle) only.
// Format: pbkdf2$<iterations>$<salt_b64url>$<hash_b64url>

const ALGO = "pbkdf2";
const DEFAULT_ITERATIONS = 210_000; // OWASP 2023 for PBKDF2-SHA256
const SALT_BYTES = 16;
const HASH_BYTES = 32;

function toB64Url(bytes: Uint8Array): string {
    let bin = "";
    for (const b of bytes) bin += String.fromCharCode(b);
    return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromB64Url(s: string): Uint8Array {
    const b64 = s.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (s.length % 4)) % 4);
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
}

async function pbkdf2(password: string, salt: Uint8Array, iterations: number, bytes: number): Promise<Uint8Array> {
    const key = await crypto.subtle.importKey(
        "raw",
        new TextEncoder().encode(password),
        "PBKDF2",
        false,
        ["deriveBits"],
    );
    const derived = await crypto.subtle.deriveBits(
        { name: "PBKDF2", salt: salt as BufferSource, iterations, hash: "SHA-256" },
        key,
        bytes * 8,
    );
    return new Uint8Array(derived);
}

function constantTimeEqual(a: Uint8Array, b: Uint8Array): boolean {
    if (a.length !== b.length) return false;
    let diff = 0;
    for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
    return diff === 0;
}

export function isHashString(s: string): boolean {
    return s.startsWith(`${ALGO}$`);
}

// Env-only resolvers — safe to call from Edge Runtime (no fs, no auth_config.json).
// _HASH wins over plaintext when both are set.
export function getAppPasswordSecret(): string | null {
    return process.env.APP_PASSWORD_HASH || process.env.APP_PASSWORD || null;
}

export function getAdminPasswordSecretFromEnv(): string | null {
    return process.env.ADMIN_PASSWORD_HASH || process.env.ADMIN_PASSWORD || null;
}

export async function hashPassword(plaintext: string, iterations = DEFAULT_ITERATIONS): Promise<string> {
    const salt = crypto.getRandomValues(new Uint8Array(SALT_BYTES));
    const hash = await pbkdf2(plaintext, salt, iterations, HASH_BYTES);
    return `${ALGO}$${iterations}$${toB64Url(salt)}$${toB64Url(hash)}`;
}

// Accepts either a hash string (pbkdf2$…) or a plaintext string.
// Plaintext branch does a constant-time byte comparison so timing is uniform either way.
export async function verifyPassword(submitted: string, stored: string): Promise<boolean> {
    if (!submitted || !stored) return false;

    if (!isHashString(stored)) {
        const a = new TextEncoder().encode(submitted);
        const b = new TextEncoder().encode(stored);
        return constantTimeEqual(a, b);
    }

    const parts = stored.split("$");
    if (parts.length !== 4 || parts[0] !== ALGO) return false;
    const iterations = parseInt(parts[1], 10);
    if (!Number.isFinite(iterations) || iterations < 1) return false;

    let salt: Uint8Array;
    let expected: Uint8Array;
    try {
        salt = fromB64Url(parts[2]);
        expected = fromB64Url(parts[3]);
    } catch {
        return false;
    }

    const actual = await pbkdf2(submitted, salt, iterations, expected.length);
    return constantTimeEqual(actual, expected);
}
