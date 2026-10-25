"use client";

import React, { FunctionComponent, useCallback, useEffect, useRef, useState } from "react";
import { FlaskConical, Loader2, Search, SlidersHorizontal } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/lorem";
import { ChunkCard, ChunkData } from "@/components/ui/chunk-card";

const API_BASE = "";

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
    query_terms?: string[];
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
    // The session's selected KB; without it the backend searches the pool's active one.
    const kbId = sessionStorage.getItem("lancy_selected_kb_id");
    const body: Record<string, unknown> = { query, bm25_enabled: bm25Enabled, reranking_enabled: rerankingEnabled };
    if (kbId) body.kb_id = kbId;
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

/** A search method switched on or off for this probe only; the saved settings stay as they are. */
function MethodToggle({ label, enabled, color, onToggle, title }: {
    label: string;
    enabled: boolean;
    color: string;
    onToggle: () => void;
    title: string;
}) {
    return (
        <button
            type="button"
            onClick={onToggle}
            aria-pressed={enabled}
            title={title}
            className={cn(
                "inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium border select-none transition-opacity hover:opacity-80",
                enabled
                    ? `${color} border-transparent`
                    : "bg-transparent text-muted-foreground border-border opacity-50"
            )}
        >
            <span className={cn("h-1.5 w-1.5 rounded-full", enabled ? "bg-current" : "bg-muted-foreground")} />
            {label}
        </button>
    );
}

export const RetrievalProbe: FunctionComponent = () => {
    const { t } = useTranslation("app");
    const [query, setQuery] = useState("");
    const [sourceFilter, setSourceFilter] = useState("");
    const [bm25Enabled, setBm25Enabled] = useState(true);
    const [rerankingEnabled, setRerankingEnabled] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    // The methods a result was run with: the toggles may change after the run.
    const [result, setResult] = useState<(RetrieveResponse & { bm25: boolean; reranking: boolean }) | null>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    const syncConfig = useCallback(() => {
        fetchSessionConfig()
            .then((cfg) => {
                setBm25Enabled(cfg.bm25_enabled);
                setRerankingEnabled(cfg.reranking_enabled);
            })
            .catch(() => {});
    }, []);

    // Seed from the saved settings on mount and whenever they are saved; the toggles
    // then change this probe only.
    useEffect(() => {
        syncConfig();
        window.addEventListener("rag-config-saved", syncConfig);
        return () => window.removeEventListener("rag-config-saved", syncConfig);
    }, [syncConfig]);

    // Another KB's results would be misleading next to the new selection.
    useEffect(() => {
        const handler = () => { setResult(null); setError(null); };
        window.addEventListener("lancy-kb-changed", handler);
        return () => window.removeEventListener("lancy-kb-changed", handler);
    }, []);

    const handleSubmit = useCallback(async () => {
        const q = query.trim();
        if (!q) return;
        setLoading(true);
        setError(null);
        try {
            const res = await runProbe(q, bm25Enabled, rerankingEnabled, sourceFilter);
            setResult({ ...res, bm25: bm25Enabled, reranking: rerankingEnabled });
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
            <div className="rounded-xl border border-border bg-card shadow-xs overflow-hidden">
                <div className="flex items-center gap-2 px-4 py-3 border-b border-border bg-muted/30">
                    <FlaskConical className="h-4 w-4 text-muted-foreground" />
                    <span className="text-sm font-medium">{t("explorer.probeTitle")}</span>
                    <span className="ml-auto text-xs text-muted-foreground">{t("explorer.keyboardHint")}</span>
                </div>

                <div className="p-4 flex flex-col gap-3">
                    <textarea
                        ref={textareaRef}
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        onKeyDown={handleKeyDown}
                        placeholder={t("explorer.queryPlaceholder")}
                        rows={3}
                        maxLength={4000}
                        className="w-full resize-none bg-transparent text-sm placeholder:text-muted-foreground/50 outline-hidden leading-relaxed"
                    />

                    <div className="flex flex-wrap items-center gap-3">
                        {/* Method toggles */}
                        <div className="flex items-center gap-1.5">
                            <SlidersHorizontal className="h-3.5 w-3.5 text-muted-foreground" />
                            <MethodToggle
                                label="BM25"
                                enabled={bm25Enabled}
                                onToggle={() => setBm25Enabled((v) => !v)}
                                title={t("explorer.methodToggleHint")}
                                color="bg-amber-500/15 text-amber-600 dark:text-amber-400"
                            />
                            <MethodToggle
                                label="Reranking"
                                enabled={rerankingEnabled}
                                onToggle={() => setRerankingEnabled((v) => !v)}
                                title={t("explorer.methodToggleHint")}
                                color="bg-rose-500/15 text-rose-600 dark:text-rose-400"
                            />
                        </div>

                        {/* Source filter */}
                        <div className="flex items-center gap-1.5 ml-2">
                            <Search className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                            <input
                                type="text"
                                value={sourceFilter}
                                onChange={(e) => setSourceFilter(e.target.value)}
                                placeholder={t("explorer.sourceFilterPlaceholder")}
                                className="text-xs bg-transparent outline-hidden placeholder:text-muted-foreground/40 text-muted-foreground w-44"
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
                                <><Loader2 className="h-3.5 w-3.5 animate-spin" /> {t("explorer.running")}</>
                            ) : (
                                <>{t("explorer.runProbe")}</>
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
                            {t("explorer.resultsSummary", { total: result.total_returned, topK: result.top_k })}
                            {result.reranking_skipped && (
                                <span className="ml-2 text-amber-500">{t("explorer.rerankingSkipped")}</span>
                            )}
                        </span>
                        <div className="flex-1 h-px bg-border" />
                    </div>

                    {/* Keyword terms */}
                    {result.bm25 && result.query_terms && result.query_terms.length > 0 && (
                        <div className="flex flex-wrap items-center gap-1.5 px-1" title={t("explorer.keywordTermsHint")}>
                            <span className="text-xs text-muted-foreground">{t("explorer.keywordTerms")}</span>
                            {result.query_terms.map((term) => (
                                <span
                                    key={term}
                                    className="px-1.5 py-0.5 rounded text-[10px] font-mono border bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/25"
                                >
                                    {term}
                                </span>
                            ))}
                        </div>
                    )}

                    {/* Chunk cards */}
                    <div className="flex flex-col gap-2">
                        {result.chunks.map((chunk, i) => {
                            const isOverK = chunk.final_rank > topK;
                            const dimLabel = result.reranking && !result.reranking_skipped
                                ? t("explorer.dimLabelReranker")
                                : t("explorer.dimLabelOutsideK");
                            return (
                                <React.Fragment key={chunk.id}>
                                    {/* Cutoff divider */}
                                    {isOverK && i > 0 && result.chunks[i - 1].final_rank <= topK && (
                                        <div className="flex items-center gap-3 my-1 px-1">
                                            <div className="flex-1 h-px bg-border/60" />
                                            <span className="text-[10px] font-mono text-muted-foreground/60 uppercase tracking-widest">
                                                {t("explorer.cutoffLabel", { topK })}
                                            </span>
                                            <div className="flex-1 h-px bg-border/60" />
                                        </div>
                                    )}
                                    <ChunkCard
                                        chunk={result.bm25 ? chunk : { ...chunk, matched_terms: [] }}
                                        topK={topK}
                                        rerankingActive={result.reranking && !result.reranking_skipped}
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
                    <p className="text-sm">{t("explorer.emptyStateTitle")}</p>
                    <p className="text-xs mt-1">{t("explorer.emptyStateSubtitle")}</p>
                </div>
            )}
        </div>
    );
};
