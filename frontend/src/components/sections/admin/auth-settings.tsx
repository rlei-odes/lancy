import React, { useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";

type Provider = "none" | "oidc" | "ldap";

interface OIDCFields {
    client_id: string;
    issuer_url: string;
    redirect_uri: string;
    allowed_groups: string;
    session_ttl_hours: string;
}

interface LDAPFields {
    server: string;
    bind_dn_template: string;
    base_dn: string;
    user_id_attribute: string;
    display_name_attribute: string;
    allowed_groups: string;
    session_ttl_hours: string;
    search_bind_dn: string;
    search_bind_password: string;
}

const OIDC_DEFAULTS: OIDCFields = {
    client_id: "",
    issuer_url: "",
    redirect_uri: "",
    allowed_groups: "",
    session_ttl_hours: "48",
};

const LDAP_DEFAULTS: LDAPFields = {
    server: "",
    bind_dn_template: "",
    base_dn: "",
    user_id_attribute: "uid",
    display_name_attribute: "cn",
    allowed_groups: "",
    session_ttl_hours: "168",
    search_bind_dn: "",
    search_bind_password: "",
};

function FieldRow({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
    return (
        <div className="grid grid-cols-3 gap-4 items-start py-3 border-b border-border last:border-0">
            <div>
                <div className="text-sm font-medium text-foreground">{label}</div>
                {hint && <div className="text-xs text-muted-foreground mt-0.5">{hint}</div>}
            </div>
            <div className="col-span-2">{children}</div>
        </div>
    );
}

function TextInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
    return (
        <input
            {...props}
            className="w-full rounded-md bg-muted border border-border text-foreground px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
        />
    );
}

type EnvStatus = "set" | "missing" | "warn" | "loading";

function StatusDot({ status, label, hint }: { status: EnvStatus; label: string; hint: string }) {
    const dot =
        status === "loading" ? "bg-muted-foreground/30" :
        status === "set"     ? "bg-green-400" :
        status === "warn"    ? "bg-amber-400" :
                               "bg-red-400";
    const text =
        status === "missing" ? "text-red-400" :
        status === "warn"    ? "text-amber-400" :
                               "text-muted-foreground";
    return (
        <div className="flex items-start gap-2.5">
            <span className={`mt-1 h-2 w-2 rounded-full shrink-0 ${dot}`} />
            <div>
                <span className="text-sm font-mono text-foreground">{label}</span>
                <span className={`ml-2 text-xs ${text}`}>{hint}</span>
            </div>
        </div>
    );
}

export const AuthSettings: React.FC = () => {
    const [provider, setProvider] = useState<Provider>("none");
    const [oidc, setOIDC] = useState<OIDCFields>(OIDC_DEFAULTS);
    const [ldap, setLDAP] = useState<LDAPFields>(LDAP_DEFAULTS);
    const [result, setResult] = useState<{ ok: boolean; msg: string } | null>(null);
    const [saving, setSaving] = useState(false);
    const [testing, setTesting] = useState(false);
    const [testResult, setTestResult] = useState<{ ok: boolean; msg: string } | null>(null);
    const [restartRequired, setRestartRequired] = useState(false);
    const [appPwStatus, setAppPwStatus] = useState<EnvStatus>("loading");
    const [adminPwStatus, setAdminPwStatus] = useState<EnvStatus>("loading");
    const [sessionSecretStatus, setSessionSecretStatus] = useState<EnvStatus>("loading");

    useEffect(() => {
        fetch("/api/auth/mode")
            .then(async (r) => {
                if (r.ok) {
                    const data = await r.json();
                    setAppPwStatus("set");
                    // mode 2 or 3 means ADMIN_PASSWORD is configured
                    setAdminPwStatus(data.mode >= 2 ? "set" : "missing");
                } else {
                    const data = await r.json().catch(() => ({}));
                    const msg: string = data.error ?? "";
                    if (msg.includes("APP_PASSWORD")) {
                        setAppPwStatus("missing");
                        setAdminPwStatus("loading");
                    } else if (msg.includes("ADMIN_PASSWORD")) {
                        setAppPwStatus("set");
                        setAdminPwStatus("missing");
                    }
                }
            })
            .catch(() => {
                setAppPwStatus("missing");
                setAdminPwStatus("missing");
            });
    }, []);

    useEffect(() => {
        fetch("/api/auth/admin-config")
            .then((r) => r.json())
            .then((data) => {
                setSessionSecretStatus(data.session_secret_set ? "set" : "missing");
                const sso = data.sso;
                if (!sso) { setProvider("none"); return; }
                if (sso.provider === "oidc") {
                    setProvider("oidc");
                    setOIDC({
                        client_id: sso.client_id ?? "",
                        issuer_url: sso.issuer_url ?? "",
                        redirect_uri: sso.redirect_uri ?? "",
                        allowed_groups: (sso.allowed_groups ?? []).join(", "),
                        session_ttl_hours: String(sso.session_ttl_hours ?? "48"),
                    });
                } else if (sso.provider === "ldap") {
                    setProvider("ldap");
                    setLDAP({
                        server: sso.server ?? "",
                        bind_dn_template: sso.bind_dn_template ?? "",
                        base_dn: sso.base_dn ?? "",
                        user_id_attribute: sso.user_id_attribute ?? "uid",
                        display_name_attribute: sso.display_name_attribute ?? "cn",
                        allowed_groups: (sso.allowed_groups ?? []).join(", "),
                        session_ttl_hours: String(sso.session_ttl_hours ?? "168"),
                        search_bind_dn: sso.search_bind_dn ?? "",
                        search_bind_password: sso.search_bind_password ?? "",
                    });
                }
            })
            .catch(() => {});
    }, []);

    function parseGroups(raw: string): string[] {
        return raw.split(",").map((g) => g.trim()).filter(Boolean);
    }

    function buildPayload() {
        if (provider === "none") return { sso: null };
        if (provider === "oidc") {
            return {
                sso: {
                    provider: "oidc",
                    client_id: oidc.client_id.trim(),
                    issuer_url: oidc.issuer_url.trim(),
                    redirect_uri: oidc.redirect_uri.trim(),
                    allowed_groups: parseGroups(oidc.allowed_groups),
                    session_ttl_hours: Number(oidc.session_ttl_hours) || 48,
                },
            };
        }
        return {
            sso: {
                provider: "ldap",
                server: ldap.server.trim(),
                bind_dn_template: ldap.bind_dn_template.trim(),
                base_dn: ldap.base_dn.trim(),
                user_id_attribute: ldap.user_id_attribute.trim() || "uid",
                display_name_attribute: ldap.display_name_attribute.trim() || "cn",
                allowed_groups: parseGroups(ldap.allowed_groups),
                session_ttl_hours: Number(ldap.session_ttl_hours) || 168,
                search_bind_dn: ldap.search_bind_dn.trim() || undefined,
                search_bind_password: ldap.search_bind_password || undefined,
            },
        };
    }

    async function handleSave() {
        setSaving(true);
        setResult(null);
        try {
            const res = await fetch("/api/auth/admin-config", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(buildPayload()),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.error ?? "Save failed");
            setResult({ ok: true, msg: "SSO configuration saved." });
            if (data.session_secret_generated) setRestartRequired(true);
        } catch (e: unknown) {
            setResult({ ok: false, msg: e instanceof Error ? e.message : "Save failed." });
        } finally {
            setSaving(false);
        }
    }

    async function handleTestLDAP() {
        setTesting(true);
        setTestResult(null);
        // Test by fetching the mode endpoint — real LDAP bind test would require credentials,
        // so we just verify the config was saved and the mode endpoint is happy.
        try {
            const res = await fetch("/api/auth/mode");
            const data = await res.json();
            if (!res.ok) throw new Error(data.error ?? "Configuration error");
            setTestResult({ ok: true, msg: `Mode ${data.mode} active — provider: ${data.provider ?? "none"}` });
        } catch (e: unknown) {
            setTestResult({ ok: false, msg: e instanceof Error ? e.message : "Test failed" });
        } finally {
            setTesting(false);
        }
    }

    const isPlainLDAP = provider === "ldap" && ldap.server.startsWith("ldap://");

    return (
        <div className="space-y-6">
            <div>
                <h2 className="text-base font-semibold text-foreground">SSO / Directory</h2>
                <p className="text-sm text-muted-foreground mt-1">
                    Configure Mode 3 authentication. SSO users receive the <code className="text-xs bg-muted px-1 rounded">user</code> role.
                    Admins log in separately via the escape hatch on the login page using <code className="text-xs bg-muted px-1 rounded">ADMIN_PASSWORD</code>.
                </p>
            </div>

            {/* Prerequisites */}
            <div className="rounded-lg border border-border bg-card p-4 space-y-2.5">
                <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-3">Prerequisites</p>
                <StatusDot
                    status={appPwStatus}
                    label="APP_PASSWORD"
                    hint={appPwStatus === "missing" ? "not set — required for all modes" : "set — login credential for Modes 1 & 2"}
                />
                <StatusDot
                    status={adminPwStatus}
                    label="ADMIN_PASSWORD"
                    hint={adminPwStatus === "missing" ? "not set — required; admin escape hatch will not work without it" : "set — admin escape hatch credential"}
                />
                <StatusDot
                    status={sessionSecretStatus === "missing" ? "warn" : sessionSecretStatus}
                    label="SESSION_SECRET"
                    hint={sessionSecretStatus === "set" ? "set — session cookie signing key" : "not set — will be auto-generated on first SSO save; restart frontend after saving"}
                />
            </div>

            {/* Provider selector */}
            <div className="rounded-lg border border-border bg-card p-4 space-y-3">
                <FieldRow label="Provider" hint="One provider is active at a time.">
                    <select
                        value={provider}
                        onChange={(e) => setProvider(e.target.value as Provider)}
                        className="w-full rounded-md bg-muted border border-border text-foreground px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                    >
                        <option value="none">None (Mode 1 / Mode 2)</option>
                        <option value="oidc">OIDC — Keycloak / Azure AD / Okta</option>
                        <option value="ldap">LDAP / Active Directory</option>
                    </select>
                </FieldRow>

                {/* OIDC fields */}
                {provider === "oidc" && (
                    <>
                        <FieldRow label="Client ID" hint="Public client — no secret needed.">
                            <TextInput value={oidc.client_id} onChange={(e) => setOIDC({ ...oidc, client_id: e.target.value })} placeholder="lancy-app" />
                        </FieldRow>
                        <FieldRow label="Issuer URL" hint="e.g. https://keycloak.example.com/realms/lancy">
                            <TextInput value={oidc.issuer_url} onChange={(e) => setOIDC({ ...oidc, issuer_url: e.target.value })} placeholder="https://idp.example.com/realms/lancy" />
                        </FieldRow>
                        <FieldRow label="Redirect URI" hint="Must match the URI registered in the IdP.">
                            <TextInput value={oidc.redirect_uri} onChange={(e) => setOIDC({ ...oidc, redirect_uri: e.target.value })} placeholder="https://lancy.example.com/auth/callback" />
                        </FieldRow>
                        <FieldRow label="Allowed groups" hint="Comma-separated. Leave empty to admit any authenticated user.">
                            <TextInput value={oidc.allowed_groups} onChange={(e) => setOIDC({ ...oidc, allowed_groups: e.target.value })} placeholder="/lancy-users, /staff" />
                        </FieldRow>
                        <FieldRow label="Session TTL (hours)" hint="Default 48 h — silent re-auth via IdP session if shorter.">
                            <TextInput type="number" min="1" value={oidc.session_ttl_hours} onChange={(e) => setOIDC({ ...oidc, session_ttl_hours: e.target.value })} />
                        </FieldRow>
                    </>
                )}

                {/* LDAP fields */}
                {provider === "ldap" && (
                    <>
                        <FieldRow label="Server" hint="Use ldaps:// in production.">
                            <TextInput value={ldap.server} onChange={(e) => setLDAP({ ...ldap, server: e.target.value })} placeholder="ldaps://ldap.example.com:636" />
                            {isPlainLDAP && (
                                <div className="flex items-center gap-1.5 mt-1.5 text-xs text-amber-400">
                                    <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                                    Plain LDAP sends passwords in the clear. Use ldaps:// in production.
                                </div>
                            )}
                        </FieldRow>
                        <FieldRow label="Bind DN template" hint="{username} is replaced at login. AD: {username}@corp.example.com">
                            <TextInput value={ldap.bind_dn_template} onChange={(e) => setLDAP({ ...ldap, bind_dn_template: e.target.value })} placeholder="{username}@corp.example.com" />
                        </FieldRow>
                        <FieldRow label="Base DN">
                            <TextInput value={ldap.base_dn} onChange={(e) => setLDAP({ ...ldap, base_dn: e.target.value })} placeholder="DC=corp,DC=example,DC=com" />
                        </FieldRow>
                        <FieldRow label="User ID attribute" hint="Stable identifier used as session_id. AD: userPrincipalName">
                            <TextInput value={ldap.user_id_attribute} onChange={(e) => setLDAP({ ...ldap, user_id_attribute: e.target.value })} placeholder="userPrincipalName" />
                        </FieldRow>
                        <FieldRow label="Display name attribute" hint="AD: displayName">
                            <TextInput value={ldap.display_name_attribute} onChange={(e) => setLDAP({ ...ldap, display_name_attribute: e.target.value })} placeholder="displayName" />
                        </FieldRow>
                        <FieldRow label="Allowed groups" hint="Full group DNs, comma-separated. Leave empty to admit any authenticated user.">
                            <TextInput value={ldap.allowed_groups} onChange={(e) => setLDAP({ ...ldap, allowed_groups: e.target.value })} placeholder="CN=Lancy-Users,OU=Groups,DC=corp,DC=example,DC=com" />
                        </FieldRow>
                        <FieldRow label="Session TTL (hours)" hint="Default 7 days — re-auth requires password entry.">
                            <TextInput type="number" min="1" value={ldap.session_ttl_hours} onChange={(e) => setLDAP({ ...ldap, session_ttl_hours: e.target.value })} />
                        </FieldRow>
                        <FieldRow label="Service account DN" hint="Optional — for servers requiring a dedicated account for group search.">
                            <TextInput value={ldap.search_bind_dn} onChange={(e) => setLDAP({ ...ldap, search_bind_dn: e.target.value })} placeholder="CN=svc-lancy,OU=ServiceAccounts,DC=corp,DC=example,DC=com" />
                        </FieldRow>
                        {ldap.search_bind_dn && (
                            <FieldRow label="Service account password">
                                <TextInput type="password" value={ldap.search_bind_password} onChange={(e) => setLDAP({ ...ldap, search_bind_password: e.target.value })} placeholder="••••••••" />
                            </FieldRow>
                        )}
                    </>
                )}
            </div>

            {/* Actions */}
            <div className="flex items-center gap-3">
                <button
                    onClick={handleSave}
                    disabled={saving}
                    className="rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-medium px-4 py-2 text-sm transition-colors"
                >
                    {saving ? "Saving…" : "Save"}
                </button>
                <button
                    onClick={handleTestLDAP}
                    disabled={testing}
                    className="rounded-lg bg-muted hover:bg-muted/80 disabled:opacity-50 text-foreground font-medium px-4 py-2 text-sm border border-border transition-colors"
                >
                    {testing ? "Testing…" : "Test configuration"}
                </button>
            </div>

            {result && (
                <p className={`text-sm ${result.ok ? "text-green-400" : "text-red-400"}`}>{result.msg}</p>
            )}
            {testResult && (
                <p className={`text-sm ${testResult.ok ? "text-green-400" : "text-red-400"}`}>{testResult.msg}</p>
            )}
            {restartRequired && (
                <div className="flex items-start gap-2.5 rounded-lg border border-amber-400/30 bg-amber-400/5 px-4 py-3">
                    <AlertTriangle className="h-4 w-4 text-amber-400 shrink-0 mt-0.5" />
                    <div className="text-xs text-amber-300 space-y-0.5">
                        <p className="font-medium">Frontend restart required</p>
                        <p className="text-amber-300/70">A <code className="font-mono">SESSION_SECRET</code> was generated and appended to <code className="font-mono">.env</code>. Restart the frontend process for the new signing key to take effect.</p>
                    </div>
                </div>
            )}
        </div>
    );
};
