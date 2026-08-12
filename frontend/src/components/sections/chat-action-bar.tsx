"use client";

import React, { FunctionComponent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Filter, Lock, X, Check, Loader2, MessageSquareOff, BookOpen } from "lucide-react";
import { useMessaging } from "@/hooks/useMessaging";
import { MessageFilter } from "@/services/conversation";
import { Message } from "@/services/message";

// ─── Types ────────────────────────────────────────────────────────────────────

interface ChatFilterKey {
    key: string;
    widget: "dropdown" | "text";
}

interface ChatFiltersConfig {
    enabled: boolean;
    keys: ChatFilterKey[];
}

interface ActiveKbFilters {
    id: string;
    chat_filters: ChatFiltersConfig;
}

// ─── KB config loader ─────────────────────────────────────────────────────────

/**
 * Reads the active KB and its admin-configured `chat_filters` block.
 * Refetches whenever the KB switches (via the `lancy-kb-changed` window event).
 */
function useActiveKbFilters(): ActiveKbFilters | null {
    const [state, setState] = useState<ActiveKbFilters | null>(null);
    useEffect(() => {
        let cancelled = false;
        const load = () => {
            fetch("/api/v1/kb", { credentials: "include" })
                .then((r) => (r.ok ? r.json() : null))
                .then((reg) => {
                    if (cancelled || !reg?.bases || !reg?.active) return;
                    const kb = reg.bases[reg.active];
                    if (!kb) return;
                    setState({
                        id: kb.id,
                        chat_filters: kb.chat_filters ?? { enabled: false, keys: [] },
                    });
                })
                .catch(() => {});
        };
        load();
        const handler = () => load();
        window.addEventListener("lancy-kb-changed", handler);
        return () => {
            cancelled = true;
            window.removeEventListener("lancy-kb-changed", handler);
        };
    }, []);
    return state;
}

// ─── Filter action (pill + popover) ───────────────────────────────────────────

interface FilterActionProps {
    kb: ActiveKbFilters;
    active: MessageFilter[];
    onChange: (filters: MessageFilter[]) => void;
}

const FilterAction: FunctionComponent<FilterActionProps> = ({ kb, active, onChange }) => {
    const { t } = useTranslation("app");
    const [open, setOpen] = useState(false);
    const containerRef = useRef<HTMLDivElement | null>(null);

    // Close on outside click
    useEffect(() => {
        if (!open) return;
        const onDoc = (e: MouseEvent) => {
            if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
                setOpen(false);
            }
        };
        document.addEventListener("mousedown", onDoc);
        return () => document.removeEventListener("mousedown", onDoc);
    }, [open]);

    const activeByKey = React.useMemo(() => {
        const map: Record<string, string> = {};
        for (const f of active) map[f.key] = f.value;
        return map;
    }, [active]);

    const removeKey = useCallback((key: string) => {
        onChange(active.filter((f) => f.key !== key));
    }, [active, onChange]);

    return (
        <div ref={containerRef} className="relative flex items-center gap-1 flex-wrap">
            {active.map((f) => (
                <span
                    key={f.key}
                    className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-blue-500/15 border border-blue-500/40 text-blue-300"
                    title={`${f.key} = ${f.value}`}
                >
                    <span className="font-mono truncate max-w-[160px]">{f.key}</span>
                    <span className="opacity-60">:</span>
                    <span className="truncate max-w-[160px]">{f.value}</span>
                    <button
                        onClick={() => removeKey(f.key)}
                        className="opacity-60 hover:opacity-100 hover:text-red-400 transition-colors"
                        title={t("chatFilters.remove")}
                    >
                        <X className="h-3 w-3" />
                    </button>
                </span>
            ))}
            <button
                onClick={() => setOpen((v) => !v)}
                className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border border-foreground/20 text-muted-foreground hover:border-blue-400/50 hover:text-blue-300 transition-colors"
                title={t("chatFilters.openPopover")}
            >
                <Filter className="h-3 w-3" />
                <span>{active.length > 0 ? t("chatFilters.edit") : t("chatFilters.add")}</span>
            </button>

            {open && (
                <FilterPopover
                    kb={kb}
                    initial={activeByKey}
                    onApply={(next) => { onChange(next); setOpen(false); }}
                    onCancel={() => setOpen(false)}
                />
            )}
        </div>
    );
};

// ─── Popover ──────────────────────────────────────────────────────────────────

interface FilterPopoverProps {
    kb: ActiveKbFilters;
    initial: Record<string, string>;
    onApply: (filters: MessageFilter[]) => void;
    onCancel: () => void;
}

interface FacetState {
    loading: boolean;
    values: string[] | null;  // null = over-threshold; text input fallback
    distinct_count: number;
    error?: string;
}

const FilterPopover: FunctionComponent<FilterPopoverProps> = ({ kb, initial, onApply, onCancel }) => {
    const { t } = useTranslation("app");
    const [selection, setSelection] = useState<Record<string, string>>(initial);
    const [facets, setFacets] = useState<Record<string, FacetState>>({});

    // Fetch dropdown values on mount for each dropdown-widget key
    useEffect(() => {
        for (const k of kb.chat_filters.keys) {
            if (k.widget !== "dropdown") continue;
            // Deliberate: each dropdown is marked loading before its own request goes
            // out, so the menu renders a spinner per key instead of appearing empty.
            // eslint-disable-next-line react-hooks/set-state-in-effect -- see above
            setFacets((f) => ({ ...f, [k.key]: { loading: true, values: null, distinct_count: 0 } }));
            const url = `/api/v1/rag/metadata-facets?key=${encodeURIComponent(k.key)}&kb_id=${encodeURIComponent(kb.id)}&threshold=120`;
            fetch(url, { credentials: "include" })
                .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
                .then((data: { distinct_count: number; values: string[] | null }) => {
                    setFacets((f) => ({
                        ...f,
                        [k.key]: { loading: false, values: data.values, distinct_count: data.distinct_count },
                    }));
                })
                .catch((e) => {
                    setFacets((f) => ({
                        ...f,
                        [k.key]: { loading: false, values: null, distinct_count: 0, error: String(e) },
                    }));
                });
        }
    }, [kb]);

    const setValue = (key: string, value: string) => {
        setSelection((s) => ({ ...s, [key]: value }));
    };

    const apply = () => {
        const filters: MessageFilter[] = kb.chat_filters.keys
            .map((k) => ({ key: k.key, op: "eq" as const, value: (selection[k.key] ?? "").trim() }))
            .filter((f) => f.value !== "");
        onApply(filters);
    };

    return (
        <div
            className="absolute bottom-full left-0 mb-2 z-50 w-[320px] max-w-[90vw] bg-card border border-border rounded-lg shadow-lg p-3 space-y-2"
        >
            <div className="text-[11px] font-medium text-foreground pb-1 border-b border-border/60">
                {t("chatFilters.popoverTitle")}
            </div>
            <div className="space-y-2 max-h-[280px] overflow-y-auto">
                {kb.chat_filters.keys.map((k) => {
                    const facet = facets[k.key];
                    const current = selection[k.key] ?? "";
                    return (
                        <div key={k.key} className="space-y-1">
                            <label className="text-[10px] font-mono text-muted-foreground block">{k.key}</label>
                            {k.widget === "dropdown" ? (
                                facet?.loading ? (
                                    <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
                                        <Loader2 className="h-3 w-3 animate-spin" />
                                        {t("chatFilters.loadingValues")}
                                    </div>
                                ) : facet?.values ? (
                                    <select
                                        value={current}
                                        onChange={(e) => setValue(k.key, e.target.value)}
                                        className="w-full bg-muted border border-border text-foreground text-xs rounded px-2 py-1 focus:outline-none focus:border-blue-400"
                                    >
                                        <option value="">{t("chatFilters.anyValue")}</option>
                                        {facet.values.map((v) => (
                                            <option key={v} value={v}>{v}</option>
                                        ))}
                                    </select>
                                ) : (
                                    <div className="text-[10px] text-amber-400">
                                        {facet?.error
                                            ? t("chatFilters.valuesUnavailable")
                                            : t("chatFilters.tooManyValues", { count: facet?.distinct_count ?? 0 })}
                                    </div>
                                )
                            ) : (
                                <input
                                    type="text"
                                    value={current}
                                    onChange={(e) => setValue(k.key, e.target.value)}
                                    placeholder={t("chatFilters.textPlaceholder")}
                                    className="w-full bg-muted border border-border text-foreground text-xs rounded px-2 py-1 focus:outline-none focus:border-blue-400"
                                />
                            )}
                        </div>
                    );
                })}
            </div>
            <div className="flex justify-end gap-2 pt-2 border-t border-border/60">
                <button
                    onClick={onCancel}
                    className="text-xs px-2.5 py-1 rounded bg-muted hover:bg-accent/20 text-foreground transition-colors"
                >
                    {t("chatFilters.cancel")}
                </button>
                <button
                    onClick={apply}
                    className="text-xs px-2.5 py-1 rounded bg-blue-600 hover:bg-blue-500 text-white font-medium transition-colors inline-flex items-center gap-1"
                >
                    <Check className="h-3 w-3" />
                    {t("chatFilters.apply")}
                </button>
            </div>
        </div>
    );
};

// ─── Frozen chip (read-only, post-first-message) ──────────────────────────────

const FrozenFilters: FunctionComponent<{ filters: MessageFilter[] }> = ({ filters }) => {
    const { t } = useTranslation("app");
    return (
        <div className="flex items-center gap-1 flex-wrap">
            <Lock className="h-3 w-3 text-muted-foreground" aria-hidden="true" />
            {filters.map((f, i) => (
                <span
                    key={`${f.key}-${i}`}
                    className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-muted border border-border text-muted-foreground"
                    title={t("chatFilters.frozenHint")}
                >
                    <span className="font-mono truncate max-w-[160px]">{f.key}</span>
                    <span className="opacity-60">:</span>
                    <span className="truncate max-w-[160px]">{f.value}</span>
                </span>
            ))}
        </div>
    );
};

// ─── Chat-only toggle ─────────────────────────────────────────────────────────

interface ChatOnlyActionProps {
    on: boolean;
    onToggle: () => void;
}

const ChatOnlyAction: FunctionComponent<ChatOnlyActionProps> = ({ on, onToggle }) => {
    const { t } = useTranslation("app");
    return (
        <button
            onClick={onToggle}
            title={on ? t("chatOnly.tooltipOn") : t("chatOnly.tooltipOff")}
            aria-pressed={on}
            className={
                on
                    ? "inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-amber-500/15 border border-amber-500/50 text-amber-300"
                    : "inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border border-foreground/20 text-muted-foreground hover:border-amber-400/50 hover:text-amber-300 transition-colors"
            }
        >
            <MessageSquareOff className="h-3 w-3" />
            <span>{t("chatOnly.label")}</span>
        </button>
    );
};

// ─── Expand-context action ────────────────────────────────────────────────────

const CHARS_PER_TOKEN = 3.5;        // conservative for German-heavy text
const BUDGET_FRACTION = 0.6;         // 60% of num_ctx — leaves room for prompt/output

interface DocStat { source_file: string; chunk_count: number; char_count: number; }

interface ExpandContextActionProps {
    lastAssistantSources: NonNullable<Message["sources"]>;
    selected: string[];
    lastSelected: string[];
    onChange: (files: string[]) => void;
}

const ExpandContextAction: FunctionComponent<ExpandContextActionProps> = ({ lastAssistantSources, selected, lastSelected, onChange }) => {
    const { t } = useTranslation("app");
    const [open, setOpen] = useState(false);
    const containerRef = useRef<HTMLDivElement | null>(null);

    useEffect(() => {
        if (!open) return;
        const onDoc = (e: MouseEvent) => {
            if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
                setOpen(false);
            }
        };
        document.addEventListener("mousedown", onDoc);
        return () => document.removeEventListener("mousedown", onDoc);
    }, [open]);

    const documents = useMemo(() => {
        const seen = new Set<string>();
        const out: string[] = [];
        for (const s of lastAssistantSources) {
            const f = (s.metadata?.source_file as string | undefined) ?? "";
            if (f && !seen.has(f)) {
                seen.add(f);
                out.push(f);
            }
        }
        return out;
    }, [lastAssistantSources]);

    const active = selected.length > 0;

    return (
        <div ref={containerRef} className="relative">
            <button
                onClick={() => setOpen((v) => !v)}
                aria-pressed={active}
                title={active ? t("expandContext.tooltipActive", { count: selected.length }) : t("expandContext.tooltipOpen")}
                className={
                    active
                        ? "inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-emerald-500/15 border border-emerald-500/50 text-emerald-300"
                        : "inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border border-foreground/20 text-muted-foreground hover:border-emerald-400/50 hover:text-emerald-300 transition-colors"
                }
            >
                <BookOpen className="h-3 w-3" />
                <span>
                    {active
                        ? t("expandContext.labelActive", { count: selected.length })
                        : t("expandContext.label")}
                </span>
            </button>
            {open && (
                <ExpandContextPopover
                    documents={documents}
                    initial={
                        selected.length > 0
                            ? selected
                            : documents.length === 1
                                ? documents
                                : lastSelected.filter((f) => documents.includes(f))
                    }
                    onApply={(next) => { onChange(next); setOpen(false); }}
                    onCancel={() => setOpen(false)}
                    onClear={() => { onChange([]); setOpen(false); }}
                />
            )}
        </div>
    );
};

interface ExpandContextPopoverProps {
    documents: string[];
    initial: string[];
    onApply: (files: string[]) => void;
    onCancel: () => void;
    onClear: () => void;
}

const ExpandContextPopover: FunctionComponent<ExpandContextPopoverProps> = ({ documents, initial, onApply, onCancel, onClear }) => {
    const { t } = useTranslation("app");
    const [checked, setChecked] = useState<Record<string, boolean>>(() => {
        const m: Record<string, boolean> = {};
        for (const f of initial) m[f] = true;
        return m;
    });
    const [stats, setStats] = useState<Record<string, DocStat> | null>(null);
    const [statsError, setStatsError] = useState<string | null>(null);
    const [numCtx, setNumCtx] = useState<number | null>(null);

    useEffect(() => {
        let cancelled = false;
        fetch("/api/v1/rag/config", { credentials: "include" })
            .then((r) => (r.ok ? r.json() : null))
            .then((cfg) => {
                if (cancelled || !cfg) return;
                setNumCtx(typeof cfg.num_ctx === "number" ? cfg.num_ctx : 8192);
            })
            .catch(() => setNumCtx(8192));
        fetch("/api/v1/rag/document-stats", {
            method: "POST",
            credentials: "include",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ source_files: documents }),
        })
            .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
            .then((data: { stats: DocStat[] }) => {
                if (cancelled) return;
                const map: Record<string, DocStat> = {};
                for (const s of data.stats) map[s.source_file] = s;
                setStats(map);
            })
            .catch((e) => { if (!cancelled) setStatsError(String(e)); });
        return () => { cancelled = true; };
    }, [documents]);

    const totals = useMemo(() => {
        let chunks = 0;
        let chars = 0;
        for (const f of documents) {
            if (!checked[f]) continue;
            const s = stats?.[f];
            if (!s) continue;
            chunks += s.chunk_count;
            chars += s.char_count;
        }
        const tokens = Math.ceil(chars / CHARS_PER_TOKEN);
        const budget = numCtx ? Math.floor(numCtx * BUDGET_FRACTION) : null;
        const pct = budget ? Math.min(100, Math.round((tokens / budget) * 100)) : 0;
        return { chunks, chars, tokens, budget, pct };
    }, [checked, stats, documents, numCtx]);

    const over = totals.budget !== null && totals.tokens > totals.budget;
    const nothingChecked = Object.values(checked).every((v) => !v);

    const barColor = totals.pct >= 90 ? "bg-red-500" : totals.pct >= 60 ? "bg-amber-400" : "bg-emerald-500";
    const barTextColor = totals.pct >= 90 ? "text-red-400" : totals.pct >= 60 ? "text-amber-300" : "text-emerald-300";

    const apply = () => {
        const picked = documents.filter((f) => checked[f]);
        onApply(picked);
    };

    return (
        <div className="absolute bottom-full left-0 mb-2 z-50 w-[380px] max-w-[95vw] bg-card border border-border rounded-lg shadow-lg p-3 space-y-2">
            <div className="text-[11px] font-medium text-foreground pb-1 border-b border-border/60">
                {t("expandContext.popoverTitle")}
            </div>
            <div className="space-y-1 max-h-[260px] overflow-y-auto">
                {documents.map((f) => {
                    const s = stats?.[f];
                    const est = s ? Math.ceil(s.char_count / CHARS_PER_TOKEN) : 0;
                    return (
                        <label key={f} className="flex items-center gap-2 text-[11px] hover:bg-muted/50 rounded px-1 py-0.5 cursor-pointer">
                            <input
                                type="checkbox"
                                checked={!!checked[f]}
                                onChange={(e) => setChecked((c) => ({ ...c, [f]: e.target.checked }))}
                                className="accent-emerald-500"
                            />
                            <span className="truncate flex-1" title={f}>{f}</span>
                            <span className="font-mono text-[10px] text-muted-foreground shrink-0">
                                {s
                                    ? `${s.chunk_count}c · ~${est.toLocaleString()}t`
                                    : statsError
                                        ? <span className="text-amber-400">{t("expandContext.statsUnavailable")}</span>
                                        : <Loader2 className="h-3 w-3 animate-spin inline" />}
                            </span>
                        </label>
                    );
                })}
            </div>
            <div className="pt-2 border-t border-border/60 space-y-1">
                <div className="flex items-center justify-between text-[10px] text-muted-foreground">
                    <span>
                        {t("expandContext.totals", { chunks: totals.chunks, tokens: totals.tokens.toLocaleString() })}
                    </span>
                    <span className={`font-mono ${barTextColor}`}>
                        {totals.budget !== null
                            ? `${totals.pct}% ${t("expandContext.ofBudget", { budget: totals.budget.toLocaleString() })}`
                            : "—"}
                    </span>
                </div>
                {totals.budget !== null && (
                    <div className="h-1.5 rounded bg-muted overflow-hidden">
                        <div className={`h-full ${barColor} transition-all`} style={{ width: `${totals.pct}%` }} />
                    </div>
                )}
                {over && (
                    <div className="text-[10px] text-red-400">
                        {t("expandContext.overBudget", { budget: totals.budget!.toLocaleString() })}
                    </div>
                )}
            </div>
            <div className="flex justify-between gap-2 pt-2 border-t border-border/60">
                <button
                    onClick={onClear}
                    className="text-xs px-2 py-1 rounded text-muted-foreground hover:text-red-400 transition-colors"
                    title={t("expandContext.clear")}
                >
                    {t("expandContext.clear")}
                </button>
                <div className="flex gap-2">
                    <button
                        onClick={onCancel}
                        className="text-xs px-2.5 py-1 rounded bg-muted hover:bg-accent/20 text-foreground transition-colors"
                    >
                        {t("expandContext.cancel")}
                    </button>
                    <button
                        onClick={apply}
                        disabled={nothingChecked || over}
                        className="text-xs px-2.5 py-1 rounded bg-emerald-600 hover:bg-emerald-500 disabled:bg-muted disabled:text-muted-foreground disabled:cursor-not-allowed text-white font-medium transition-colors inline-flex items-center gap-1"
                    >
                        <Check className="h-3 w-3" />
                        {t("expandContext.apply")}
                    </button>
                </div>
            </div>
        </div>
    );
};

// ─── Public: the bar ──────────────────────────────────────────────────────────

/**
 * Row of chat-scoped action controls rendered between the suggestion tiles and
 * the chat input. Hosts one component per action; designed so new actions slot
 * in as sibling components without cross-action coordination.
 */
export const ChatActionBar: FunctionComponent = () => {
    const {
        activeConversationId,
        chatFilters, setChatFilters, frozenChatFilters,
        chatOnly, setChatOnly,
        expandContextFiles, setExpandContextFiles, lastExpandContextFiles,
        thread,
    } = useMessaging();
    const kb = useActiveKbFilters();

    const lastAssistantSources = useMemo(() => {
        for (let i = thread.length - 1; i >= 0; i--) {
            const m = thread[i];
            if (m.role === "assistant" && m.sources && m.sources.length > 0) return m.sources;
        }
        return null;
    }, [thread]);

    // Which filter-side UI (if any) do we render?
    const showFrozenFilters = !!activeConversationId && frozenChatFilters.length > 0;
    const showFilterAction = !activeConversationId
        && !!kb && kb.chat_filters.enabled && kb.chat_filters.keys.length > 0;
    const expandArmed = expandContextFiles.length > 0;

    return (
        <div className="px-1 pb-1.5 flex items-center gap-2 flex-wrap">
            {showFrozenFilters && <FrozenFilters filters={frozenChatFilters} />}
            {showFilterAction && kb && !expandArmed && (
                <FilterAction kb={kb} active={chatFilters} onChange={setChatFilters} />
            )}
            {!expandArmed && (
                <ChatOnlyAction on={chatOnly} onToggle={() => setChatOnly(!chatOnly)} />
            )}
            {lastAssistantSources && (
                <ExpandContextAction
                    lastAssistantSources={lastAssistantSources}
                    selected={expandContextFiles}
                    lastSelected={lastExpandContextFiles}
                    onChange={setExpandContextFiles}
                />
            )}
        </div>
    );
};
