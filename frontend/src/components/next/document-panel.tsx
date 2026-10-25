import React, { FunctionComponent, useEffect, useMemo, useRef, useState } from "react";
import { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { Check, ChevronRight, ExternalLink } from "lucide-react";
import { cn } from "@/lib/lorem";
import { ERROR_ID, LOADING_ID, useMessaging } from "@/hooks/useMessaging";
import { documentsForAnswer, RetrievedChunk, RetrievedDocument } from "@/lib/retrieved";
import { resolveSourceUrl } from "@/lib/sources";
import { useDocumentPanel } from "@/components/next/document-panel-context";
import { useContextBudget } from "@/components/next/use-context-budget";

/** Shown in full up to this length; longer chunks start clamped. */
const CLAMP_CHARS = 420;

/** "3 documents · 1 cited": the same words under an answer and in the panel header. */
export const documentSummary = (t: TFunction<"app">, documents: RetrievedDocument[]): string =>
    `${t("next.documentsButton", { count: documents.length })} · ${t("next.citedCount", { count: documents.filter((d) => d.citedCount > 0).length })}`;

export const MethodPill: FunctionComponent<{ kind: "semantic" | "keyword" }> = ({ kind }) => {
    const { t } = useTranslation("app");
    return (
        <span
            className={cn(
                "rounded-full px-2 text-[11px] font-medium leading-5",
                kind === "semantic" ? "bg-cyan-500/10 text-cyan-700 dark:text-cyan-300" : "bg-amber-500/15 text-amber-800 dark:text-amber-300",
            )}
        >
            {kind === "semantic" ? t("next.semantic") : t("next.keyword")}
        </span>
    );
};

/** One square per chunk: filled when cited, outlined when sent, dashed and faint when the reranker discarded it. */
const ChunkDot: FunctionComponent<{ chunk: RetrievedChunk }> = ({ chunk }) => (
    <span
        className={cn(
            "h-2.5 w-2.5 rounded-[3px] border-[1.5px]",
            chunk.cited ? "border-primary bg-primary" : chunk.sent ? "border-muted-foreground/60" : "border-dashed border-muted-foreground/40",
        )}
    />
);

const ChunkView: FunctionComponent<{ chunk: RetrievedChunk }> = ({ chunk }) => {
    const { t } = useTranslation("app");
    const long = chunk.content.length > CLAMP_CHARS;
    const [clamped, setClamped] = useState(long);
    const { ranks, position, sent } = chunk;

    const rankParts = sent ? [t("next.rankContext", { rank: position })] : [];
    if (ranks.semantic != null) rankParts.push(t("next.rankSemantic", { rank: ranks.semantic }));
    if (ranks.bm25 != null) rankParts.push(t("next.rankKeyword", { rank: ranks.bm25 }));
    if (ranks.pre_rerank != null) {
        if (!sent) rankParts.push(t("next.rerankCut", { rank: ranks.pre_rerank }));
        else if (ranks.pre_rerank > position) rankParts.push(t("next.rerankUp", { rank: ranks.pre_rerank }));
        else if (ranks.pre_rerank < position) rankParts.push(t("next.rerankDown", { rank: ranks.pre_rerank }));
    }

    return (
        <div
            className={cn(
                "flex flex-col gap-1.5 rounded-lg border bg-background px-3 py-2.5",
                chunk.cited ? "border-primary" : sent ? "border-border" : "border-dashed border-border",
            )}
        >
            <div className="flex items-start gap-2">
                <div className="flex-1 text-[13px] font-semibold leading-snug">{chunk.title.replace(/^#+\s*/, "")}</div>
                {chunk.cited && (
                    <span className="flex shrink-0 items-center gap-1 rounded-full bg-primary px-2 text-[11px] font-medium leading-5 text-primary-foreground">
                        <Check className="h-3 w-3" strokeWidth={3} />
                        {t("next.cited")}
                    </span>
                )}
                {!sent && <span className="shrink-0 rounded-full bg-muted px-2 text-[11px] font-medium leading-5 text-muted-foreground">{t("next.notSent")}</span>}
            </div>
            {rankParts.length > 0 && <div className="font-mono text-[11px] text-muted-foreground">{rankParts.join(" · ")}</div>}
            {chunk.mimeType.startsWith("image/") ? (
                // An inline base64 image: next/image has nothing to optimise or cache here.
                // eslint-disable-next-line @next/next/no-img-element
                <img src={`data:${chunk.mimeType};base64,${chunk.content}`} alt={chunk.title} className="h-auto max-w-full rounded" />
            ) : (
                <div className={cn("whitespace-pre-wrap text-[13px] leading-relaxed", clamped && "line-clamp-6", !sent && "text-muted-foreground")}>{chunk.content}</div>
            )}
            {long && !chunk.mimeType.startsWith("image/") && (
                <button type="button" onClick={() => setClamped((v) => !v)} className="self-start text-xs font-medium text-primary hover:underline">
                    {clamped ? t("next.showMore") : t("next.showLess")}
                </button>
            )}
        </div>
    );
};

const DocumentTile: FunctionComponent<{ doc: RetrievedDocument }> = ({ doc }) => {
    const { t } = useTranslation("app");
    const { expandedFile, setExpandedFile, selected, toggleSelected } = useDocumentPanel();
    const ref = useRef<HTMLDivElement>(null);
    const expanded = expandedFile === doc.file;
    const isSelected = selected.includes(doc.file);
    const onlyDiscarded = doc.sentCount === 0;
    const discardedCount = doc.chunks.length - doc.sentCount;
    const ext = (doc.file.split(".").pop() ?? "").slice(0, 4).toUpperCase();

    // A citation can open a tile far down the list.
    useEffect(() => {
        if (expanded) ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }, [expanded]);

    return (
        <div ref={ref} className={cn("rounded-xl border bg-card", expanded || isSelected ? "border-primary" : "border-border", onlyDiscarded && !expanded && "opacity-70")}>
            <div className="flex items-start gap-2.5 p-3 pl-3.5">
                <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => toggleSelected(doc.file)}
                    aria-label={t("next.selectDocument", { name: doc.file })}
                    className="mt-1 h-4 w-4 shrink-0 accent-primary"
                />
                <button
                    type="button"
                    onClick={() => setExpandedFile(expanded ? null : doc.file)}
                    aria-expanded={expanded}
                    className="flex min-w-0 flex-1 flex-col gap-2 text-left"
                >
                    <div className="flex w-full items-start gap-2">
                        {ext && (
                            <span className="mt-px shrink-0 rounded border border-border px-1 text-[10px] font-semibold leading-[18px] text-muted-foreground">{ext}</span>
                        )}
                        <span className="min-w-0 flex-1 text-sm font-medium leading-snug [overflow-wrap:anywhere]">{doc.file}</span>
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5">
                        <span className="flex gap-[3px]">
                            {doc.chunks.map((c) => (
                                <ChunkDot key={c.id} chunk={c} />
                            ))}
                        </span>
                        <span className="mr-1 text-xs text-muted-foreground">
                            {onlyDiscarded ? t("next.chunksDiscarded", { count: discardedCount }) : t("next.chunks", { count: doc.sentCount })}
                            {!onlyDiscarded && discardedCount > 0 && ` ${t("next.chunksDiscarded", { count: discardedCount })}`}
                        </span>
                        {doc.semantic && <MethodPill kind="semantic" />}
                        {doc.keyword && <MethodPill kind="keyword" />}
                    </div>
                </button>
                <div className="flex shrink-0 items-center">
                    <a
                        href={resolveSourceUrl(doc.file, doc.chunks[0]?.metadata)}
                        target="_blank"
                        rel="noopener noreferrer"
                        aria-label={t("next.openDocument")}
                        title={t("next.openDocument")}
                        className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-primary"
                    >
                        <ExternalLink className="h-4 w-4" />
                    </a>
                    <button
                        type="button"
                        onClick={() => setExpandedFile(expanded ? null : doc.file)}
                        aria-hidden="true"
                        tabIndex={-1}
                        className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted"
                    >
                        <ChevronRight className={cn("h-4 w-4 transition-transform", expanded && "rotate-90")} />
                    </button>
                </div>
            </div>
            {expanded && (
                <div className="flex flex-col gap-2 px-3 pb-3">
                    {doc.chunks.map((c) => (
                        <ChunkView key={c.id} chunk={c} />
                    ))}
                </div>
            )}
        </div>
    );
};

const SelectionBar: FunctionComponent = () => {
    const { t } = useTranslation("app");
    const { selected, clearSelection } = useDocumentPanel();
    const { setExpandContextFiles, setChatOnly } = useMessaging();
    const { tokens, budget, over } = useContextBudget(selected);

    return (
        <div className="flex shrink-0 flex-col gap-2 border-t border-border bg-card px-4 py-3">
            <div className="flex items-baseline justify-between gap-2 text-[13px] text-muted-foreground">
                <span>{t("next.selected", { count: selected.length })}</span>
                {tokens !== null && budget !== null && (
                    <span className={cn("font-mono text-[11px]", over && "text-destructive")}>
                        {t("next.budget", { tokens, budget })}
                    </span>
                )}
            </div>
            {over && <div className="text-xs text-destructive">{t("next.overBudget")}</div>}
            <div className="flex justify-end gap-2">
                <button type="button" onClick={clearSelection} className="h-9 rounded-lg px-3 text-[13px] hover:bg-muted">
                    {t("next.clearSelection")}
                </button>
                <button
                    type="button"
                    disabled={over}
                    onClick={() => {
                        // Chat-only skips every document, which would silently ignore this selection.
                        setChatOnly(false);
                        setExpandContextFiles(selected);
                        clearSelection();
                    }}
                    className="h-9 rounded-lg bg-primary px-3.5 text-[13px] font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
                >
                    {t("next.askWithSelected")}
                </button>
            </div>
        </div>
    );
};

const Legend: FunctionComponent<{ withDiscarded: boolean }> = ({ withDiscarded }) => {
    const { t } = useTranslation("app");
    const item = (dot: string, label: string) => (
        <span className="flex items-center gap-1">
            <span className={cn("h-2 w-2 rounded-[2px]", dot)} />
            {label}
        </span>
    );
    return (
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-muted-foreground">
            {item("bg-primary", t("next.legendCited"))}
            {item("border-[1.5px] border-muted-foreground/60", t("next.legendSent"))}
            {withDiscarded && item("border-[1.5px] border-dashed border-muted-foreground/40", t("next.legendDiscarded"))}
        </div>
    );
};

export const DocumentPanel: FunctionComponent = () => {
    const { t } = useTranslation("app");
    const { thread, sending: waiting } = useMessaging();
    const { messageId, selected } = useDocumentPanel();
    const [citedOnly, setCitedOnly] = useState(false);

    const answers = useMemo(
        () => thread.filter((m) => m.role === "assistant" && m.id && m.id !== LOADING_ID && m.id !== ERROR_ID),
        [thread],
    );
    // An answer being written has no id yet; its documents come with its first chunk,
    // its citations only with the last.
    const last = thread[thread.length - 1];
    const live = waiting && last?.role === "assistant" && !last.id && last.retrieved?.length ? last : undefined;
    const shown = answers.find((m) => m.id === messageId) ?? (waiting ? live : answers[answers.length - 1]);
    const shownIndex = shown === live ? answers.length : answers.indexOf(shown!);
    const { documents, discarded, chunkCount, recorded } = useMemo(
        () => (shown ? documentsForAnswer(shown) : { documents: [], discarded: [], chunkCount: 0, recorded: true }),
        [shown],
    );
    const citedDocs = documents.filter((d) => d.citedCount > 0);
    const visible = citedOnly ? citedDocs : documents;
    const hasAny = documents.length + discarded.length > 0;

    const segment = (active: boolean) =>
        cn("h-7 rounded-md px-3 text-[13px] font-medium", active ? "bg-card text-foreground shadow-xs" : "text-muted-foreground hover:text-foreground");

    return (
        <aside aria-label={t("next.documents")} className="flex h-full w-[512px] shrink-0 flex-col border-l border-border bg-muted/50">
            <div className="flex h-14 shrink-0 items-center gap-2 border-b border-border px-4">
                <h2 className="text-base font-semibold">{t("next.documents")}</h2>
                {shown && documents.length > 0 && (
                    <span className="truncate text-[13px] text-muted-foreground">
                        {[
                            t("next.answerLabel", { index: shownIndex + 1 }),
                            documentSummary(t, documents),
                            t("next.chunks", { count: chunkCount }),
                        ].join(" · ")}
                    </span>
                )}
            </div>

            <div className="flex flex-1 flex-col gap-2 overflow-y-auto p-3">
                {waiting && !shown ? (
                    <p className="px-1 py-2 text-sm text-muted-foreground">{t("next.panelSearching")}</p>
                ) : !shown ? (
                    <p className="px-1 py-2 text-sm text-muted-foreground">{t("next.panelEmpty")}</p>
                ) : !hasAny ? (
                    <p className="px-1 py-2 text-sm text-muted-foreground">{t("next.panelNoDocuments")}</p>
                ) : (
                    <>
                        <div className="flex flex-wrap items-center justify-between gap-2 px-1 pb-1">
                            <div role="group" className="flex rounded-lg bg-muted p-[3px]">
                                <button type="button" onClick={() => setCitedOnly(false)} className={segment(!citedOnly)}>
                                    {t("next.filterAll", { count: documents.length })}
                                </button>
                                <button type="button" onClick={() => setCitedOnly(true)} className={segment(citedOnly)}>
                                    {t("next.filterCited", { count: citedDocs.length })}
                                </button>
                            </div>
                            <Legend withDiscarded={discarded.length > 0 || documents.some((d) => d.sentCount < d.chunks.length)} />
                        </div>
                        {!recorded && <p className="px-1 pb-1 text-xs text-muted-foreground">{t("next.notRecorded")}</p>}
                        {shown === live && <p className="px-1 pb-1 text-xs text-muted-foreground">{t("next.citationsPending")}</p>}
                        {visible.map((doc) => (
                            <DocumentTile key={doc.file} doc={doc} />
                        ))}
                        {!citedOnly && discarded.length > 0 && (
                            <>
                                <div className="flex flex-col gap-0.5 px-1 pt-4">
                                    <h3 className="text-xs font-semibold text-muted-foreground">{t("next.discardedSection", { count: discarded.length })}</h3>
                                    <p className="text-xs text-muted-foreground">{t("next.discardedHint")}</p>
                                </div>
                                {discarded.map((doc) => (
                                    <DocumentTile key={doc.file} doc={doc} />
                                ))}
                            </>
                        )}
                    </>
                )}
            </div>

            {selected.length > 0 && <SelectionBar />}
        </aside>
    );
};
