"use client";

import React, { FunctionComponent, useEffect, useState } from "react";
import {
    Bar,
    BarChart,
    CartesianGrid,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from "recharts";
import { BarChart2, Clock, FileText, Library, Loader2, TrendingUp } from "lucide-react";
import { useTranslation } from "react-i18next";

const API_BASE = typeof window !== "undefined" ? "" : (process.env.SERVER_URL ?? "");

// ─── Types ────────────────────────────────────────────────────────────────────

interface KBBase { id: string; name: string; }
interface KBRegistry { active: string; bases: Record<string, KBBase>; }

interface IngestionEntry {
    timestamp: string;
    chunks_added: number;
    files_added: number;
    files_skipped_store: number;
    files_skipped_batch: number;
    was_reset: boolean;
}

interface KbStats {
    kb_id: string;
    computed_at: string;
    scope: "full" | "incremental";
    total_chunks: number;
    total_documents: number;
    avg_chunk_chars: number;
    p50_chunk_chars: number;
    p95_chunk_chars: number;
    chunk_size_distribution: Record<string, number>;
    chunks_per_document_distribution: Record<string, number>;
    ingestion_history: IngestionEntry[];
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function formatNum(n: number | string): string {
    return Math.round(Number(n)).toString().replace(/\B(?=(\d{3})+(?!\d))/g, "'");
}

function cssVar(name: string): string {
    if (typeof document === "undefined") return "#888";
    const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return raw ? `hsl(${raw})` : "#888";
}

// ─── Card wrapper ─────────────────────────────────────────────────────────────

function ChartCard({ title, icon, children }: {
    title: string;
    icon: React.ReactNode;
    children: React.ReactNode;
}) {
    return (
        <div className="rounded-xl border border-border bg-card shadow-sm overflow-hidden">
            <div className="flex items-center gap-2 px-4 py-3 border-b border-border bg-muted/30">
                {icon}
                <span className="text-sm font-medium">{title}</span>
            </div>
            <div className="p-4">{children}</div>
        </div>
    );
}

// ─── Chart: Chunk size distribution ──────────────────────────────────────────

function ChunkSizeChart({ data }: { data: Record<string, number> }) {
    const { t } = useTranslation("app");
    const chartData = Object.entries(data).map(([range, count]) => ({ range, count }));
    const primary = cssVar("--primary");
    const muted = cssVar("--muted-foreground");
    const border = cssVar("--border");
    return (
        <ChartCard
            title={t("explorer.analyticsChunkSizeTitle")}
            icon={<TrendingUp className="h-4 w-4 text-muted-foreground" />}
        >
            <ResponsiveContainer width="100%" height={200}>
                <BarChart data={chartData} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke={border} vertical={false} />
                    <XAxis dataKey="range" tick={{ fontSize: 10, fill: muted }} interval={0} angle={-35} textAnchor="end" height={48} />
                    <YAxis tick={{ fontSize: 11, fill: muted }} width={40} tickFormatter={formatNum} />
                    <Tooltip
                        contentStyle={{ background: cssVar("--card"), border: `1px solid ${border}`, borderRadius: 8, fontSize: 12 }}
                        labelStyle={{ color: cssVar("--foreground") }}
                        formatter={(value) => [formatNum(value as number), t("explorer.analyticsChunkSizeY")]}
                    />
                    <Bar dataKey="count" fill={primary} radius={[3, 3, 0, 0]} />
                </BarChart>
            </ResponsiveContainer>
        </ChartCard>
    );
}

// ─── Chart: Chunks per document distribution ──────────────────────────────────

const CPD_KEYS = [...Array.from({ length: 100 }, (_, i) => String(i + 1)), "100+"];

function ChunksPerDocChart({ data }: { data: Record<string, number> }) {
    const { t } = useTranslation("app");
    const chartData = CPD_KEYS.map((key) => ({ x: key, docs: data[key] ?? 0 }));
    const primary = cssVar("--primary");
    const muted = cssVar("--muted-foreground");
    const border = cssVar("--border");
    const xTickFormatter = (val: string) => {
        if (val === "100+") return val;
        const n = parseInt(val, 10);
        return n % 10 === 0 ? val : "";
    };
    return (
        <ChartCard
            title={t("explorer.analyticsChunksPerDocTitle")}
            icon={<FileText className="h-4 w-4 text-muted-foreground" />}
        >
            <ResponsiveContainer width="100%" height={200}>
                <BarChart data={chartData} margin={{ top: 4, right: 8, bottom: 4, left: 0 }} barCategoryGap="1%">
                    <CartesianGrid strokeDasharray="3 3" stroke={border} vertical={false} />
                    <XAxis dataKey="x" tick={{ fontSize: 10, fill: muted }} tickFormatter={xTickFormatter} />
                    <YAxis tick={{ fontSize: 11, fill: muted }} width={40} allowDecimals={false} tickFormatter={formatNum} />
                    <Tooltip
                        contentStyle={{ background: cssVar("--card"), border: `1px solid ${border}`, borderRadius: 8, fontSize: 12 }}
                        labelStyle={{ color: cssVar("--foreground") }}
                        formatter={(value) => [formatNum(value as number), t("explorer.analyticsChunksPerDocY")]}
                        labelFormatter={(label) => `${t("explorer.analyticsChunksPerDocX")}: ${label}`}
                    />
                    <Bar dataKey="docs" fill={primary} radius={[2, 2, 0, 0]} />
                </BarChart>
            </ResponsiveContainer>
        </ChartCard>
    );
}

// ─── Chart: Ingestion history ─────────────────────────────────────────────────

function IngestionChart({ data }: { data: IngestionEntry[] }) {
    const { t } = useTranslation("app");

    // Group by calendar day — multiple runs on the same day are stacked
    const dateMap = new Map<string, { label: string; incremental: number; reset: number }>();
    for (const entry of data) {
        const date = new Date(entry.timestamp).toLocaleDateString(undefined, { month: "short", day: "numeric" });
        const existing = dateMap.get(date) ?? { label: date, incremental: 0, reset: 0 };
        if (entry.was_reset) {
            existing.reset += entry.chunks_added;
        } else {
            existing.incremental += entry.chunks_added;
        }
        dateMap.set(date, existing);
    }
    const chartData = Array.from(dateMap.values());

    const primary = cssVar("--primary");
    const destructive = cssVar("--destructive");
    const muted = cssVar("--muted-foreground");
    const border = cssVar("--border");

    return (
        <ChartCard
            title={t("explorer.analyticsIngestionTitle")}
            icon={<Clock className="h-4 w-4 text-muted-foreground" />}
        >
            <div className="flex items-center gap-4 mb-3 text-xs text-muted-foreground">
                <span className="flex items-center gap-1.5">
                    <span className="h-2.5 w-2.5 rounded-sm" style={{ background: primary }} />
                    {t("explorer.analyticsIngestionIncremental")}
                </span>
                <span className="flex items-center gap-1.5">
                    <span className="h-2.5 w-2.5 rounded-sm" style={{ background: destructive }} />
                    {t("explorer.analyticsIngestionReset")}
                </span>
            </div>
            <ResponsiveContainer width="100%" height={180}>
                <BarChart data={chartData} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke={border} vertical={false} />
                    <XAxis dataKey="label" tick={{ fontSize: 11, fill: muted }} />
                    <YAxis tick={{ fontSize: 11, fill: muted }} width={40} tickFormatter={formatNum} />
                    <Tooltip
                        contentStyle={{ background: cssVar("--card"), border: `1px solid ${border}`, borderRadius: 8, fontSize: 12 }}
                        labelStyle={{ color: cssVar("--foreground"), fontWeight: 500, marginBottom: 2 }}
                        formatter={(value, name) => {
                            if (!value) return null;
                            const label = name === "incremental"
                                ? t("explorer.analyticsIngestionIncremental")
                                : t("explorer.analyticsIngestionReset");
                            return [`${formatNum(value as number)} ${t("explorer.analyticsIngestionY")}`, label];
                        }}
                    />
                    <Bar dataKey="incremental" stackId="a" fill={primary} />
                    <Bar dataKey="reset" stackId="a" fill={destructive} radius={[3, 3, 0, 0]} />
                </BarChart>
            </ResponsiveContainer>
        </ChartCard>
    );
}

// ─── Summary panel ────────────────────────────────────────────────────────────

function SummaryPanel({ stats }: { stats: KbStats }) {
    const { t } = useTranslation("app");
    const items = [
        { label: t("explorer.analyticsTotalChunks"), value: formatNum(stats.total_chunks) },
        { label: t("explorer.analyticsTotalDocs"), value: formatNum(stats.total_documents) },
        { label: t("explorer.analyticsAvgChars"), value: `${formatNum(stats.avg_chunk_chars)} ${t("explorer.analyticsChars")}` },
        { label: t("explorer.analyticsP50"), value: `${formatNum(stats.p50_chunk_chars)} ${t("explorer.analyticsChars")}` },
        { label: t("explorer.analyticsP95"), value: `${formatNum(stats.p95_chunk_chars)} ${t("explorer.analyticsChars")}` },
    ];
    return (
        <div className="rounded-xl border border-border bg-card shadow-sm overflow-hidden">
            <div className="flex items-center gap-2 px-4 py-3 border-b border-border bg-muted/30">
                <BarChart2 className="h-4 w-4 text-muted-foreground" />
                <span className="text-sm font-medium">{t("explorer.analyticsSummaryTitle")}</span>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 divide-x divide-y divide-border">
                {items.map(({ label, value }) => (
                    <div key={label} className="px-4 py-3 flex flex-col gap-0.5">
                        <span className="text-xs text-muted-foreground">{label}</span>
                        <span className="text-base font-semibold tabular-nums">{value}</span>
                    </div>
                ))}
            </div>
        </div>
    );
}

// ─── Main component ───────────────────────────────────────────────────────────

export const KbAnalytics: FunctionComponent<{ active: boolean }> = ({ active }) => {
    const { t } = useTranslation("app");
    const [kbRegistry, setKbRegistry] = useState<KBRegistry | null>(null);
    const [selectedKb, setSelectedKb] = useState<string>("");
    const [stats, setStats] = useState<KbStats | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [notFound, setNotFound] = useState(false);

    useEffect(() => {
        if (!active) return;
        fetch(`${API_BASE}/api/v1/kb`, { credentials: "include" })
            .then((r) => r.json())
            .then((reg: KBRegistry) => {
                setKbRegistry(reg);
                setSelectedKb(reg.active);
            })
            .catch(() => {});
    }, [active]);

    useEffect(() => {
        if (!selectedKb) return;
        setLoading(true);
        setError(null);
        setNotFound(false);
        setStats(null);
        fetch(`${API_BASE}/api/v1/kb/${selectedKb}/stats`, { credentials: "include" })
            .then(async (r) => {
                if (r.status === 404) { setNotFound(true); return; }
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                setStats(await r.json());
            })
            .catch((e) => setError(e instanceof Error ? e.message : "Unknown error"))
            .finally(() => setLoading(false));
    }, [selectedKb]);

    const kbOptions = kbRegistry
        ? Object.values(kbRegistry.bases).map((kb) => ({ id: kb.id, name: kb.name }))
        : [];

    return (
        <div className="flex flex-col gap-6 w-full">
            {/* KB selector */}
            <div className="rounded-xl border border-border bg-card shadow-sm overflow-hidden">
                <div className="flex items-center gap-2 px-4 py-3 border-b border-border bg-muted/30">
                    <Library className="h-4 w-4 text-muted-foreground" />
                    <span className="text-sm font-medium">{t("explorer.analyticsKbLabel")}</span>
                    {stats && (
                        <span className="ml-auto text-xs text-muted-foreground">
                            {t(stats.scope === "incremental"
                                ? "explorer.analyticsScopeIncremental"
                                : "explorer.analyticsScopeFull")}
                            {" · "}
                            {t("explorer.analyticsComputedAt", {
                                date: new Date(stats.computed_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }),
                            })}
                        </span>
                    )}
                </div>
                <div className="px-4 py-3">
                    <select
                        value={selectedKb}
                        onChange={(e) => setSelectedKb(e.target.value)}
                        className="text-sm bg-transparent outline-none text-foreground border border-border rounded-md px-2 py-1"
                    >
                        {kbOptions.map((kb) => (
                            <option key={kb.id} value={kb.id}>{kb.name}</option>
                        ))}
                    </select>
                </div>
            </div>

            {/* Error */}
            {error && (
                <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                    {error}
                </div>
            )}

            {/* Loading */}
            {loading && (
                <div className="flex items-center justify-center py-16 text-muted-foreground/50">
                    <Loader2 className="h-6 w-6 animate-spin" />
                </div>
            )}

            {/* No stats yet */}
            {!loading && notFound && (
                <div className="flex flex-col items-center justify-center py-16 text-center text-muted-foreground/50">
                    <BarChart2 className="h-10 w-10 mb-3 opacity-30" />
                    <p className="text-sm">{t("explorer.analyticsNoStats")}</p>
                    <p className="text-xs mt-1">{t("explorer.analyticsNoStatsHint")}</p>
                </div>
            )}

            {/* Charts */}
            {!loading && stats && (
                <>
                    <SummaryPanel stats={stats} />
                    <ChunkSizeChart data={stats.chunk_size_distribution} />
                    <ChunksPerDocChart data={stats.chunks_per_document_distribution} />
                    <IngestionChart data={stats.ingestion_history} />
                </>
            )}
        </div>
    );
};
