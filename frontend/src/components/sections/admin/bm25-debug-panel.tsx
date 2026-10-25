import React, { FunctionComponent, useEffect, useState } from "react";
import { Loader2, RefreshCw, TextSearch } from "lucide-react";

interface Hit {
    id: string;
    source_file: string;
    title: string;
    score: number;
    matched_terms: string[];
}

interface Variant {
    query: string;
    terms: string[];
    expansion_terms: string[];
    language: string | null;
    hits: Hit[];
}

interface Search {
    timestamp: string;
    question: string;
    variants: Variant[];
}

const TermList: FunctionComponent<{ terms: string[]; className: string }> = ({ terms, className }) => (
    <span className="inline-flex flex-wrap gap-1">
        {terms.map((term) => (
            <span key={term} className={`px-1.5 py-0.5 rounded text-[10px] font-mono border ${className}`}>{term}</span>
        ))}
    </span>
);

const OWN_TERM = "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/25";
const EXPANSION_TERM = "bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/25";

const VariantView: FunctionComponent<{ variant: Variant }> = ({ variant }) => (
    <div className="space-y-1.5 pl-3 border-l border-border">
        <div className="text-xs">
            <span className="font-mono">{variant.query}</span>
            <span className="ml-2 text-muted-foreground">language: {variant.language ?? "none detected"}</span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
            terms <TermList terms={variant.terms} className={OWN_TERM} />
        </div>
        {variant.expansion_terms.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                expansions <TermList terms={variant.expansion_terms} className={EXPANSION_TERM} />
            </div>
        )}
        {variant.hits.length === 0 ? (
            <p className="text-xs text-muted-foreground">No keyword hits.</p>
        ) : (
            <table className="w-full text-xs">
                <tbody>
                    {variant.hits.map((hit, i) => (
                        <tr key={hit.id} className="border-t border-border/60 align-top">
                            <td className="py-1 pr-2 font-mono text-muted-foreground tabular-nums">{i + 1}</td>
                            <td className="py-1 pr-2 font-mono tabular-nums">{hit.score.toFixed(2)}</td>
                            <td className="py-1 pr-2 break-all">{hit.source_file || hit.title || hit.id}</td>
                            <td className="py-1"><TermList terms={hit.matched_terms} className={OWN_TERM} /></td>
                        </tr>
                    ))}
                </tbody>
            </table>
        )}
    </div>
);

export const Bm25DebugPanel: FunctionComponent = () => {
    const [enabled, setEnabled] = useState<boolean | null>(null);
    const [toggling, setToggling] = useState(false);
    const [searches, setSearches] = useState<Search[] | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const load = () => {
        setLoading(true);
        setError(null);
        Promise.all([
            fetch("/api/admin/bm25-debug/status").then((r) => r.ok ? r.json() : Promise.reject("Failed to load status")),
            fetch("/api/admin/bm25-debug/searches").then((r) => r.ok ? r.json() : Promise.reject("Failed to load searches")),
        ])
            .then(([status, data]) => { setEnabled(status.enabled); setSearches(data.searches); })
            .catch((e) => setError(String(e)))
            .finally(() => setLoading(false));
    };

    const toggle = async () => {
        if (enabled === null) return;
        setToggling(true);
        try {
            const res = await fetch(`/api/admin/bm25-debug/${enabled ? "disable" : "enable"}`, { method: "POST" });
            if (!res.ok) throw new Error();
            const d = await res.json();
            setEnabled(d.enabled);
            if (!d.enabled) setSearches([]);
        } catch {
            /* leave state unchanged */
        } finally {
            setToggling(false);
        }
    };

    useEffect(() => { load(); }, []);

    return (
        <div className="space-y-4">
            {/* Header */}
            <div className="flex items-center justify-between gap-3 flex-wrap">
                <div className="flex items-center gap-2">
                    <h2 className="text-sm font-semibold">BM25 Debug</h2>
                    {enabled !== null && (
                        <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-mono font-medium ${
                            enabled
                                ? "bg-amber-500/15 text-amber-600 dark:text-amber-400"
                                : "bg-muted text-muted-foreground"
                        }`}>
                            {enabled ? "ON" : "off"}
                        </span>
                    )}
                </div>
                <div className="flex items-center gap-2">
                    <button
                        onClick={load}
                        className="text-muted-foreground hover:text-foreground transition-colors"
                        title="Refresh"
                    >
                        <RefreshCw className="h-4 w-4" />
                    </button>
                    <button
                        onClick={toggle}
                        disabled={toggling || enabled === null}
                        className={`flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md disabled:opacity-50 transition-colors ${
                            enabled
                                ? "bg-amber-500 text-white hover:bg-amber-600"
                                : "bg-primary text-primary-foreground hover:bg-primary/80"
                        }`}
                    >
                        {toggling ? <Loader2 className="h-3 w-3 animate-spin" /> : <TextSearch className="h-3 w-3" />}
                        {enabled ? "Disable" : "Enable"}
                    </button>
                </div>
            </div>

            {/* Info */}
            <p className="text-xs text-muted-foreground">
                When enabled, keeps the last 50 questions in memory with each keyword search made for them: the query
                variant, the terms it was analysed into (stop words dropped, stems added), extra terms from keyword
                extraction, the language detected and every keyword hit it passed on, with score and matched terms. Never written to
                disk; resets to off and empties on backend restart or when disabled. Entries contain users&apos; questions.
            </p>

            {error && <p className="text-sm text-destructive">{error}</p>}

            {loading && (
                <div className="flex items-center gap-2 text-sm text-muted-foreground py-8 justify-center">
                    <Loader2 className="h-4 w-4 animate-spin" /> Loading…
                </div>
            )}
            {!loading && searches && searches.length === 0 && (
                <div className="rounded-lg border border-border py-10 text-center text-xs text-muted-foreground">
                    {enabled
                        ? "No searches yet — ask a question in the chat, then refresh."
                        : "Recording is off. Enable it to start capturing keyword searches."}
                </div>
            )}
            {!loading && searches && searches.map((search, i) => (
                <div key={`${search.timestamp}-${i}`} className="rounded-lg border border-border p-3 space-y-3">
                    <div className="flex items-baseline justify-between gap-3">
                        <span className="text-sm">{search.question}</span>
                        <span className="text-[10px] font-mono text-muted-foreground shrink-0">{search.timestamp}</span>
                    </div>
                    {search.variants.length === 0
                        ? <p className="text-xs text-muted-foreground">No keyword search ran for this question.</p>
                        : search.variants.map((variant, j) => <VariantView key={j} variant={variant} />)}
                </div>
            ))}
        </div>
    );
};
