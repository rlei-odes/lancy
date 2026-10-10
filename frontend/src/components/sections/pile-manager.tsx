"use client";

import React, { FunctionComponent, useCallback, useEffect, useRef, useState } from "react";
import {
    AlertTriangle,
    CheckCircle2,
    FileStack,
    Loader2,
    Play,
    Plus,
    Trash2,
    Upload,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/lorem";

const API_BASE = "";
const POLL_MS = 2_000;   // while a pile is processing

// Narrows the file dialog only. The backend derives the real allowlist from its
// chunker table and answers 422 for anything else, so this drifting costs a
// confusing dialog, never a bad upload.
const ACCEPT = [
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".md", ".txt", ".epub", ".eml",
    ".png", ".jpg", ".jpeg", ".gif", ".tiff", ".bmp", ".webp",
].join(",");

// ─── Types ────────────────────────────────────────────────────────────────────

export interface PileFile {
    filename: string;
    source_file: string;
    uploaded_at: string;
    size_bytes: number;
}

export interface Pile {
    id: string;
    name: string;
    created_at: string;
    processed_at: string | null;
    status: "draft" | "processing" | "ready" | "stale" | "failed";
    error: string;
    embedding_key: string;
    vs_type: string;
    chunk_count: number;
    file_count: number;
    pending_files: number;
    has_store: boolean;
    embedding_current: boolean;
}

export interface PileDetail extends Pile {
    files: PileFile[];
}

// ─── API ──────────────────────────────────────────────────────────────────────

async function failWith(r: Response, fallback: string): Promise<never> {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail ?? fallback);
}

export async function fetchPiles(): Promise<Pile[]> {
    const r = await fetch(`${API_BASE}/api/v1/piles`, { credentials: "include" });
    if (!r.ok) await failWith(r, "Could not load piles");
    return r.json();
}

export async function fetchPile(id: string): Promise<PileDetail> {
    const r = await fetch(`${API_BASE}/api/v1/piles/${id}`, { credentials: "include" });
    if (!r.ok) await failWith(r, "Could not load pile");
    return r.json();
}

async function createPile(name: string): Promise<Pile> {
    const r = await fetch(`${API_BASE}/api/v1/piles`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
    });
    if (!r.ok) await failWith(r, "Could not create pile");
    return r.json();
}

async function deletePile(id: string): Promise<void> {
    const r = await fetch(`${API_BASE}/api/v1/piles/${id}`, {
        method: "DELETE",
        credentials: "include",
    });
    if (!r.ok) await failWith(r, "Could not delete pile");
}

async function uploadFile(pileId: string, file: File): Promise<void> {
    const form = new FormData();
    form.append("file", file);
    const r = await fetch(`${API_BASE}/api/v1/piles/${pileId}/files`, {
        method: "POST",
        credentials: "include",
        body: form,
    });
    if (!r.ok) await failWith(r, `Upload of ${file.name} failed`);
}

async function deleteFile(pileId: string, filename: string): Promise<void> {
    const r = await fetch(
        `${API_BASE}/api/v1/piles/${pileId}/files/${encodeURIComponent(filename)}`,
        { method: "DELETE", credentials: "include" }
    );
    if (!r.ok) await failWith(r, "Could not remove file");
}

async function processPile(pileId: string): Promise<void> {
    const r = await fetch(`${API_BASE}/api/v1/piles/${pileId}/process`, {
        method: "POST",
        credentials: "include",
    });
    if (!r.ok) await failWith(r, "Could not start processing");
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function formatBytes(n: number): string {
    if (n < 1024) return `${n} B`;
    if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} KB`;
    return `${(n / 1024 ** 2).toFixed(1)} MB`;
}

/** Whether the pile's store reflects the files currently in it. `stale` means the
 *  file set changed after the last run, so its chunks describe a different set. */
function needsProcessing(p: Pile): boolean {
    return p.status === "draft" || p.status === "stale" || p.status === "failed";
}

// ─── Component ────────────────────────────────────────────────────────────────

export const PileManager: FunctionComponent<{ active?: boolean }> = ({ active = true }) => {
    const { t } = useTranslation("app");

    const [piles, setPiles] = useState<Pile[]>([]);
    const [selected, setSelected] = useState<PileDetail | null>(null);
    const [newName, setNewName] = useState("");
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const fileRef = useRef<HTMLInputElement>(null);

    const refresh = useCallback(async (keepId?: string) => {
        const list = await fetchPiles();
        setPiles(list);
        const id = keepId ?? selected?.id;
        if (id && list.some((p) => p.id === id)) {
            setSelected(await fetchPile(id));
        } else if (id) {
            setSelected(null);  // deleted elsewhere
        }
    }, [selected?.id]);

    useEffect(() => {
        if (!active) return;
        setLoading(true);
        refresh()
            .catch((e) => setError(e instanceof Error ? e.message : "Could not load piles"))
            .finally(() => setLoading(false));
        // refresh is recreated whenever the selection changes; depending on it here
        // would re-run this initial load on every click.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [active]);

    // Processing runs in the background on the server, so the only way to learn it
    // finished is to ask. Polling stops as soon as nothing is in flight.
    const anyProcessing = piles.some((p) => p.status === "processing");
    useEffect(() => {
        if (!active || !anyProcessing) return;
        const timer = setInterval(() => { refresh().catch(() => {}); }, POLL_MS);
        return () => clearInterval(timer);
    }, [active, anyProcessing, refresh]);

    const run = async (fn: () => Promise<void>, keepId?: string) => {
        setBusy(true);
        setError(null);
        try {
            await fn();
            await refresh(keepId);
        } catch (e) {
            setError(e instanceof Error ? e.message : "Request failed");
        } finally {
            setBusy(false);
        }
    };

    const handleCreate = async () => {
        const name = newName.trim();
        if (!name) return;
        setBusy(true);
        setError(null);
        try {
            const created = await createPile(name);
            setNewName("");
            // refresh() selects the id it is handed. Selecting inside run() instead
            // would be undone by its own refresh, which falls back to the `selected`
            // captured at render time — the pile that was open before this one.
            await refresh(created.id);
        } catch (e) {
            setError(e instanceof Error ? e.message : "Could not create pile");
        } finally {
            setBusy(false);
        }
    };

    const uploadFiles = useCallback(async (files: File[], pileId: string) => {
        if (!files.length) return;
        await run(async () => {
            // Sequential: each upload is a separate request and both the per-pile file
            // cap and the per-owner byte budget are checked server-side per call.
            // Uploading in parallel would let a batch overshoot either one.
            for (const f of files) await uploadFile(pileId, f);
        }, pileId);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [refresh]);

    const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const files = Array.from(e.target.files ?? []);
        e.target.value = "";  // allow re-picking the same file
        if (!selected) return;
        await uploadFiles(files, selected.id);
    };

    // ── Drag and drop ────────────────────────────────────────────────────────
    // dragenter/dragleave fire for every descendant the pointer crosses, so a plain
    // boolean flickers off as soon as the cursor moves over a child. Counting enters
    // against leaves is what keeps the highlight stable.
    const dragDepth = useRef(0);
    const [dragOver, setDragOver] = useState(false);
    const droppable = !!selected && !busy && selected.status !== "processing";

    const onDragEnter = (e: React.DragEvent) => {
        if (!droppable) return;
        e.preventDefault();
        dragDepth.current += 1;
        setDragOver(true);
    };

    const onDragOver = (e: React.DragEvent) => {
        if (!droppable) return;
        e.preventDefault();  // without this the browser navigates to the dropped file
        e.dataTransfer.dropEffect = "copy";
    };

    const onDragLeave = (e: React.DragEvent) => {
        if (!droppable) return;
        e.preventDefault();
        dragDepth.current = Math.max(0, dragDepth.current - 1);
        if (dragDepth.current === 0) setDragOver(false);
    };

    const onDrop = async (e: React.DragEvent) => {
        if (!droppable || !selected) return;
        e.preventDefault();
        dragDepth.current = 0;
        setDragOver(false);
        // `dataTransfer.files` is emptied once the event handler returns, so the list
        // is copied before the first await.
        await uploadFiles(Array.from(e.dataTransfer.files ?? []), selected.id);
    };

    // Spelled out rather than built from the status string: the translation keys are
    // strictly typed, so a template literal is not a valid key.
    const statusLabel = (p: Pile): string => {
        switch (p.status) {
            case "processing": return t("piles.statusProcessing");
            case "ready":      return t("piles.statusReady");
            case "stale":      return t("piles.statusStale");
            case "failed":     return t("piles.statusFailed");
            default:           return t("piles.statusDraft");
        }
    };

    const statusTone = (p: Pile): string =>
        p.status === "ready" ? "text-emerald-600 dark:text-emerald-400"
            : p.status === "failed" ? "text-destructive"
                : p.status === "processing" ? "text-muted-foreground"
                    : "text-amber-600 dark:text-amber-400";

    return (
        <div className="flex flex-col gap-6 w-full">
            {/* Intro */}
            <div className="rounded-lg border border-border bg-muted/20 px-4 py-3 flex gap-2.5">
                <FileStack className="h-4 w-4 text-muted-foreground shrink-0 mt-0.5" />
                <div className="text-xs leading-relaxed">
                    <p className="font-medium mb-1">{t("piles.introTitle")}</p>
                    <p className="text-muted-foreground">{t("piles.introBody")}</p>
                </div>
            </div>

            {error && (
                <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 flex gap-2.5">
                    <AlertTriangle className="h-4 w-4 text-destructive shrink-0 mt-0.5" />
                    <span className="text-xs">{error}</span>
                </div>
            )}

            <div className="grid grid-cols-1 lg:grid-cols-[18rem_1fr] gap-6">
                {/* Pile list */}
                <div className="flex flex-col gap-3">
                    <div className="flex items-center gap-2">
                        <input
                            value={newName}
                            onChange={(e) => setNewName(e.target.value)}
                            onKeyDown={(e) => { if (e.key === "Enter") handleCreate(); }}
                            placeholder={t("piles.newPlaceholder")}
                            className="flex-1 min-w-0 bg-background border border-border rounded-md px-2 py-1.5 text-xs outline-none focus:border-muted-foreground/50 placeholder:text-muted-foreground/40"
                        />
                        <button
                            onClick={handleCreate}
                            disabled={busy || !newName.trim()}
                            className="flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs border border-border hover:bg-muted disabled:opacity-40 transition-colors"
                        >
                            <Plus className="h-3.5 w-3.5" /> {t("piles.create")}
                        </button>
                    </div>

                    {loading ? (
                        <div className="flex items-center gap-2 text-xs text-muted-foreground px-1">
                            <Loader2 className="h-3.5 w-3.5 animate-spin" /> {t("piles.loading")}
                        </div>
                    ) : piles.length === 0 ? (
                        <p className="text-xs text-muted-foreground/70 px-1">{t("piles.empty")}</p>
                    ) : (
                        <div className="flex flex-col gap-1.5">
                            {piles.map((p) => (
                                <button
                                    key={p.id}
                                    onClick={() => fetchPile(p.id).then(setSelected).catch(() => {})}
                                    className={cn(
                                        "text-left rounded-lg border px-3 py-2 transition-colors",
                                        selected?.id === p.id
                                            ? "border-border bg-card shadow-sm"
                                            : "border-transparent bg-muted/30 hover:bg-muted/60"
                                    )}
                                >
                                    <div className="flex items-center gap-2">
                                        <span className="text-xs font-medium truncate">{p.name}</span>
                                        {p.status === "processing" && (
                                            <Loader2 className="h-3 w-3 animate-spin text-muted-foreground shrink-0" />
                                        )}
                                    </div>
                                    <div className="flex items-center gap-2 mt-0.5">
                                        <span className={cn("text-[10px] font-mono", statusTone(p))}>
                                            {statusLabel(p)}
                                        </span>
                                        <span className="text-[10px] font-mono text-muted-foreground/60">
                                            {t("piles.fileCount", { n: p.pending_files })}
                                        </span>
                                    </div>
                                </button>
                            ))}
                        </div>
                    )}
                </div>

                {/* Pile detail — also the drop target for file uploads */}
                {selected && (
                    <div
                        onDragEnter={onDragEnter}
                        onDragOver={onDragOver}
                        onDragLeave={onDragLeave}
                        onDrop={onDrop}
                        className={cn(
                            "rounded-xl border bg-card shadow-sm overflow-hidden transition-colors",
                            dragOver ? "border-primary ring-2 ring-primary/30" : "border-border"
                        )}
                    >
                        <div className="flex items-center gap-2 px-4 py-3 border-b border-border bg-muted/30">
                            <span className="text-sm font-medium truncate">{selected.name}</span>
                            <span className={cn("text-[10px] font-mono", statusTone(selected))}>
                                {statusLabel(selected)}
                            </span>
                            <button
                                onClick={() => run(async () => {
                                    await deletePile(selected.id);
                                    setSelected(null);
                                })}
                                disabled={busy || selected.status === "processing"}
                                className="ml-auto flex items-center gap-1 px-2 py-1 rounded-md text-xs text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:opacity-40 transition-colors"
                            >
                                <Trash2 className="h-3.5 w-3.5" /> {t("piles.delete")}
                            </button>
                        </div>

                        <div className="p-4 flex flex-col gap-4">
                            {/* State banner */}
                            {selected.status === "failed" && (
                                <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs">
                                    {t("piles.failedHint", { error: selected.error || "unknown" })}
                                </div>
                            )}
                            {selected.status === "stale" && (
                                <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs">
                                    {t("piles.staleHint")}
                                </div>
                            )}
                            {selected.has_store && !selected.embedding_current && (
                                <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs">
                                    {t("piles.embeddingDriftHint")}
                                </div>
                            )}
                            {selected.status === "ready" && (
                                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                                    <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
                                    {t("piles.readyHint", {
                                        chunks: selected.chunk_count,
                                        files: selected.file_count,
                                    })}
                                </div>
                            )}

                            {/* Actions */}
                            <div className="flex flex-wrap items-center gap-2">
                                <button
                                    onClick={() => fileRef.current?.click()}
                                    disabled={busy || selected.status === "processing"}
                                    className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs border border-border hover:bg-muted disabled:opacity-40 transition-colors"
                                >
                                    <Upload className="h-3.5 w-3.5" /> {t("piles.addFiles")}
                                </button>
                                <input
                                    ref={fileRef}
                                    type="file"
                                    multiple
                                    accept={ACCEPT}
                                    onChange={handleUpload}
                                    className="hidden"
                                />
                                <button
                                    onClick={() => run(async () => { await processPile(selected.id); }, selected.id)}
                                    disabled={
                                        busy
                                        || selected.status === "processing"
                                        || selected.files.length === 0
                                        || !needsProcessing(selected)
                                    }
                                    className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs border border-border hover:bg-muted disabled:opacity-40 transition-colors"
                                >
                                    {selected.status === "processing"
                                        ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                        : <Play className="h-3.5 w-3.5" />}
                                    {t("piles.process")}
                                </button>
                                {busy && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
                            </div>

                            {/* Drop zone — the whole panel accepts a drop, this is the
                                part that says so. */}
                            <div
                                onClick={() => { if (droppable) fileRef.current?.click(); }}
                                className={cn(
                                    "rounded-lg border border-dashed px-4 py-6 text-center transition-colors",
                                    droppable && "cursor-pointer hover:bg-muted/40",
                                    dragOver
                                        ? "border-primary bg-primary/5"
                                        : "border-border/70"
                                )}
                            >
                                <Upload className={cn(
                                    "h-5 w-5 mx-auto mb-1.5",
                                    dragOver ? "text-primary" : "text-muted-foreground/50"
                                )} />
                                <p className="text-xs text-muted-foreground">
                                    {dragOver ? t("piles.dropNow") : t("piles.dropHint")}
                                </p>
                                <p className="text-[10px] text-muted-foreground/60 mt-1 font-mono">
                                    {t("piles.dropTypes")}
                                </p>
                            </div>

                            {/* File list */}
                            {selected.files.length === 0 ? (
                                <p className="text-xs text-muted-foreground/70">{t("piles.noFiles")}</p>
                            ) : (
                                <div className="rounded-lg border border-border divide-y divide-border">
                                    {selected.files.map((f) => (
                                        <div key={f.filename} className="flex items-center gap-3 px-3 py-2">
                                            <span className="text-xs truncate flex-1" title={f.source_file}>
                                                {f.source_file}
                                            </span>
                                            <span className="text-[10px] font-mono text-muted-foreground/60 shrink-0">
                                                {formatBytes(f.size_bytes)}
                                            </span>
                                            <button
                                                onClick={() => run(
                                                    async () => { await deleteFile(selected.id, f.filename); },
                                                    selected.id
                                                )}
                                                disabled={busy || selected.status === "processing"}
                                                className="p-1 rounded hover:bg-destructive/10 hover:text-destructive disabled:opacity-30 transition-colors shrink-0"
                                            >
                                                <Trash2 className="h-3.5 w-3.5" />
                                            </button>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};
