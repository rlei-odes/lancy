"use client";

import React, { FunctionComponent, useEffect, useState } from "react";
import { Library } from "lucide-react";
import { useTranslation } from "react-i18next";
import { adoptKb, Kb, Pool } from "@/components/next/kb-picker";

/**
 * The knowledge base the Advanced Features tabs work on. Selecting one does what the
 * chat's KB picker does — remember it for the session and announce it — so the
 * probe, chunk browser, analytics and batch analysis all follow the same choice.
 * A KB not loaded in the pool cannot be searched, so it is shown but not selectable.
 */
export const ExplorerKbSelect: FunctionComponent = () => {
    const { t } = useTranslation("app");
    const [kbs, setKbs] = useState<Kb[]>([]);
    const [pool, setPool] = useState<Pool>({ loaded: [], active: null });
    const [selectedId, setSelectedId] = useState("");

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
                // Announce only a change: the tabs already read the remembered KB on mount.
                if (resolved !== saved && reg.bases[resolved]) adoptKb(reg.bases[resolved], p);
            })
            .catch((e) => console.error("[explorer-kb-select] loading knowledge bases failed:", e));
    }, []);

    const choose = (id: string) => {
        const kb = kbs.find((k) => k.id === id);
        if (!kb) return;
        setSelectedId(id);
        adoptKb(kb, pool);
    };

    if (kbs.length === 0) return null;

    return (
        <div className="flex items-center gap-2 rounded-xl border border-border bg-card shadow-xs px-4 py-2.5">
            <Library className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm font-medium">{t("explorer.analyticsKbLabel")}</span>
            <select
                value={selectedId}
                onChange={(e) => choose(e.target.value)}
                className="ml-1 text-xs [font-family:inherit] bg-muted border border-border text-foreground rounded px-2 py-1 focus:outline-hidden focus:border-blue-400 transition-colors"
            >
                {kbs.map((kb) => {
                    const pooled = pool.loaded.includes(kb.id);
                    return (
                        <option key={kb.id} value={kb.id} disabled={!pooled}>
                            {kb.name}{pooled ? "" : ` — ${t("next.kbNotLoaded")}`}
                        </option>
                    );
                })}
            </select>
        </div>
    );
};
