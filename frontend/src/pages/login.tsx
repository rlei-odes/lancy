import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/router";
import Head from "next/head";
import Image from "next/image";
import { UserManager } from "oidc-client-ts";
import { config } from "@/config";
import type { AuthMode } from "./api/auth/mode";

type View = "loading" | "oidc" | "ldap" | "password" | "admin-escape";

export default function Login() {
    const router = useRouter();
    const [view, setView] = useState<View>("loading");
    const [username, setUsername] = useState("");
    const [password, setPassword] = useState("");
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(false);
    const [authMode, setAuthMode] = useState<AuthMode | null>(null);

    useEffect(() => {
        fetch("/api/auth/mode")
            .then((r) => r.json())
            .then((data: AuthMode) => {
                setAuthMode(data);
                if (data.mode === 3 && data.provider === "oidc") setView("oidc");
                else if (data.mode === 3 && data.provider === "ldap") setView("ldap");
                else setView("password");
            })
            .catch(() => setView("password"));
    }, []);

    const redirect = (router.query.redirect as string) || "/";

    // ── OIDC redirect ─────────────────────────────────────────────────────────
    async function handleSSOLogin() {
        setError("");
        setLoading(true);
        try {
            const configRes = await fetch("/api/auth/sso-config");
            if (!configRes.ok) throw new Error("SSO configuration unavailable");
            const { client_id, issuer_url, redirect_uri } = await configRes.json();

            const userManager = new UserManager({
                authority: issuer_url,
                client_id,
                redirect_uri,
                response_type: "code",
                scope: "openid profile email",
            });
            await userManager.signinRedirect({ state: redirect });
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : "Login service unavailable — try the admin password login");
            setLoading(false);
        }
    }

    // ── Password / LDAP submit ────────────────────────────────────────────────
    async function handleSubmit(e: FormEvent) {
        e.preventDefault();
        setError("");
        setLoading(true);
        try {
            const body =
                view === "ldap"
                    ? { username, password }
                    : { password };

            const res = await fetch("/api/auth/login", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            });

            if (res.ok) {
                router.push(redirect);
            } else {
                const data = await res.json().catch(() => ({}));
                setError(data.error ?? "Login failed");
            }
        } catch {
            setError("Connection error");
        } finally {
            setLoading(false);
        }
    }

    const isMode3 = authMode?.mode === 3;

    return (
        <>
            <Head>
                <title>Sign in — {config.app.name}</title>
                <link rel="icon" href={config.app.favicon} />
            </Head>
            <div className="min-h-screen flex items-center justify-center bg-gray-950">
                <div className="w-full max-w-sm space-y-6 px-8 py-10 bg-gray-900 rounded-2xl shadow-xl border border-gray-800">
                    {/* Logo + title */}
                    <div className="flex flex-col items-center gap-3">
                        <Image src={config.app.logo} alt="Logo" width={48} height={48} className="rounded" />
                        <h1 className="text-xl font-semibold text-white">{config.app.name}</h1>
                        <p className="text-sm text-gray-400">
                            {view === "loading" ? "Loading…" : "Sign in to continue"}
                        </p>
                    </div>

                    {/* ── OIDC view ── */}
                    {view === "oidc" && (
                        <div className="space-y-3">
                            {error && <p className="text-sm text-red-400">{error}</p>}
                            <button
                                onClick={handleSSOLogin}
                                disabled={loading}
                                className="w-full rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-medium py-2.5 text-sm transition-colors"
                            >
                                {loading ? "Redirecting…" : "Log in with SSO"}
                            </button>
                        </div>
                    )}

                    {/* ── LDAP / password form ── */}
                    {(view === "ldap" || view === "password" || view === "admin-escape") && (
                        <form onSubmit={handleSubmit} className="space-y-4">
                            {view === "ldap" && (
                                <div>
                                    <label className="block text-sm text-gray-400 mb-1">Username</label>
                                    <input
                                        type="text"
                                        autoFocus
                                        value={username}
                                        onChange={(e) => setUsername(e.target.value)}
                                        className="w-full rounded-lg bg-gray-800 border border-gray-700 text-white px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                                        placeholder="username"
                                        required
                                    />
                                </div>
                            )}
                            <div>
                                <label className="block text-sm text-gray-400 mb-1">
                                    {view === "admin-escape" ? "Admin password" : "Password"}
                                </label>
                                <input
                                    type="password"
                                    autoFocus={view !== "ldap"}
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                    className="w-full rounded-lg bg-gray-800 border border-gray-700 text-white px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                                    placeholder="••••••••"
                                    required
                                />
                            </div>

                            {error && <p className="text-sm text-red-400">{error}</p>}

                            <button
                                type="submit"
                                disabled={loading}
                                className="w-full rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-medium py-2.5 text-sm transition-colors"
                            >
                                {loading ? "Signing in…" : "Sign in"}
                            </button>
                        </form>
                    )}

                    {/* ── Escape hatch: admin password login ── */}
                    {/* Always shown in Mode 3 so admins can log in if SSO is broken. */}
                    {isMode3 && view !== "admin-escape" && (
                        <p className="text-center text-xs text-gray-600">
                            <button
                                onClick={() => { setError(""); setView("admin-escape"); }}
                                className="hover:text-gray-400 underline underline-offset-2 transition-colors"
                            >
                                Admin login
                            </button>
                        </p>
                    )}
                    {view === "admin-escape" && (
                        <p className="text-center text-xs text-gray-600">
                            <button
                                onClick={() => { setError(""); setView(authMode?.provider === "oidc" ? "oidc" : "ldap"); }}
                                className="hover:text-gray-400 underline underline-offset-2 transition-colors"
                            >
                                Back to SSO login
                            </button>
                        </p>
                    )}
                </div>
            </div>
        </>
    );
}
