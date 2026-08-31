/**
 * Intent: the audit line identifies the caller, and logging never breaks auth.
 *
 * `clientIp` is what auth.log records for a login attempt. Behind a reverse proxy
 * the socket address is the proxy, so X-Forwarded-For is trusted — which means a
 * direct client can set it. That is a deliberate trade (an audit hint, not an
 * access decision) and the parsing has to stay predictable either way.
 *
 * `logAuth` sits inside the login path, so a full disk or an unwritable logs/
 * directory must not throw: an audit line is never worth failing a login over.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { NextApiRequest } from "next";

import { clientIp } from "@/lib/log-auth";

function req(headers: Record<string, string | string[]>, remoteAddress?: string) {
    return { headers, socket: remoteAddress ? { remoteAddress } : {} } as unknown as NextApiRequest;
}

describe("clientIp", () => {
    it("takes the first hop from X-Forwarded-For", () => {
        expect(clientIp(req({ "x-forwarded-for": "203.0.113.7, 10.0.0.1, 10.0.0.2" }))).toBe(
            "203.0.113.7",
        );
    });

    it("trims whitespace around the address", () => {
        expect(clientIp(req({ "x-forwarded-for": "  203.0.113.7  , 10.0.0.1" }))).toBe("203.0.113.7");
    });

    it("handles a single-hop header", () => {
        expect(clientIp(req({ "x-forwarded-for": "203.0.113.7" }))).toBe("203.0.113.7");
    });

    it("falls back to the socket address when the header is absent", () => {
        expect(clientIp(req({}, "198.51.100.4"))).toBe("198.51.100.4");
    });

    it("falls back when the header arrives as an array", () => {
        // Node gives an array for a repeated header; only the string form is parsed.
        expect(clientIp(req({ "x-forwarded-for": ["203.0.113.7"] }, "198.51.100.4"))).toBe(
            "198.51.100.4",
        );
    });

    it("returns the placeholder when neither is available", () => {
        expect(clientIp(req({}))).toBe("-");
    });

    it("never returns undefined, so the log line always has a field", () => {
        for (const r of [req({}), req({}, "1.2.3.4"), req({ "x-forwarded-for": "" })]) {
            expect(typeof clientIp(r)).toBe("string");
        }
    });
});

describe("logAuth", () => {
    beforeEach(() => {
        vi.resetModules();
    });

    afterEach(() => {
        vi.restoreAllMocks();
        vi.doUnmock("fs");
    });

    /** Load log-auth.ts with `fs` replaced, capturing what it would append. */
    async function withFakeFs(overrides: Record<string, unknown> = {}) {
        const appended: string[] = [];
        const fake = {
            statSync: () => ({ size: 0 }),
            appendFileSync: (_p: string, line: string) => appended.push(line),
            existsSync: () => false,
            renameSync: () => {},
            rmSync: () => {},
            ...overrides,
        };
        vi.doMock("fs", () => ({ default: fake, ...fake }));

        const { logAuth } = await import("@/lib/log-auth");
        return { logAuth, appended };
    }

    it("writes one timestamped line naming the event", async () => {
        const { logAuth, appended } = await withFakeFs();

        logAuth("login_success", { user: "alice", ip: "203.0.113.7" });

        expect(appended).toHaveLength(1);
        expect(appended[0]).toMatch(
            /^\d{4}-\d{2}-\d{2}T[\d:.]+Z login_success user="alice" ip="203\.0\.113\.7"\n$/,
        );
    });

    it("omits empty and null fields rather than logging them as blanks", async () => {
        const { logAuth, appended } = await withFakeFs();

        logAuth("login_failure", { user: "bob", reason: null, detail: undefined, note: "" });

        expect(appended[0]).toContain('user="bob"');
        expect(appended[0]).not.toContain("reason");
        expect(appended[0]).not.toContain("detail");
        expect(appended[0]).not.toContain("note");
    });

    it("logs an event with no fields at all", async () => {
        const { logAuth, appended } = await withFakeFs();

        logAuth("logout");

        expect(appended[0]).toMatch(/ logout\n$/);
    });

    it("does not throw when the log file cannot be written", async () => {
        const { logAuth } = await withFakeFs({
            appendFileSync: () => {
                throw new Error("ENOSPC: no space left on device");
            },
        });

        expect(() => logAuth("login_success", { user: "alice" })).not.toThrow();
    });

    it("does not throw when rotation fails mid-cascade", async () => {
        const { logAuth } = await withFakeFs({
            statSync: () => ({ size: 999_999_999 }), // over MAX_BYTES, forces rotation
            existsSync: () => true,
            renameSync: () => {
                throw new Error("EACCES");
            },
        });

        expect(() => logAuth("login_success", { user: "alice" })).not.toThrow();
    });

    it("rotates once the file outgrows the limit", async () => {
        const renames: [string, string][] = [];
        const { logAuth } = await withFakeFs({
            statSync: () => ({ size: 999_999_999 }),
            existsSync: () => true,
            renameSync: (from: string, to: string) => renames.push([from, to]),
        });

        logAuth("login_success", { user: "alice" });

        // The live file becomes .1, and the cascade shifts the older ones outward.
        expect(renames.at(-1)?.[1]).toMatch(/auth\.log\.1$/);
        expect(renames.length).toBeGreaterThan(1);
    });

    it("does not rotate while the file is under the limit", async () => {
        const renames: string[] = [];
        const { logAuth } = await withFakeFs({
            statSync: () => ({ size: 10 }),
            renameSync: (from: string) => renames.push(from),
        });

        logAuth("login_success", { user: "alice" });

        expect(renames).toEqual([]);
    });

    it("still appends when the log file does not exist yet", async () => {
        const { logAuth, appended } = await withFakeFs({
            statSync: () => {
                throw new Error("ENOENT");
            },
        });

        logAuth("login_success", { user: "alice" });

        expect(appended).toHaveLength(1);
    });
});
