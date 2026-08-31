/**
 * Intent: the middleware is the only thing standing between an anonymous request
 * and the backend, and the only thing that distinguishes `user` from `admin`.
 *
 * Every API call reaches the backend through a Next.js rewrite, so any path the
 * middleware lets through is proxied unauthenticated. Two boundaries matter:
 *
 *   - authenticated vs not — an API path answers 401, a page redirects to /login
 *   - user vs admin — the mutating KB, reindex, branding and /api/admin routes
 *     must answer 403 for a `user` role. LDAP logins are always `user`, so this
 *     is the real permission check for every non-admin session.
 *
 * The module resolves its secrets at import time, so each scenario re-imports it
 * with the environment already set.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

import { signToken } from "@/lib/auth";

const SECRET = "test-app-password";

type Middleware = (req: NextRequest) => Promise<Response>;

/** Import middleware.ts fresh with the given environment applied. */
async function loadMiddleware(env: Record<string, string | undefined>): Promise<Middleware> {
    vi.resetModules();
    for (const [k, v] of Object.entries(env)) {
        if (v === undefined) delete process.env[k];
        else process.env[k] = v;
    }
    return (await import("@/middleware")).middleware as Middleware;
}

function request(pathname: string, init: { method?: string; cookie?: string; auth?: string } = {}) {
    const headers = new Headers();
    if (init.cookie) headers.set("cookie", init.cookie);
    if (init.auth) headers.set("authorization", init.auth);
    return new NextRequest(`http://localhost:3000${pathname}`, {
        method: init.method ?? "GET",
        headers,
    });
}

async function cookieFor(role: "admin" | "user") {
    return `rag_auth=${await signToken(role, SECRET)}`;
}

const PROTECTED_ENV = {
    APP_PASSWORD: SECRET,
    APP_PASSWORD_HASH: undefined,
    ADMIN_PASSWORD: undefined,
    ADMIN_PASSWORD_HASH: undefined,
    SESSION_SECRET: undefined, // falls back to APP_SECRET for signing
};

beforeEach(() => {
    vi.resetModules();
});

// ─── unauthenticated ──────────────────────────────────────────────────────────

describe("with no session", () => {
    it.each([
        "/api/v1/rag/config",
        "/api/v1/kb",
        "/v1/chat/completions",
        "/files/report.pdf",
    ])("answers 401 for the API path %s", async (path) => {
        const middleware = await loadMiddleware(PROTECTED_ENV);
        const res = await middleware(request(path));

        expect(res.status).toBe(401);
        expect(await res.json()).toEqual({ error: "Unauthorized" });
    });

    it("redirects a page request to /login carrying the original path", async () => {
        const middleware = await loadMiddleware(PROTECTED_ENV);
        const res = await middleware(request("/explorer"));

        expect(res.status).toBe(307);
        const location = new URL(res.headers.get("location")!);
        expect(location.pathname).toBe("/login");
        expect(location.searchParams.get("redirect")).toBe("/explorer");
    });

    it.each([
        "/login",
        "/auth/callback",
        "/api/auth/login",
        "/api/v1/files/public.pdf",
        "/api/v1/branding",
        "/uploads/logo.png",
        "/_next/static/chunk.js",
        "/favicon.ico",
    ])("lets the public path %s through", async (path) => {
        const middleware = await loadMiddleware(PROTECTED_ENV);
        const res = await middleware(request(path));

        expect(res.status).toBe(200);
        expect(res.headers.get("location")).toBeNull();
    });

    it("answers a CORS preflight without requiring a session", async () => {
        const middleware = await loadMiddleware(PROTECTED_ENV);
        const res = await middleware(request("/api/v1/rag/config", { method: "OPTIONS" }));

        expect(res.status).toBe(204);
        expect(res.headers.get("Access-Control-Allow-Methods")).toContain("POST");
    });

    it("rejects a session cookie signed with the wrong secret", async () => {
        const middleware = await loadMiddleware(PROTECTED_ENV);
        const forged = `rag_auth=${await signToken("admin", "not-the-secret")}`;
        const res = await middleware(request("/api/v1/rag/config", { cookie: forged }));

        expect(res.status).toBe(401);
    });
});

// ─── the admin boundary ───────────────────────────────────────────────────────

const ADMIN_ONLY: [string, string][] = [
    ["POST", "/api/v1/kb"],
    ["PUT", "/api/v1/kb/default"],
    ["DELETE", "/api/v1/kb/default"],
    ["POST", "/api/v1/kb/default/documents"],
    ["POST", "/api/v1/kb/default/activate"],
    ["POST", "/api/v1/kb/default/deactivate"],
    ["POST", "/api/v1/rag/reindex"],
    ["POST", "/api/v1/rag/reindex-cancel"],
    ["GET", "/api/admin/users"],
    ["POST", "/api/admin/anything"],
    // NOTE: PUT /api/v1/branding and DELETE /api/v1/branding/avatar are declared
    // admin-only in isAdminOnlyRequest but are not enforced. See the "known gap"
    // block at the bottom of this file.
];

describe("role=user", () => {
    it.each(ADMIN_ONLY)("is refused %s %s with 403", async (method, path) => {
        const middleware = await loadMiddleware(PROTECTED_ENV);
        const res = await middleware(request(path, { method, cookie: await cookieFor("user") }));

        expect(res.status).toBe(403);
        expect(await res.json()).toEqual({ error: "Forbidden" });
    });

    it.each([
        ["GET", "/api/v1/kb"],
        ["GET", "/api/v1/kb/pool"],
        ["POST", "/api/v1/rag/query"],
        ["POST", "/api/v1/rag/analyze-document"],
        ["GET", "/api/v1/branding"],
    ])("is allowed %s %s", async (method, path) => {
        const middleware = await loadMiddleware(PROTECTED_ENV);
        const res = await middleware(request(path, { method, cookie: await cookieFor("user") }));

        expect(res.status).toBe(200);
    });

    it("is redirected away from /docs to /redoc", async () => {
        const middleware = await loadMiddleware(PROTECTED_ENV);
        const res = await middleware(request("/docs", { cookie: await cookieFor("user") }));

        expect(res.status).toBe(307);
        expect(new URL(res.headers.get("location")!).pathname).toBe("/redoc");
    });
});

describe("role=admin", () => {
    it.each(ADMIN_ONLY)("is allowed %s %s", async (method, path) => {
        const middleware = await loadMiddleware(PROTECTED_ENV);
        const res = await middleware(request(path, { method, cookie: await cookieFor("admin") }));

        expect(res.status).toBe(200);
    });

    it("reaches /docs directly", async () => {
        const middleware = await loadMiddleware(PROTECTED_ENV);
        const res = await middleware(request("/docs", { cookie: await cookieFor("admin") }));

        expect(res.status).toBe(200);
    });

    it("is granted by a Bearer token matching the app password", async () => {
        const middleware = await loadMiddleware(PROTECTED_ENV);
        const res = await middleware(
            request("/api/v1/kb", { method: "POST", auth: `Bearer ${SECRET}` }),
        );

        expect(res.status).toBe(200);
    });

    it("is not granted by a wrong Bearer token", async () => {
        const middleware = await loadMiddleware(PROTECTED_ENV);
        const res = await middleware(
            request("/api/v1/kb", { method: "POST", auth: "Bearer wrong-token" }),
        );

        expect(res.status).toBe(401);
    });
});

// ─── the role is passed downstream, not trusted from the client ───────────────

describe("x-user-role", () => {
    it("is set from the verified cookie", async () => {
        const middleware = await loadMiddleware(PROTECTED_ENV);
        const res = await middleware(request("/api/v1/kb", { cookie: await cookieFor("user") }));

        expect(res.headers.get("x-middleware-request-x-user-role")).toBe("user");
    });

    it("cannot be spoofed by the client sending its own header", async () => {
        const middleware = await loadMiddleware(PROTECTED_ENV);
        const req = request("/api/v1/kb", { method: "POST", cookie: await cookieFor("user") });
        req.headers.set("x-user-role", "admin");

        const res = await middleware(req);
        expect(res.status).toBe(403);
    });
});

// ─── Mode 1: no APP_PASSWORD configured ───────────────────────────────────────

describe("open access (no APP_PASSWORD set)", () => {
    it("treats everyone as admin", async () => {
        const middleware = await loadMiddleware({ ...PROTECTED_ENV, APP_PASSWORD: undefined });

        for (const [method, path] of ADMIN_ONLY) {
            const res = await middleware(request(path, { method }));
            expect(res.status, `${method} ${path}`).toBe(200);
        }
    });

    it("does not redirect page requests to /login", async () => {
        const middleware = await loadMiddleware({ ...PROTECTED_ENV, APP_PASSWORD: undefined });
        const res = await middleware(request("/explorer"));

        expect(res.status).toBe(200);
    });
});

// ─── known gap ────────────────────────────────────────────────────────────────
//
// `isAdminOnlyRequest` declares these two admin-only:
//
//     if (method === "PUT" && pathname === "/api/v1/branding") return true;
//     if (method === "DELETE" && pathname === "/api/v1/branding/avatar") return true;
//
// but PUBLIC_PREFIXES contains "/api/v1/branding" and is checked first, so the
// middleware returns NextResponse.next() before the role is ever resolved. Both
// rules are unreachable, and both routes are reachable with no credentials at all.
//
// The prefix is presumably there so the login page can read the logo and colours
// before authenticating — that only requires GET.
//
// These use `it.fails`, so they pass while the gap exists and start failing the
// moment it is closed. When that happens, delete this block and move the two
// entries back into ADMIN_ONLY above.

describe("known gap: branding writes are not gated", () => {
    it.fails("PUT /api/v1/branding should refuse an anonymous request", async () => {
        const middleware = await loadMiddleware(PROTECTED_ENV);
        const res = await middleware(request("/api/v1/branding", { method: "PUT" }));

        expect(res.status).toBe(401);
    });

    it.fails("DELETE /api/v1/branding/avatar should refuse an anonymous request", async () => {
        const middleware = await loadMiddleware(PROTECTED_ENV);
        const res = await middleware(request("/api/v1/branding/avatar", { method: "DELETE" }));

        expect(res.status).toBe(401);
    });

    it.fails("PUT /api/v1/branding should refuse role=user", async () => {
        const middleware = await loadMiddleware(PROTECTED_ENV);
        const res = await middleware(
            request("/api/v1/branding", { method: "PUT", cookie: await cookieFor("user") }),
        );

        expect(res.status).toBe(403);
    });

    it("GET /api/v1/branding is public by design — the login page needs it", async () => {
        const middleware = await loadMiddleware(PROTECTED_ENV);
        const res = await middleware(request("/api/v1/branding"));

        expect(res.status).toBe(200);
    });
});
