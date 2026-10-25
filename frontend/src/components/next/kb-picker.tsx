import React, { FunctionComponent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, ChevronDown, Database, Lock } from "lucide-react";
import { cn } from "@/lib/lorem";
import { corpusLanguageShares } from "@/lib/corpus-languages";
import { useMessaging } from "@/hooks/useMessaging";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";

interface Kb {
    id: string;
    name: string;
    embedding_backend: string;
    embedding_model: string;
    files?: number;
    chunks?: number;
    language_counts?: Record<string, number>;
    corpus_languages?: string[];
}

/** "DE 81 % · EN 12 %": the languages HyDE, query expansion and keyword extraction write in. */
function languageLabel(kb: Kb): string {
    return corpusLanguageShares(kb.language_counts, kb.corpus_languages)
        .map(({ code, share }) => (share == null ? code.toUpperCase() : `${code.toUpperCase()} ${Math.round(share * 100)} %`))
        .join(" · ");
}

interface Pool {
    loaded: string[];
    active: string | null;
    emb_key?: { backend: string; model: string } | null;
}

const chip = "flex h-8 items-center gap-1.5 rounded-lg border border-border px-2.5 text-[13px] text-muted-foreground";

/**
 * Adopt a KB for this session: remember it and tell the other surfaces. Settings
 * are the admin's; a user switching KB must not save any — a config save rebuilds
 * the shared agents for everyone. Loading a KB into the pool is an admin action
 * and stays on the admin side.
 */
function adoptKb(kb: Kb, pool: Pool) {
    sessionStorage.setItem("lancy_selected_kb_id", kb.id);
    sessionStorage.setItem("lancy_selected_kb_name", kb.name);
    window.dispatchEvent(new CustomEvent("lancy-kb-changed", { detail: { kbId: kb.id } }));

    const compatible = !pool.emb_key || (kb.embedding_backend === pool.emb_key.backend && kb.embedding_model === pool.emb_key.model);
    sessionStorage.setItem("lancy_kb_incompatible", compatible ? "0" : "1");
    window.dispatchEvent(new CustomEvent("lancy-kb-pool-state", { detail: { kbId: kb.id, compatible } }));
}

export const KbPicker: FunctionComponent = () => {
    const { t } = useTranslation("app");
    const { activeConversationId, conversations } = useMessaging();
    const [kbs, setKbs] = useState<Kb[]>([]);
    const [pool, setPool] = useState<Pool>({ loaded: [], active: null });
    const [selectedId, setSelectedId] = useState<string | null>(null);

    // Resolve this session's KB once: the remembered one if the pool can serve it,
    // else the pool's active one. Showing a KB the pool does not hold would name one
    // KB while answers come from another.
    useEffect(() => {
        Promise.all([
            fetch("/api/v1/kb", { credentials: "include" }).then((r) => (r.ok ? r.json() : null)),
            fetch("/api/v1/kb/pool", { credentials: "include" }).then((r) => (r.ok ? r.json() : null)),
        ])
            .then(([reg, poolStatus]: [{ active: string; bases: Record<string, Kb> } | null, Pool | null]) => {
                if (!reg?.bases) return;
                const p = poolStatus ?? { loaded: [], active: null };
                const saved = sessionStorage.getItem("lancy_selected_kb_id");
                const candidate = saved && reg.bases[saved] ? saved : reg.active;
                const resolved = p.loaded.includes(candidate) ? candidate : p.active && reg.bases[p.active] ? p.active : reg.active;
                setKbs(Object.values(reg.bases));
                setPool(p);
                setSelectedId(resolved);
                const kb = reg.bases[resolved];
                if (kb) adoptKb(kb, p);
            })
            .catch((e) => console.error("[kb-picker] loading knowledge bases failed:", e));
    }, []);

    const conversation = conversations.find((c) => c.id === activeConversationId);
    if (activeConversationId) {
        if (!conversation?.kb_name) return null;
        return (
            <span className={chip} title={t("next.kbLocked", { name: conversation.kb_name })}>
                <Lock className="h-3.5 w-3.5" />
                {conversation.kb_name}
            </span>
        );
    }

    const selected = kbs.find((kb) => kb.id === selectedId);
    if (!selected) return null;

    const choose = (kb: Kb) => {
        setSelectedId(kb.id);
        adoptKb(kb, pool);
    };

    return (
        <DropdownMenu>
            <DropdownMenuTrigger asChild>
                <button type="button" className={cn(chip, "hover:bg-muted hover:text-foreground")}>
                    <Database className="h-3.5 w-3.5" />
                    {selected.name}
                    <ChevronDown className="h-3.5 w-3.5" />
                </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent side="top" align="start" className="min-w-56">
                <DropdownMenuLabel className="text-xs font-medium text-muted-foreground">{t("next.kbLabel")}</DropdownMenuLabel>
                {kbs.map((kb) => {
                    const pooled = pool.loaded.includes(kb.id);
                    const languages = languageLabel(kb);
                    return (
                        <DropdownMenuItem key={kb.id} disabled={!pooled} onClick={() => choose(kb)} className="items-start gap-2 py-2">
                            <Check className={cn("mt-0.5 h-4 w-4 shrink-0", kb.id === selectedId ? "opacity-100" : "opacity-0")} />
                            <span className="flex flex-1 flex-col">
                                <span>{kb.name}</span>
                                {kb.files != null && kb.chunks != null && (
                                    <span className="text-xs text-muted-foreground">
                                        {t("next.documentsButton", { count: kb.files })} · {t("next.chunks", { count: kb.chunks })}
                                    </span>
                                )}
                                {languages && <span className="text-xs text-muted-foreground">{languages}</span>}
                            </span>
                            {!pooled && <span className="text-xs text-muted-foreground">{t("next.kbNotLoaded")}</span>}
                        </DropdownMenuItem>
                    );
                })}
            </DropdownMenuContent>
        </DropdownMenu>
    );
};
