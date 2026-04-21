import { FormEvent, useState } from "react";
import { useRouter } from "next/router";
import Head from "next/head";
import Image from "next/image";
import { config } from "@/config";

export default function Login() {
    const router = useRouter();
    const [password, setPassword] = useState("");
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(false);

    async function handleSubmit(e: FormEvent) {
        e.preventDefault();
        setError("");
        setLoading(true);
        try {
            const res = await fetch("/api/auth/login", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ password }),
            });
            if (res.ok) {
                const redirect = (router.query.redirect as string) || "/";
                router.push(redirect);
            } else {
                const data = await res.json().catch(() => ({}));
                setError(data.error ?? "Anmeldung fehlgeschlagen");
            }
        } catch {
            setError("Verbindungsfehler");
        } finally {
            setLoading(false);
        }
    }

    return (
        <>
            <Head>
                <title>Anmelden — {config.app.name}</title>
                <link rel="icon" href={config.app.favicon} />
            </Head>
            <div className="min-h-screen flex items-center justify-center bg-gray-950">
                <div className="w-full max-w-sm space-y-6 px-8 py-10 bg-gray-900 rounded-2xl shadow-xl border border-gray-800">
                    <div className="flex flex-col items-center gap-3">
                        <Image
                            src={config.app.logo}
                            alt="Logo"
                            width={48}
                            height={48}
                            className="rounded"
                        />
                        <h1 className="text-xl font-semibold text-white">{config.app.name}</h1>
                        <p className="text-sm text-gray-400">Bitte melden Sie sich an</p>
                    </div>

                    <form onSubmit={handleSubmit} className="space-y-4">
                        <div>
                            <label className="block text-sm text-gray-400 mb-1">Passwort</label>
                            <input
                                type="password"
                                autoFocus
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                                className="w-full rounded-lg bg-gray-800 border border-gray-700 text-white px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                                placeholder="••••••••"
                                required
                            />
                        </div>

                        {error && (
                            <p className="text-sm text-red-400">{error}</p>
                        )}

                        <button
                            type="submit"
                            disabled={loading}
                            className="w-full rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-medium py-2.5 text-sm transition-colors"
                        >
                            {loading ? "Wird geprüft…" : "Anmelden"}
                        </button>
                    </form>
                </div>
            </div>
        </>
    );
}
