import { NextRequest, NextResponse } from "next/server";
import { verifyToken } from "@/lib/auth";

const APP_PASSWORD = process.env.APP_PASSWORD || "";

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
    "/api/auth/",
    "/api/v1/files/",
    "/api/v1/branding",
    "/uploads/",
];

function isAdminOnlyRequest(method: string, pathname: string): boolean {
    if (method === "POST" && pathname === "/api/v1/kb") return true;
    if (method === "PUT" && pathname.startsWith("/api/v1/kb/")) return true;
    if (method === "DELETE" && pathname.startsWith("/api/v1/kb/")) return true;
    if (method === "POST" && pathname.startsWith("/api/v1/kb/") && pathname.endsWith("/documents")) return true;
    if (method === "POST" && pathname === "/api/v1/rag/reindex") return true;
    if (method === "POST" && pathname === "/api/v1/rag/reindex/cancel") return true;
    if (pathname.startsWith("/api/admin/")) return true;
    if (method === "PUT" && pathname === "/api/v1/branding") return true;
    if (method === "DELETE" && pathname === "/api/v1/branding/avatar") return true;
    return false;
}

function withCors(response: NextResponse): NextResponse {
    Object.entries(CORS_HEADERS).forEach(([k, v]) => response.headers.set(k, v));
    return response;
}

// Returns the role for this request, or null if unauthenticated.
// Mode 1 (no APP_PASSWORD): open access, everyone is admin.
// Mode 2: role is embedded in the signed cookie.
async function getRole(request: NextRequest): Promise<"admin" | "user" | null> {
    if (!APP_PASSWORD) return "admin";

    // Bearer token for API clients (Open WebUI, curl, etc.) — always admin
    const authHeader = request.headers.get("authorization") ?? "";
    if (authHeader === `Bearer ${APP_PASSWORD}`) return "admin";

    const cookie = request.cookies.get("rag_auth");
    if (!cookie?.value) return null;

    return verifyToken(cookie.value, APP_PASSWORD);
}

export async function middleware(request: NextRequest) {
    if (request.method === "OPTIONS") {
        return new NextResponse(null, { status: 204, headers: CORS_HEADERS });
    }

    const { pathname } = request.nextUrl;

    if (PUBLIC_PREFIXES.some((p) => pathname.startsWith(p))) {
        return NextResponse.next();
    }

    const role = await getRole(request);

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
    if (role) {
        const requestHeaders = new Headers(request.headers);
        requestHeaders.set("x-user-role", role);
        const sessionId = request.cookies.get("session_id")?.value;
        if (sessionId) requestHeaders.set("x-session-id", sessionId);
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