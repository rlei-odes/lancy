import React, { FunctionComponent, useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw, RotateCcw } from "lucide-react";

interface FailedFile {
    hash: string;
    source_file: string;
    reason: string; // "error" | "no_chunks" | "timeout" | "crashed"
    error: string;
    at: string;
}

const REASON: Record<string, { label: string; cls: string }> = {
    error:     { label: "error",     cls: "bg-red-500/15 text-red-600 dark:text-red-400" },
    crashed:   { label: "crashed",   cls: "bg-red-500/15 text-red-600 dark:text-red-400" },
    timeout:   { label: "timeout",   cls: "bg-amber-500/15 text-amber-600 dark:text-amber-400" },
    no_chunks: { label: "no text",   cls: "bg-muted text-muted-foreground" },
};

/**
 * Files a KB could not index. Incremental runs skip them, so without this list
 * they are invisible once the log has scrolled past. Retrying forgets a file;
 * for the active KB it also starts incremental indexing, which then tries it.
 */
export const FailedFiles: FunctionComponent<{ kbs: { id: string; name: string }[]; activeKbId: string }> = ({ kbs, activeKbId }) => {
    const [kbId, setKbId] = useState(activeKbId);
    const [files, setFiles] = useState<FailedFile[] | null>(null);
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState<string | null>(null);

    useEffect(() => { if (activeKbId) setKbId((id) => id || activeKbId); }, [activeKbId]);

    const load = useCallback(() => {
        if (!kbId) return;
        fetch(`/api/v1/kb/${kbId}/failed-files`, { credentials: "include" })
            .then((r) => (r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`)))
            .then((d) => setFiles(d.files))
            .catch((e) => setMessage(`Failed to load: ${e}`));
    }, [kbId]);

    useEffect(() => { setFiles(null); setMessage(null); load(); }, [load]);

    const retry = async (hash?: string) => {
        setBusy(true);
        setMessage(null);
        try {
            const q = hash ? `?hash=${encodeURIComponent(hash)}` : "";
            const r = await fetch(`/api/v1/kb/${kbId}/failed-files${q}`, { method: "DELETE", credentials: "include" });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            if (kbId === activeKbId) {
                const run = await fetch("/api/v1/rag/reindex", {
                    method: "POST", credentials: "include",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ reset: false }),
                });
                setMessage(run.status === 409
                    ? "Indexing is already running — the files are tried on the next incremental run."
                    : run.ok ? "Incremental indexing started." : `Could not start indexing (HTTP ${run.status}).`);
            } else {
                setMessage("Queued — tried on the next incremental indexing of this KB.");
            }
            load();
        } catch (e) {
            setMessage(`Retry failed: ${e}`);
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="space-y-2">
            <div className="flex items-center justify-between gap-3 flex-wrap">
                <h2 className="text-sm font-semibold">
                    Not indexed{files && files.length > 0 ? ` (${files.length})` : ""}
                </h2>
                <div className="flex items-center gap-2">
                    <select
                        value={kbId}
                        onChange={(e) => setKbId(e.target.value)}
                        className="text-xs [font-family:inherit] bg-muted border border-border text-foreground rounded px-2 py-1 focus:outline-hidden focus:border-blue-400 transition-colors"
                    >
                        {kbs.map((k) => <option key={k.id} value={k.id}>{k.name}</option>)}
                    </select>
                    <button onClick={load} className="text-muted-foreground hover:text-foreground transition-colors" title="Refresh">
                        <RefreshCw className="h-4 w-4" />
                    </button>
                    {files && files.length > 0 && (
                        <button
                            onClick={() => retry()}
                            disabled={busy}
                            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/80 disabled:opacity-50 transition-colors"
                        >
                            {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <RotateCcw className="h-3 w-3" />}
                            Retry all
                        </button>
                    )}
                </div>
            </div>
            <p className="text-xs text-muted-foreground">
                Files that failed to index. Incremental indexing skips them until they change, you retry them, or a full re-index runs.
            </p>
            {message && <p className="text-xs text-muted-foreground">{message}</p>}
            {files && files.length === 0 && (
                <p className="text-xs text-muted-foreground py-2">Every file of this KB was indexed.</p>
            )}
            {files && files.length > 0 && (
                <div className="overflow-x-auto rounded-lg border border-border">
                    <table className="w-full text-xs">
                        <thead>
                            <tr className="border-b border-border bg-muted/40">
                                <th className="text-left px-3 py-2 font-medium text-muted-foreground">Filename</th>
                                <th className="text-left px-3 py-2 font-medium text-muted-foreground">Reason</th>
                                <th className="text-left px-3 py-2 font-medium text-muted-foreground">Error</th>
                                <th className="text-left px-3 py-2 font-medium text-muted-foreground whitespace-nowrap">Last tried</th>
                                <th />
                            </tr>
                        </thead>
                        <tbody>
                            {files.map((f) => {
                                const reason = REASON[f.reason] ?? { label: f.reason, cls: "bg-muted text-muted-foreground" };
                                return (
                                    <tr key={f.hash} className="border-b border-border/50 last:border-0 hover:bg-muted/20 transition-colors">
                                        <td className="px-3 py-2 max-w-xs truncate font-mono" title={f.source_file}>{f.source_file}</td>
                                        <td className="px-3 py-2 whitespace-nowrap">
                                            <span className={`inline-flex px-1.5 py-0.5 rounded font-mono font-medium ${reason.cls}`}>{reason.label}</span>
                                        </td>
                                        <td className="px-3 py-2 max-w-sm truncate font-mono text-destructive/80" title={f.error || undefined}>{f.error}</td>
                                        <td className="px-3 py-2 font-mono whitespace-nowrap text-muted-foreground">{new Date(f.at).toLocaleString()}</td>
                                        <td className="px-3 py-2 text-right">
                                            <button
                                                onClick={() => retry(f.hash)}
                                                disabled={busy}
                                                className="text-muted-foreground hover:text-foreground disabled:opacity-50 transition-colors"
                                                title="Retry this file"
                                            >
                                                <RotateCcw className="h-3.5 w-3.5" />
                                            </button>
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
};
