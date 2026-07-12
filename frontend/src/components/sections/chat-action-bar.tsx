"use client";

import React, { FunctionComponent, useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Filter, Lock, X, Check, Loader2 } from "lucide-react";
import { useMessaging } from "@/hooks/useMessaging";
import { MessageFilter } from "@/services/conversation";

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

// ─── Public: the bar ──────────────────────────────────────────────────────────

/**
 * Row of chat-scoped action controls rendered between the suggestion tiles and
 * the chat input. Currently hosts one action (chat filters); designed so future
 * actions (e.g. "chat only" toggle, "expand context") can slot in as sibling
 * components without coordination.
 */
export const ChatActionBar: FunctionComponent = () => {
    const { activeConversationId, chatFilters, setChatFilters, frozenChatFilters } = useMessaging();
    const kb = useActiveKbFilters();

    // Frozen mode: existing conversation with persisted filters
    if (activeConversationId) {
        if (frozenChatFilters.length === 0) return null;
        return (
            <div className="px-1 pb-1.5 flex items-center">
                <FrozenFilters filters={frozenChatFilters} />
            </div>
        );
    }

    // Editable mode: new conversation, only shows if admin has enabled + configured keys
    if (!kb || !kb.chat_filters.enabled || kb.chat_filters.keys.length === 0) return null;

    return (
        <div className="px-1 pb-1.5 flex items-center">
            <FilterAction kb={kb} active={chatFilters} onChange={setChatFilters} />
        </div>
    );
};
