import React, { FunctionComponent, useEffect, useRef, useState } from "react";
import { CheckCircle2, Loader2, Square } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useRole } from "@/hooks/useRole";

const API_BASE = "";
const DONE_VISIBLE_MS = 8000; // How long the "finished" banner stays visible

interface IndexStatus {
    indexing: boolean;
    phase: string;
    current_file: string;
    file_index: number;
    total_files: number;
    chunks_so_far: number;
    embed_batch: number;
    embed_total_batches: number;
    caption_index: number;
    caption_total: number;
    kb_name: string;
    finished_at: string;
    last_result?: { reset: boolean } | null;
    queued: number;
    captioning_enabled: boolean;
}

export const IndexingStatus: FunctionComponent = () => {
    const { t } = useTranslation("app");
    const { role } = useRole();
    const isAdmin = role !== "user";
    const [status, setStatus] = useState<IndexStatus | null>(null);
    const [showDone, setShowDone] = useState(false);
    const [confirmStop, setConfirmStop] = useState(false);
    const [cancelling, setCancelling] = useState(false);
    const doneTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const prevFinishedAt = useRef<string>("");
    const isFirstPoll = useRef(true);

    const handleStop = async () => {
        await fetch(`${API_BASE}/api/v1/rag/reindex-cancel`, { method: "POST", credentials: "include" });
        setConfirmStop(false);
        setCancelling(true);
    };

    useEffect(() => {
        let active = true;
        let timer: ReturnType<typeof setTimeout>;
        const isIndexingRef = { current: false };

        const poll = async () => {
            try {
                const r = await fetch(`${API_BASE}/api/v1/rag/reindex-status`, { credentials: "include" });
                if (r.ok) {
                    const data: IndexStatus = await r.json();
                    if (!active) return;
                    isIndexingRef.current = data.indexing;
                    setStatus(data);
                    if (!data.indexing) setCancelling(false);

                    // When indexing just finished (finished_at changed), show done banner.
                    // Skip on first poll — finished_at may be stale from a previous run.
                    if (!data.indexing && data.finished_at && data.finished_at !== prevFinishedAt.current) {
                        if (!isFirstPoll.current) {
                            setShowDone(true);
                            if (doneTimer.current) clearTimeout(doneTimer.current);
                            doneTimer.current = setTimeout(() => setShowDone(false), DONE_VISIBLE_MS);
                        }
                        prevFinishedAt.current = data.finished_at;
                    }
                    isFirstPoll.current = false;
                }
            } catch { /* ignore */ }
            if (active) {
                timer = setTimeout(poll, isIndexingRef.current ? 5000 : 15000);
            }
        };

        poll();
        return () => {
            active = false;
            clearTimeout(timer);
            if (doneTimer.current) clearTimeout(doneTimer.current);
        };
    }, []);

    // Show progress banner while indexing
    if (status?.indexing) {
        const isEmbedding = status.phase === "embedding";
        const isCaptioning = status.phase === "captioning";
        // Primary progress: file_index/total_files — monotonically increasing for folder scans.
        // Single-file uploads (total_files === 1) sit at 100% since file_index is already 1.
        const pct = status.total_files > 0
            ? Math.round((status.file_index / status.total_files) * 100)
            : 0;
        // Show "File i/N · " prefix only when processing multiple files.
        const filePrefix = status.total_files > 1
            ? `${status.file_index}/${status.total_files} · `
            : "";
        const phaseLabel = cancelling
            ? t("rag.indexingCancelling")
            : isCaptioning
                ? filePrefix + t("rag.indexingPhaseCaptioning")
                : isEmbedding
                    ? filePrefix + t("rag.indexingPhaseEmbedding")
                    : filePrefix + t("rag.indexingPhaseLoading");

        return (
            <>
                <div className="mx-2 mb-1 px-3 py-2 rounded-md bg-blue-950/60 border border-blue-800/50 text-xs text-blue-200 space-y-1">
                    <div className="flex items-center gap-1.5 font-medium">
                        <Loader2 className="h-3 w-3 animate-spin shrink-0" />
                        <span className="flex-1 truncate">
                            {phaseLabel}
                            {!cancelling && status.kb_name && (
                                <span className="opacity-60 font-normal"> · {status.kb_name}</span>
                            )}
                        </span>
                        {!cancelling && isAdmin && (
                            <button
                                onClick={() => setConfirmStop(true)}
                                className="shrink-0 flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] bg-red-900/60 hover:bg-red-800/80 text-red-300 transition-colors"
                                title={t("rag.indexingStopTitle")}
                            >
                                <Square className="h-2.5 w-2.5" />
                                {t("rag.indexingStopBtn")}
                            </button>
                        )}
                    </div>
                    {!isEmbedding && !isCaptioning && status.current_file && (
                        <div className="truncate opacity-70" title={status.current_file}>
                            {status.current_file}
                        </div>
                    )}
                    <div className="flex items-center gap-2">
                        <div className="flex-1 h-1 bg-blue-900 rounded-full overflow-hidden">
                            <div
                                className="h-full bg-blue-400 rounded-full transition-all duration-500"
                                style={{ width: `${pct}%` }}
                            />
                        </div>
                        <span className="opacity-70 shrink-0">
                            {isEmbedding
                                ? (status.embed_total_batches > 0 ? `Batch ${status.embed_batch}/${status.embed_total_batches}` : "…")
                                : isCaptioning
                                    ? (status.caption_total > 0 ? `${status.caption_index}/${status.caption_total} images` : "…")
                                    : `${status.chunks_so_far} chunks`
                            }
                            {status.queued > 0 && (
                                <span className="ml-1.5 opacity-60">+{status.queued} queued</span>
                            )}
                        </span>
                    </div>
                </div>
                {confirmStop && isAdmin && (
                    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
                        <div className="bg-background border border-border rounded-lg p-6 mx-4 max-w-sm w-full shadow-xl">
                            <h3 className="text-base font-semibold text-foreground mb-2">{t("rag.indexingStopTitle")}</h3>
                            <p className="text-sm text-muted-foreground mb-5">{t("rag.indexingStopDesc")}</p>
                            <div className="flex justify-end gap-2">
                                <button
                                    className="px-4 py-2 text-sm rounded-md hover:bg-muted transition-colors"
                                    onClick={() => setConfirmStop(false)}
                                >{t("cancel")}</button>
                                <button
                                    className="px-4 py-2 text-sm rounded-md bg-destructive text-destructive-foreground hover:bg-destructive/80 transition-colors"
                                    onClick={handleStop}
                                >{t("confirm")}</button>
                            </div>
                        </div>
                    </div>
                )}
            </>
        );
    }

    // Show done banner briefly after completion
    if (showDone && status?.finished_at) {
        const time = new Date(status.finished_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        const kbLabel = status.kb_name ? ` · ${status.kb_name}` : "";
        const doneKey = status.last_result?.reset === true
            ? "rag.indexingDoneFull"
            : status.last_result?.reset === false
                ? "rag.indexingDoneIncremental"
                : "rag.indexingDone";
        return (
            <div className="mx-2 mb-1 px-3 py-2 rounded-md bg-green-950/60 border border-green-800/50 text-xs text-green-200 flex items-center gap-1.5">
                <CheckCircle2 className="h-3 w-3 shrink-0 text-green-400" />
                <span>{t(doneKey, { kb: kbLabel, time })}</span>
            </div>
        );
    }

    return null;
};
