import { useEffect, useState } from "react";
import { useRouter } from "next/router";
import Head from "next/head";
import { UserManager } from "oidc-client-ts";

export default function OIDCCallback() {
    const router = useRouter();
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        async function handleCallback() {
            let client_id: string, issuer_url: string, redirect_uri: string;
            try {
                const configRes = await fetch("/api/auth/sso-config");
                if (!configRes.ok) throw new Error("SSO not configured");
                ({ client_id, issuer_url, redirect_uri } = await configRes.json());
            } catch {
                setError("SSO configuration unavailable — try the admin password login");
                return;
            }

            const userManager = new UserManager({
                authority: issuer_url,
                client_id,
                redirect_uri,
                response_type: "code",
                scope: "openid profile email",
            });

            try {
                const user = await userManager.signinRedirectCallback();
                const idToken = user.id_token;
                if (!idToken) throw new Error("No ID token received from identity provider");

                const verifyRes = await fetch("/api/auth/verify-token", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ id_token: idToken }),
                });

                if (!verifyRes.ok) {
                    const data = await verifyRes.json().catch(() => ({}));
                    throw new Error(data.error ?? "Token verification failed");
                }

                router.replace("/");
            } catch (e: unknown) {
                const msg = e instanceof Error ? e.message : "Login failed";
                // Distinguish state mismatch (PKCE/security error) from auth errors.
                if (msg.toLowerCase().includes("state")) {
                    setError("Login failed (security check) — please try again");
                } else {
                    setError(msg);
                }
            }
        }

        handleCallback();
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    return (
        <>
            <Head><title>Signing in…</title></Head>
            <div className="min-h-screen flex items-center justify-center bg-gray-950">
                {error ? (
                    <div className="text-center space-y-4 px-6">
                        <p className="text-red-400 text-sm">{error}</p>
                        <a href="/login" className="text-blue-400 hover:underline text-sm block">
                            Back to login
                        </a>
                    </div>
                ) : (
                    <p className="text-gray-400 text-sm">Signing you in…</p>
                )}
            </div>
        </>
    );
}
