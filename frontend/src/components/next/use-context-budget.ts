import { useEffect, useState } from "react";
import { BUDGET_FRACTION, CHARS_PER_TOKEN } from "@/components/sections/chat-action-bar";

export interface ContextBudget {
    /** Estimated tokens the files add to the context; null until their sizes are known. */
    tokens: number | null;
    budget: number | null;
    /** LLM calls that read the files, as the backend will split them; null until known. */
    passes: number | null;
}

/** Estimates the tokens reading these files in full adds, and how many passes the backend will read them in. */
export function useContextBudget(files: string[]): ContextBudget {
    const [numCtx, setNumCtx] = useState<number | null>(null);
    const [chars, setChars] = useState<{ key: string; total: number; passes: number } | null>(null);
    const key = files.join("\n");

    useEffect(() => {
        fetch("/api/v1/rag/config", { credentials: "include" })
            .then((r) => (r.ok ? r.json() : null))
            .then((cfg) => setNumCtx(typeof cfg?.num_ctx === "number" ? cfg.num_ctx : 8192))
            .catch(() => setNumCtx(8192));
    }, []);

    useEffect(() => {
        if (!key) return;
        let cancelled = false;
        fetch("/api/v1/rag/document-stats", {
            method: "POST",
            credentials: "include",
            headers: { "content-type": "application/json" },
            // The session's KB: the backend's default is the pool's active one, which may hold other documents.
            body: JSON.stringify({ source_files: key.split("\n"), kb_id: sessionStorage.getItem("lancy_selected_kb_id") ?? undefined }),
        })
            .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
            .then((data: { stats: { char_count: number }[]; passes?: number }) => {
                if (!cancelled) setChars({ key, total: data.stats.reduce((n, s) => n + s.char_count, 0), passes: data.passes ?? 1 });
            })
            .catch((e) => console.warn("[context-budget] document stats unavailable:", e));
        return () => {
            cancelled = true;
        };
    }, [key]);

    const tokens = key && chars?.key === key ? Math.ceil(chars.total / CHARS_PER_TOKEN) : null;
    const budget = numCtx ? Math.floor(numCtx * BUDGET_FRACTION) : null;
    const passes = key && chars?.key === key ? chars.passes : null;
    return { tokens, budget, passes };
}
