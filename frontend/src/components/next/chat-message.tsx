import React, { FunctionComponent, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { BookOpen, Copy, Check, FileText, Pencil, RefreshCcw, ThumbsDown, ThumbsUp } from "lucide-react";
import { cn } from "@/lib/lorem";
import { ERROR_ID, LOADING_ID, useMessaging } from "@/hooks/useMessaging";
import { useBranding } from "@/hooks/useBranding";
import { Message, MessageTypes, Reaction } from "@/services/message";
import { Avatar, AvatarImage } from "@/components/ui/avatar";
import { Markdown } from "@/components/ui/markdown";
import { MessageEdit } from "@/components/ui/message-edit";
import { ThreadNavigation } from "@/components/ui/thread-navigation";
import { documentsForAnswer } from "@/lib/retrieved";
import { useDocumentPanel } from "@/components/next/document-panel-context";
import { documentSummary } from "@/components/next/document-panel";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";

/** A hover card naming documents, marking those the answer cited. */
const DocumentList: FunctionComponent<{ heading: string; files: { file: string; cited?: boolean }[]; children: React.ReactElement }> = ({
    heading,
    files,
    children,
}) => {
    const { t } = useTranslation("app");
    return (
        <HoverCard openDelay={200} closeDelay={100}>
            <HoverCardTrigger asChild>{children}</HoverCardTrigger>
            <HoverCardContent align="start" className="w-80 p-3">
                <div className="mb-1.5 text-xs font-medium text-muted-foreground">{heading}</div>
                <ul className="flex flex-col gap-1">
                    {files.map(({ file, cited }) => (
                        <li key={file} className="flex items-center gap-2 text-[13px]">
                            <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                            <span className="flex-1 truncate" title={file}>{file}</span>
                            {cited && <span className="shrink-0 text-[11px] font-medium text-primary">{t("next.cited")}</span>}
                        </li>
                    ))}
                </ul>
            </HoverCardContent>
        </HoverCard>
    );
};

const iconButton = "flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground";

const QUERY_PHASES = {
    preprocessing: "rag.queryPhasePreprocessing",
    retrieving: "rag.queryPhaseRetrieving",
    reranking: "rag.queryPhaseReranking",
    generating: "rag.queryPhaseGenerating",
    reading: "rag.queryPhaseReading",
    merging: "rag.queryPhaseMerging",
} as const;

/** What the backend is doing while the answer has not started streaming. */
const QueryPhase: FunctionComponent = () => {
    const { t } = useTranslation("app");
    const [status, setStatus] = useState<{ phase: string; pass: number; passes: number }>({ phase: "idle", pass: 0, passes: 0 });
    const phaseKey = QUERY_PHASES[status.phase as keyof typeof QUERY_PHASES];

    useEffect(() => {
        const interval = setInterval(() => {
            fetch("/api/v1/rag/query-status")
                .then((r) => r.json())
                .then((data) => setStatus({ phase: data.phase ?? "idle", pass: data.pass ?? 0, passes: data.passes ?? 0 }))
                .catch(() => {});
        }, 500);
        return () => clearInterval(interval);
    }, []);

    return (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
            {phaseKey && <span>{t(phaseKey, { pass: status.pass, passes: status.passes })}</span>}
            <span className="blink h-3 w-3 rounded-full bg-foreground" />
        </div>
    );
};

const MetaLine: FunctionComponent<{ message: Message; kbName?: string }> = ({ message, kbName }) => {
    const { retrieval_stats: stats, llm_model, query_duration_ms, tokens_per_second } = message;
    const parts: string[] = [];
    if (kbName) parts.push(kbName);
    if (stats) {
        parts.push(stats.reranker_active ? `${stats.candidates_retrieved} → ${stats.chunks_to_llm} chunks` : `${stats.chunks_to_llm} chunks`);
        if (stats.reranker_active) parts.push(`${stats.reranker_swaps} swap${stats.reranker_swaps !== 1 ? "s" : ""}`);
    }
    if (llm_model) parts.push(llm_model.replace(/\s+T=[\d.]+/g, ""));
    if (query_duration_ms != null) parts.push(`${(query_duration_ms / 1000).toFixed(1)} s`);
    if (tokens_per_second != null) parts.push(`${tokens_per_second.toFixed(1)} tok/s`);
    if (parts.length === 0) return null;

    return (
        <div className="select-none font-mono text-[11px] text-muted-foreground/80">
            {parts.join(" · ")}
            {stats?.reranker_fallback && (
                <span
                    className="text-amber-600 dark:text-amber-400"
                    title="The reranking model returned an unusable response. Sources are in the retriever's original order."
                >
                    {" · rerank failed — original order"}
                </span>
            )}
        </div>
    );
};

const Reactions: FunctionComponent<{ messageId: string; reaction?: Reaction }> = ({ messageId, reaction }) => {
    const { t } = useTranslation("app");
    const { reactToMessage } = useMessaging();
    const [local, setLocal] = useState<Reaction["content"] | null>(null);
    const current = local ?? reaction?.content ?? null;

    const react = (content: Reaction["content"]) => {
        if (current) return;
        reactToMessage(messageId, content);
        setLocal(content);
    };

    return (
        <>
            <button type="button" aria-label={t("next.goodAnswer")} disabled={!!current} onClick={() => react(":thumbsup:")} className={iconButton}>
                <ThumbsUp className={cn("h-4 w-4", current === ":thumbsup:" && "fill-current text-primary")} />
            </button>
            <button type="button" aria-label={t("next.badAnswer")} disabled={!!current} onClick={() => react(":thumbsdown:")} className={iconButton}>
                <ThumbsDown className={cn("h-4 w-4", current === ":thumbsdown:" && "fill-current text-primary")} />
            </button>
        </>
    );
};

const CopyButton: FunctionComponent<{ text: string }> = ({ text }) => {
    const { t } = useTranslation("app");
    const [copied, setCopied] = useState(false);

    const copy = () => {
        navigator.clipboard
            .writeText(text)
            .then(() => {
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
            })
            .catch((e) => console.warn("[copy] clipboard write failed:", e));
    };

    return (
        <button type="button" aria-label={copied ? t("next.copied") : t("next.copy")} title={copied ? t("next.copied") : t("next.copy")} onClick={copy} className={iconButton}>
            {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
        </button>
    );
};

const UserMessage: FunctionComponent<{ message: Message }> = ({ message }) => {
    const { t } = useTranslation("app");
    const { sendMessage } = useMessaging();
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState(message.content);

    if (editing) {
        return (
            <MessageEdit
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onCancel={() => setEditing(false)}
                onPressEnter={() => {
                    setEditing(false);
                    sendMessage(draft, MessageTypes.NEXT, message.parent_id);
                }}
            />
        );
    }

    const onlyThese = message.expand_context ?? [];

    return (
        <div className="group flex flex-col items-end gap-1">
            <div className="flex max-w-[80%] items-start gap-2">
                {onlyThese.length > 0 && (
                    <DocumentList heading={t("next.onlyTheseDocuments", { count: onlyThese.length })} files={onlyThese.map((file) => ({ file }))}>
                        <span
                            tabIndex={0}
                            aria-label={t("next.onlyTheseDocuments", { count: onlyThese.length })}
                            className="mt-2 flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                        >
                            <BookOpen className="h-4 w-4" />
                        </span>
                    </DocumentList>
                )}
                <div className="min-w-0 whitespace-pre-wrap rounded-2xl bg-muted px-4 py-2.5 [overflow-wrap:anywhere]">{message.content}</div>
            </div>
            <div className="flex items-center gap-1 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                <ThreadNavigation messageId={message.id} />
                <button type="button" aria-label={t("next.edit")} onClick={() => setEditing(true)} className={iconButton}>
                    <Pencil className="h-4 w-4" />
                </button>
            </div>
        </div>
    );
};

const AssistantMessage: FunctionComponent<{ message: Message; isLast: boolean; kbName?: string }> = ({ message, isLast, kbName }) => {
    const { t } = useTranslation("app");
    const { agentAvatarUrl, agentName } = useBranding();
    const { sendMessage, sending, cursor } = useMessaging();
    const { open, messageId, showDocuments, flash } = useDocumentPanel();
    const rootRef = useRef<HTMLDivElement>(null);
    const flashed = flash?.id === message.id ? flash.key : null;

    // Stepped to from the documents panel: bring this answer into view.
    useEffect(() => {
        if (flashed !== null) rootRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }, [flashed]);
    const [followUps, setFollowUps] = useState(message.follow_up_questions);
    const [syncedFollowUps, setSyncedFollowUps] = useState(message.follow_up_questions);

    // Follow-ups arrive with the last stream chunk; clicking one clears them. Synced
    // during render, as in the classic message item.
    if (message.follow_up_questions !== syncedFollowUps) {
        setSyncedFollowUps(message.follow_up_questions);
        if (message.follow_up_questions?.length) setFollowUps(message.follow_up_questions);
    }

    const isLoading = message.id === LOADING_ID;
    const isError = message.id === ERROR_ID;
    const isSaved = !!message.id && !isLoading && !isError;
    const documents = isSaved ? documentsForAnswer(message).documents : [];
    // Mirrors the panel: with no answer picked it follows the latest one.
    const panelShowsThis = open && (messageId === message.id || (messageId === null && isLast));

    return (
        <div ref={rootRef} className="group relative flex gap-3.5 scroll-my-6">
            {/* A new key remounts the tint, so stepping back to the same answer shows it again. */}
            {flashed !== null && <span key={flashed} aria-hidden className="pointer-events-none absolute -inset-2 rounded-xl bg-primary/10 answer-flash" />}
            <Avatar className="h-7 w-7 rounded-lg border border-border bg-white p-0.5 dark:bg-white">
                <AvatarImage src={agentAvatarUrl} alt={agentName} className="object-contain" />
            </Avatar>
            <div className="flex min-w-0 flex-1 flex-col gap-2">
                {isLoading ? (
                    <QueryPhase />
                ) : isError ? (
                    <div className="rounded-lg border border-destructive bg-destructive/15 p-4">{message.content}</div>
                ) : (
                    <div className="[&>p:last-child]:mb-0">
                        <Markdown content={message.content} sources={message.sources} onCitationClick={isSaved ? (file) => showDocuments(message.id, file) : undefined} />
                    </div>
                )}

                {isSaved && (
                    <div className="flex flex-wrap items-center gap-1">
                        {/* Stays on every answer: in a long conversation it says which turn used which documents. */}
                        {documents.length > 0 && (
                            <DocumentList
                                heading={t("next.documentsOfAnswer")}
                                files={documents.map((d) => ({ file: d.file, cited: d.citedCount > 0 }))}
                            >
                                <button
                                    type="button"
                                    onClick={() => showDocuments(message.id)}
                                    className={cn(
                                        "flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-[13px] font-medium",
                                        panelShowsThis ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground",
                                    )}
                                >
                                    <FileText className="h-4 w-4" />
                                    {documentSummary(t, documents)}
                                </button>
                            </DocumentList>
                        )}
                        <div className={cn("flex flex-wrap items-center gap-1 transition-opacity", !isLast && "opacity-0 group-hover:opacity-100 focus-within:opacity-100")}>
                            <ThreadNavigation messageId={message.id} />
                            <CopyButton text={message.content} />
                            <Reactions messageId={message.id} reaction={message.reaction} />
                            {message.parent_id && (
                                <button
                                    type="button"
                                    aria-label={t("next.regenerate")}
                                    title={t("next.regenerate")}
                                    disabled={sending}
                                    onClick={() => sendMessage(message.parent_id!, MessageTypes.REDO, message.parent_id)}
                                    className={iconButton}
                                >
                                    <RefreshCcw className="h-4 w-4" />
                                </button>
                            )}
                        </div>
                    </div>
                )}

                {isSaved && <MetaLine message={message} kbName={message.kb_name ?? kbName} />}

                {isLast && !!followUps?.length && (
                    <div className="flex flex-wrap gap-2 pt-1">
                        {followUps.map((question) => (
                            <button
                                key={question}
                                type="button"
                                disabled={sending}
                                onClick={() => {
                                    setFollowUps([]);
                                    sendMessage(question, MessageTypes.NEXT, cursor);
                                }}
                                className="rounded-full border border-border px-3 py-1.5 text-left text-[13px] hover:bg-muted"
                            >
                                {question}
                            </button>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
};

export const ChatMessage: FunctionComponent<{ message: Message; isLast: boolean; kbName?: string }> = ({ message, isLast, kbName }) =>
    message.role === "user" ? <UserMessage message={message} /> : <AssistantMessage message={message} isLast={isLast} kbName={kbName} />;
