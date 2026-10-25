import { useEffect, useState } from "react";
import { BUDGET_FRACTION, CHARS_PER_TOKEN } from "@/components/sections/chat-action-bar";

export interface ContextBudget {
    /** Estimated tokens the files add to the context; null until their sizes are known. */
    tokens: number | null;
    budget: number | null;
    over: boolean;
}

/** Estimates whether reading these files in full fits the model's context, as the expand-context popover does. */
export function useContextBudget(files: string[]): ContextBudget {
    const [numCtx, setNumCtx] = useState<number | null>(null);
    const [chars, setChars] = useState<{ key: string; total: number } | null>(null);
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
            body: JSON.stringify({ source_files: key.split("\n") }),
        })
            .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
            .then((data: { stats: { char_count: number }[] }) => {
                if (!cancelled) setChars({ key, total: data.stats.reduce((n, s) => n + s.char_count, 0) });
            })
            .catch((e) => console.warn("[context-budget] document stats unavailable:", e));
        return () => {
            cancelled = true;
        };
    }, [key]);

    const tokens = key && chars?.key === key ? Math.ceil(chars.total / CHARS_PER_TOKEN) : null;
    const budget = numCtx ? Math.floor(numCtx * BUDGET_FRACTION) : null;
    return { tokens, budget, over: tokens !== null && budget !== null && tokens > budget };
}
