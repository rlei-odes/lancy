/**
 * Intent: passwords verify against their own hash and nothing else.
 *
 * `verifyPassword` is the single gate in front of both the app password and the
 * admin escape — middleware calls it for every Bearer token. It accepts either a
 * `pbkdf2$…` hash or a plaintext secret, and the plaintext branch exists so a
 * bare APP_PASSWORD still works. Both branches must reject on mismatch, and
 * neither may throw on malformed input: a throw inside middleware is a 500 on
 * every route, not a denied login.
 */

import { describe, expect, it, afterEach } from "vitest";

import {
    getAdminPasswordSecretFromEnv,
    getAppPasswordSecret,
    hashPassword,
    isHashString,
    verifyPassword,
} from "@/lib/password-hash";

// Keep the derivation cheap; the iteration count is not what these tests assert.
const FAST = 1_000;

describe("hashPassword", () => {
    it("produces the documented pbkdf2$iterations$salt$hash format", async () => {
        const stored = await hashPassword("correct horse", FAST);
        const parts = stored.split("$");

        expect(parts).toHaveLength(4);
        expect(parts[0]).toBe("pbkdf2");
        expect(Number(parts[1])).toBe(FAST);
        expect(isHashString(stored)).toBe(true);
    });

    it("salts, so the same password never hashes to the same string twice", async () => {
        const a = await hashPassword("same-password", FAST);
        const b = await hashPassword("same-password", FAST);

        expect(a).not.toBe(b);
        expect(await verifyPassword("same-password", a)).toBe(true);
        expect(await verifyPassword("same-password", b)).toBe(true);
    });

    it("never embeds the plaintext in the stored hash", async () => {
        const secret = "super-secret-password";
        expect(await hashPassword(secret, FAST)).not.toContain(secret);
    });
});

describe("verifyPassword — hashed branch", () => {
    it("accepts the right password and rejects a wrong one", async () => {
        const stored = await hashPassword("right", FAST);

        expect(await verifyPassword("right", stored)).toBe(true);
        expect(await verifyPassword("wrong", stored)).toBe(false);
    });

    it("rejects a near miss rather than matching on a prefix", async () => {
        const stored = await hashPassword("password123", FAST);

        for (const attempt of ["password12", "password1234", "Password123", "", " password123"]) {
            expect(await verifyPassword(attempt, stored)).toBe(false);
        }
    });

    it.each([
        ["empty submission", "", "pbkdf2$1000$c2FsdA$aGFzaA"],
        ["empty stored", "anything", ""],
        ["too few fields", "anything", "pbkdf2$1000$onlythree"],
        ["unknown algorithm", "anything", "scrypt$1000$c2FsdA$aGFzaA"],
        ["non-numeric iterations", "anything", "pbkdf2$abc$c2FsdA$aGFzaA"],
        ["zero iterations", "anything", "pbkdf2$0$c2FsdA$aGFzaA"],
        ["negative iterations", "anything", "pbkdf2$-1$c2FsdA$aGFzaA"],
    ])("returns false without throwing on %s", async (_label, submitted, stored) => {
        await expect(verifyPassword(submitted, stored)).resolves.toBe(false);
    });
});

describe("verifyPassword — plaintext branch", () => {
    it("compares a bare APP_PASSWORD exactly", async () => {
        expect(await verifyPassword("letmein", "letmein")).toBe(true);
        expect(await verifyPassword("letmein!", "letmein")).toBe(false);
        expect(await verifyPassword("letmei", "letmein")).toBe(false);
    });

    it("is case- and whitespace-sensitive", async () => {
        expect(await verifyPassword("LetMeIn", "letmein")).toBe(false);
        expect(await verifyPassword("letmein ", "letmein")).toBe(false);
    });

    it("does not treat a plaintext secret as a hash to be parsed", async () => {
        // A plaintext password containing $ must still compare literally.
        expect(await verifyPassword("a$b$c$d", "a$b$c$d")).toBe(true);
        expect(await verifyPassword("a$b$c$e", "a$b$c$d")).toBe(false);
    });
});

describe("env secret resolution", () => {
    const saved = { ...process.env };

    afterEach(() => {
        process.env = { ...saved };
    });

    it("prefers the _HASH variable over the plaintext one", () => {
        process.env.APP_PASSWORD_HASH = "pbkdf2$1000$c2FsdA$aGFzaA";
        process.env.APP_PASSWORD = "plaintext";
        expect(getAppPasswordSecret()).toBe("pbkdf2$1000$c2FsdA$aGFzaA");

        process.env.ADMIN_PASSWORD_HASH = "pbkdf2$1000$c2FsdA$YWRtaW4";
        process.env.ADMIN_PASSWORD = "admin-plain";
        expect(getAdminPasswordSecretFromEnv()).toBe("pbkdf2$1000$c2FsdA$YWRtaW4");
    });

    it("falls back to the plaintext variable when no hash is set", () => {
        delete process.env.APP_PASSWORD_HASH;
        process.env.APP_PASSWORD = "plaintext";
        expect(getAppPasswordSecret()).toBe("plaintext");
    });

    it("returns null when neither is set — the signal for open access (Mode 1)", () => {
        delete process.env.APP_PASSWORD_HASH;
        delete process.env.APP_PASSWORD;
        expect(getAppPasswordSecret()).toBeNull();

        delete process.env.ADMIN_PASSWORD_HASH;
        delete process.env.ADMIN_PASSWORD;
        expect(getAdminPasswordSecretFromEnv()).toBeNull();
    });

    it("treats an empty string as unset rather than as an empty password", () => {
        process.env.APP_PASSWORD_HASH = "";
        process.env.APP_PASSWORD = "";
        expect(getAppPasswordSecret()).toBeNull();
    });
});
