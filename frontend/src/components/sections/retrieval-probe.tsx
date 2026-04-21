"use client";

import React, { FunctionComponent, useCallback, useEffect, useRef, useState } from "react";
import { FlaskConical, Loader2, Search, SlidersHorizontal } from "lucide-react";
import { cn } from "@/lib/lorem";
import { ChunkCard, ChunkData } from "@/components/ui/chunk-card";

const API_BASE = typeof window !== "undefined" ? "" : (process.env.SERVER_URL ?? "");

interface SessionConfig {
    retriever_top_k: number;
    bm25_enabled: boolean;
    reranking_enabled: boolean;
}

interface RetrieveResponse {
    chunks: ChunkData[];
    top_k: number;
    total_returned: number;
    reranking_skipped: boolean;
}

async function fetchSessionConfig(): Promise<SessionConfig> {
    const r = await fetch(`${API_BASE}/api/v1/rag/config`, { credentials: "include" });
    if (!r.ok) throw new Error("Failed to load session config");
    return r.json();
}

async function runProbe(
    query: string,
    bm25Enabled: boolean,
    rerankingEnabled: boolean,
    sourceFilter: string
): Promise<RetrieveResponse> {
    const body: Record<string, unknown> = { query, bm25_enabled: bm25Enabled, reranking_enabled: rerankingEnabled };
    if (sourceFilter.trim()) body.filters = { source_file: sourceFilter.trim() };
    const r = await fetch(`${API_BASE}/api/v1/rag/retrieve`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error(err.detail ?? "Retrieval failed");
    }
    return r.json();
}

function MethodIndicator({ label, enabled, color }: {
    label: string;
    enabled: boolean;
    color: string;
}) {
    return (
        <span
            className={cn(
                "inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium border select-none",
                enabled
                    ? `${color} border-transparent`
                    : "bg-transparent text-muted-foreground border-border opacity-50"
            )}
        >
            <span className={cn("h-1.5 w-1.5 rounded-full", enabled ? "bg-current" : "bg-muted-foreground")} />
            {label}
        </span>
    );
}

export const RetrievalProbe: FunctionComponent = () => {
    const [query, setQuery] = useState("");
    const [sourceFilter, setSourceFilter] = useState("");
    const [bm25Enabled, setBm25Enabled] = useState(true);
    const [rerankingEnabled, setRerankingEnabled] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [result, setResult] = useState<RetrieveResponse | null>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    const syncConfig = useCallback(() => {
        fetchSessionConfig()
            .then((cfg) => {
                setBm25Enabled(cfg.bm25_enabled);
                setRerankingEnabled(cfg.reranking_enabled);
            })
            .catch(() => {});
    }, []);

    // Seed on mount and refresh whenever the sidebar saves
    useEffect(() => {
        syncConfig();
        window.addEventListener("rag-config-saved", syncConfig);
        return () => window.removeEventListener("rag-config-saved", syncConfig);
    }, [syncConfig]);

    const handleSubmit = useCallback(async () => {
        const q = query.trim();
        if (!q) return;
        setLoading(true);
        setError(null);
        try {
            // Re-fetch session config so indicators and probe always use the latest sidebar settings
            const cfg = await fetchSessionConfig().catch(() => ({ bm25_enabled: bm25Enabled, reranking_enabled: rerankingEnabled, retriever_top_k: 5 }));
            setBm25Enabled(cfg.bm25_enabled);
            setRerankingEnabled(cfg.reranking_enabled);
            const res = await runProbe(q, cfg.bm25_enabled, cfg.reranking_enabled, sourceFilter);
            setResult(res);
        } catch (e) {
            setError(e instanceof Error ? e.message : "Unknown error");
        } finally {
            setLoading(false);
        }
    }, [query, bm25Enabled, rerankingEnabled, sourceFilter]);

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleSubmit();
        }
    };

    const topK = result?.top_k ?? 0;

    return (
        <div className="flex flex-col gap-6 w-full">
            {/* Query input card */}
            <div className="rounded-xl border border-border bg-card shadow-sm overflow-hidden">
                <div className="flex items-center gap-2 px-4 py-3 border-b border-border bg-muted/30">
                    <FlaskConical className="h-4 w-4 text-muted-foreground" />
                    <span className="text-sm font-medium">Retrieval Probe</span>
                    <span className="ml-auto text-xs text-muted-foreground">Enter to run · Shift+Enter for newline</span>
                </div>

                <div className="p-4 flex flex-col gap-3">
                    <textarea
                        ref={textareaRef}
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        onKeyDown={handleKeyDown}
                        placeholder="Enter a query to probe the retrieval pipeline…"
                        rows={3}
                        className="w-full resize-none bg-transparent text-sm placeholder:text-muted-foreground/50 outline-none leading-relaxed"
                    />

                    <div className="flex flex-wrap items-center gap-3">
                        {/* Method toggles */}
                        <div className="flex items-center gap-1.5">
                            <SlidersHorizontal className="h-3.5 w-3.5 text-muted-foreground" />
                            <MethodIndicator label="BM25" enabled={bm25Enabled} color="bg-amber-500/15 text-amber-400" />
                            <MethodIndicator label="Reranking" enabled={rerankingEnabled} color="bg-violet-500/15 text-violet-400" />
                        </div>

                        {/* Source filter */}
                        <div className="flex items-center gap-1.5 ml-2">
                            <Search className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                            <input
                                type="text"
                                value={sourceFilter}
                                onChange={(e) => setSourceFilter(e.target.value)}
                                placeholder="filter by source file…"
                                className="text-xs bg-transparent outline-none placeholder:text-muted-foreground/40 text-muted-foreground w-44"
                            />
                        </div>

                        {/* Run button */}
                        <button
                            onClick={handleSubmit}
                            disabled={loading || !query.trim()}
                            className={cn(
                                "ml-auto flex items-center gap-2 px-4 py-1.5 rounded-md text-sm font-medium transition-all",
                                "bg-primary text-primary-foreground hover:bg-primary/90",
                                "disabled:opacity-40 disabled:cursor-not-allowed"
                            )}
                        >
                            {loading ? (
                                <><Loader2 className="h-3.5 w-3.5 animate-spin" /> Running…</>
                            ) : (
                                <>Run probe</>
                            )}
                        </button>
                    </div>
                </div>
            </div>

            {/* Error */}
            {error && (
                <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                    {error}
                </div>
            )}

            {/* Results */}
            {result && !loading && (
                <div className="flex flex-col gap-2">
                    {/* Results header */}
                    <div className="flex items-center gap-3 px-1">
                        <span className="text-xs text-muted-foreground font-mono">
                            {result.total_returned} chunks · top_k = {result.top_k}
                            {result.reranking_skipped && (
                                <span className="ml-2 text-amber-500">(reranking skipped)</span>
                            )}
                        </span>
                        <div className="flex-1 h-px bg-border" />
                    </div>

                    {/* Chunk cards */}
                    <div className="flex flex-col gap-2">
                        {result.chunks.map((chunk, i) => {
                            const isOverK = chunk.final_rank > topK;
                            const dimLabel = rerankingEnabled && !result.reranking_skipped
                                ? "reranker\ncandidate"
                                : "outside\nk";
                            return (
                                <React.Fragment key={chunk.id}>
                                    {/* Cutoff divider */}
                                    {isOverK && i > 0 && result.chunks[i - 1].final_rank <= topK && (
                                        <div className="flex items-center gap-3 my-1 px-1">
                                            <div className="flex-1 h-px bg-border/60" />
                                            <span className="text-[10px] font-mono text-muted-foreground/60 uppercase tracking-widest">
                                                cutoff — top_{topK}
                                            </span>
                                            <div className="flex-1 h-px bg-border/60" />
                                        </div>
                                    )}
                                    <ChunkCard
                                        chunk={chunk}
                                        topK={topK}
                                        rerankingActive={rerankingEnabled && !result.reranking_skipped}
                                        rerankingSkipped={result.reranking_skipped}
                                        dimmed={isOverK}
                                        dimLabel={isOverK ? dimLabel : undefined}
                                    />
                                </React.Fragment>
                            );
                        })}
                    </div>
                </div>
            )}

            {/* Empty state */}
            {!result && !loading && !error && (
                <div className="flex flex-col items-center justify-center py-16 text-center text-muted-foreground/50">
                    <FlaskConical className="h-10 w-10 mb-3 opacity-30" />
                    <p className="text-sm">Enter a query above to probe the retrieval pipeline.</p>
                    <p className="text-xs mt-1">Results show ranked chunks with per-method scores.</p>
                </div>
            )}
        </div>
    );
};
