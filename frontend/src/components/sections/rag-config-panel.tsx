"use client";

import React, { FunctionComponent, useEffect, useState, useCallback, useRef } from "react";
import { RefreshCw, ChevronDown, ChevronRight, AlertCircle, CheckCircle2, Loader2, Save, Trash2, Plus, Pencil, X, Database } from "lucide-react";
import { useTranslation } from "react-i18next";

// ─── Types ────────────────────────────────────────────────────────────────────

/** KB-level config: embedding + file limits. Persisted per KB on the backend. */
interface KBConfig {
    embedding_backend: "local" | "ollama" | "litellm" | "custom";
    embedding_model: string;
    embedding_ollama_host: string;
    embedding_custom_base_url: string;
    embedding_custom_api_key: string;
    nomic_prefix: boolean;
    max_file_size_mb: number;
    embedding_batch_size: number;
    pdf_ocr_enabled: boolean;
    max_chunk_tokens: number;
    vs_type: "chromadb" | "pgvector";
    vs_connection_string: string;
    image_indexing_enabled: boolean;
    image_embedding_model: string;
}

/** Session-level config: retrieval, LLM, prompt. No re-index needed. */
interface SessionConfig {
    retriever_top_k: number;
    rrf_k: number;
    bm25_enabled: boolean;
    query_expansion: number;
    hyde_enabled: boolean;
    reranking_enabled: boolean;
    reranking_candidate_pool: number;
    llm_backend: "ollama" | "litellm" | "custom";
    llm_model: string;
    llm_temperature: number;
    ollama_host: string;
    utility_llm_model: string;
    num_ctx: number;
    system_prompt: string;
    follow_up_count: number;
    image_retrieval_enabled: boolean;
    image_retriever_top_k: number;
    custom_base_url: string;
    custom_api_key: string;
}

interface KBInfo extends KBConfig {
    id: string;
    name: string;
    data_dirs: string[];
    vs_path: string;
    chunks: number;
    files: number;
    last_indexed: string | null;
}

interface KBRegistry {
    active: string;
    bases: Record<string, KBInfo>;
}

/** Flat snapshot saved in presets (KB + session combined, no data_dirs). */
interface PresetData extends KBConfig, SessionConfig {}

interface Preset {
    name: string;
    data: PresetData;
}

// ─── Constants ────────────────────────────────────────────────────────────────

const EMBEDDING_MODELS: Record<string, string[]> = {
    local: ["nomic-ai/nomic-embed-text-v1", "all-MiniLM-L6-v2", "BAAI/bge-m3", "intfloat/multilingual-e5-large"],
    custom: ["text-embedding-3-small", "text-embedding-3-large", "text-embedding-ada-002"],
    ollama: ["nomic-embed-text", "mxbai-embed-large", "all-minilm"],
    litellm: [],  // loaded dynamically from /api/v1/rag/litellm-models
};

const LLM_MODELS: Record<string, string[]> = {
    ollama: ["mistral-nemo:12b", "llama3.2:3b", "qwen2.5:7b", "gemma3:12b"],
    litellm: [],  // loaded dynamically from /api/v1/rag/litellm-models
    custom: [],   // free-text model name
};

const DEFAULT_KB_CONFIG: KBConfig = {
    embedding_backend: "local",
    embedding_model: "nomic-ai/nomic-embed-text-v1",
    embedding_ollama_host: "",
    embedding_custom_base_url: "",
    embedding_custom_api_key: "",
    nomic_prefix: true,
    max_file_size_mb: 20,
    embedding_batch_size: 50,
    pdf_ocr_enabled: true,
    max_chunk_tokens: 0,
    vs_type: "chromadb",
    vs_connection_string: "",
    image_indexing_enabled: false,
    image_embedding_model: "Qwen/Qwen3-VL-Embedding-2B",
};

const DEFAULT_SESSION: SessionConfig = {
    retriever_top_k: 5,
    rrf_k: 60,
    bm25_enabled: true,
    query_expansion: 0,
    hyde_enabled: false,
    reranking_enabled: false,
    reranking_candidate_pool: 15,
    llm_backend: "ollama",
    llm_model: "mistral-nemo:12b",
    llm_temperature: 0.2,
    ollama_host: "",
    utility_llm_model: "",
    num_ctx: 8192,
    system_prompt: "",
    follow_up_count: 3,
    image_retrieval_enabled: false,
    image_retriever_top_k: 1,
    custom_base_url: "",
    custom_api_key: "",
};

// ─── Sub-components ───────────────────────────────────────────────────────────

type SectionEffect = "instant" | "reindex";

const EFFECT_DOT: Record<SectionEffect, string> = {
    instant: "bg-green-500",
    reindex: "bg-amber-500",
};

const SectionHeader: FunctionComponent<{
    title: string;
    open: boolean;
    onToggle: () => void;
    effect?: SectionEffect;
    effectTitle?: string;
}> = ({ title, open, onToggle, effect, effectTitle }) => (
    <button
        onClick={onToggle}
        className="w-full flex items-center justify-between py-2 px-1 text-left font-semibold text-xs uppercase tracking-widest text-muted-foreground hover:text-foreground transition-colors border-b border-border/50"
    >
        <span className="flex items-center gap-1.5">
            {title}
            {effect && (
                <span
                    title={effectTitle}
                    className={`inline-block w-1.5 h-1.5 rounded-full ${EFFECT_DOT[effect]} opacity-80`}
                />
            )}
        </span>
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
    </button>
);

const FieldRow: FunctionComponent<{ label: string; hint?: string; children: React.ReactNode }> = ({ label, hint, children }) => (
    <div className="flex flex-col gap-1 py-2">
        <div className="flex items-center justify-between">
            <label className="text-xs text-foreground font-medium">{label}</label>
            {children}
        </div>
        {hint && <p className="text-[10px] text-muted-foreground leading-relaxed">{hint}</p>}
    </div>
);

const NumberInput: FunctionComponent<{
    value: number; min: number; max: number; step?: number; onChange: (v: number) => void;
}> = ({ value, min, max, step = 1, onChange }) => (
    <div className="flex items-center gap-2">
        <input
            type="range" min={min} max={max} step={step} value={value}
            onChange={(e) => onChange(Number(e.target.value))}
            className="w-24 accent-blue-400"
        />
        <span className="text-xs text-blue-400 font-mono w-10 text-right">{value}</span>
    </div>
);

const Toggle: FunctionComponent<{ checked: boolean; onChange: (v: boolean) => void }> = ({ checked, onChange }) => (
    <button
        onClick={() => onChange(!checked)}
        className={`relative w-9 h-5 rounded-full transition-colors duration-200 focus:outline-none ${checked ? "bg-blue-500" : "bg-muted-foreground/40"}`}
    >
        <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform duration-200 ${checked ? "translate-x-4" : "translate-x-0"}`} />
    </button>
);

const SelectInput: FunctionComponent<{
    value: string; options: string[]; onChange: (v: string) => void;
}> = ({ value, options, onChange }) => (
    <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="bg-muted border border-border text-foreground text-xs rounded px-2 py-1 focus:outline-none focus:border-blue-400 transition-colors w-full max-w-[260px]"
    >
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
);

// ─── Presets (per-KB) ─────────────────────────────────────────────────────────

const BUILTIN_PRESETS: Preset[] = [
    {
        // Ausgewogener Einstieg — funktioniert für die meisten Projektdokumente
        name: "Standard",
        data: { ...DEFAULT_KB_CONFIG, ...DEFAULT_SESSION, retriever_top_k: 5, llm_temperature: 0.3 },
    },
    {
        // Angebote & Ausschreibungen: breites Retrieval, präzise LLM-Antwort
        // Gut für: Lieferantenvergleiche, Kostenpositionen, Konditionen
        name: "Angebots-Analyse",
        data: { ...DEFAULT_KB_CONFIG, ...DEFAULT_SESSION, retriever_top_k: 8, reranking_candidate_pool: 20, llm_temperature: 0.15 },
    },
    {
        // Technische Dokumente: EPDs, Datenblätter, Normen
        // Sehr tiefe Temperatur → nur belegte Fakten, keine Interpolation
        name: "Technische Fakten",
        data: { ...DEFAULT_KB_CONFIG, ...DEFAULT_SESSION, max_file_size_mb: 30, retriever_top_k: 6, llm_temperature: 0.05, follow_up_count: 2 },
    },
    {
        // Mehrere Dokumente vergleichen: top-k hoch, BM25 + semantisch
        // Gut für: Bietervergleich, Produktvergleich, Multi-Lieferanten-KB
        name: "Multi-Dok Vergleich",
        data: { ...DEFAULT_KB_CONFIG, ...DEFAULT_SESSION, retriever_top_k: 10, query_expansion: 1, reranking_candidate_pool: 25, llm_temperature: 0.2 },
    },
    {
        // Maximale Qualität mit bge-m3 (multilingual, braucht Re-Index)
        // Gut für: mehrsprachige Projektdokumente, komplexe Fachbegriffe
        name: "bge-m3 Präzision",
        data: { ...DEFAULT_KB_CONFIG, ...DEFAULT_SESSION, embedding_model: "BAAI/bge-m3", nomic_prefix: false, embedding_batch_size: 10, retriever_top_k: 7, reranking_candidate_pool: 20, llm_temperature: 0.2 },
    },
    {
        // Qualität mit langsameren Methoden — nur wenn Zeit keine Rolle spielt
        name: "Qualität (langsam)",
        data: { ...DEFAULT_KB_CONFIG, ...DEFAULT_SESSION, retriever_top_k: 8, query_expansion: 2, hyde_enabled: true, reranking_candidate_pool: 20, llm_temperature: 0.2 },
    },
    {
        // Schnellste lokale Option für erste Orientierung
        name: "Schnell (lokal)",
        data: { ...DEFAULT_KB_CONFIG, ...DEFAULT_SESSION, retriever_top_k: 4, reranking_candidate_pool: 10, llm_model: "llama3.2:3b", llm_temperature: 0.3, follow_up_count: 2 },
    },
];

// ─── KB form ──────────────────────────────────────────────────────────────────

interface KBFormProps {
    initial?: { name: string; data_dirs: string[] };
    onSubmit: (name: string, data_dirs: string[]) => void;
    onCancel: () => void;
    isCreate: boolean;
    t: (key: string) => string;
}

const KBForm: FunctionComponent<KBFormProps> = ({ initial, onSubmit, onCancel, isCreate, t }) => {
    const [name, setName] = useState(initial?.name ?? "");
    const [dirs, setDirs] = useState<string[]>(initial?.data_dirs ?? ["data/"]);
    const nameRef = useRef<HTMLInputElement>(null);

    useEffect(() => { nameRef.current?.focus(); }, []);

    const addDir = () => setDirs((d) => [...d, "data/"]);
    const removeDir = (i: number) => setDirs((d) => d.filter((_, idx) => idx !== i));
    const updateDir = (i: number, v: string) => setDirs((d) => d.map((dir, idx) => idx === i ? v : dir));

    return (
        <div className="px-3 py-2.5 bg-muted/80 border-b border-border/60 space-y-2">
            <p className="text-[10px] font-semibold uppercase tracking-widest text-blue-400">{isCreate ? t("rag.kbFormNew") : t("rag.kbFormEdit")}</p>
            <input
                ref={nameRef}
                value={name}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && name.trim()) onSubmit(name.trim(), dirs); if (e.key === "Escape") onCancel(); }}
                placeholder={t("rag.kbNamePlaceholder")}
                className="w-full bg-card border border-blue-500/60 text-foreground text-xs rounded px-2 py-1 focus:outline-none focus:border-blue-400 font-mono"
            />
            <div>
                <p className="text-[10px] text-muted-foreground mb-1">{t("rag.kbDirsLabel")}</p>
                <div className="space-y-1">
                    {dirs.map((dir, i) => (
                        <div key={i} className="flex gap-1">
                            <input
                                value={dir}
                                onChange={(e) => updateDir(i, e.target.value)}
                                className="flex-1 bg-card border border-border text-foreground text-xs rounded px-2 py-1 focus:outline-none focus:border-blue-400 font-mono"
                            />
                            {dirs.length > 1 && (
                                <button onClick={() => removeDir(i)} className="px-1.5 rounded text-muted-foreground hover:text-red-400 hover:bg-red-900/20 transition-colors">
                                    <X size={10} />
                                </button>
                            )}
                        </div>
                    ))}
                </div>
                <button
                    onClick={addDir}
                    className="mt-1 flex items-center gap-1 text-[10px] text-muted-foreground hover:text-blue-400 transition-colors"
                >
                    <Plus size={9} /> {t("rag.kbAddDir")}
                </button>
            </div>
            <div className="flex gap-1.5 pt-1">
                <button
                    onClick={() => { if (name.trim()) onSubmit(name.trim(), dirs); }}
                    disabled={!name.trim()}
                    className="flex-1 text-xs py-1 rounded bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-40 transition-colors font-medium"
                >
                    {isCreate ? t("rag.kbCreate") : t("rag.kbSave")}
                </button>
                <button
                    onClick={onCancel}
                    className="px-3 text-xs py-1 rounded bg-muted hover:bg-accent/20 text-foreground transition-colors"
                >
                    {t("rag.kbAbort")}
                </button>
            </div>
        </div>
    );
};

// ─── Main Panel ───────────────────────────────────────────────────────────────

const API_BASE = process.env.NEXT_PUBLIC_SERVER_URL || "";

async function fetchUserPresets(kbId: string): Promise<Preset[]> {
    try {
        const r = await fetch(`${API_BASE}/api/v1/rag/presets/${encodeURIComponent(kbId)}`, { credentials: "include" });
        if (r.ok) return await r.json();
    } catch { /* ignore */ }
    return [];
}
async function persistUserPresets(kbId: string, presets: Preset[]): Promise<void> {
    try {
        await fetch(`${API_BASE}/api/v1/rag/presets/${encodeURIComponent(kbId)}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(presets),
            credentials: "include",
        });
    } catch { /* ignore */ }
}

interface StatusMessage { type: "idle" | "loading" | "success" | "error"; text: string; }

export const RagConfigPanel: FunctionComponent = () => {
    const { t } = useTranslation("app");

    // KB state
    const [kbRegistry, setKbRegistry] = useState<KBRegistry | null>(null);
    const [activeKb, setActiveKb] = useState<KBInfo | null>(null);
    const [kbConfig, setKbConfig] = useState<KBConfig>(DEFAULT_KB_CONFIG);

    // Session state
    const [session, setSession] = useState<SessionConfig>(DEFAULT_SESSION);

    // UI state
    const [dirty, setDirty] = useState(false);
    const [status, setStatus] = useState<StatusMessage>({ type: "idle", text: "" });
    const [kbForm, setKbForm] = useState<"create" | "edit" | null>(null);
    const [isIndexing, setIsIndexing] = useState(false);
    const prevFinishedAt = useRef<string>("");
    const [showResetConfirm, setShowResetConfirm] = useState(false);

    // LiteLLM dynamic model list
    const [litellmModels, setLitellmModels] = useState<string[]>([]);
    const [litellmLoading, setLitellmLoading] = useState(false);

    // Ollama embedding model list (fetched from Ollama host)
    const [ollamaEmbedModels, setOllamaEmbedModels] = useState<string[]>([]);
    const [ollamaEmbedLoading, setOllamaEmbedLoading] = useState(false);

    // Ollama LLM model list (fetched dynamically from ollama_host)
    const [ollamaLlmModels, setOllamaLlmModels] = useState<string[]>([]);
    const [ollamaLlmLoading, setOllamaLlmLoading] = useState(false);

    // Presets (per-KB)
    const [userPresets, setUserPresets] = useState<Preset[]>([]);
    const [selectedPreset, setSelectedPreset] = useState("Standard");
    const [showSaveAs, setShowSaveAs] = useState(false);
    const [saveAsName, setSaveAsName] = useState("");
    const saveAsRef = useRef<HTMLInputElement>(null);

    const allPresets = [...BUILTIN_PRESETS, ...userPresets];

    // Sections
    const [sections, setSections] = useState({ retrieval: true, embedding: true, llm: true, prompt: false });
    const toggle = (s: keyof typeof sections) => setSections((prev) => ({ ...prev, [s]: !prev[s] }));

    // ── Loaders ──────────────────────────────────────────────────────────────

    const fetchKbRegistry = useCallback(async () => {
        try {
            const r = await fetch(`${API_BASE}/api/v1/kb`, { credentials: "include" });
            if (!r.ok) return;
            const reg: KBRegistry = await r.json();
            setKbRegistry(reg);
            const kb = reg.bases[reg.active];
            if (kb) {
                setActiveKb(kb);
                setKbConfig({
                    embedding_backend: ((kb.embedding_backend as string) === "openai" ? "custom" : kb.embedding_backend) as KBConfig["embedding_backend"],
                    embedding_model: kb.embedding_model,
                    embedding_ollama_host: kb.embedding_ollama_host ?? "",
                    embedding_custom_base_url: kb.embedding_custom_base_url ?? "",
                    embedding_custom_api_key: kb.embedding_custom_api_key ?? "",
                    nomic_prefix: kb.nomic_prefix,
                    max_file_size_mb: kb.max_file_size_mb,
                    embedding_batch_size: kb.embedding_batch_size ?? 50,
                    pdf_ocr_enabled: kb.pdf_ocr_enabled ?? true,
                    max_chunk_tokens: kb.max_chunk_tokens ?? 0,
                    vs_type: (kb.vs_type as KBConfig["vs_type"]) ?? "chromadb",
                    vs_connection_string: kb.vs_connection_string ?? "",
                    image_indexing_enabled: kb.image_indexing_enabled ?? false,
                    image_embedding_model: kb.image_embedding_model ?? "Qwen/Qwen3-VL-Embedding-2B",
                });
                setUserPresets(await fetchUserPresets(kb.id));
            }
        } catch { /* ignore */ }
    }, []);

    const fetchSessionConfig = useCallback(async () => {
        try {
            const r = await fetch(`${API_BASE}/api/v1/rag/config`, { credentials: "include" });
            if (r.ok) {
                const raw = await r.json();
                // Normalize legacy backends to new ones
                const backend: SessionConfig["llm_backend"] =
                    raw.llm_backend === "openai" || raw.llm_backend === "anthropic" ? "custom" : raw.llm_backend ?? "ollama";
                setSession({
                    ...DEFAULT_SESSION,
                    ...raw,
                    llm_backend: backend,
                    custom_base_url: raw.custom_base_url ?? "",
                    custom_api_key: raw.custom_api_key ?? "",
                });
                setDirty(false);
            }
        } catch { /* ignore */ }
    }, []);

    const litellmFetchingRef = useRef(false);
    const fetchLitellmModels = useCallback(async () => {
        if (litellmFetchingRef.current) return;  // prevent concurrent duplicate calls
        litellmFetchingRef.current = true;
        setLitellmLoading(true);
        try {
            const r = await fetch(`${API_BASE}/api/v1/rag/litellm-models`, { credentials: "include" });
            if (r.ok) {
                const models: string[] = await r.json();
                setLitellmModels(models);
            }
        } catch { /* ignore */ }
        finally {
            setLitellmLoading(false);
            litellmFetchingRef.current = false;
        }
    }, []);

    useEffect(() => {
        fetchKbRegistry();
        fetchSessionConfig();
    }, [fetchKbRegistry, fetchSessionConfig]);

    // Fetch LiteLLM models when either backend uses litellm
    useEffect(() => {
        if (session.llm_backend === "litellm" || kbConfig.embedding_backend === "litellm") {
            if (litellmModels.length === 0 && !litellmFetchingRef.current) fetchLitellmModels();
        }
    }, [session.llm_backend, kbConfig.embedding_backend, litellmModels.length, fetchLitellmModels]);

    // Once litellm models load, auto-select the first model if none set
    useEffect(() => {
        if (litellmModels.length > 0) {
            if (session.llm_backend === "litellm" && !session.llm_model)
                setSession((s) => ({ ...s, llm_model: litellmModels[0] }));
            if (kbConfig.embedding_backend === "litellm" && !kbConfig.embedding_model)
                setKbConfig((c) => ({ ...c, embedding_model: litellmModels[0] }));
        }
    }, [litellmModels]);

    // Fetch Ollama embedding models when backend=ollama and host is set
    const fetchOllamaEmbedModels = useCallback(async (host: string) => {
        if (!host) { setOllamaEmbedModels([]); return; }
        setOllamaEmbedLoading(true);
        try {
            const r = await fetch(`${API_BASE}/api/v1/rag/ollama-models?host=${encodeURIComponent(host)}`, { credentials: "include" });
            if (r.ok) setOllamaEmbedModels(await r.json());
            else setOllamaEmbedModels([]);
        } catch { setOllamaEmbedModels([]); }
        finally { setOllamaEmbedLoading(false); }
    }, []);

    useEffect(() => {
        if (kbConfig.embedding_backend === "ollama") {
            fetchOllamaEmbedModels(kbConfig.embedding_ollama_host);
        } else {
            setOllamaEmbedModels([]);
        }
    }, [kbConfig.embedding_backend, kbConfig.embedding_ollama_host, fetchOllamaEmbedModels]);

    // Fetch Ollama LLM models when llm_backend=ollama and ollama_host is set
    const fetchOllamaLlmModels = useCallback(async (host: string) => {
        const effectiveHost = host || "localhost:11434";
        setOllamaLlmLoading(true);
        try {
            const r = await fetch(`${API_BASE}/api/v1/rag/ollama-models?host=${encodeURIComponent(effectiveHost)}`, { credentials: "include" });
            if (r.ok) setOllamaLlmModels(await r.json());
            else setOllamaLlmModels([]);
        } catch { setOllamaLlmModels([]); }
        finally { setOllamaLlmLoading(false); }
    }, []);

    useEffect(() => {
        if (session.llm_backend === "ollama") {
            fetchOllamaLlmModels(session.ollama_host);
        } else {
            setOllamaLlmModels([]);
        }
    }, [session.llm_backend, session.ollama_host, fetchOllamaLlmModels]);

    useEffect(() => {
        let active = true;
        const poll = async () => {
            try {
                const r = await fetch(`${API_BASE}/api/v1/rag/reindex-status`, { credentials: "include" });
                if (r.ok && active) {
                    const d = await r.json();
                    setIsIndexing(!!d.indexing);
                    // Detect completion: same pattern as indexing-status.tsx
                    if (!d.indexing && d.finished_at && d.finished_at !== prevFinishedAt.current) {
                        prevFinishedAt.current = d.finished_at;
                        if (d.last_result) {
                            const { chunks_indexed, files_processed, files_skipped_store, files_skipped_batch } = d.last_result;
                            const skippedStore: number = files_skipped_store ?? 0;
                            const skippedBatch: number = files_skipped_batch ?? 0;
                            const skipParts: string[] = [];
                            if (skippedStore > 0) skipParts.push(`${skippedStore} ${t("rag.skipReasonStore")}`);
                            if (skippedBatch > 0) skipParts.push(`${skippedBatch} ${t("rag.skipReasonBatch")}`);
                            const finishedTime = new Date(d.finished_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
                            const base = skipParts.length > 0
                                ? t("rag.statusIndexedWithSkips", { chunks: chunks_indexed, files: files_processed, skipped: skipParts.join(", ") })
                                : t("rag.statusIndexed", { chunks: chunks_indexed, files: files_processed });
                            setStatus({ type: "success", text: `${base} · ${finishedTime}` });
                            await fetchKbRegistry();
                        }
                    }
                }
            } catch { /* ignore */ }
        };
        poll();
        const id = setInterval(poll, 3000);
        return () => { active = false; clearInterval(id); };
    }, [t, fetchKbRegistry]);

    useEffect(() => { if (showSaveAs) saveAsRef.current?.focus(); }, [showSaveAs]);

    // ── KB actions ────────────────────────────────────────────────────────────

    const switchKb = async (id: string) => {
        if (id === activeKb?.id) return;
        setStatus({ type: "loading", text: t("rag.statusSwitchingKb") });
        try {
            const r = await fetch(`${API_BASE}/api/v1/kb/${id}/activate`, {
                method: "POST", credentials: "include",
            });
            if (r.ok) {
                const kb: KBInfo = await r.json();
                setActiveKb(kb);
                setKbConfig({
                    embedding_backend: ((kb.embedding_backend as string) === "openai" ? "custom" : kb.embedding_backend) as KBConfig["embedding_backend"],
                    embedding_model: kb.embedding_model,
                    embedding_ollama_host: kb.embedding_ollama_host ?? "",
                    embedding_custom_base_url: kb.embedding_custom_base_url ?? "",
                    embedding_custom_api_key: kb.embedding_custom_api_key ?? "",
                    nomic_prefix: kb.nomic_prefix,
                    max_file_size_mb: kb.max_file_size_mb,
                    embedding_batch_size: kb.embedding_batch_size ?? 50,
                    pdf_ocr_enabled: kb.pdf_ocr_enabled ?? true,
                    max_chunk_tokens: kb.max_chunk_tokens ?? 0,
                    vs_type: (kb.vs_type as KBConfig["vs_type"]) ?? "chromadb",
                    vs_connection_string: kb.vs_connection_string ?? "",
                    image_indexing_enabled: kb.image_indexing_enabled ?? false,
                    image_embedding_model: kb.image_embedding_model ?? "Qwen/Qwen3-VL-Embedding-2B",
                });
                const loaded = await fetchUserPresets(kb.id);
                setUserPresets(loaded);
                setSelectedPreset("Standard");
                setKbRegistry((prev) => prev ? { ...prev, active: id } : prev);
                setDirty(false);
                setStatus({ type: "success", text: t("rag.statusKbActive", { name: kb.name }) });
            } else {
                setStatus({ type: "error", text: t("rag.statusError", { code: r.status }) });
            }
        } catch { setStatus({ type: "error", text: t("rag.statusConnError") }); }
        setTimeout(() => setStatus({ type: "idle", text: "" }), 3000);
    };

    const createKb = async (name: string, data_dirs: string[]) => {
        setStatus({ type: "loading", text: t("rag.statusCreatingKb") });
        setKbForm(null);
        try {
            const r = await fetch(`${API_BASE}/api/v1/kb`, {
                method: "POST", credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name, data_dirs, ...kbConfig }),
            });
            if (r.ok) {
                const kb: KBInfo = await r.json();
                setStatus({ type: "success", text: t("rag.statusKbCreated", { name: kb.name }) });
                await fetchKbRegistry();
                await switchKb(kb.id);
            } else {
                setStatus({ type: "error", text: t("rag.statusError", { code: r.status }) });
            }
        } catch { setStatus({ type: "error", text: t("rag.statusConnError") }); }
        setTimeout(() => setStatus({ type: "idle", text: "" }), 4000);
    };

    const updateKb = async (name: string, data_dirs: string[]) => {
        if (!activeKb) return;
        setKbForm(null);
        try {
            const r = await fetch(`${API_BASE}/api/v1/kb/${activeKb.id}`, {
                method: "PUT", credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name, data_dirs, ...kbConfig }),
            });
            if (r.ok) {
                const kb: KBInfo = await r.json();
                setActiveKb(kb);
                setKbRegistry((prev) => prev
                    ? { ...prev, bases: { ...prev.bases, [kb.id]: kb } }
                    : prev);
                setStatus({ type: "success", text: t("rag.statusKbUpdated") });
            }
        } catch { /* ignore */ }
        setTimeout(() => setStatus({ type: "idle", text: "" }), 3000);
    };

    const deleteKb = async () => {
        if (!activeKb) return;
        if (!confirm(t("rag.confirmDelete", { name: activeKb.name }))) return;
        setStatus({ type: "loading", text: t("rag.statusDeletingKb") });
        try {
            const r = await fetch(`${API_BASE}/api/v1/kb/${activeKb.id}`, {
                method: "DELETE", credentials: "include",
            });
            if (r.ok) {
                setStatus({ type: "success", text: t("rag.statusDeleted") });
                await fetchKbRegistry();
            } else {
                const err = await r.json();
                setStatus({ type: "error", text: err.detail || t("rag.statusError", { code: r.status }) });
            }
        } catch { setStatus({ type: "error", text: t("rag.statusConnError") }); }
        setTimeout(() => setStatus({ type: "idle", text: "" }), 3000);
    };

    // ── Config actions ────────────────────────────────────────────────────────

    const updateSession = useCallback(<K extends keyof SessionConfig>(key: K, value: SessionConfig[K]) => {
        setSession((s) => ({ ...s, [key]: value }));
        setDirty(true);
    }, []);

    const updateKbConfig = useCallback(<K extends keyof KBConfig>(key: K, value: KBConfig[K]) => {
        setKbConfig((c) => ({ ...c, [key]: value }));
        setDirty(true);
    }, []);

    const updateEmbeddingBackend = useCallback((backend: KBConfig["embedding_backend"]) => {
        const firstModel = EMBEDDING_MODELS[backend]?.[0] ?? "";
        setKbConfig((c) => ({
            ...c,
            embedding_backend: backend,
            embedding_model: firstModel,
            nomic_prefix: backend === "local" && firstModel.includes("nomic"),
        }));
        setDirty(true);
        // litellm fetch is handled by the useEffect watching embedding_backend
    }, []);

    const updateLlmBackend = useCallback((backend: SessionConfig["llm_backend"]) => {
        const firstModel = LLM_MODELS[backend]?.[0] ?? "";
        setSession((s) => ({ ...s, llm_backend: backend, llm_model: firstModel }));
        setDirty(true);
        // litellm fetch is handled by the useEffect watching llm_backend
    }, []);

    const saveAll = async () => {
        setStatus({ type: "loading", text: t("rag.statusSaving") });
        try {
            const [sessionRes, kbRes] = await Promise.all([
                fetch(`${API_BASE}/api/v1/rag/config`, {
                    method: "POST", credentials: "include",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(session),
                }),
                activeKb ? fetch(`${API_BASE}/api/v1/kb/${activeKb.id}`, {
                    method: "PUT", credentials: "include",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        name: activeKb.name,
                        data_dirs: activeKb.data_dirs,
                        ...kbConfig,
                    }),
                }) : Promise.resolve({ ok: true }),
            ]);
            if (sessionRes.ok && (kbRes as Response).ok) {
                setDirty(false);
                setStatus({ type: "success", text: t("rag.statusSaved") });
            } else {
                setStatus({ type: "error", text: t("rag.statusSaveError") });
            }
        } catch { setStatus({ type: "error", text: t("rag.statusConnError") }); }
        setTimeout(() => setStatus({ type: "idle", text: "" }), 3000);
    };

    const reindex = async (reset: boolean) => {
        if (isIndexing) {
            setStatus({ type: "error", text: t("rag.statusAlreadyIndexing") });
            setTimeout(() => setStatus({ type: "idle", text: "" }), 3000);
            return;
        }
        setStatus({ type: "loading", text: reset ? t("rag.statusFullReindex") : t("rag.statusIncrementalIndex") });
        try {
            const r = await fetch(`${API_BASE}/api/v1/rag/reindex`, {
                method: "POST", credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ reset }),
            });
            // POST returns immediately with {"started": true} — result arrives via polling
            if (r.status === 409) {
                setStatus({ type: "error", text: t("rag.statusAlreadyIndexing") });
            } else if (!r.ok) {
                setStatus({ type: "error", text: t("rag.statusError", { code: r.status }) });
            }
            // On success: keep loading state; polling loop will set success when finished_at changes
        } catch { setStatus({ type: "error", text: t("rag.statusConnError") }); }
    };

    // ── Presets ───────────────────────────────────────────────────────────────

    const loadPreset = useCallback((name: string) => {
        const preset = allPresets.find((p) => p.name === name);
        if (!preset) return;
        setKbConfig({
            embedding_backend: preset.data.embedding_backend,
            embedding_model: preset.data.embedding_model,
            embedding_ollama_host: preset.data.embedding_ollama_host ?? "",
            embedding_custom_base_url: preset.data.embedding_custom_base_url ?? "",
            embedding_custom_api_key: preset.data.embedding_custom_api_key ?? "",
            nomic_prefix: preset.data.nomic_prefix,
            max_file_size_mb: preset.data.max_file_size_mb,
            embedding_batch_size: preset.data.embedding_batch_size ?? 50,
            pdf_ocr_enabled: preset.data.pdf_ocr_enabled ?? true,
            max_chunk_tokens: preset.data.max_chunk_tokens ?? 0,
            vs_type: preset.data.vs_type ?? "chromadb",
            vs_connection_string: preset.data.vs_connection_string ?? "",
            image_indexing_enabled: preset.data.image_indexing_enabled ?? false,
            image_embedding_model: preset.data.image_embedding_model ?? "Qwen/Qwen3-VL-Embedding-2B",
        });
        setSession({
            retriever_top_k: preset.data.retriever_top_k,
            rrf_k: preset.data.rrf_k,
            bm25_enabled: preset.data.bm25_enabled,
            query_expansion: preset.data.query_expansion,
            hyde_enabled: preset.data.hyde_enabled,
            reranking_enabled: preset.data.reranking_enabled ?? false,
            reranking_candidate_pool: preset.data.reranking_candidate_pool ?? 15,
            llm_backend: (preset.data.llm_backend as SessionConfig["llm_backend"]) ?? "ollama",
            llm_model: preset.data.llm_model,
            llm_temperature: preset.data.llm_temperature,
            ollama_host: preset.data.ollama_host ?? "",
            utility_llm_model: preset.data.utility_llm_model ?? "",
            num_ctx: preset.data.num_ctx ?? 8192,
            system_prompt: preset.data.system_prompt,
            follow_up_count: preset.data.follow_up_count,
            image_retrieval_enabled: preset.data.image_retrieval_enabled ?? false, 
            image_retriever_top_k: preset.data.image_retriever_top_k ?? 1,
            custom_base_url: (preset.data as any).custom_base_url ?? "",
            custom_api_key: (preset.data as any).custom_api_key ?? "",
        });
        setSelectedPreset(name);
        setDirty(true);
    }, [allPresets]);

    const saveAsPreset = useCallback(() => {
        const name = saveAsName.trim();
        if (!name || !activeKb) return;
        const data: PresetData = { ...kbConfig, ...session };
        const updated = [...userPresets.filter((p) => p.name !== name), { name, data }];
        setUserPresets(updated);
        persistUserPresets(activeKb.id, updated);
        setSelectedPreset(name);
        setDirty(false);
        setSaveAsName("");
        setShowSaveAs(false);
        setStatus({ type: "success", text: t("rag.statusPresetSaved", { name }) });
        setTimeout(() => setStatus({ type: "idle", text: "" }), 2500);
    }, [saveAsName, kbConfig, session, userPresets, activeKb]);

    const deletePreset = useCallback((name: string) => {
        if (!activeKb) return;
        const updated = userPresets.filter((p) => p.name !== name);
        setUserPresets(updated);
        persistUserPresets(activeKb.id, updated);
        if (selectedPreset === name) setSelectedPreset("Standard");
    }, [userPresets, selectedPreset, activeKb]);

    const kbList = kbRegistry ? Object.values(kbRegistry.bases) : [];

    // ── Render ────────────────────────────────────────────────────────────────

    return (
        <div className="flex flex-col h-full bg-card border-l border-border text-card-foreground overflow-hidden" style={{ fontFamily: "'JetBrains Mono', 'Fira Code', monospace" }}>

            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-border/60 bg-card/80 backdrop-blur-sm">
                <div>
                    <div className="text-xs font-bold tracking-widest uppercase text-blue-400">{t("rag.title")}</div>
                    {activeKb && (
                        <div className="text-[10px] text-muted-foreground mt-0.5">
                            {activeKb.chunks > 0
                                ? t("rag.chunksInfo", { chunks: activeKb.chunks, files: activeKb.files })
                                : t("rag.notIndexed")}
                        </div>
                    )}
                </div>
                <button
                    onClick={() => { fetchKbRegistry(); fetchSessionConfig(); }}
                    className="p-1.5 rounded hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
                    title={t("rag.refresh")}
                >
                    <RefreshCw size={12} />
                </button>
            </div>

            {/* KB Selector */}
            <div className="px-3 py-2 border-b border-border/60 bg-card/60 space-y-1.5">
                <div className="flex items-center gap-1">
                    <Database size={10} className="text-blue-400 flex-shrink-0" />
                    <span className="text-[9px] font-semibold uppercase tracking-widest text-blue-400">{t("rag.kbLabel")}</span>
                </div>
                <div className="flex items-center gap-1">
                    <select
                        value={activeKb?.id ?? ""}
                        onChange={(e) => switchKb(e.target.value)}
                        className="flex-1 bg-muted border border-border text-foreground text-xs rounded px-2 py-1 focus:outline-none focus:border-blue-400"
                    >
                        {kbList.map((kb) => (
                            <option key={kb.id} value={kb.id}>{kb.name}</option>
                        ))}
                    </select>
                    <button
                        onClick={() => setKbForm("create")}
                        title={t("rag.kbCreateTitle")}
                        className="p-1.5 rounded bg-muted hover:bg-muted/80 text-foreground transition-colors"
                    >
                        <Plus size={12} />
                    </button>
                    <button
                        onClick={() => setKbForm(kbForm === "edit" ? null : "edit")}
                        title={t("rag.kbEditTitle")}
                        className={`p-1.5 rounded transition-colors ${kbForm === "edit" ? "bg-blue-700 text-white" : "bg-muted hover:bg-muted/80 text-foreground"}`}
                    >
                        <Pencil size={12} />
                    </button>
                    {kbList.length > 1 && (
                        <button
                            onClick={deleteKb}
                            title={t("rag.kbDeleteTitle")}
                            className="p-1.5 rounded bg-red-900/40 hover:bg-red-800/60 text-red-400 hover:text-red-200 transition-colors"
                        >
                            <Trash2 size={12} />
                        </button>
                    )}
                </div>
                {activeKb && (
                    <div className="space-y-0.5">
                        <div className="text-[9px] text-muted-foreground uppercase tracking-widest font-semibold">{t("rag.kbDirs")}</div>
                        <div className="flex flex-wrap gap-1">
                            {activeKb.data_dirs.map((d, i) => (
                                <span key={i} className="inline-flex items-center px-1.5 py-0.5 rounded bg-muted border border-border text-[10px] text-foreground font-mono">{d}</span>
                            ))}
                            <button
                                onClick={() => setKbForm(kbForm === "edit" ? null : "edit")}
                                title={t("rag.kbDirsEditTitle")}
                                className="inline-flex items-center px-1.5 py-0.5 rounded bg-muted border border-border text-[10px] text-muted-foreground hover:text-blue-400 hover:border-blue-500/50 transition-colors"
                            >
                                <Pencil size={8} className="mr-0.5" />{t("rag.kbEditBtn")}
                            </button>
                        </div>
                        {activeKb.last_indexed && (
                            <div className="text-[9px] text-green-600/70">
                                {t("rag.indexedOn", { date: new Date(activeKb.last_indexed).toLocaleString([], { dateStyle: "short", timeStyle: "short" }) })}
                            </div>
                        )}
                    </div>
                )}
            </div>

            {/* KB Create / Edit form */}
            {kbForm === "create" && (
                <KBForm
                    onSubmit={createKb}
                    onCancel={() => setKbForm(null)}
                    isCreate={true}
                    t={t as (key: string) => string}
                />
            )}
            {kbForm === "edit" && activeKb && (
                <KBForm
                    initial={{ name: activeKb.name, data_dirs: activeKb.data_dirs }}
                    onSubmit={updateKb}
                    onCancel={() => setKbForm(null)}
                    isCreate={false}
                    t={t as (key: string) => string}
                />
            )}

            {/* Preset toolbar */}
            <div className="px-3 py-2 border-b border-border/60 bg-card/60 space-y-1.5">
                <div className="text-[9px] text-muted-foreground uppercase tracking-widest font-semibold">{t("rag.presetLabel")}</div>
                <div className="flex items-center gap-1.5">
                    <select
                        value={selectedPreset}
                        onChange={(e) => loadPreset(e.target.value)}
                        className="flex-1 bg-muted border border-border text-foreground text-xs rounded px-2 py-1 focus:outline-none focus:border-blue-400"
                    >
                        {allPresets.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
                    </select>
                    <button
                        onClick={() => { setShowSaveAs((v) => !v); setSaveAsName(""); }}
                        title={t("rag.presetSaveTitle")}
                        className="p-1.5 rounded bg-muted hover:bg-muted/80 text-foreground transition-colors"
                    >
                        <Save size={12} />
                    </button>
                    {userPresets.some((p) => p.name === selectedPreset) && (
                        <button
                            onClick={() => deletePreset(selectedPreset)}
                            title={t("rag.presetDeleteTitle")}
                            className="p-1.5 rounded bg-red-900/40 hover:bg-red-800/60 text-red-400 hover:text-red-200 transition-colors"
                        >
                            <Trash2 size={12} />
                        </button>
                    )}
                </div>
                {showSaveAs && (
                    <div className="flex items-center gap-1.5">
                        <input
                            ref={saveAsRef}
                            value={saveAsName}
                            onChange={(e) => setSaveAsName(e.target.value)}
                            onKeyDown={(e) => { if (e.key === "Enter") saveAsPreset(); if (e.key === "Escape") setShowSaveAs(false); }}
                            placeholder={t("rag.presetNamePlaceholder")}
                            className="flex-1 bg-muted border border-blue-500 text-foreground text-xs rounded px-2 py-1 focus:outline-none font-mono"
                        />
                        <button onClick={saveAsPreset} disabled={!saveAsName.trim()} className="px-2 py-1 rounded text-xs bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-40 transition-colors">OK</button>
                        <button onClick={() => setShowSaveAs(false)} className="px-2 py-1 rounded text-xs bg-muted hover:bg-accent/20 text-foreground transition-colors">✕</button>
                    </div>
                )}
                {dirty && <div className="text-[9px] text-amber-400 text-center">{t("rag.presetUnsaved")}</div>}
            </div>

            {/* Scrollable params */}
            <div className="flex-1 overflow-y-auto px-4 py-2 space-y-4 scrollbar-thin scrollbar-thumb-border scrollbar-track-transparent">

                {/* ── Prompt ── */}
                <div>
                    <SectionHeader title={t("rag.sectionPrompt")} open={sections.prompt} onToggle={() => toggle("prompt")} effect="instant" effectTitle={t("rag.effectInstant")} />
                    {sections.prompt && (
                        <div className="pt-1 space-y-0 divide-y divide-border">
                            <FieldRow label={t("rag.fieldFollowUp")} hint={t("rag.fieldFollowUpHint")}>
                                <NumberInput value={session.follow_up_count} min={0} max={10} step={1} onChange={(v) => updateSession("follow_up_count", v)} />
                            </FieldRow>
                            <div className="flex flex-col gap-1 py-2">
                                <label className="text-xs text-foreground font-medium">{t("rag.fieldSystemPrompt")}</label>
                                <p className="text-[10px] text-muted-foreground">{t("rag.fieldSystemPromptHint")}</p>
                                <textarea
                                    value={session.system_prompt}
                                    onChange={(e) => updateSession("system_prompt", e.target.value)}
                                    placeholder={t("rag.fieldSystemPromptPlaceholder")}
                                    rows={8}
                                    className="bg-muted border border-border text-foreground text-[10px] rounded px-2 py-1.5 focus:outline-none focus:border-blue-400 w-full font-mono resize-y leading-relaxed"
                                />
                            </div>
                        </div>
                    )}
                </div>

                {/* ── LLM ── */}
                <div>
                    <SectionHeader title={t("rag.sectionLlm")} open={sections.llm} onToggle={() => toggle("llm")} effect="instant" effectTitle={t("rag.effectInstant")} />
                    {sections.llm && (
                        <div className="pt-1 space-y-0 divide-y divide-border">
                            <FieldRow label={t("rag.fieldLlmBackend")}>
                                <SelectInput
                                    value={session.llm_backend}
                                    options={["ollama", "litellm", "custom"]}
                                    onChange={(v) => updateLlmBackend(v as SessionConfig["llm_backend"])}
                                />
                            </FieldRow>
                            {session.llm_backend === "ollama" && (
                                <FieldRow label={t("rag.fieldOllamaHost")} hint={t("rag.fieldOllamaHostHint")}>
                                    <input
                                        type="text"
                                        value={session.ollama_host}
                                        onChange={(e) => updateSession("ollama_host", e.target.value)}
                                        placeholder="http://192.168.1.x:11434"
                                        className="bg-muted border border-border text-foreground text-[10px] rounded px-2 py-1 focus:outline-none focus:border-blue-400 w-full font-mono"
                                    />
                                </FieldRow>
                            )}
                            <FieldRow label={t("rag.fieldLlmModel")}>
                                {session.llm_backend === "litellm" ? (
                                    litellmLoading ? (
                                        <span className="text-[10px] text-muted-foreground italic">Laden...</span>
                                    ) : litellmModels.length > 0 ? (
                                        <SelectInput
                                            value={session.llm_model || litellmModels[0]}
                                            options={litellmModels}
                                            onChange={(v) => updateSession("llm_model", v)}
                                        />
                                    ) : (
                                        <input
                                            type="text"
                                            value={session.llm_model}
                                            onChange={(e) => updateSession("llm_model", e.target.value)}
                                            placeholder="anthropic/claude-haiku-4-5-20251001"
                                            className="bg-muted border border-border text-foreground text-[10px] rounded px-2 py-1 focus:outline-none focus:border-blue-400 w-44 font-mono"
                                        />
                                    )
                                ) : session.llm_backend === "custom" ? (
                                    <input
                                        type="text"
                                        value={session.llm_model}
                                        onChange={(e) => updateSession("llm_model", e.target.value)}
                                        placeholder="claude-haiku-4-5-20251001"
                                        className="bg-muted border border-border text-foreground text-[10px] rounded px-2 py-1 focus:outline-none focus:border-blue-400 w-44 font-mono"
                                    />
                                ) : session.llm_backend === "ollama" ? (
                                    ollamaLlmLoading ? (
                                        <span className="text-[10px] text-muted-foreground italic">Laden...</span>
                                    ) : ollamaLlmModels.length > 0 ? (
                                        <SelectInput
                                            value={session.llm_model}
                                            options={ollamaLlmModels}
                                            onChange={(v) => updateSession("llm_model", v)}
                                        />
                                    ) : (
                                        <input
                                            type="text"
                                            value={session.llm_model}
                                            onChange={(e) => updateSession("llm_model", e.target.value)}
                                            placeholder="mistral-nemo:12b"
                                            className="bg-muted border border-border text-foreground text-[10px] rounded px-2 py-1 focus:outline-none focus:border-blue-400 w-44 font-mono"
                                        />
                                    )
                                ) : (
                                    <SelectInput
                                        value={session.llm_model}
                                        options={LLM_MODELS[session.llm_backend] || [session.llm_model]}
                                        onChange={(v) => updateSession("llm_model", v)}
                                    />
                                )}
                            </FieldRow>
                            {session.llm_backend === "custom" && (
                                <>
                                    <FieldRow label="Base URL" hint="OpenAI-kompatibler Endpoint, z.B. https://api.anthropic.com/v1">
                                        <input
                                            type="text"
                                            value={session.custom_base_url}
                                            onChange={(e) => updateSession("custom_base_url", e.target.value)}
                                            placeholder="https://api.anthropic.com/v1"
                                            className="bg-muted border border-border text-foreground text-[10px] rounded px-2 py-1 focus:outline-none focus:border-blue-400 w-full font-mono"
                                        />
                                    </FieldRow>
                                    <FieldRow label="API Key">
                                        <input
                                            type="password"
                                            value={session.custom_api_key}
                                            onChange={(e) => updateSession("custom_api_key", e.target.value)}
                                            placeholder="sk-ant-..."
                                            className="bg-muted border border-border text-foreground text-[10px] rounded px-2 py-1 focus:outline-none focus:border-blue-400 w-full font-mono"
                                        />
                                    </FieldRow>
                                </>
                            )}
                            <FieldRow label={t("rag.fieldTemperature")} hint={t("rag.fieldTemperatureHint")}>
                                <NumberInput value={session.llm_temperature} min={0} max={1} step={0.05} onChange={(v) => updateSession("llm_temperature", v)} />
                            </FieldRow>
                            <FieldRow label={t("rag.fieldUtilityModel")} hint={t("rag.fieldUtilityModelHint")}>
                                <input
                                    type="text"
                                    value={session.utility_llm_model}
                                    onChange={(e) => updateSession("utility_llm_model", e.target.value)}
                                    placeholder={t("rag.fieldUtilityModelPlaceholder")}
                                    className="bg-muted border border-border text-foreground text-[10px] rounded px-2 py-1 focus:outline-none focus:border-blue-400 w-full font-mono"
                                />
                            </FieldRow>
                            {session.llm_backend === "ollama" && (
                                <FieldRow label={t("rag.fieldNumCtx")} hint={t("rag.fieldNumCtxHint")}>
                                    <NumberInput value={session.num_ctx} min={512} max={131072} step={512} onChange={(v) => updateSession("num_ctx", v)} />
                                </FieldRow>
                            )}
                        </div>
                    )}
                </div>

                {/* ── Embedding ── */}
                <div>
                    {(() => {
                        const embDirty = !activeKb?.last_indexed || (
                            activeKb.embedding_backend !== kbConfig.embedding_backend ||
                            activeKb.embedding_model !== kbConfig.embedding_model ||
                            activeKb.nomic_prefix !== kbConfig.nomic_prefix ||
                            activeKb.max_file_size_mb !== kbConfig.max_file_size_mb ||
                            activeKb.embedding_batch_size !== kbConfig.embedding_batch_size ||
                            activeKb.max_chunk_tokens !== kbConfig.max_chunk_tokens ||
                            (activeKb.vs_type ?? "chromadb") !== kbConfig.vs_type
                        );
                        return <SectionHeader title={t("rag.sectionEmbedding")} open={sections.embedding} onToggle={() => toggle("embedding")} effect={embDirty ? "reindex" : "instant"} effectTitle={embDirty ? t("rag.effectReindex") : t("rag.effectInstant")} />;
                    })()}
                    {sections.embedding && (
                        <div className="pt-1 space-y-0 divide-y divide-border">
                            <FieldRow label={t("rag.fieldVsType")} hint={t("rag.fieldVsTypeHint")}>
                                <SelectInput
                                    value={kbConfig.vs_type}
                                    options={["chromadb", "pgvector"]}
                                    onChange={(v) => updateKbConfig("vs_type", v as KBConfig["vs_type"])}
                                />
                            </FieldRow>
                            {kbConfig.vs_type === "pgvector" && (
                                <FieldRow label="Connection URL" hint="postgresql://user:pass@host:5432/db">
                                    <input
                                        type="text"
                                        value={kbConfig.vs_connection_string}
                                        onChange={(e) => updateKbConfig("vs_connection_string", e.target.value)}
                                        placeholder="postgresql://user:pass@localhost:5432/rag"
                                        className="bg-muted border border-border text-foreground text-[10px] rounded px-2 py-1 focus:outline-none focus:border-blue-400 w-full font-mono"
                                    />
                                </FieldRow>
                            )}
                            <FieldRow label={t("rag.fieldEmbBackend")} hint={t("rag.fieldEmbBackendHint")}>
                                <SelectInput
                                    value={kbConfig.embedding_backend}
                                    options={["litellm", "ollama", "custom"]}
                                    onChange={(v) => updateEmbeddingBackend(v as KBConfig["embedding_backend"])}
                                />
                            </FieldRow>
                            {kbConfig.embedding_backend === "ollama" && (
                                <div className="flex flex-col gap-1 py-2 border-b border-border">
                                    <label className="text-xs text-foreground font-medium">{t("rag.fieldOllamaHost")}</label>
                                    <input
                                        type="text"
                                        value={kbConfig.embedding_ollama_host}
                                        onChange={(e) => updateKbConfig("embedding_ollama_host", e.target.value)}
                                        placeholder="http://192.168.1.x:11434"
                                        className="bg-muted border border-border text-foreground text-[10px] rounded px-2 py-1 focus:outline-none focus:border-blue-400 w-full font-mono"
                                    />
                                    {t("rag.fieldOllamaHostHint") && <p className="text-[10px] text-muted-foreground leading-relaxed">{t("rag.fieldOllamaHostHint")}</p>}
                                </div>
                            )}
                            {kbConfig.embedding_backend === "custom" && (<>
                                <FieldRow label={t("rag.fieldCustomUrl")} hint={t("rag.fieldCustomUrlHint")}>
                                    <input
                                        type="text"
                                        value={kbConfig.embedding_custom_base_url}
                                        onChange={(e) => updateKbConfig("embedding_custom_base_url", e.target.value)}
                                        placeholder="https://api.openai.com/v1"
                                        className="bg-muted border border-border text-foreground text-[10px] rounded px-2 py-1 focus:outline-none focus:border-blue-400 w-40 font-mono"
                                    />
                                </FieldRow>
                                <FieldRow label={t("rag.fieldCustomKey")}>
                                    <input
                                        type="password"
                                        value={kbConfig.embedding_custom_api_key}
                                        onChange={(e) => updateKbConfig("embedding_custom_api_key", e.target.value)}
                                        placeholder="sk-..."
                                        className="bg-muted border border-border text-foreground text-[10px] rounded px-2 py-1 focus:outline-none focus:border-blue-400 w-40 font-mono"
                                    />
                                </FieldRow>
                            </>)}
                            <FieldRow label={t("rag.fieldEmbModel")}>
                                {kbConfig.embedding_backend === "litellm" ? (
                                    litellmLoading ? (
                                        <span className="text-[10px] text-muted-foreground italic">Laden...</span>
                                    ) : litellmModels.length > 0 ? (
                                        <SelectInput
                                            value={kbConfig.embedding_model || litellmModels[0]}
                                            options={litellmModels}
                                            onChange={(v) => { updateKbConfig("embedding_model", v); updateKbConfig("nomic_prefix", false); }}
                                        />
                                    ) : (
                                        <input
                                            type="text"
                                            value={kbConfig.embedding_model}
                                            onChange={(e) => updateKbConfig("embedding_model", e.target.value)}
                                            placeholder="voyage/voyage-3"
                                            className="bg-muted border border-border text-foreground text-[10px] rounded px-2 py-1 focus:outline-none focus:border-blue-400 w-40 font-mono"
                                        />
                                    )
                                ) : kbConfig.embedding_backend === "ollama" ? (
                                    ollamaEmbedLoading ? (
                                        <span className="text-[10px] text-muted-foreground italic">Laden...</span>
                                    ) : ollamaEmbedModels.length > 0 ? (
                                        <SelectInput
                                            value={kbConfig.embedding_model || ollamaEmbedModels[0]}
                                            options={ollamaEmbedModels}
                                            onChange={(v) => { updateKbConfig("embedding_model", v); updateKbConfig("nomic_prefix", v.includes("nomic")); }}
                                        />
                                    ) : (
                                        <input
                                            type="text"
                                            value={kbConfig.embedding_model}
                                            onChange={(e) => updateKbConfig("embedding_model", e.target.value)}
                                            placeholder="nomic-embed-text"
                                            className="bg-muted border border-border text-foreground text-[10px] rounded px-2 py-1 focus:outline-none focus:border-blue-400 w-40 font-mono"
                                        />
                                    )
                                ) : (
                                    <SelectInput
                                        value={kbConfig.embedding_model}
                                        options={EMBEDDING_MODELS[kbConfig.embedding_backend] || [kbConfig.embedding_model]}
                                        onChange={(v) => { updateKbConfig("embedding_model", v); updateKbConfig("nomic_prefix", v.includes("nomic")); }}
                                    />
                                )}
                            </FieldRow>
                            <FieldRow label={t("rag.fieldNomicPrefix")} hint={t("rag.fieldNomicPrefixHint")}>
                                <Toggle checked={kbConfig.nomic_prefix} onChange={(v) => updateKbConfig("nomic_prefix", v)} />
                            </FieldRow>
                            <FieldRow label={t("rag.fieldMaxFileSize")} hint={t("rag.fieldMaxFileSizeHint")}>
                                <NumberInput value={kbConfig.max_file_size_mb} min={1} max={100} step={1} onChange={(v) => updateKbConfig("max_file_size_mb", v)} />
                            </FieldRow>
                            <FieldRow label={t("rag.fieldEmbedBatchSize")} hint={t("rag.fieldEmbedBatchSizeHint")}>
                                <NumberInput value={kbConfig.embedding_batch_size} min={5} max={1000} step={5} onChange={(v) => updateKbConfig("embedding_batch_size", v)} />
                            </FieldRow>
                            <FieldRow label={t("rag.fieldMaxChunkTokens")} hint={t("rag.fieldMaxChunkTokensHint")}>
                                <NumberInput value={kbConfig.max_chunk_tokens} min={0} max={2000} step={1} onChange={(v) => updateKbConfig("max_chunk_tokens", v)} />
                            </FieldRow>
                            <FieldRow label={t("rag.fieldPdfOcr")} hint={t("rag.fieldPdfOcrHint")}>
                                <Toggle checked={kbConfig.pdf_ocr_enabled} onChange={(v) => updateKbConfig("pdf_ocr_enabled", v)} />
                            </FieldRow>
                            <FieldRow label={t("rag.fieldImageIndexing")} hint={t("rag.fieldImageIndexingHint")}>
                                <Toggle checked={kbConfig.image_indexing_enabled} onChange={(v) => updateKbConfig("image_indexing_enabled", v)} />
                            </FieldRow>
                            {(kbConfig.image_indexing_enabled) && (
                                <FieldRow label={t("rag.fieldImageEmbeddingModel")} hint="">
                                    <input
                                        className="w-full bg-transparent border border-border rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-ring"
                                        value={kbConfig.image_embedding_model}
                                        onChange={(e) => updateKbConfig("image_embedding_model", e.target.value)}
                                    />
                                </FieldRow>
                            )}
                        </div>
                    )}
                </div>

                {/* ── Retrieval ── */}
                <div>
                    <SectionHeader title={t("rag.sectionRetrieval")} open={sections.retrieval} onToggle={() => toggle("retrieval")} effect="instant" effectTitle={t("rag.effectInstant")} />
                    {sections.retrieval && (
                        <div className="pt-1 space-y-0 divide-y divide-border">
                            <FieldRow label={t("rag.fieldTopK")} hint={t("rag.fieldTopKHint")}>
                                <NumberInput value={session.retriever_top_k} min={1} max={30} onChange={(v) => updateSession("retriever_top_k", v)} />
                            </FieldRow>
                            <FieldRow label={t("rag.fieldBm25")} hint={t("rag.fieldBm25Hint")}>
                                <Toggle checked={session.bm25_enabled} onChange={(v) => updateSession("bm25_enabled", v)} />
                            </FieldRow>
                            <FieldRow label={t("rag.fieldRrfK")} hint={t("rag.fieldRrfKHint")}>
                                <NumberInput value={session.rrf_k} min={1} max={200} step={1} onChange={(v) => updateSession("rrf_k", v)} />
                            </FieldRow>
                            <FieldRow label={t("rag.fieldQueryExp")} hint={t("rag.fieldQueryExpHint")}>
                                <NumberInput value={session.query_expansion} min={0} max={5} onChange={(v) => updateSession("query_expansion", v)} />
                            </FieldRow>
                            <FieldRow label={t("rag.fieldHyde")} hint={t("rag.fieldHydeHint")}>
                                <Toggle checked={session.hyde_enabled} onChange={(v) => updateSession("hyde_enabled", v)} />
                            </FieldRow>
                            <FieldRow label={t("rag.fieldReranking")} hint={t("rag.fieldRerankingHint")}>
                                <Toggle checked={session.reranking_enabled} onChange={(v) => updateSession("reranking_enabled", v)} />
                            </FieldRow>
                            {session.reranking_enabled && (
                                <FieldRow label={t("rag.fieldRerankPool")} hint={t("rag.fieldRerankPoolHint")}>
                                    <NumberInput value={session.reranking_candidate_pool} min={3} max={50} step={1} onChange={(v) => updateSession("reranking_candidate_pool", v)} />
                                </FieldRow>
                            )}
                            <FieldRow label={t("rag.fieldImageRetrieval")} hint={t("rag.fieldImageRetrievalHint")}>
                                <Toggle checked={session.image_retrieval_enabled} onChange={(v) => updateSession("image_retrieval_enabled", v)} />
                            </FieldRow>
                            {session.image_retrieval_enabled && (
                                <FieldRow label={t("rag.fieldImageRetrieverTopK")} hint={t("rag.fieldImageRetrieverTopKHint")}>
                                    <NumberInput value={session.image_retriever_top_k} min={1} max={4} step={1} onChange={(v) => updateSession("image_retriever_top_k", v)} />
                                </FieldRow>
                            )}
                        </div>
                    )}
                </div>
            </div>

            {/* Status bar */}
            {status.type !== "idle" && (
                <div className={`px-4 py-2 text-[10px] flex items-center gap-2 border-t border-border/60 ${
                    status.type === "error" ? "text-red-400 bg-red-900/10" :
                    status.type === "success" ? "text-blue-400 bg-blue-900/10" :
                    "text-muted-foreground"
                }`}>
                    {status.type === "loading" && <Loader2 size={10} className="animate-spin" />}
                    {status.type === "success" && <CheckCircle2 size={10} />}
                    {status.type === "error" && <AlertCircle size={10} />}
                    <span>{status.text}</span>
                </div>
            )}

            {/* Action buttons */}
            <div className="px-4 py-3 border-t border-border/60 bg-card/80 space-y-2">
                <button
                    onClick={saveAll}
                    disabled={!dirty || status.type === "loading"}
                    title={t("rag.btnSaveTitle")}
                    className={`w-full text-xs py-1.5 rounded font-medium transition-all duration-150 ${
                        dirty ? "bg-blue-600 hover:bg-blue-500 text-white" : "bg-muted text-muted-foreground cursor-not-allowed"
                    }`}
                >
                    {t("rag.btnSave")}
                </button>
                <div className="flex gap-2">
                    <button
                        onClick={() => reindex(false)}
                        disabled={status.type === "loading" || isIndexing}
                        title={isIndexing ? t("rag.btnAlreadyIndexingTitle") : t("rag.btnIncrementalTitle")}
                        className="flex-1 text-xs py-1.5 rounded font-medium bg-muted hover:bg-accent/20 text-foreground transition-colors disabled:opacity-40"
                    >
                        {isIndexing ? <span className="flex items-center justify-center gap-1"><Loader2 size={10} className="animate-spin" />{t("rag.btnIndexing")}</span> : t("rag.btnIncrementalIndex")}
                    </button>
                    <button
                        onClick={() => setShowResetConfirm(true)}
                        disabled={status.type === "loading" || isIndexing}
                        title={isIndexing ? t("rag.btnAlreadyIndexingTitle") : t("rag.btnReindexTitle")}
                        className="flex-1 text-xs py-1.5 rounded font-medium bg-amber-700/60 hover:bg-amber-600/70 text-amber-200 transition-colors disabled:opacity-40"
                    >
                        {t("rag.btnReindex")}
                    </button>
                </div>
                {showResetConfirm && (
                    <div className="rounded border border-amber-600/50 bg-amber-950/40 p-3 text-xs space-y-2">
                        <p className="text-amber-200 font-medium">{t("rag.resetConfirmTitle")}</p>
                        <p className="text-amber-200/70">
                            {activeKb && activeKb.chunks > 0
                                ? t("rag.resetConfirmBodyWithChunks", { chunks: activeKb.chunks, files: activeKb.files })
                                : t("rag.resetConfirmBody")}
                        </p>
                        <div className="flex gap-2 pt-1">
                            <button
                                onClick={() => { setShowResetConfirm(false); reindex(true); }}
                                className="flex-1 py-1 rounded bg-amber-700 hover:bg-amber-600 text-amber-100 font-medium transition-colors"
                            >
                                {t("rag.resetConfirmOk")}
                            </button>
                            <button
                                onClick={() => setShowResetConfirm(false)}
                                className="flex-1 py-1 rounded bg-muted hover:bg-accent/20 text-foreground transition-colors"
                            >
                                {t("rag.resetConfirmCancel")}
                            </button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};
