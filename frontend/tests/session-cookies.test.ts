/**
 * Intent: a password login (the shared admin, or Mode 2) is owned by the browser,
 * never by the personal login that came before it in the same browser.
 *
 * The bug this guards: LDAP login wrote the person's id into session_id, and the
 * admin login after it reused session_id as-is, so the admin read and wrote that
 * person's conversations. Now the admin's id is browser_id, which an LDAP id is
 * never adopted into, and every login clears session_id.
 */

import { describe, expect, it } from "vitest";

import { browserIdFor, loginCookies } from "@/lib/session-cookies";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const BROWSER = "6f1c2b9e-1d3a-4c5e-9f7a-2b3c4d5e6f70";
const LEGACY = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d";

describe("browserIdFor", () => {
    it("keeps this browser's id", () => {
        expect(browserIdFor({ browser_id: BROWSER, session_id: "rleimer" })).toBe(BROWSER);
    });

    it("does not adopt an LDAP id left in session_id by a personal login", () => {
        const id = browserIdFor({ session_id: "rleimer" });

        expect(id).not.toBe("rleimer");
        expect(id).toMatch(UUID);
    });

    it("adopts a random session_id from before browser_id, keeping that browser's history", () => {
        expect(browserIdFor({ session_id: LEGACY })).toBe(LEGACY);
    });

    it("prefers browser_id over a legacy session_id", () => {
        expect(browserIdFor({ browser_id: BROWSER, session_id: LEGACY })).toBe(BROWSER);
    });

    it("ignores a browser_id that is not one this app issued", () => {
        expect(browserIdFor({ browser_id: "rleimer" })).toMatch(UUID);
    });

    it("gives a fresh browser a new random id each time", () => {
        expect(browserIdFor({})).not.toBe(browserIdFor({}));
    });
});

describe("loginCookies", () => {
    const base = { authToken: "tok", ttl: 3600, browserId: BROWSER, secure: [] as string[] };

    it("sets the token and browser_id, and clears session_id", () => {
        const cookies = loginCookies({ ...base, displayName: "Roger" });

        expect(cookies.some((c) => c.startsWith("rag_auth=tok;"))).toBe(true);
        expect(cookies.some((c) => c.startsWith(`browser_id=${BROWSER};`))).toBe(true);
        expect(cookies).toContain("session_id=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax");
        expect(cookies.some((c) => c.startsWith("lancy_display_name=Roger;"))).toBe(true);
    });

    it("clears a personal display name on a password login", () => {
        const cookies = loginCookies({ ...base, displayName: null });

        expect(cookies).toContain("lancy_display_name=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax");
    });
});
