import React, { FunctionComponent, useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, Loader2, RefreshCw } from "lucide-react";

const STATUSES = ["success", "crashed", "timeout", "no_chunks"];
const PAGE_SIZE = 50;

interface KBMeta {
    id: string;
    name: string;
}

interface IngestEvent {
    id: number;
    ts: string;
    kb_id: string;
    document_id: string;
    filename: string;
    status: string;
    chunks: number | null;
    file_size_mb: number | null;
    duration_ms: number | null;
    error: string | null;
}

interface IngestEventPage {
    events: IngestEvent[];
    total: number;
    limit: number;
    offset: number;
}

function StatusBadge({ status }: { status: string }) {
    const cls: Record<string, string> = {
        success:   "bg-green-500/15 text-green-600 dark:text-green-400",
        crashed:   "bg-red-500/15 text-red-600 dark:text-red-400",
        timeout:   "bg-amber-500/15 text-amber-600 dark:text-amber-400",
        no_chunks: "bg-muted text-muted-foreground",
    };
    return (
        <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-mono font-medium ${cls[status] ?? "bg-muted text-muted-foreground"}`}>
            {status}
        </span>
    );
}

function fmtDuration(ms: number | null): string {
    if (ms === null) return "—";
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
}

function fmtSize(mb: number | null): string {
    if (mb === null) return "—";
    if (mb < 1) return `${(mb * 1024).toFixed(0)} KB`;
    return `${mb.toFixed(1)} MB`;
}

function fmtTs(ts: string): string {
    try { return new Date(ts).toLocaleString(); } catch { return ts; }
}

export const IngestionLog: FunctionComponent = () => {
    const [kbs, setKbs] = useState<KBMeta[]>([]);
    const [filterKb, setFilterKb] = useState("");
    const [filterStatus, setFilterStatus] = useState("");
    const [filterDays, setFilterDays] = useState("30");
    const [page, setPage] = useState<IngestEventPage | null>(null);
    const [offset, setOffset] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        fetch("/api/v1/kb", { credentials: "include" })
            .then((r) => r.json())
            .then((reg) => setKbs(Object.values(reg.bases as Record<string, KBMeta>)))
            .catch(() => {});
    }, []);

    const load = (off: number) => {
        setLoading(true);
        setError(null);
        const params = new URLSearchParams();
        if (filterKb) params.set("kb_id", filterKb);
        if (filterStatus) params.set("status", filterStatus);
        if (filterDays) params.set("days", filterDays);
        params.set("limit", String(PAGE_SIZE));
        params.set("offset", String(off));
        fetch(`/api/admin/ingest-events?${params}`)
            .then((r) => r.ok ? r.json() : Promise.reject("Failed to load"))
            .then((data: IngestEventPage) => { setPage(data); setOffset(off); })
            .catch((e) => setError(String(e)))
            .finally(() => setLoading(false));
    };

    // Reset to page 0 when filters change
    // eslint-disable-next-line react-hooks/exhaustive-deps
    useEffect(() => { load(0); }, [filterKb, filterStatus, filterDays]);

    const kbName = (id: string) => kbs.find((k) => k.id === id)?.name ?? id;

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between gap-3 flex-wrap">
                <h2 className="text-sm font-semibold">Ingestion Log</h2>
                <div className="flex items-center gap-2 flex-wrap">
                    <select
                        value={filterKb}
                        onChange={(e) => setFilterKb(e.target.value)}
                        className="text-xs border border-border rounded px-2 py-1 bg-background text-foreground"
                    >
                        <option value="">All KBs</option>
                        {kbs.map((k) => <option key={k.id} value={k.id}>{k.name}</option>)}
                    </select>
                    <select
                        value={filterStatus}
                        onChange={(e) => setFilterStatus(e.target.value)}
                        className="text-xs border border-border rounded px-2 py-1 bg-background text-foreground"
                    >
                        <option value="">All statuses</option>
                        {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
                    </select>
                    <select
                        value={filterDays}
                        onChange={(e) => setFilterDays(e.target.value)}
                        className="text-xs border border-border rounded px-2 py-1 bg-background text-foreground"
                    >
                        <option value="">All time</option>
                        <option value="1">Last 24h</option>
                        <option value="7">Last 7d</option>
                        <option value="30">Last 30d</option>
                        <option value="90">Last 90d</option>
                    </select>
                    <button
                        onClick={() => load(offset)}
                        className="text-muted-foreground hover:text-foreground transition-colors"
                        title="Refresh"
                    >
                        <RefreshCw className="h-4 w-4" />
                    </button>
                </div>
            </div>

            {loading && (
                <div className="flex items-center gap-2 text-sm text-muted-foreground py-8 justify-center">
                    <Loader2 className="h-4 w-4 animate-spin" /> Loading…
                </div>
            )}
            {error && <p className="text-sm text-destructive">{error}</p>}

            {page && !loading && (
                <>
                    <div className="overflow-x-auto rounded-lg border border-border">
                        <table className="w-full text-xs">
                            <thead>
                                <tr className="border-b border-border bg-muted/40">
                                    <th className="text-left px-3 py-2 font-medium text-muted-foreground whitespace-nowrap">Timestamp</th>
                                    <th className="text-left px-3 py-2 font-medium text-muted-foreground">KB</th>
                                    <th className="text-left px-3 py-2 font-medium text-muted-foreground">Filename</th>
                                    <th className="text-left px-3 py-2 font-medium text-muted-foreground">Status</th>
                                    <th className="text-right px-3 py-2 font-medium text-muted-foreground">Chunks</th>
                                    <th className="text-right px-3 py-2 font-medium text-muted-foreground whitespace-nowrap">Size</th>
                                    <th className="text-right px-3 py-2 font-medium text-muted-foreground whitespace-nowrap">Duration</th>
                                    <th className="text-left px-3 py-2 font-medium text-muted-foreground">Error</th>
                                </tr>
                            </thead>
                            <tbody>
                                {page.events.length === 0 && (
                                    <tr>
                                        <td colSpan={8} className="text-center py-8 text-muted-foreground">
                                            No events found.
                                        </td>
                                    </tr>
                                )}
                                {page.events.map((ev) => (
                                    <tr key={ev.id} className="border-b border-border/50 last:border-0 hover:bg-muted/20 transition-colors">
                                        <td className="px-3 py-2 font-mono whitespace-nowrap text-muted-foreground">{fmtTs(ev.ts)}</td>
                                        <td className="px-3 py-2 whitespace-nowrap">{kbName(ev.kb_id)}</td>
                                        <td className="px-3 py-2 max-w-xs truncate font-mono" title={ev.filename}>{ev.filename}</td>
                                        <td className="px-3 py-2 whitespace-nowrap"><StatusBadge status={ev.status} /></td>
                                        <td className="px-3 py-2 text-right font-mono">{ev.chunks ?? "—"}</td>
                                        <td className="px-3 py-2 text-right font-mono whitespace-nowrap">{fmtSize(ev.file_size_mb)}</td>
                                        <td className="px-3 py-2 text-right font-mono whitespace-nowrap">{fmtDuration(ev.duration_ms)}</td>
                                        <td className="px-3 py-2 max-w-xs truncate font-mono text-destructive/80" title={ev.error ?? undefined}>
                                            {ev.error ?? ""}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>

                    <div className="flex items-center justify-between text-xs text-muted-foreground">
                        <span>
                            {page.total === 0
                                ? "No results"
                                : `${offset + 1}–${Math.min(offset + PAGE_SIZE, page.total)} of ${page.total}`}
                        </span>
                        <div className="flex items-center gap-1">
                            <button
                                onClick={() => load(Math.max(0, offset - PAGE_SIZE))}
                                disabled={offset === 0}
                                className="p-1 rounded hover:bg-muted disabled:opacity-40 transition-colors"
                            >
                                <ChevronLeft className="h-4 w-4" />
                            </button>
                            <button
                                onClick={() => load(offset + PAGE_SIZE)}
                                disabled={offset + PAGE_SIZE >= page.total}
                                className="p-1 rounded hover:bg-muted disabled:opacity-40 transition-colors"
                            >
                                <ChevronRight className="h-4 w-4" />
                            </button>
                        </div>
                    </div>
                </>
            )}
        </div>
    );
};
