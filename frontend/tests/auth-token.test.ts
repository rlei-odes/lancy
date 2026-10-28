/**
 * Intent: a session cookie proves a role and a user id, and cannot be forged,
 * self-promoted or moved to another user.
 *
 * `role.exp.subject.hmac` is the whole session model. Middleware trusts whatever
 * `verifySession` returns: it sets `x-user-role` from the role, and `x-session-id`,
 * which conversations and piles are owned by, from the subject. So every rejection
 * here is a privilege boundary: a token signed with another secret, a `user` token
 * edited to say `admin`, one edited to name someone else, or an expired one must
 * all come back `null`.
 */

import { describe, expect, it, vi, afterEach } from "vitest";

import { signToken, verifySession, verifyToken } from "@/lib/auth";

const SECRET = "test-signing-secret";
const SUB = "alice";

afterEach(() => {
    vi.useRealTimers();
});

describe("round trip", () => {
    it.each(["admin", "user"] as const)("preserves the %s role", async (role) => {
        expect(await verifyToken(await signToken(role, SUB, SECRET), SECRET)).toBe(role);
    });

    it.each([
        ["an LDAP uid", "rleimer"],
        ["a UPN with dots", "roger.leimer@microdul.local"],
        ["an LDAP DN", "uid=r.l,ou=People,dc=example,dc=org"],
        ["a browser id", "6f1c2b9e-1d3a-4c5e-9f7a-2b3c4d5e6f70"],
        ["non-ASCII", "jürg.müller"],
    ])("preserves the subject: %s", async (_label, sub) => {
        expect(await verifySession(await signToken("user", sub, SECRET), SECRET)).toEqual({ role: "user", sub });
    });

    it("puts the role and expiry in the clear, then the subject, with the signature last", async () => {
        const token = await signToken("user", SUB, SECRET, 3600);
        const [role, exp, sub, sig] = token.split(".");

        expect(role).toBe("user");
        expect(Number(exp)).toBeGreaterThan(Math.floor(Date.now() / 1000));
        expect(Buffer.from(sub, "hex").toString()).toBe(SUB);
        expect(sig).toMatch(/^[0-9a-f]{64}$/);
    });

    it("refuses to sign a token without a subject", async () => {
        await expect(signToken("admin", "", SECRET)).rejects.toThrow();
    });
});

describe("forgery is rejected", () => {
    it("rejects a token signed with a different secret", async () => {
        const token = await signToken("admin", SUB, "the-real-secret");
        expect(await verifyToken(token, "an-attackers-guess")).toBeNull();
    });

    it("rejects a user token edited to claim admin", async () => {
        const token = await signToken("user", SUB, SECRET);
        const forged = token.replace(/^user\./, "admin.");

        expect(forged.startsWith("admin.")).toBe(true);
        expect(await verifyToken(forged, SECRET)).toBeNull();
    });

    it("rejects a token edited to name another user", async () => {
        const token = await signToken("user", "mallory", SECRET);
        const [role, exp, , sig] = token.split(".");
        const forged = `${role}.${exp}.${Buffer.from("alice").toString("hex")}.${sig}`;

        expect(await verifySession(forged, SECRET)).toBeNull();
    });

    it("rejects a token from before the subject was added, so that session logs in again", async () => {
        // Same secret, same HMAC, old two-field payload: only the missing subject is wrong.
        const exp = Math.floor(Date.now() / 1000) + 3600;
        const payload = `admin.${exp}`;
        const key = await crypto.subtle.importKey(
            "raw",
            new TextEncoder().encode(SECRET),
            { name: "HMAC", hash: "SHA-256" },
            false,
            ["sign"],
        );
        const raw = await crypto.subtle.sign({ name: "HMAC", hash: "SHA-256" }, key, new TextEncoder().encode(payload));
        const hex = Array.from(new Uint8Array(raw), (b) => b.toString(16).padStart(2, "0")).join("");

        expect(await verifySession(`${payload}.${hex}`, SECRET)).toBeNull();
    });

    it("rejects a token whose expiry was pushed out", async () => {
        const token = await signToken("admin", SUB, SECRET, 60);
        const [role, exp, sub, sig] = token.split(".");
        const extended = `${role}.${Number(exp) + 86_400}.${sub}.${sig}`;

        expect(await verifyToken(extended, SECRET)).toBeNull();
    });

    it("rejects a token with a flipped signature byte", async () => {
        const token = await signToken("admin", SUB, SECRET);
        const lastDot = token.lastIndexOf(".");
        const sig = token.slice(lastDot + 1);
        const flipped = (sig[0] === "a" ? "b" : "a") + sig.slice(1);

        expect(await verifyToken(`${token.slice(0, lastDot)}.${flipped}`, SECRET)).toBeNull();
    });

    it("rejects an unknown role even when the signature is valid for it", async () => {
        // Sign a "superadmin" payload the honest way, then check verify still refuses it.
        const exp = Math.floor(Date.now() / 1000) + 3600;
        const payload = `superadmin.${exp}.${Buffer.from(SUB).toString("hex")}`;
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

        const token = await signToken("admin", SUB, SECRET, 3600);
        expect(await verifyToken(token, SECRET)).toBe("admin");

        vi.setSystemTime(new Date("2026-01-01T00:59:00Z"));
        expect(await verifyToken(token, SECRET)).toBe("admin");

        vi.setSystemTime(new Date("2026-01-01T01:00:01Z"));
        expect(await verifyToken(token, SECRET)).toBeNull();
    });

    it("defaults to a 30-day window", async () => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));

        const exp = Number((await signToken("user", SUB, SECRET)).split(".")[1]);
        expect(exp - Math.floor(Date.now() / 1000)).toBe(60 * 60 * 24 * 30);
    });
});

describe("malformed input returns null, never throws", () => {
    it.each([
        ["empty string", ""],
        ["no separator", "notatoken"],
        ["missing signature", "admin.9999999999"],
        ["too many payload fields", "admin.9999999999.616c696365.extra.abcdef"],
        ["non-hex subject", "admin.9999999999.alice.abcdef"],
        ["empty subject", "admin.9999999999..abcdef"],
        ["non-hex signature", "admin.9999999999.zzzz"],
        ["odd-length signature", "admin.9999999999.abc"],
        ["empty role", ".9999999999.abcdef"],
        ["non-numeric expiry", "admin.notanumber.abcdef"],
    ])("%s", async (_label, token) => {
        await expect(verifyToken(token, SECRET)).resolves.toBeNull();
    });
});
