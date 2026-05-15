"use client";

import React, {
    FunctionComponent,
    useCallback,
    useEffect,
    useMemo,
    useRef,
    useState,
} from "react";
import {
    ColumnDef,
    flexRender,
    getCoreRowModel,
    useReactTable,
} from "@tanstack/react-table";
import { ChevronDown, ChevronRight, Loader2, Plus, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/lorem";
import ReactMarkdown from "react-markdown";
import RemarkGfm from "remark-gfm";

// Lightweight markdown renderer for chunk content — remark-gfm only (no sanitizer stack
// needed for raw document content, and the full chat pipeline swallows table nodes).
const ChunkMarkdown: FunctionComponent<{ content: string }> = ({ content }) => (
    <ReactMarkdown
        remarkPlugins={[RemarkGfm]}
        components={{
            h1: ({ node: _n, ...p }) => <h2 className="text-sm font-semibold mt-3 mb-1 text-foreground" {...p} />,
            h2: ({ node: _n, ...p }) => <h3 className="text-xs font-semibold mt-2 mb-1 text-foreground" {...p} />,
            h3: ({ node: _n, ...p }) => <h4 className="text-xs font-semibold mt-2 mb-1 text-foreground" {...p} />,
            p: ({ node: _n, ...p }) => <p className="mb-2 leading-relaxed" {...p} />,
            ul: ({ node: _n, ...p }) => <ul className="list-disc list-outside ml-4 my-1 space-y-0.5" {...p} />,
            ol: ({ node: _n, ...p }) => <ol className="list-decimal list-outside ml-4 my-1 space-y-0.5" {...p} />,
            li: ({ node: _n, ...p }) => <li className="leading-relaxed" {...p} />,
            strong: ({ node: _n, ...p }) => <strong className="font-semibold text-foreground" {...p} />,
            em: ({ node: _n, ...p }) => <em className="italic" {...p} />,
            blockquote: ({ node: _n, ...p }) => <blockquote className="border-l-4 border-muted-foreground/30 pl-3 italic my-2 text-muted-foreground" {...p} />,
            pre: ({ node: _n, ...p }) => <pre className="bg-muted text-foreground text-[0.7rem] p-3 rounded my-2 overflow-x-auto border" {...p} />,
            code: ({ node: _n, ...p }) => <code className="bg-muted px-1 py-0.5 rounded font-mono text-[0.9em]" {...p} />,
            hr: () => <hr className="my-3 border-t border-border" />,
            table: ({ node: _n, ...p }) => (
                <div className="my-3 overflow-x-auto">
                    <table className="w-full border-collapse border border-border text-xs" {...p} />
                </div>
            ),
            thead: ({ node: _n, ...p }) => <thead className="bg-muted" {...p} />,
            tbody: ({ node: _n, ...p }) => <tbody className="divide-y divide-border" {...p} />,
            tr: ({ node: _n, ...p }) => <tr className="border-b border-border last:border-0" {...p} />,
            th: ({ node: _n, ...p }) => <th className="px-2 py-1.5 font-semibold text-left text-foreground border-r border-border last:border-r-0" {...p} />,
            td: ({ node: _n, ...p }) => <td className="px-2 py-1.5 text-left text-foreground/80 border-r border-border last:border-r-0" {...p} />,
        }}
    >
        {content}
    </ReactMarkdown>
);

const API_BASE = "";

// Fixed baseline columns — not duplicated in dynamic extras
const BASELINE_META_KEYS = new Set([
    "source_file", "chunk_index", "mime_type", "title",
    "file_hash", "tags", "embedding",
]);

// Suggestions for the add-filter datalist (tags and file_hash excluded)
const KNOWN_META_KEYS = [
    "document_id", "author", "document_class", "document_type",
    "document_created_at", "document_released_at", "source_url",
    "source_file", "chunk_index", "mime_type",
];

interface FilterRow {
    key: string;
    value: string;
}

interface ChunkBrowseItem {
    id: string;
    content: string;
    title: string;
    metadata: Record<string, unknown>;
}

interface ChunkBrowseResponse {
    chunks: ChunkBrowseItem[];
    returned: number;
    offset: number;
    has_more: boolean;
}

async function fetchFileList(): Promise<string[]> {
    const r = await fetch(`${API_BASE}/api/v1/rag/store-info`, { credentials: "include" });
    if (!r.ok) return [];
    const data = await r.json();
    return (data.file_list as string[]) ?? [];
}

async function fetchChunks(
    fileFilter: string,
    extraFilters: FilterRow[],
    limit: number,
    offset: number
): Promise<ChunkBrowseResponse> {
    const filters: { key: string; op: string; value: string }[] = [];
    if (fileFilter) {
        filters.push({ key: "source_file", op: "eq", value: fileFilter });
    }
    for (const f of extraFilters) {
        if (f.key.trim() && f.value.trim()) {
            filters.push({ key: f.key.trim(), op: "eq", value: f.value.trim() });
        }
    }
    const r = await fetch(`${API_BASE}/api/v1/rag/chunks`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filters, limit, offset }),
    });
    if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error((err as { detail?: string }).detail ?? "Browse failed");
    }
    return r.json();
}

function basename(path: string): string {
    return path.split(/[/\\]/).pop() ?? path;
}

export const ChunkBrowser: FunctionComponent<{ active: boolean }> = ({ active }) => {
    const { t } = useTranslation("app");

    // Filter input state
    const [fileList, setFileList] = useState<string[]>([]);
    const [selectedFile, setSelectedFile] = useState("");   // full path sent to backend
    const [fileInput, setFileInput] = useState("");         // display text in the typeahead
    const [showFileSuggestions, setShowFileSuggestions] = useState(false);
    const fileComboRef = useRef<HTMLDivElement>(null);
    const [extraFilters, setExtraFilters] = useState<FilterRow[]>([]);

    // Applied filter state — snapshot at Browse time, used for Load More
    const appliedFileRef = useRef("");
    const appliedExtraRef = useRef<FilterRow[]>([]);

    // Result state
    const [allChunks, setAllChunks] = useState<ChunkBrowseItem[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [hasMore, setHasMore] = useState(false);
    const [currentOffset, setCurrentOffset] = useState(0);
    const [browsed, setBrowsed] = useState(false);

    // Expanded row IDs
    const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

    // Refresh file list whenever this tab becomes active
    useEffect(() => {
        if (active) {
            fetchFileList().then(setFileList).catch(() => {});
        }
    }, [active]);

    // Close suggestions on outside click
    useEffect(() => {
        const handler = (e: MouseEvent) => {
            if (!fileComboRef.current?.contains(e.target as Node)) {
                setShowFileSuggestions(false);
            }
        };
        document.addEventListener("mousedown", handler);
        return () => document.removeEventListener("mousedown", handler);
    }, []);

    const fileSuggestions = useMemo(() => {
        if (!fileInput.trim()) return [];
        const q = fileInput.toLowerCase();
        return fileList.filter(f => basename(f).toLowerCase().includes(q)).slice(0, 15);
    }, [fileInput, fileList]);

    const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        setFileInput(e.target.value);
        setSelectedFile("");          // clear any previous explicit selection
        setShowFileSuggestions(true);
    };

    const handleFileSelect = (fullPath: string) => {
        setSelectedFile(fullPath);
        setFileInput(basename(fullPath));
        setShowFileSuggestions(false);
    };

    const handleFileClear = () => {
        setSelectedFile("");
        setFileInput("");
        setShowFileSuggestions(false);
    };

    const toggleExpanded = useCallback((id: string) => {
        setExpandedIds(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    }, []);

    const doFetch = useCallback(async (
        file: string,
        extra: FilterRow[],
        offset: number,
        append: boolean
    ) => {
        setLoading(true);
        setError(null);
        try {
            const resp = await fetchChunks(file, extra, 50, offset);
            setAllChunks(prev => append ? [...prev, ...resp.chunks] : resp.chunks);
            setHasMore(resp.has_more);
            setCurrentOffset(offset + resp.returned);
            setBrowsed(true);
        } catch (e) {
            setError(e instanceof Error ? e.message : "Unknown error");
        } finally {
            setLoading(false);
        }
    }, []);

    const handleBrowse = useCallback(() => {
        appliedFileRef.current = selectedFile;
        appliedExtraRef.current = extraFilters;
        setExpandedIds(new Set());
        doFetch(selectedFile, extraFilters, 0, false);
    }, [selectedFile, extraFilters, doFetch]);

    const handleLoadMore = useCallback(() => {
        doFetch(appliedFileRef.current, appliedExtraRef.current, currentOffset, true);
    }, [doFetch, currentOffset]);

    // Derive extra columns from the union of metadata keys in loaded chunks
    const dynamicKeys = useMemo(() => {
        const keys = new Set<string>();
        for (const chunk of allChunks) {
            for (const key of Object.keys(chunk.metadata)) {
                if (!BASELINE_META_KEYS.has(key)) keys.add(key);
            }
        }
        return Array.from(keys).sort();
    }, [allChunks]);

    const columns = useMemo<ColumnDef<ChunkBrowseItem>[]>(() => [
        {
            id: "num",
            header: t("explorer.browserColNum"),
            size: 48,
            cell: ({ row }) => (
                <span className="text-muted-foreground font-mono text-xs">{row.index + 1}</span>
            ),
        },
        {
            id: "expand",
            header: "",
            size: 28,
            cell: ({ row }) => (
                <span className="text-muted-foreground/60">
                    {expandedIds.has(row.original.id)
                        ? <ChevronDown className="h-3.5 w-3.5" />
                        : <ChevronRight className="h-3.5 w-3.5" />}
                </span>
            ),
        },
        {
            id: "file",
            header: t("explorer.browserColFile"),
            cell: ({ row }) => {
                const full = String(row.original.metadata.source_file ?? "");
                return full
                    ? <span title={full} className="text-xs truncate max-w-[160px] block">{basename(full)}</span>
                    : <span className="text-muted-foreground/40 text-xs">—</span>;
            },
        },
        {
            id: "title",
            header: "Title",
            cell: ({ row }) => {
                const v = row.original.title;
                return v
                    ? <span title={v} className="text-xs truncate max-w-[200px] block">{v}</span>
                    : <span className="text-muted-foreground/40 text-xs">—</span>;
            },
        },
        {
            id: "chunk_index",
            header: t("explorer.browserColChunkIndex"),
            size: 64,
            cell: ({ row }) => {
                const v = row.original.metadata.chunk_index;
                return v !== undefined
                    ? <span className="text-xs font-mono text-muted-foreground">{String(v)}</span>
                    : null;
            },
        },
        {
            id: "mime_type",
            header: "Type",
            cell: ({ row }) => {
                const v = String(row.original.metadata.mime_type ?? "");
                return v ? <span title={v} className="text-xs text-muted-foreground truncate max-w-[100px] block">{v}</span> : null;
            },
        },
        ...dynamicKeys.map<ColumnDef<ChunkBrowseItem>>(key => ({
            id: key,
            header: key,
            cell: ({ row }: { row: { original: ChunkBrowseItem } }) => {
                const v = row.original.metadata[key];
                if (v === undefined || v === "") return null;
                const s = String(v);
                return <span title={s} className="text-xs text-muted-foreground truncate max-w-[120px] block">{s}</span>;
            },
        })),
    ], [t, dynamicKeys, expandedIds]);

    const table = useReactTable({
        data: allChunks,
        columns,
        getCoreRowModel: getCoreRowModel(),
    });

    // All choosable keys: fixed known keys (minus source_file, which has its own dropdown)
    // plus any extra keys observed in the current result set.
    const allFilterKeys = useMemo(() =>
        [...new Set([
            ...KNOWN_META_KEYS.filter(k => k !== "source_file"),
            ...dynamicKeys,
        ])],
        [dynamicKeys]
    );

    return (
        <div className="flex flex-col gap-4 w-full">
            {/* Filter controls */}
            <div className="max-w-2xl rounded-xl border border-border bg-card shadow-sm p-4 flex flex-col gap-3">
                <div className="flex flex-wrap items-center gap-3">
                    <label className="text-xs text-muted-foreground shrink-0">
                        {t("explorer.browserFileLabel")}
                    </label>
                    {/* File typeahead — filters the already-fetched list client-side */}
                    <div ref={fileComboRef} className="relative flex-1 min-w-0 max-w-xs">
                        <div className={cn(
                            "flex items-center gap-1 border rounded bg-muted px-2 py-1 transition-colors focus-within:border-blue-400",
                            selectedFile ? "border-primary/50" : "border-border"
                        )}>
                            <input
                                type="text"
                                value={fileInput}
                                onChange={handleFileInputChange}
                                onFocus={() => fileInput && setShowFileSuggestions(true)}
                                placeholder={t("explorer.browserFileAll")}
                                className="text-xs [font-family:inherit] bg-transparent outline-none flex-1 min-w-0"
                            />
                            {(fileInput || selectedFile) && (
                                <button onClick={handleFileClear} className="shrink-0 text-muted-foreground hover:text-foreground transition-colors">
                                    <X className="h-3 w-3" />
                                </button>
                            )}
                        </div>
                        {showFileSuggestions && fileSuggestions.length > 0 && (
                            <div className="absolute top-full left-0 right-0 mt-1 z-20 bg-card border border-border rounded-md shadow-md overflow-hidden">
                                {fileSuggestions.map(f => (
                                    <button
                                        key={f}
                                        onMouseDown={e => { e.preventDefault(); handleFileSelect(f); }}
                                        title={f}
                                        className="w-full text-left px-3 py-1.5 text-xs hover:bg-muted truncate block"
                                    >
                                        {basename(f)}
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>

                    <button
                        onClick={() => setExtraFilters(prev => [...prev, { key: "", value: "" }])}
                        className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground px-2 py-1.5 rounded-md hover:bg-muted/50 transition-colors"
                    >
                        <Plus className="h-3.5 w-3.5" />
                        {t("explorer.browserAddFilter")}
                    </button>

                    <button
                        onClick={handleBrowse}
                        disabled={loading}
                        className={cn(
                            "ml-auto flex items-center gap-2 px-4 py-1.5 rounded-md text-sm font-medium transition-all",
                            "bg-primary text-primary-foreground hover:bg-primary/90",
                            "disabled:opacity-40 disabled:cursor-not-allowed"
                        )}
                    >
                        {loading && !hasMore
                            ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />{t("explorer.browserBrowsing")}</>
                            : t("explorer.browserBrowse")}
                    </button>
                </div>

                {/* Extra filter rows */}
                {extraFilters.map((f, i) => {
                    // Keys already committed in other rows — hide them from this row's dropdown.
                    const usedElsewhere = new Set(
                        extraFilters
                            .filter((_, j) => j !== i)
                            .map(r => r.key)
                            .filter(k => k !== "")
                    );
                    const options = allFilterKeys.filter(k => !usedElsewhere.has(k));

                    return (
                        <div key={i} className="flex items-center gap-2">
                            <select
                                value={f.key}
                                onChange={e => setExtraFilters(prev =>
                                    prev.map((r, ri) => ri === i ? { ...r, key: e.target.value } : r)
                                )}
                                className="text-xs [font-family:inherit] bg-muted border border-border text-foreground rounded px-2 py-1 focus:outline-none focus:border-blue-400 transition-colors flex-1"
                            >
                                <option value="">{t("explorer.browserFilterKeyPlaceholder")}</option>
                                {options.map(k => <option key={k} value={k}>{k}</option>)}
                                {/* Keep current key visible even if it ended up excluded (edge case) */}
                                {f.key && !options.includes(f.key) && (
                                    <option value={f.key}>{f.key}</option>
                                )}
                            </select>
                            <span className="text-xs text-muted-foreground">=</span>
                            <input
                                value={f.value}
                                onChange={e => setExtraFilters(prev =>
                                    prev.map((r, ri) => ri === i ? { ...r, value: e.target.value } : r)
                                )}
                                placeholder={t("explorer.browserFilterValuePlaceholder")}
                                className="text-xs [font-family:inherit] bg-muted border border-border text-foreground rounded px-2 py-1 focus:outline-none focus:border-blue-400 transition-colors flex-1"
                            />
                            <button
                                onClick={() => setExtraFilters(prev => prev.filter((_, ri) => ri !== i))}
                                className="text-muted-foreground hover:text-destructive transition-colors"
                            >
                                <X className="h-3.5 w-3.5" />
                            </button>
                        </div>
                    );
                })}
            </div>

            {/* Error */}
            {error && (
                <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                    {error}
                </div>
            )}

            {/* Idle state */}
            {!browsed && !loading && !error && (
                <div className="flex flex-col items-center justify-center py-16 text-center text-muted-foreground/50">
                    <p className="text-sm">{t("explorer.browserIdleTitle")}</p>
                </div>
            )}

            {/* Empty state */}
            {browsed && !loading && !error && allChunks.length === 0 && (
                <div className="flex flex-col items-center justify-center py-16 text-center text-muted-foreground/50">
                    <p className="text-sm">{t("explorer.browserEmptyTitle")}</p>
                </div>
            )}

            {/* Results table */}
            {allChunks.length > 0 && (
                <div className="flex flex-col gap-2">
                    <div className="flex items-center gap-3 px-1">
                        <span className="text-xs text-muted-foreground font-mono">
                            {t("explorer.browserResultsSummary", { shown: allChunks.length })}
                        </span>
                        <div className="flex-1 h-px bg-border" />
                    </div>

                    <div className="rounded-xl border border-border bg-card shadow-sm overflow-hidden">
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead>
                                    {table.getHeaderGroups().map(hg => (
                                        <tr key={hg.id} className="border-b border-border bg-muted/30">
                                            {hg.headers.map(header => (
                                                <th
                                                    key={header.id}
                                                    className="px-3 py-2 text-left text-xs font-medium text-muted-foreground whitespace-nowrap"
                                                    style={header.column.columnDef.size
                                                        ? { width: header.column.columnDef.size }
                                                        : undefined}
                                                >
                                                    {flexRender(header.column.columnDef.header, header.getContext())}
                                                </th>
                                            ))}
                                        </tr>
                                    ))}
                                </thead>
                                <tbody>
                                    {table.getRowModel().rows.map(row => (
                                        <React.Fragment key={row.id}>
                                            <tr
                                                onClick={() => toggleExpanded(row.original.id)}
                                                className={cn(
                                                    "border-b border-border/50 cursor-pointer transition-colors",
                                                    expandedIds.has(row.original.id)
                                                        ? "bg-muted/40"
                                                        : "hover:bg-muted/20"
                                                )}
                                            >
                                                {row.getVisibleCells().map(cell => (
                                                    <td key={cell.id} className="px-3 py-2 align-middle">
                                                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                                                    </td>
                                                ))}
                                            </tr>
                                            {expandedIds.has(row.original.id) && (
                                                <tr className="border-b border-border/50 bg-muted/20">
                                                    <td colSpan={columns.length} className="px-4 py-3">
                                                        <div className="max-h-96 overflow-y-auto text-xs">
                                                            <ChunkMarkdown content={row.original.content} />
                                                        </div>
                                                    </td>
                                                </tr>
                                            )}
                                        </React.Fragment>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>

                    {/* Load more */}
                    {hasMore && (
                        <div className="flex justify-center pt-2">
                            <button
                                onClick={handleLoadMore}
                                disabled={loading}
                                className={cn(
                                    "flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-all",
                                    "border border-border hover:bg-muted/50",
                                    "disabled:opacity-40 disabled:cursor-not-allowed"
                                )}
                            >
                                {loading
                                    ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />{t("explorer.browserBrowsing")}</>
                                    : t("explorer.browserLoadMore")}
                            </button>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};
