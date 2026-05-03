import React, { FunctionComponent, useEffect, useState } from "react";
import { Clock, Database, HardDrive, Loader2, MessageSquare, RefreshCw, Save, Trash2 } from "lucide-react";

interface ConvDbStats {
    db_type: string;
    path: string | null;
    size_bytes: number | null;
    conv_count: number;
    message_count: number;
    reaction_count: number;
    user_count: number;
}

interface VsDbStats {
    vs_type: string;
    path: string | null;
    size_bytes: number | null;
    chunk_count: number | null;
}

interface DbStats {
    conv_db: ConvDbStats;
    vs_db: VsDbStats;
}

interface AdminConfig {
    auto_cleanup_enabled: boolean;
    auto_cleanup_months: number;
    auto_cleanup_last_run: string | null;
}

function fmtBytes(bytes: number | null): string {
    if (bytes === null) return "—";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function StatRow({ label, value }: { label: string; value: React.ReactNode }) {
    return (
        <div className="flex justify-between items-center py-2 border-b border-border/50 last:border-0">
            <span className="text-xs text-muted-foreground">{label}</span>
            <span className="text-xs font-medium font-mono">{value}</span>
        </div>
    );
}

function SectionCard({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
    return (
        <div className="rounded-lg border border-border p-4 bg-muted/20 space-y-1">
            <div className="flex items-center gap-2 mb-3">
                {icon}
                <h3 className="text-sm font-semibold">{title}</h3>
            </div>
            {children}
        </div>
    );
}

export const DatabaseStats: FunctionComponent = () => {
    const [stats, setStats] = useState<DbStats | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [clearMonths, setClearMonths] = useState(6);
    const [clearing, setClearing] = useState(false);
    const [clearResult, setClearResult] = useState<string | null>(null);

    const [autoCfg, setAutoCfg] = useState<AdminConfig | null>(null);
    const [autoCfgSaving, setAutoCfgSaving] = useState(false);
    const [autoCfgResult, setAutoCfgResult] = useState<string | null>(null);

    const load = () => {
        setLoading(true);
        setError(null);
        fetch("/api/admin/stats/db")
            .then((r) => r.ok ? r.json() : Promise.reject("Failed to load DB stats"))
            .then(setStats)
            .catch((e) => setError(String(e)))
            .finally(() => setLoading(false));
    };

    const loadAutoCfg = () => {
        fetch("/api/admin/config")
            .then((r) => r.ok ? r.json() : Promise.reject())
            .then(setAutoCfg)
            .catch(() => {});
    };

    const saveAutoCfg = async () => {
        if (!autoCfg) return;
        setAutoCfgSaving(true);
        setAutoCfgResult(null);
        try {
            const res = await fetch("/api/admin/config", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(autoCfg),
            });
            if (!res.ok) throw new Error();
            setAutoCfg(await res.json());
            setAutoCfgResult("Saved.");
        } catch {
            setAutoCfgResult("Failed to save.");
        } finally {
            setAutoCfgSaving(false);
        }
    };

    useEffect(() => { load(); loadAutoCfg(); }, []);

    const handleClear = async () => {
        if (!confirm(`Delete all conversations older than ${clearMonths} months? This cannot be undone.`)) return;
        setClearing(true);
        setClearResult(null);
        try {
            const res = await fetch("/api/admin/clear", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ older_than_months: clearMonths }),
            });
            if (!res.ok) throw new Error("Request failed");
            const data = await res.json();
            setClearResult(
                `Deleted: ${data.deleted_conversations} conversations, ${data.deleted_messages} messages, ${data.deleted_reactions} reactions, ${data.deleted_sources} sources.`
            );
            load();
        } catch {
            setClearResult("Failed to clear records. Check server logs.");
        } finally {
            setClearing(false);
        }
    };

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold">Database</h2>
                <button onClick={load} className="text-muted-foreground hover:text-foreground transition-colors" title="Refresh">
                    <RefreshCw className="h-4 w-4" />
                </button>
            </div>

            {loading && (
                <div className="flex items-center gap-2 text-sm text-muted-foreground py-8 justify-center">
                    <Loader2 className="h-4 w-4 animate-spin" /> Loading…
                </div>
            )}
            {error && <p className="text-sm text-destructive">{error}</p>}

            {stats && (
                <>
                    {/* Conversation DB */}
                    <SectionCard title="Conversation Database" icon={<MessageSquare className="h-4 w-4 text-muted-foreground" />}>
                        <StatRow label="Backend" value={stats.conv_db.db_type.toUpperCase()} />
                        {stats.conv_db.path && (
                            <StatRow label="Path" value={<span className="truncate max-w-xs block text-right">{stats.conv_db.path}</span>} />
                        )}
                        <StatRow label="File size" value={fmtBytes(stats.conv_db.size_bytes)} />
                        <StatRow label="Conversations" value={stats.conv_db.conv_count.toLocaleString()} />
                        <StatRow label="Messages" value={stats.conv_db.message_count.toLocaleString()} />
                        <StatRow label="Reactions" value={stats.conv_db.reaction_count.toLocaleString()} />
                        <StatRow label="Users / sessions" value={stats.conv_db.user_count.toLocaleString()} />
                    </SectionCard>

                    {/* Vector DB */}
                    <SectionCard title="Vector Store" icon={<Database className="h-4 w-4 text-muted-foreground" />}>
                        <StatRow label="Backend" value={stats.vs_db.vs_type} />
                        {stats.vs_db.path && (
                            <StatRow label="Path" value={<span className="truncate max-w-xs block text-right">{stats.vs_db.path}</span>} />
                        )}
                        <StatRow label="Disk usage" value={fmtBytes(stats.vs_db.size_bytes)} />
                        <StatRow label="Chunks indexed" value={stats.vs_db.chunk_count?.toLocaleString() ?? "—"} />
                    </SectionCard>

                    {/* DB Connections (read-only) */}
                    <SectionCard title="Connection Settings" icon={<HardDrive className="h-4 w-4 text-muted-foreground" />}>
                        <p className="text-xs text-muted-foreground pb-2">
                            Connection strings are configured via environment variables (<code className="font-mono">DATABASE_URL</code>).
                            Editing them here is not yet supported.
                        </p>
                        <StatRow label="Vector store backend" value={stats.vs_db.vs_type} />
                        <StatRow label="Conversation DB backend" value={stats.conv_db.db_type} />
                    </SectionCard>

                    {/* Auto-cleanup */}
                    <SectionCard title="Automatic Cleanup" icon={<Clock className="h-4 w-4 text-muted-foreground" />}>
                        <p className="text-xs text-muted-foreground pb-3">
                            Automatically delete conversations older than the configured threshold. Runs once daily at startup.
                        </p>
                        {autoCfg && (
                            <>
                                <div className="flex items-center gap-3 flex-wrap">
                                    <label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer">
                                        <input
                                            type="checkbox"
                                            checked={autoCfg.auto_cleanup_enabled}
                                            onChange={(e) => setAutoCfg({ ...autoCfg, auto_cleanup_enabled: e.target.checked })}
                                        />
                                        Enabled
                                    </label>
                                    <label className="text-xs text-muted-foreground whitespace-nowrap ml-2">Delete after</label>
                                    <input
                                        type="number"
                                        min={1}
                                        max={99}
                                        value={autoCfg.auto_cleanup_months}
                                        disabled={!autoCfg.auto_cleanup_enabled}
                                        onChange={(e) => setAutoCfg({ ...autoCfg, auto_cleanup_months: Math.min(99, Math.max(1, parseInt(e.target.value) || 1)) })}
                                        className="w-16 text-xs border border-border rounded px-2 py-1 bg-background text-foreground disabled:opacity-50"
                                    />
                                    <label className="text-xs text-muted-foreground">months</label>
                                    <button
                                        onClick={saveAutoCfg}
                                        disabled={autoCfgSaving}
                                        className="ml-auto flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/80 disabled:opacity-50 transition-colors"
                                    >
                                        {autoCfgSaving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
                                        Save
                                    </button>
                                </div>
                                {autoCfg.auto_cleanup_last_run && (
                                    <p className="text-xs text-muted-foreground mt-2">
                                        Last run: {new Date(autoCfg.auto_cleanup_last_run).toLocaleString()}
                                    </p>
                                )}
                                {autoCfgResult && (
                                    <p className="text-xs text-muted-foreground mt-1 pt-1 border-t border-border/50">{autoCfgResult}</p>
                                )}
                            </>
                        )}
                    </SectionCard>

                    {/* Manual clear */}
                    <SectionCard title="Clear Old Records" icon={<Trash2 className="h-4 w-4 text-muted-foreground" />}>
                        <p className="text-xs text-muted-foreground pb-3">
                            Permanently delete conversations, messages, and reactions older than a given number of months.
                            Vector store data is not affected.
                        </p>
                        <div className="flex items-center gap-3">
                            <label className="text-xs text-muted-foreground whitespace-nowrap">Older than</label>
                            <input
                                type="number"
                                min={1}
                                max={99}
                                value={clearMonths}
                                onChange={(e) => setClearMonths(Math.min(99, Math.max(1, parseInt(e.target.value) || 1)))}
                                className="w-16 text-xs border border-border rounded px-2 py-1 bg-background text-foreground"
                            />
                            <label className="text-xs text-muted-foreground">months</label>
                            <button
                                onClick={handleClear}
                                disabled={clearing}
                                className="ml-auto flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md bg-destructive text-destructive-foreground hover:bg-destructive/80 disabled:opacity-50 transition-colors"
                            >
                                {clearing ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
                                Delete
                            </button>
                        </div>
                        {clearResult && (
                            <p className="text-xs text-muted-foreground mt-2 pt-2 border-t border-border/50">{clearResult}</p>
                        )}
                    </SectionCard>

                    {/* Backup */}
                    <SectionCard title="Backup" icon={<HardDrive className="h-4 w-4 text-muted-foreground" />}>
                        <p className="text-xs text-muted-foreground">
                            The per-user config database (<code className="font-mono">user_config.db</code>) is backed up automatically at startup when older than 24 hours.
                            For the conversation database, use a manual file copy or scheduled job.
                        </p>
                        {stats.conv_db.path && (
                            <p className="text-xs text-muted-foreground mt-1">
                                Conversation DB: <code className="font-mono">{stats.conv_db.path}</code>
                            </p>
                        )}
                    </SectionCard>
                </>
            )}
        </div>
    );
};
