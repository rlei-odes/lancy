import { NextRequest, NextResponse } from "next/server";
import { verifySession } from "@/lib/auth";
import { getAppPasswordSecret, getAdminPasswordSecretFromEnv, verifyPassword } from "@/lib/password-hash";

const APP_SECRET = getAppPasswordSecret();
const ADMIN_SECRET = getAdminPasswordSecretFromEnv();
const SIGNING_KEY = process.env.SESSION_SECRET || APP_SECRET || "";

const CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
};

// Paths that are always public (no auth required)
const PUBLIC_PREFIXES = [
    "/_next/",
    "/favicon",
    "/assets/",
    "/login",
    "/auth/",        // OIDC callback page (/auth/callback)
    "/api/auth/",
    "/api/v1/branding",
    "/uploads/",
];

function isAdminOnlyRequest(method: string, pathname: string): boolean {
    if (method === "POST" && pathname === "/api/v1/kb") return true;
    if (method === "PUT" && pathname.startsWith("/api/v1/kb/")) return true;
    if (method === "DELETE" && pathname.startsWith("/api/v1/kb/")) return true;
    if (method === "POST" && pathname.startsWith("/api/v1/kb/") && pathname.endsWith("/documents")) return true;
    if (method === "POST" && pathname.startsWith("/api/v1/kb/") && pathname.endsWith("/activate")) return true;
    if (method === "POST" && pathname.startsWith("/api/v1/kb/") && pathname.endsWith("/deactivate")) return true;
    if (method === "POST" && pathname === "/api/v1/rag/reindex") return true;
    if (method === "POST" && pathname === "/api/v1/rag/reindex-cancel") return true;
    // Settings are shared: a save rebuilds the agents everyone answers with.
    if (method === "POST" && pathname === "/api/v1/rag/config") return true;
    if (pathname.startsWith("/api/admin/")) return true;
    if (method === "PUT" && pathname === "/api/v1/branding") return true;
    if (method === "DELETE" && pathname === "/api/v1/branding/avatar") return true;
    return false;
}

function withCors(response: NextResponse): NextResponse {
    Object.entries(CORS_HEADERS).forEach(([k, v]) => response.headers.set(k, v));
    return response;
}

// Returns the role and, for a logged-in session, the user id, or null if unauthenticated.
// Mode 1 (no APP_PASSWORD): open access, everyone is admin.
// Mode 2/3: role and user id are both in the signed cookie.
// Bearer API clients and Mode 1 carry no user id; the backend then falls back to "admin".
async function getSession(request: NextRequest): Promise<{ role: "admin" | "user"; sub: string | null } | null> {
    if (!APP_SECRET) return { role: "admin", sub: null };

    // Bearer token for API clients (Open WebUI, curl, etc.) — always admin
    const authHeader = request.headers.get("authorization") ?? "";
    if (authHeader.startsWith("Bearer ")) {
        const token = authHeader.slice(7);
        if (await verifyPassword(token, APP_SECRET)) return { role: "admin", sub: null };
        if (ADMIN_SECRET && (await verifyPassword(token, ADMIN_SECRET))) return { role: "admin", sub: null };
    }

    const cookie = request.cookies.get("rag_auth");
    if (!cookie?.value) return null;

    return verifySession(cookie.value, SIGNING_KEY);
}

export async function middleware(request: NextRequest) {
    if (request.method === "OPTIONS") {
        return new NextResponse(null, { status: 204, headers: CORS_HEADERS });
    }

    const { pathname } = request.nextUrl;

    if (PUBLIC_PREFIXES.some((p) => pathname.startsWith(p))) {
        return NextResponse.next();
    }

    const session = await getSession(request);
    const role = session?.role ?? null;

    if (pathname === "/docs") {
        if (!role) {
            const loginUrl = new URL("/login", request.url);
            loginUrl.searchParams.set("redirect", pathname);
            return NextResponse.redirect(loginUrl);
        }
        if (role === "user") return NextResponse.redirect(new URL("/redoc", request.url));
    }

    if (role === "user" && isAdminOnlyRequest(request.method, pathname)) {
        return new NextResponse(JSON.stringify({ error: "Forbidden" }), {
            status: 403,
            headers: { "Content-Type": "application/json", ...CORS_HEADERS },
        });
    }
    if (session) {
        const requestHeaders = new Headers(request.headers);
        requestHeaders.set("x-user-role", session.role);
        // The backend owns conversations and piles by this header, so it comes only
        // from the signed token, never from a header or cookie the client sent.
        requestHeaders.delete("x-session-id");
        if (session.sub) requestHeaders.set("x-session-id", session.sub);
        return withCors(NextResponse.next({ request: { headers: requestHeaders } }));
    }

    if (
        pathname.startsWith("/api/") ||
        pathname.startsWith("/v1/") ||
        pathname.startsWith("/files/")
    ) {
        return new NextResponse(JSON.stringify({ error: "Unauthorized" }), {
            status: 401,
            headers: { "Content-Type": "application/json", ...CORS_HEADERS },
        });
    }

    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("redirect", pathname);
    return NextResponse.redirect(loginUrl);
}

export const config = {
    matcher: ["/((?!_next/static|_next/image).*)"],
};