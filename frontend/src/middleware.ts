import { NextRequest, NextResponse } from "next/server";

const API_KEY = process.env.API_KEY || "";

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
];

function withCors(response: NextResponse): NextResponse {
    Object.entries(CORS_HEADERS).forEach(([k, v]) => response.headers.set(k, v));
    return response;
}

export function middleware(request: NextRequest) {
    // CORS preflight — respond immediately with CORS headers
    if (request.method === "OPTIONS") {
        return new NextResponse(null, { status: 204, headers: CORS_HEADERS });
    }

    if (!API_KEY) return NextResponse.next();

    const { pathname } = request.nextUrl;

    if (PUBLIC_PREFIXES.some((p) => pathname.startsWith(p))) {
        return NextResponse.next();
    }

    // Bearer token — for API clients (Open WebUI, curl, etc.)
    const authHeader = request.headers.get("authorization") ?? "";
    if (authHeader === `Bearer ${API_KEY}`) {
        return withCors(NextResponse.next());
    }

    // Session cookie — for browser users after login
    const cookie = request.cookies.get("rag_auth");
    if (cookie?.value === API_KEY) {
        return NextResponse.next();
    }

    // API / proxy routes → 401 JSON (not browser navigation)
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

    // Browser → redirect to login
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("redirect", pathname);
    return NextResponse.redirect(loginUrl);
}

export const config = {
    matcher: ["/((?!_next/static|_next/image).*)"],
};
