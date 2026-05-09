import React, { FunctionComponent, useEffect, useState } from "react";
import { Bug, Loader2, RefreshCw } from "lucide-react";

const LINE_OPTIONS = [100, 200, 500, 1000];

export const LlmDebugPanel: FunctionComponent = () => {
    const [enabled, setEnabled] = useState<boolean | null>(null);
    const [toggling, setToggling] = useState(false);
    const [lines, setLines] = useState(100);
    const [logLines, setLogLines] = useState<string[] | null>(null);
    const [totalLines, setTotalLines] = useState<number>(0);
    const [logLoading, setLogLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const loadStatus = () => {
        fetch("/api/admin/llm-debug/status")
            .then((r) => r.ok ? r.json() : Promise.reject())
            .then((d) => setEnabled(d.enabled))
            .catch(() => {});
    };

    const loadLog = () => {
        setLogLoading(true);
        setError(null);
        fetch(`/api/admin/llm-debug/log?lines=${lines}`)
            .then((r) => r.ok ? r.json() : Promise.reject("Failed to load log"))
            .then((d) => { setLogLines(d.lines); setTotalLines(d.total_lines); })
            .catch((e) => setError(String(e)))
            .finally(() => setLogLoading(false));
    };

    const toggle = async () => {
        if (enabled === null) return;
        setToggling(true);
        const endpoint = enabled ? "/api/admin/llm-debug/disable" : "/api/admin/llm-debug/enable";
        try {
            const res = await fetch(endpoint, { method: "POST" });
            if (!res.ok) throw new Error();
            const d = await res.json();
            setEnabled(d.enabled);
        } catch {
            /* leave state unchanged */
        } finally {
            setToggling(false);
        }
    };

    useEffect(() => { loadStatus(); loadLog(); }, []);
    // Reload log when line count changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
    useEffect(() => { if (logLines !== null) loadLog(); }, [lines]);

    const empty = logLines !== null && logLines.length === 0;

    return (
        <div className="space-y-4">
            {/* Header */}
            <div className="flex items-center justify-between gap-3 flex-wrap">
                <div className="flex items-center gap-2">
                    <h2 className="text-sm font-semibold">LLM Debug</h2>
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
                    <select
                        value={lines}
                        onChange={(e) => setLines(Number(e.target.value))}
                        className="text-xs [font-family:inherit] bg-muted border border-border text-foreground rounded px-2 py-1 focus:outline-none focus:border-blue-400 transition-colors"
                    >
                        {LINE_OPTIONS.map((n) => (
                            <option key={n} value={n}>Last {n} lines</option>
                        ))}
                    </select>
                    <button
                        onClick={() => { loadStatus(); loadLog(); }}
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
                        {toggling ? <Loader2 className="h-3 w-3 animate-spin" /> : <Bug className="h-3 w-3" />}
                        {enabled ? "Disable" : "Enable"}
                    </button>
                </div>
            </div>

            {/* Info */}
            <p className="text-xs text-muted-foreground">
                When enabled, every LLM call writes the full prompt and response to{" "}
                <code className="font-mono">logs/llm_debug.log</code>.
                Resets to off on backend restart. Prompts may contain retrieved document content and conversation history.
            </p>

            {/* Log viewer */}
            {error && <p className="text-sm text-destructive">{error}</p>}

            <div className="rounded-lg border border-border overflow-hidden">
                {logLoading && (
                    <div className="flex items-center gap-2 text-sm text-muted-foreground py-8 justify-center">
                        <Loader2 className="h-4 w-4 animate-spin" /> Loading…
                    </div>
                )}
                {!logLoading && empty && (
                    <div className="py-10 text-center text-xs text-muted-foreground">
                        {enabled
                            ? "No log entries yet — send a query to capture the first entry."
                            : "Debug mode is off. Enable it to start capturing LLM calls."}
                    </div>
                )}
                {!logLoading && logLines && logLines.length > 0 && (
                    <>
                        <div className="px-3 py-1.5 border-b border-border bg-muted/40 flex justify-between items-center">
                            <span className="text-xs text-muted-foreground font-mono">logs/llm_debug.log</span>
                            {totalLines > lines && (
                                <span className="text-xs text-muted-foreground">
                                    showing last {lines} of {totalLines} lines
                                </span>
                            )}
                        </div>
                        <pre className="text-xs font-mono p-3 overflow-x-auto whitespace-pre-wrap leading-relaxed text-foreground/80 max-h-[60vh] overflow-y-auto">
                            {logLines.join("\n")}
                        </pre>
                    </>
                )}
            </div>
        </div>
    );
};
