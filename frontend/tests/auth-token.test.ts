/**
 * Intent: a session cookie proves a role, and cannot be forged or self-promoted.
 *
 * `role.exp.hmac` is the whole session model. Middleware trusts whatever
 * `verifyToken` returns and sets `x-user-role` from it, so every rejection here
 * is a privilege boundary: a token signed with another secret, a `user` token
 * edited to say `admin`, or an expired one must all come back `null` rather than
 * a role.
 */

import { describe, expect, it, vi, afterEach } from "vitest";

import { signToken, verifyToken } from "@/lib/auth";

const SECRET = "test-signing-secret";

afterEach(() => {
    vi.useRealTimers();
});

describe("round trip", () => {
    it.each(["admin", "user"] as const)("preserves the %s role", async (role) => {
        expect(await verifyToken(await signToken(role, SECRET), SECRET)).toBe(role);
    });

    it("puts the role and expiry in the clear, with the signature last", async () => {
        const token = await signToken("user", SECRET, 3600);
        const [role, exp, sig] = token.split(".");

        expect(role).toBe("user");
        expect(Number(exp)).toBeGreaterThan(Math.floor(Date.now() / 1000));
        expect(sig).toMatch(/^[0-9a-f]{64}$/);
    });
});

describe("forgery is rejected", () => {
    it("rejects a token signed with a different secret", async () => {
        const token = await signToken("admin", "the-real-secret");
        expect(await verifyToken(token, "an-attackers-guess")).toBeNull();
    });

    it("rejects a user token edited to claim admin", async () => {
        const token = await signToken("user", SECRET);
        const forged = token.replace(/^user\./, "admin.");

        expect(forged.startsWith("admin.")).toBe(true);
        expect(await verifyToken(forged, SECRET)).toBeNull();
    });

    it("rejects a token whose expiry was pushed out", async () => {
        const token = await signToken("admin", SECRET, 60);
        const [role, exp, sig] = token.split(".");
        const extended = `${role}.${Number(exp) + 86_400}.${sig}`;

        expect(await verifyToken(extended, SECRET)).toBeNull();
    });

    it("rejects a token with a flipped signature byte", async () => {
        const token = await signToken("admin", SECRET);
        const lastDot = token.lastIndexOf(".");
        const sig = token.slice(lastDot + 1);
        const flipped = (sig[0] === "a" ? "b" : "a") + sig.slice(1);

        expect(await verifyToken(`${token.slice(0, lastDot)}.${flipped}`, SECRET)).toBeNull();
    });

    it("rejects an unknown role even when the signature is valid for it", async () => {
        // Sign a "superadmin" payload the honest way, then check verify still refuses it.
        const exp = Math.floor(Date.now() / 1000) + 3600;
        const payload = `superadmin.${exp}`;
        const key = await crypto.subtle.importKey(
            "raw",
            new TextEncoder().encode(SECRET),
            { name: "HMAC", hash: "SHA-256" },
            false,
            ["sign"],
        );
        const raw = await crypto.subtle.sign(
            { name: "HMAC", hash: "SHA-256" },
            key,
            new TextEncoder().encode(payload),
        );
        const hex = Array.from(new Uint8Array(raw), (b) => b.toString(16).padStart(2, "0")).join("");

        expect(await verifyToken(`${payload}.${hex}`, SECRET)).toBeNull();
    });
});

describe("expiry", () => {
    it("accepts a token inside its window and refuses it once past", async () => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));

        const token = await signToken("admin", SECRET, 3600);
        expect(await verifyToken(token, SECRET)).toBe("admin");

        vi.setSystemTime(new Date("2026-01-01T00:59:00Z"));
        expect(await verifyToken(token, SECRET)).toBe("admin");

        vi.setSystemTime(new Date("2026-01-01T01:00:01Z"));
        expect(await verifyToken(token, SECRET)).toBeNull();
    });

    it("defaults to a 30-day window", async () => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));

        const exp = Number((await signToken("user", SECRET)).split(".")[1]);
        expect(exp - Math.floor(Date.now() / 1000)).toBe(60 * 60 * 24 * 30);
    });
});

describe("malformed input returns null, never throws", () => {
    it.each([
        ["empty string", ""],
        ["no separator", "notatoken"],
        ["missing signature", "admin.9999999999"],
        ["too many payload fields", "admin.9999999999.extra.abcdef"],
        ["non-hex signature", "admin.9999999999.zzzz"],
        ["odd-length signature", "admin.9999999999.abc"],
        ["empty role", ".9999999999.abcdef"],
        ["non-numeric expiry", "admin.notanumber.abcdef"],
    ])("%s", async (_label, token) => {
        await expect(verifyToken(token, SECRET)).resolves.toBeNull();
    });
});
