import React, { FunctionComponent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Copy, Check, FileText, Pencil, RefreshCcw, ThumbsDown, ThumbsUp } from "lucide-react";
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

const iconButton = "flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground";

const QUERY_PHASES = {
    preprocessing: "rag.queryPhasePreprocessing",
    retrieving: "rag.queryPhaseRetrieving",
    reranking: "rag.queryPhaseReranking",
    generating: "rag.queryPhaseGenerating",
} as const;

/** What the backend is doing while the answer has not started streaming. */
const QueryPhase: FunctionComponent = () => {
    const { t } = useTranslation("app");
    const [phase, setPhase] = useState<string>("idle");
    const phaseKey = QUERY_PHASES[phase as keyof typeof QUERY_PHASES];

    useEffect(() => {
        const interval = setInterval(() => {
            fetch("/api/v1/rag/query-status")
                .then((r) => r.json())
                .then((data) => setPhase(data.phase ?? "idle"))
                .catch(() => {});
        }, 500);
        return () => clearInterval(interval);
    }, []);

    return (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
            {phaseKey && <span>{t(phaseKey)}</span>}
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

    return (
        <div className="group flex flex-col items-end gap-1">
            <div className="max-w-[80%] whitespace-pre-wrap rounded-2xl bg-muted px-4 py-2.5 [overflow-wrap:anywhere]">{message.content}</div>
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
    const { open, messageId, showDocuments } = useDocumentPanel();
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
        <div className="group flex gap-3.5">
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
                    <div className={cn("flex flex-wrap items-center gap-1 transition-opacity", !isLast && "opacity-0 group-hover:opacity-100 focus-within:opacity-100")}>
                        {documents.length > 0 && (
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
                        )}
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
