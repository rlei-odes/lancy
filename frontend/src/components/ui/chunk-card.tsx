"use client";

import React, { FunctionComponent, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/lorem";

export interface ChunkScores {
    semantic_score?: number | null;
    bm25_score?: number | null;
    rrf_score?: number | null;
    pre_rerank_rank?: number | null;
}

export interface ChunkData {
    id: string;
    content: string;
    metadata: Record<string, unknown>;
    final_rank: number;
    scores: ChunkScores;
}

interface Props {
    chunk: ChunkData;
    topK: number;
    rerankingActive: boolean;
    rerankingSkipped?: boolean;
    dimmed?: boolean;
    dimLabel?: string;
}

function ScoreBadge({ label, value, color }: { label: string; value: string; color: string }) {
    return (
        <span className={cn("inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-medium border", color)}>
            <span className="opacity-60">{label}</span>
            <span>{value}</span>
        </span>
    );
}

function fmt(n: number, decimals = 3) {
    return n.toFixed(decimals);
}

export const ChunkCard: FunctionComponent<Props> = ({
    chunk,
    topK,
    rerankingActive,
    rerankingSkipped = false,
    dimmed = false,
    dimLabel,
}) => {
    const { t } = useTranslation("app");
    const [expanded, setExpanded] = useState(false);

    const { scores, final_rank, content, metadata } = chunk;
    const showPreRank =
        rerankingActive &&
        !rerankingSkipped &&
        scores.pre_rerank_rank != null;

    const metaEntries = Object.entries(metadata).filter(([, v]) => v != null && v !== "");

    return (
        <div
            className={cn(
                "group relative flex gap-3 rounded-lg border border-border bg-card transition-all duration-200",
                dimmed ? "opacity-40" : "opacity-100",
                "hover:border-border/80 hover:shadow-sm"
            )}
        >
            {/* Rank column */}
            <div className="flex flex-col items-center justify-start pt-3 pl-4 pr-1 shrink-0">
                <span
                    className={cn(
                        "font-mono font-bold text-2xl leading-none tabular-nums",
                        final_rank <= topK ? "text-foreground" : "text-muted-foreground"
                    )}
                >
                    {String(final_rank).padStart(2, "0")}
                </span>
                {dimmed && dimLabel && (
                    <span className="mt-1.5 text-[9px] font-mono uppercase tracking-wider text-muted-foreground text-center leading-tight max-w-[40px]">
                        {dimLabel}
                    </span>
                )}
            </div>

            {/* Content column */}
            <div className="flex flex-col flex-1 py-3 pr-4 min-w-0">
                {/* Score badges */}
                <div className="flex flex-wrap gap-1.5 mb-2.5">
                    {scores.semantic_score != null && (
                        <ScoreBadge
                            label="SEM"
                            value={fmt(scores.semantic_score)}
                            color="bg-cyan-500/10 text-cyan-600 dark:text-cyan-400 border-cyan-500/25"
                        />
                    )}
                    {scores.bm25_score != null && (
                        <ScoreBadge
                            label="BM25"
                            value={fmt(scores.bm25_score, 2)}
                            color="bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/25"
                        />
                    )}
                    {scores.rrf_score != null && (
                        <ScoreBadge
                            label="RRF"
                            value={fmt(scores.rrf_score, 4)}
                            color="bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/25"
                        />
                    )}
                    {showPreRank && scores.pre_rerank_rank != null && (
                        <ScoreBadge
                            label="PRE-RANK"
                            value={`#${scores.pre_rerank_rank}`}
                            color="bg-slate-500/10 text-slate-600 dark:text-slate-400 border-slate-500/25"
                        />
                    )}
                </div>

                {/* Chunk text */}
                <div
                    className="cursor-pointer"
                    onClick={() => setExpanded((v) => !v)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => e.key === "Enter" && setExpanded((v) => !v)}
                >
                    <p
                        className={cn(
                            "text-sm text-foreground/90 leading-relaxed whitespace-pre-wrap break-words font-mono text-[12px]",
                            !expanded && "line-clamp-4"
                        )}
                    >
                        {content}
                    </p>
                    <button
                        className="mt-1.5 flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
                        onClick={(e) => { e.stopPropagation(); setExpanded((v) => !v); }}
                    >
                        {expanded ? (
                            <><ChevronUp className="h-3 w-3" /> {t("explorer.collapse")}</>
                        ) : (
                            <><ChevronDown className="h-3 w-3" /> {t("explorer.expand")}</>
                        )}
                    </button>
                </div>

                {/* Metadata */}
                {metaEntries.length > 0 && (
                    <div className="mt-3 pt-2.5 border-t border-border/60 flex flex-wrap gap-x-4 gap-y-1">
                        {metaEntries.map(([key, val]) => (
                            <span key={key} className="text-[11px] font-mono text-muted-foreground">
                                <span className="text-muted-foreground/60">{key}:</span>{" "}
                                <span className="text-foreground/70">{String(val)}</span>
                            </span>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
};
