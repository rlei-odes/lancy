"use client";

import React, { FunctionComponent, useCallback, useEffect, useRef, useState } from "react";
import {
    AlertTriangle,
    CheckCircle2,
    ChevronDown,
    ChevronRight,
    ChevronUp,
    Clock,
    Download,
    FileStack,
    ListChecks,
    Loader2,
    PauseCircle,
    Plus,
    Save,
    StopCircle,
    Trash2,
    Upload,
    XCircle,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/lorem";
import { formatDuration } from "@/lib/duration";
import { fetchPile, fetchPiles, type Pile } from "@/components/sections/pile-manager";

const API_BASE = "";
const STORAGE_KEY = "batchAnalysisQuestions";
const SET_STORAGE_KEY = "batchAnalysisQuestionSet";  // name of the loaded set, if any
const POLL_MS = 2_000;         // while a job is running — same cadence as pile processing
const BUDGET_FRACTION = 0.6;   // mirrors _BUDGET_FRACTION in rag_router.py
const CHARS_PER_TOKEN = 3.5;   // same approximation the backend uses
const STATS_BATCH = 100;       // DocumentStatsRequest caps source_files at 100
const MANY_QUESTIONS = 10;     // past this, warn rather than block — the right limit is model-dependent

// ─── Types ────────────────────────────────────────────────────────────────────

type FieldType = "text" | "boolean" | "number" | "choice";
type IdField = "source_file" | "document_id";

interface QuestionRow {
    key: string;       // React list key only — never leaves the browser
    question: string;
    type: FieldType;
    choices: string;   // comma-separated; only used when type === "choice"
}

interface DocumentStat {
    source_file: string;
    chunk_count: number;
    char_count: number;
}

interface Verification {
    ids: string[];
    duplicates: number;
    ready: string[];
    missing: string[];
    overBudget: string[];
    budgetChars: number;
    llmBackend: string;
    llmModel: string;
    numCtx: number;
}

interface Issue {
    code: "noQuestions" | "noQuestion" | "choices";
    column?: string;
}

interface ResultRow {
    _id: string;
    _status: string;
    _chunk_count: number;
    _char_count: number;
    [field: string]: string | number | boolean | null;
}

/** A question as stored server-side — in a saved set, or with a job for labelling its results. */
export interface StoredQuestion {
    question: string;
    type: FieldType;
    choices: string;
}

export interface QuestionSet {
    name: string;
    rows: StoredQuestion[];
}

/** The display form a job carries; also what the JSON download states. */
export interface JobQuestion {
    field: string;
    question: string;
    type: FieldType;
    choices?: string[];
}

export interface JobResult {
    ident: string;
    status: string;
    chunk_count: number;
    char_count: number;
    result: Record<string, unknown> | null;
    elapsed_s: number;
}

export type JobStatus = "running" | "done" | "cancelled" | "interrupted" | "failed";

export interface BatchJob {
    status: JobStatus;
    kb_id: string | null;
    pile_id: string | null;
    id_field: IdField;
    prompt: string;
    schema: Record<string, unknown>;
    questions: JobQuestion[];
    ids: string[];
    current_ident: string;
    cancel_requested: boolean;
    error: string;
    started_at: string;
    updated_at: string;
    results: JobResult[];
}

/** What decides whether a job's results answer the current questions. Mirrors
 *  `_definition()` in batch_router.py. */
export interface JobDefinition {
    kbId: string | null;
    pileId: string | null;
    idField: IdField;
    prompt: string;
    schema: Record<string, unknown>;
}

// ─── Question rows → prompt + schema ──────────────────────────────────────────

function newRow(): QuestionRow {
    return {
        key: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        question: "",
        type: "text",
        choices: "",
    };
}

/** Schema property names are positional and not user-editable: the LLM only needs them
 *  to line the QUESTIONS block up with the schema, and a fixed `questionN` does that
 *  without anything for the user to get wrong. Readability is handled at export time
 *  by exportColumn(). */
export function fieldName(index: number): string {
    return `question${index + 1}`;
}

/** `question3_how_many_towers_are_menti` — the export header only. Keeps the number so
 *  columns stay in question order, appends a slug so the CSV is readable on its own. */
export function exportColumn(row: Pick<QuestionRow, "question">, index: number): string {
    const slug = row.question
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "_")
        .replace(/^_+|_+$/g, "")
        .slice(0, 40)
        .replace(/_+$/, "");
    return slug ? `${fieldName(index)}_${slug}` : fieldName(index);
}

export function parseChoices(raw: string): string[] {
    const seen = new Set<string>();
    return raw
        .split(",")
        .map((s) => s.trim())
        .filter((s) => s && !seen.has(s) && seen.add(s));
}

/** Every property is required and additionalProperties is false — OpenAI-compatible
 *  backends reject anything looser under strict mode. */
export function buildSchema(rows: QuestionRow[]): Record<string, unknown> {
    const properties: Record<string, unknown> = {};
    for (const [i, r] of rows.entries()) {
        const base: Record<string, unknown> = { description: r.question.trim() };
        switch (r.type) {
            case "boolean": base.type = "boolean"; break;
            // Nullable so "not stated in this document" has a legal answer. Without it
            // strict decoding forces a number, and the honesty rule turns "couldn't tell"
            // into a confident 0.
            case "number":  base.type = ["number", "null"]; break;
            case "choice":  base.type = "string"; base.enum = parseChoices(r.choices); break;
            default:        base.type = "string";
        }
        properties[fieldName(i)] = base;
    }
    return {
        type: "object",
        properties,
        required: rows.map((_, i) => fieldName(i)),
        additionalProperties: false,
    };
}

/** The schema carries the answer format (enum values, types), so the prompt stays
 *  the plain question text — no generated format hints to translate. */
export function buildPrompt(rows: QuestionRow[]): string {
    return rows.map((r, i) => `${fieldName(i)}: ${r.question.trim()}`).join("\n");
}

/** Problems the builder can still produce. Anything not listed here is structurally
 *  impossible, which is why there is no schema linting step. */
export function validateRows(rows: QuestionRow[]): Issue[] {
    const issues: Issue[] = [];
    if (rows.length === 0) issues.push({ code: "noQuestions" });
    for (const [i, r] of rows.entries()) {
        const column = fieldName(i);
        if (!r.question.trim()) issues.push({ code: "noQuestion", column });
        if (r.type === "choice" && parseChoices(r.choices).length < 2)
            issues.push({ code: "choices", column });
    }
    return issues;
}

// ─── Identifier list ──────────────────────────────────────────────────────────

/** Mirrors read_id_list() in scripts/batch-analyze.py: blanks and '#' comments are
 *  dropped, repeats collapse to the first occurrence. */
export function parseIdList(text: string): { ids: string[]; duplicates: number } {
    const ids: string[] = [];
    const seen = new Set<string>();
    let duplicates = 0;
    for (const line of text.split("\n")) {
        const s = line.trim();
        if (!s || s.startsWith("#")) continue;
        if (seen.has(s)) { duplicates++; continue; }
        seen.add(s);
        ids.push(s);
    }
    return { ids, duplicates };
}

// ─── Question sets ────────────────────────────────────────────────────────────

/** Drop the React key — it identifies a row on screen, not a question. */
export function toStoredRows(rows: QuestionRow[]): StoredQuestion[] {
    return rows.map(({ question, type, choices }) => ({ question, type, choices }));
}

const FIELD_TYPES: FieldType[] = ["text", "boolean", "number", "choice"];

/** Saved sets come back from the server; anything malformed is dropped rather than
 *  trusted, and a set that leaves nothing usable yields the usual single empty row. */
export function fromStoredRows(stored: unknown): QuestionRow[] {
    const rows = (Array.isArray(stored) ? stored : [])
        .filter((s): s is StoredQuestion => !!s && typeof s.question === "string")
        .map((s) => ({
            ...newRow(),
            question: s.question,
            type: FIELD_TYPES.includes(s.type) ? s.type : "text",
            choices: typeof s.choices === "string" ? s.choices : "",
        }));
    return rows.length ? rows : [newRow()];
}

export function sameQuestions(rows: QuestionRow[], stored: StoredQuestion[]): boolean {
    return JSON.stringify(toStoredRows(rows)) === JSON.stringify(toStoredRows(fromStoredRows(stored)));
}

// ─── Jobs ─────────────────────────────────────────────────────────────────────

export function jobQuestions(rows: QuestionRow[]): JobQuestion[] {
    return rows.map((r, i) => ({
        field: fieldName(i),
        question: r.question.trim(),
        type: r.type,
        ...(r.type === "choice" ? { choices: parseChoices(r.choices) } : {}),
    }));
}

/** Key order must not decide equality — the backend compares with sort_keys. */
export function stableStringify(v: unknown): string {
    if (Array.isArray(v)) return `[${v.map(stableStringify).join(",")}]`;
    if (v && typeof v === "object") {
        const entries = Object.entries(v as Record<string, unknown>).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
        return `{${entries.map(([k, val]) => `${JSON.stringify(k)}:${stableStringify(val)}`).join(",")}}`;
    }
    return JSON.stringify(v);
}

export function sameDefinition(job: BatchJob, def: JobDefinition): boolean {
    return (
        (job.kb_id ?? null) === def.kbId &&
        (job.pile_id ?? null) === def.pileId &&
        job.id_field === def.idField &&
        job.prompt === def.prompt &&
        stableStringify(job.schema) === stableStringify(def.schema)
    );
}

/** The identifiers a run would analyze: everything ready, minus what the job already
 *  answered — but only when the job answers the same questions. Failed documents
 *  count as unanswered, so they are retried. */
export function idsToRun(job: BatchJob | null, ready: string[], def: JobDefinition): string[] {
    if (!job || !sameDefinition(job, def)) return ready;
    const answered = new Set(job.results.filter((r) => !r.status.startsWith("error")).map((r) => r.ident));
    return ready.filter((id) => !answered.has(id));
}

export function jobCounts(job: BatchJob): { total: number; done: number; ok: number; skipped: number; errors: number } {
    let ok = 0, skipped = 0, errors = 0;
    for (const r of job.results) {
        if (r.status === "ok") ok++;
        else if (r.status.startsWith("error")) errors++;
        else skipped++;
    }
    return { total: Math.max(job.ids.length, job.results.length), done: job.results.length, ok, skipped, errors };
}

export function toResultRow(r: JobResult): ResultRow {
    const row: ResultRow = { _id: r.ident, _status: r.status, _chunk_count: r.chunk_count, _char_count: r.char_count };
    for (const [k, v] of Object.entries(r.result ?? {})) {
        row[k] = typeof v === "object" && v !== null ? JSON.stringify(v) : (v as string | number | boolean);
    }
    return row;
}

// ─── Export helpers ───────────────────────────────────────────────────────────

export function csvCell(v: unknown): string {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** `keys` index the result rows, `headers` are what the file shows — they differ because
 *  the schema fields stay `questionN` while the export spells the question out. */
export function toCsv(keys: string[], headers: string[], rows: ResultRow[]): string {
    const lines = [headers.map(csvCell).join(",")];
    for (const row of rows) lines.push(keys.map((k) => csvCell(row[k])).join(","));
    return lines.join("\n");
}

function download(filename: string, content: string, mime: string): void {
    const url = URL.createObjectURL(new Blob([content], { type: mime }));
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
}

// ─── API ──────────────────────────────────────────────────────────────────────

async function fetchConfig(): Promise<{ llm_backend: string; llm_model: string; num_ctx: number }> {
    const r = await fetch(`${API_BASE}/api/v1/rag/config`, { credentials: "include" });
    if (!r.ok) throw new Error("Failed to load session config");
    return r.json();
}

async function fetchTemplate(): Promise<string> {
    const r = await fetch(`${API_BASE}/api/v1/rag/analyze-prompt-template`, { credentials: "include" });
    if (!r.ok) throw new Error("Failed to load prompt template");
    return (await r.json()).template ?? "";
}

// The session's selected KB (set by the RAG config panel). Selecting a pooled KB is
// session-local, so requests must name it — the backend's default is the pool's
// global active KB, which may be a different one.
function selectedKbId(): string | null {
    return sessionStorage.getItem("lancy_selected_kb_id");
}

async function fetchDocStats(
    ids: string[],
    idField: IdField,
    pileId: string | null,
    kbId: string | null
): Promise<DocumentStat[]> {
    const out: DocumentStat[] = [];
    for (let i = 0; i < ids.length; i += STATS_BATCH) {
        const r = await fetch(`${API_BASE}/api/v1/rag/document-stats`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                source_files: ids.slice(i, i + STATS_BATCH),
                id_field: idField,
                ...(pileId ? { pile_id: pileId } : kbId ? { kb_id: kbId } : {}),
            }),
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({}));
            throw new Error(err.detail ?? "Document stats failed");
        }
        out.push(...((await r.json()).stats ?? []));
    }
    return out;
}

async function batchRequest<T>(path: string, init?: RequestInit): Promise<T> {
    const r = await fetch(`${API_BASE}/api/v1/batch${path}`, {
        credentials: "include",
        ...init,
        headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    });
    if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error((err as { detail?: string }).detail ?? `HTTP ${r.status}`);
    }
    return r.json();
}

const fetchJob = () => batchRequest<BatchJob | null>("/job");
const fetchQuestionSets = () => batchRequest<QuestionSet[]>("/question-sets");

// ─── Layout ───────────────────────────────────────────────────────────────────

const Card: FunctionComponent<{ step: number; title: string; icon: React.ReactNode; children: React.ReactNode }> = ({
    step, title, icon, children,
}) => (
    <div className="rounded-xl border border-border bg-card shadow-xs overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-border bg-muted/30">
            <span className="flex items-center justify-center h-5 w-5 rounded-full bg-muted text-[10px] font-mono font-semibold text-muted-foreground">
                {step}
            </span>
            {icon}
            <span className="text-sm font-medium">{title}</span>
        </div>
        <div className="p-4">{children}</div>
    </div>
);

/** Live view of the server-side job: a segmented bar (ok / skipped / error), the
 *  document in flight, timing, and what the user may do meanwhile. */
const BatchStatus: FunctionComponent<{ job: BatchJob }> = ({ job }) => {
    const { t } = useTranslation("app");
    const running = job.status === "running";

    // Ticks the elapsed clock between polls; the bar itself moves with the poll.
    const [now, setNow] = useState(() => Date.now());
    useEffect(() => {
        if (!running) return;
        const id = setInterval(() => setNow(Date.now()), 1_000);
        return () => clearInterval(id);
    }, [running]);

    const c = jobCounts(job);
    const pct = (n: number) => (c.total ? (n / c.total) * 100 : 0);
    const remaining = c.total - c.done;
    const timed = job.results.filter((r) => r.elapsed_s > 0);
    const avg = timed.length ? timed.reduce((a, r) => a + r.elapsed_s, 0) / timed.length : 0;
    const elapsed = ((running ? now : Date.parse(job.updated_at)) - Date.parse(job.started_at)) / 1000;

    const head = {
        running:     { icon: <Loader2 className="h-4 w-4 animate-spin text-primary" />, label: t("explorer.batchStatusRunning") },
        done:        { icon: <CheckCircle2 className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />, label: t("explorer.batchStatusDone") },
        cancelled:   { icon: <StopCircle className="h-4 w-4 text-muted-foreground" />, label: t("explorer.batchStatusCancelled") },
        interrupted: { icon: <PauseCircle className="h-4 w-4 text-amber-600 dark:text-amber-400" />, label: t("explorer.batchStatusInterrupted") },
        failed:      { icon: <XCircle className="h-4 w-4 text-destructive" />, label: t("explorer.batchStatusFailed") },
    }[job.status];

    return (
        <div className="rounded-lg border border-border bg-muted/20 p-3 space-y-2.5">
            <div className="flex items-center gap-2">
                {head.icon}
                <span className="text-sm font-medium">{head.label}</span>
                <span className="ml-auto text-xs font-mono text-muted-foreground">
                    {t("explorer.batchProgress", { done: c.done, total: c.total })} · {Math.round(pct(c.done))}%
                </span>
            </div>

            <div className="relative h-2.5 w-full rounded-full bg-muted overflow-hidden flex">
                <div className="h-full bg-emerald-500 transition-all duration-500" style={{ width: `${pct(c.ok)}%` }} />
                <div className="h-full bg-amber-500 transition-all duration-500" style={{ width: `${pct(c.skipped)}%` }} />
                <div className="h-full bg-destructive transition-all duration-500" style={{ width: `${pct(c.errors)}%` }} />
                {running && remaining > 0 && (
                    // The document in flight: one slot wide, pulsing.
                    <div className="h-full bg-primary/40 animate-pulse" style={{ width: `${pct(1)}%` }} />
                )}
            </div>

            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                <span className="flex items-center gap-1.5">
                    <span className="h-2 w-2 rounded-full bg-emerald-500" />{t("explorer.batchStatusOk", { n: c.ok })}
                </span>
                <span className="flex items-center gap-1.5">
                    <span className="h-2 w-2 rounded-full bg-amber-500" />{t("explorer.batchStatusSkipped", { n: c.skipped })}
                </span>
                <span className="flex items-center gap-1.5">
                    <span className="h-2 w-2 rounded-full bg-destructive" />{t("explorer.batchStatusErrors", { n: c.errors })}
                </span>
                <span className="ml-auto flex items-center gap-1.5 font-mono">
                    <Clock className="h-3 w-3" />
                    {t("explorer.batchStatusElapsed", { time: formatDuration(elapsed) })}
                    {running && avg > 0 && remaining > 0 && ` · ${t("explorer.batchEta", { time: formatDuration(avg * remaining) })}`}
                </span>
            </div>

            {running && (job.cancel_requested ? (
                <p className="text-xs text-amber-600 dark:text-amber-400">{t("explorer.batchStatusStopping")}</p>
            ) : job.current_ident && (
                <p className="text-xs font-mono truncate" title={job.current_ident}>
                    {t("explorer.batchStatusCurrent", { name: job.current_ident })}
                </p>
            ))}
            {running && (
                <p className="text-xs text-muted-foreground">{t("explorer.batchStatusLeaveHint")}</p>
            )}
            {job.status === "interrupted" && remaining > 0 && (
                <p className="text-xs text-amber-600 dark:text-amber-400">{t("explorer.batchStatusInterruptedHint", { n: remaining })}</p>
            )}
            {job.status === "cancelled" && remaining > 0 && (
                <p className="text-xs text-muted-foreground">{t("explorer.batchStatusResumeHint", { n: remaining })}</p>
            )}
            {job.status === "failed" && job.error && (
                <p className="text-xs text-destructive font-mono">{job.error}</p>
            )}
        </div>
    );
};

const inputCls =
    "bg-background border border-border rounded-md px-2 py-1.5 text-xs outline-hidden focus:border-muted-foreground/50 transition-colors";

// ─── Component ────────────────────────────────────────────────────────────────

export const BatchAnalysis: FunctionComponent = () => {
    const { t } = useTranslation("app");

    const [idField, setIdField] = useState<IdField>("source_file");
    const [docText, setDocText] = useState("");
    // null = the active Knowledge Base; otherwise one of the user's own piles.
    const [pileId, setPileId] = useState<string | null>(null);
    const [piles, setPiles] = useState<Pile[]>([]);
    const [rows, setRows] = useState<QuestionRow[]>([]);
    const [template, setTemplate] = useState("");
    const [showPrompt, setShowPrompt] = useState(false);

    const [verifying, setVerifying] = useState(false);
    const [verification, setVerification] = useState<Verification | null>(null);
    const [error, setError] = useState<string | null>(null);

    // The server-side job: the run's definition, progress and results.
    const [job, setJob] = useState<BatchJob | null>(null);
    const [starting, setStarting] = useState(false);
    const fileRef = useRef<HTMLInputElement>(null);

    // Read in an effect, not during render: sessionStorage does not exist server-side.
    const [kbId, setKbId] = useState<string | null>(null);

    const [sets, setSets] = useState<QuestionSet[]>([]);
    const [setName, setSetName] = useState<string | null>(null);        // the loaded set
    const [saveAsName, setSaveAsName] = useState<string | null>(null);  // null = input hidden
    const [setNotice, setSetNotice] = useState<string | null>(null);

    // Restore the question set — without raw mode a reload would otherwise lose it
    useEffect(() => {
        try {
            setSetName(localStorage.getItem(SET_STORAGE_KEY));
            const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
            if (Array.isArray(saved) && saved.length) { setRows(saved); return; }
        } catch { /* corrupt entry — fall through to the seed row */ }
        setRows([newRow()]);
    }, []);

    useEffect(() => {
        if (rows.length) localStorage.setItem(STORAGE_KEY, JSON.stringify(rows));
    }, [rows]);

    useEffect(() => {
        try {
            if (setName) localStorage.setItem(SET_STORAGE_KEY, setName);
            else localStorage.removeItem(SET_STORAGE_KEY);
        } catch { /* storage unavailable — the loaded name is a convenience only */ }
    }, [setName]);

    useEffect(() => { fetchQuestionSets().then(setSets).catch(() => {}); }, []);

    useEffect(() => {
        setKbId(selectedKbId());
        const handler = () => { setKbId(selectedKbId()); setVerification(null); };
        window.addEventListener("lancy-kb-changed", handler);
        return () => window.removeEventListener("lancy-kb-changed", handler);
    }, []);

    // Coming back to the page: show the job, and the documents it was run over.
    useEffect(() => {
        fetchJob()
            .then((j) => {
                setJob(j);
                if (!j) return;
                setDocText(j.ids.join("\n"));
                setPileId(j.pile_id);
                setIdField(j.id_field);
            })
            .catch(() => {});
    }, []);

    const jobStatus = job?.status;
    useEffect(() => {
        if (jobStatus !== "running") return;
        const id = setInterval(() => { fetchJob().then(setJob).catch(() => {}); }, POLL_MS);
        return () => clearInterval(id);
    }, [jobStatus]);

    useEffect(() => { fetchTemplate().then(setTemplate).catch(() => {}); }, []);

    // Only processed piles can be analyzed. `has_store` rather than
    // `embedding_current`: this path reads chunk text and never embeds, so a pile
    // built with a since-replaced embedding model is still perfectly analyzable.
    useEffect(() => {
        fetchPiles()
            .then((all) => setPiles(all.filter((p) => p.has_store)))
            .catch(() => {});
    }, []);

    // Selecting a pile fills the identifier list from its files, so the common case
    // needs no typing. The textarea stays editable for trimming the list down.
    const selectPile = useCallback(async (id: string | null) => {
        setPileId(id);
        setVerification(null);
        setError(null);
        if (id === null) {
            setDocText("");
            return;
        }
        setIdField("source_file");  // a pile's chunks carry no DMS document_id
        try {
            const detail = await fetchPile(id);
            setDocText(detail.files.map((f) => f.source_file).join("\n"));
        } catch (e) {
            setError(e instanceof Error ? e.message : "Could not load pile");
        }
    }, []);

    const { ids, duplicates } = parseIdList(docText);
    const issues = validateRows(rows);
    const schema = buildSchema(rows);
    const prompt = buildPrompt(rows);
    const sourceKb = pileId ? null : kbId;
    const definition: JobDefinition = { kbId: sourceKb, pileId, idField, prompt, schema };

    const running = job?.status === "running";
    const todo = idsToRun(job, verification?.ready ?? [], definition);

    // Results are labelled by the questions they answer, which are the job's — the
    // editor may already hold different ones.
    const jobQs = job?.questions ?? [];
    const results = job ? job.results.map(toResultRow) : [];
    const meta = ["_id", "_status", "_chunk_count", "_char_count"];
    const columns = [...meta, ...jobQs.map((q) => q.field)];
    const exportHeaders = [...meta, ...jobQs.map(exportColumn)];

    const loadedSet = sets.find((s) => s.name === setName) ?? null;
    const setModified = !!loadedSet && !sameQuestions(rows, loadedSet.rows);

    const patchRow = (key: string, patch: Partial<QuestionRow>) =>
        setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch } : r)));

    const moveRow = (index: number, delta: number) =>
        setRows((prev) => {
            const next = [...prev];
            const target = index + delta;
            if (target < 0 || target >= next.length) return prev;
            [next[index], next[target]] = [next[target], next[index]];
            return next;
        });

    const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (file) setDocText(await file.text());
        e.target.value = "";  // allow re-picking the same file
        setVerification(null);
    };

    const handleVerify = useCallback(async () => {
        setVerifying(true);
        setError(null);
        setVerification(null);
        try {
            const cfg = await fetchConfig();
            const budgetChars = Math.floor(cfg.num_ctx * BUDGET_FRACTION * CHARS_PER_TOKEN);
            const stats = await fetchDocStats(ids, idField, pileId, sourceKb);
            const byId = new Map(stats.map((s) => [s.source_file, s]));

            const ready: string[] = [];
            const missing: string[] = [];
            const overBudget: string[] = [];
            for (const id of ids) {
                const s = byId.get(id);
                if (!s || s.chunk_count === 0) missing.push(id);
                else if (s.char_count > budgetChars) overBudget.push(id);
                else ready.push(id);
            }
            setVerification({
                ids, duplicates, ready, missing, overBudget, budgetChars,
                llmBackend: cfg.llm_backend, llmModel: cfg.llm_model, numCtx: cfg.num_ctx,
            });
        } catch (e) {
            setError(e instanceof Error ? e.message : "Verification failed");
        } finally {
            setVerifying(false);
        }
    }, [ids, duplicates, idField, pileId, sourceKb]);

    const handleRun = async () => {
        setError(null);
        // Results of a job with other questions or another source would read as
        // answers to these questions, so the server replaces that job — after a yes.
        const replace = !!job && job.results.length > 0 && !sameDefinition(job, definition);
        if (replace && !window.confirm(t("explorer.batchConfirmReplace", { n: job.results.length }))) return;
        setStarting(true);
        try {
            setJob(await batchRequest<BatchJob>("/job", {
                method: "POST",
                body: JSON.stringify({
                    ids: todo,
                    id_field: idField,
                    prompt,
                    response_schema: schema,
                    questions: jobQuestions(rows),
                    ...(pileId ? { pile_id: pileId } : sourceKb ? { kb_id: sourceKb } : {}),
                    replace,
                }),
            }));
        } catch (e) {
            setError(e instanceof Error ? e.message : "Could not start the analysis");
        } finally {
            setStarting(false);
        }
    };

    const handleStop = async () => {
        try {
            await batchRequest("/job/cancel", { method: "POST" });
            setJob(await fetchJob());
        } catch (e) {
            setError(e instanceof Error ? e.message : "Could not stop the analysis");
        }
    };

    const handleClear = async () => {
        try {
            await batchRequest("/job", { method: "DELETE" });
            setJob(null);
        } catch (e) {
            setError(e instanceof Error ? e.message : "Could not clear the results");
        }
    };

    // ── Question sets ─────────────────────────────────────────────────────────

    const loadSet = (name: string) => {
        const target = sets.find((s) => s.name === name);
        if (!target) return;
        const unsaved = rows.some((r) => r.question.trim()) && !sameQuestions(rows, target.rows) && (!loadedSet || setModified);
        if (unsaved && !window.confirm(t("explorer.batchSetsConfirmLoad", { name }))) return;
        setRows(fromStoredRows(target.rows));
        setSetName(name);
    };

    const saveSet = async (name: string) => {
        const n = name.trim();
        if (!n) return;
        try {
            await batchRequest(`/question-sets/${encodeURIComponent(n)}`, {
                method: "PUT",
                body: JSON.stringify({ rows: toStoredRows(rows) }),
            });
            setSets(await fetchQuestionSets());
            setSetName(n);
            setSaveAsName(null);
            setSetNotice(t("explorer.batchSetsSaved", { name: n }));
            setTimeout(() => setSetNotice(null), 2_500);
        } catch (e) {
            setError(e instanceof Error ? e.message : "Could not save the question set");
        }
    };

    const deleteSet = async () => {
        if (!setName || !window.confirm(t("explorer.batchSetsConfirmDelete", { name: setName }))) return;
        try {
            await batchRequest(`/question-sets/${encodeURIComponent(setName)}`, { method: "DELETE" });
            setSets(await fetchQuestionSets());
            setSetName(null);
        } catch (e) {
            setError(e instanceof Error ? e.message : "Could not delete the question set");
        }
    };

    const statusTone = (status: string) =>
        status === "ok" ? "text-emerald-600 dark:text-emerald-400"
            : status.startsWith("error") ? "text-destructive"
                : "text-amber-600 dark:text-amber-400";

    return (
        <div className="flex flex-col gap-6 w-full">
            {/* Intro */}
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 flex gap-2.5">
                <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
                <div className="text-xs leading-relaxed">
                    <p className="font-medium mb-1">{t("explorer.batchIntroTitle")}</p>
                    <p className="text-muted-foreground">{t("explorer.batchIntroBody")}</p>
                </div>
            </div>

            {/* 1 — Documents */}
            <Card step={1} title={t("explorer.batchDocsTitle")} icon={<FileStack className="h-4 w-4 text-muted-foreground" />}>
                <div className="flex flex-col gap-3">
                    <div className="flex flex-wrap items-center gap-3">
                        <label className="text-xs text-muted-foreground">{t("explorer.batchSourceLabel")}</label>
                        <select
                            value={pileId ?? ""}
                            onChange={(e) => selectPile(e.target.value || null)}
                            className={inputCls}
                        >
                            <option value="">{t("explorer.batchSourceKb")}</option>
                            {piles.map((p) => (
                                <option key={p.id} value={p.id}>{p.name}</option>
                            ))}
                        </select>
                        {pileId === null && piles.length === 0 && (
                            <span className="text-xs text-muted-foreground/70">
                                {t("explorer.batchSourceNoPiles")}
                            </span>
                        )}
                    </div>
                    <div className="flex flex-wrap items-center gap-3">
                        <label className="text-xs text-muted-foreground">{t("explorer.batchIdFieldLabel")}</label>
                        <select
                            value={idField}
                            onChange={(e) => { setIdField(e.target.value as IdField); setVerification(null); }}
                            disabled={pileId !== null}
                            className={cn(inputCls, pileId !== null && "opacity-50")}
                        >
                            <option value="source_file">source_file</option>
                            <option value="document_id">document_id</option>
                        </select>
                        <span className="text-xs text-muted-foreground/70">
                            {pileId !== null
                                ? t("explorer.batchIdFieldHintPile")
                                : t(idField === "source_file" ? "explorer.batchIdFieldHintFile" : "explorer.batchIdFieldHintDoc")}
                        </span>
                        <button
                            onClick={() => fileRef.current?.click()}
                            className="ml-auto flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs border border-border hover:bg-muted transition-colors"
                        >
                            <Upload className="h-3.5 w-3.5" /> {t("explorer.batchUploadDocs")}
                        </button>
                        <input ref={fileRef} type="file" accept=".txt,text/plain" onChange={handleUpload} className="hidden" />
                    </div>
                    <textarea
                        value={docText}
                        onChange={(e) => { setDocText(e.target.value); setVerification(null); }}
                        placeholder={t("explorer.batchDocsPlaceholder")}
                        rows={6}
                        className="w-full resize-y bg-background border border-border rounded-md px-3 py-2 text-xs font-mono outline-hidden focus:border-muted-foreground/50 placeholder:text-muted-foreground/40"
                    />
                    <span className="text-xs text-muted-foreground font-mono">
                        {t("explorer.batchDocsCount", { n: ids.length })}
                        {duplicates > 0 && ` · ${t("explorer.batchDocsDuplicates", { n: duplicates })}`}
                    </span>
                </div>
            </Card>

            {/* 2 — Questions */}
            <Card step={2} title={t("explorer.batchQuestionsTitle")} icon={<ListChecks className="h-4 w-4 text-muted-foreground" />}>
                <div className="flex flex-col gap-3">
                    {/* Saved question sets — loading one makes it the active set in the editor */}
                    <div className="flex flex-wrap items-center gap-2 pb-3 border-b border-border">
                        <label className="text-xs text-muted-foreground">{t("explorer.batchSetsLabel")}</label>
                        <select
                            value={setName ?? ""}
                            onChange={(e) => e.target.value && loadSet(e.target.value)}
                            disabled={running}
                            className={cn(inputCls, "min-w-48")}
                        >
                            <option value="">{t("explorer.batchSetsUnsaved")}</option>
                            {sets.map((s) => (
                                <option key={s.name} value={s.name}>{s.name}</option>
                            ))}
                        </select>
                        {setModified && (
                            <span className="text-xs text-amber-600 dark:text-amber-400">{t("explorer.batchSetsModified")}</span>
                        )}
                        {saveAsName === null ? (
                            <>
                                {loadedSet && (
                                    <button
                                        onClick={() => saveSet(loadedSet.name)}
                                        disabled={!setModified || issues.length > 0}
                                        className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs border border-border hover:bg-muted transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                                    >
                                        <Save className="h-3.5 w-3.5" /> {t("explorer.batchSetsSave")}
                                    </button>
                                )}
                                <button
                                    onClick={() => setSaveAsName(loadedSet ? "" : setName ?? "")}
                                    disabled={issues.length > 0}
                                    className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs border border-border hover:bg-muted transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                                >
                                    {!loadedSet && <Save className="h-3.5 w-3.5" />} {t("explorer.batchSetsSaveAs")}
                                </button>
                                {loadedSet && (
                                    <button
                                        onClick={deleteSet}
                                        title={t("explorer.batchSetsDelete")}
                                        className="p-1.5 rounded-md text-muted-foreground hover:text-destructive hover:bg-muted transition-colors"
                                    >
                                        <Trash2 className="h-3.5 w-3.5" />
                                    </button>
                                )}
                            </>
                        ) : (
                            <form
                                className="flex items-center gap-2"
                                onSubmit={(e) => { e.preventDefault(); saveSet(saveAsName); }}
                            >
                                <input
                                    autoFocus
                                    value={saveAsName}
                                    onChange={(e) => setSaveAsName(e.target.value)}
                                    maxLength={100}
                                    placeholder={t("explorer.batchSetsNamePlaceholder")}
                                    className={cn(inputCls, "min-w-48")}
                                />
                                <button
                                    type="submit"
                                    disabled={!saveAsName.trim()}
                                    className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                                >
                                    <Save className="h-3.5 w-3.5" /> {t("explorer.batchSetsSave")}
                                </button>
                                <button
                                    type="button"
                                    onClick={() => setSaveAsName(null)}
                                    className="px-2.5 py-1.5 rounded-md text-xs border border-border hover:bg-muted transition-colors"
                                >
                                    {t("explorer.batchSetsCancel")}
                                </button>
                            </form>
                        )}
                        {setNotice && <span className="text-xs text-emerald-600 dark:text-emerald-400">{setNotice}</span>}
                    </div>

                    {rows.map((row, i) => {
                        const choices = parseChoices(row.choices);
                        return (
                            <div key={row.key} className="rounded-lg border border-border bg-muted/20 p-3 flex flex-col gap-2">
                                <div className="flex flex-wrap items-center gap-2">
                                    <span
                                        className="px-2 py-1 rounded-md bg-muted text-xs font-mono text-muted-foreground select-none"
                                        title={t("explorer.batchColumnHint")}
                                    >
                                        {fieldName(i)}
                                    </span>
                                    <select
                                        value={row.type}
                                        onChange={(e) => {
                                            const type = e.target.value as FieldType;
                                            // Suggest an escape hatch the first time a row becomes a choice —
                                            // without one, strict decoding forces a wrong answer when none fits.
                                            const seed = type === "choice" && !row.choices.trim()
                                                ? { choices: t("explorer.batchChoicesDefault") }
                                                : {};
                                            patchRow(row.key, { type, ...seed });
                                        }}
                                        className={inputCls}
                                    >
                                        <option value="text">{t("explorer.batchTypeText")}</option>
                                        <option value="boolean">{t("explorer.batchTypeBoolean")}</option>
                                        <option value="number">{t("explorer.batchTypeNumber")}</option>
                                        <option value="choice">{t("explorer.batchTypeChoice")}</option>
                                    </select>
                                    <div className="ml-auto flex items-center gap-0.5">
                                        <button
                                            onClick={() => moveRow(i, -1)}
                                            disabled={i === 0}
                                            className="p-1 rounded hover:bg-muted disabled:opacity-25 transition-colors"
                                        >
                                            <ChevronUp className="h-3.5 w-3.5" />
                                        </button>
                                        <button
                                            onClick={() => moveRow(i, 1)}
                                            disabled={i === rows.length - 1}
                                            className="p-1 rounded hover:bg-muted disabled:opacity-25 transition-colors"
                                        >
                                            <ChevronDown className="h-3.5 w-3.5" />
                                        </button>
                                        <button
                                            onClick={() => setRows((prev) => prev.filter((r) => r.key !== row.key))}
                                            className="p-1 rounded hover:bg-destructive/10 hover:text-destructive transition-colors"
                                        >
                                            <Trash2 className="h-3.5 w-3.5" />
                                        </button>
                                    </div>
                                </div>
                                <textarea
                                    value={row.question}
                                    onChange={(e) => patchRow(row.key, { question: e.target.value })}
                                    placeholder={t("explorer.batchQuestionPlaceholder")}
                                    rows={2}
                                    className="w-full resize-y bg-background border border-border rounded-md px-2 py-1.5 text-xs outline-hidden focus:border-muted-foreground/50 placeholder:text-muted-foreground/40"
                                />
                                {row.type === "choice" && (
                                    <div className="flex flex-col gap-1.5">
                                        <input
                                            value={row.choices}
                                            onChange={(e) => patchRow(row.key, { choices: e.target.value })}
                                            placeholder={t("explorer.batchChoicesPlaceholder")}
                                            className={cn(inputCls, "w-full")}
                                        />
                                        {choices.length > 0 && (
                                            <div className="flex flex-wrap gap-1">
                                                {choices.map((c) => (
                                                    <span key={c} className="px-2 py-0.5 rounded-full bg-muted text-[11px] font-mono text-muted-foreground">
                                                        {c}
                                                    </span>
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>
                        );
                    })}

                    <div className="flex flex-wrap items-center gap-2">
                        <button
                            onClick={() => setRows((prev) => [...prev, newRow()])}
                            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs border border-border hover:bg-muted transition-colors"
                        >
                            <Plus className="h-3.5 w-3.5" /> {t("explorer.batchAddQuestion")}
                        </button>
                        <div className="ml-auto flex items-center gap-2">
                            <button
                                onClick={() => download("prompt.txt", prompt, "text/plain;charset=utf-8")}
                                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs border border-border hover:bg-muted transition-colors"
                            >
                                <Download className="h-3.5 w-3.5" /> prompt.txt
                            </button>
                            <button
                                onClick={() => download("schema.json", JSON.stringify(schema, null, 2), "application/json")}
                                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs border border-border hover:bg-muted transition-colors"
                            >
                                <Download className="h-3.5 w-3.5" /> schema.json
                            </button>
                        </div>
                    </div>

                    {/* Full prompt preview */}
                    <div className="border-t border-border pt-3">
                        <button
                            onClick={() => setShowPrompt((v) => !v)}
                            className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
                        >
                            {showPrompt ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                            {t("explorer.batchShowPrompt")}
                        </button>
                        {showPrompt && (
                            <pre className="mt-2 max-h-96 overflow-auto rounded-md bg-muted/40 p-3 text-[11px] leading-relaxed font-mono whitespace-pre-wrap">
                                {template
                                    ? template.replace("{user_prompt}", prompt).replace("{schema}", JSON.stringify(schema, null, 2))
                                    : t("explorer.batchPromptUnavailable")}
                            </pre>
                        )}
                    </div>

                    {rows.length > MANY_QUESTIONS && (
                        <p className="text-xs text-amber-600 dark:text-amber-400">
                            {t("explorer.batchManyQuestions", { n: rows.length })}
                        </p>
                    )}

                    {issues.length > 0 && (
                        <ul className="text-xs text-destructive list-disc pl-4 space-y-0.5">
                            {issues.map((issue, i) => (
                                <li key={i}>
                                    {issue.code === "noQuestions" && t("explorer.batchIssueNoQuestions")}
                                    {issue.code === "noQuestion" && t("explorer.batchIssueNoQuestion", { column: issue.column })}
                                    {issue.code === "choices" && t("explorer.batchIssueChoices", { column: issue.column })}
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            </Card>

            {/* 3 — Verify & run */}
            <Card step={3} title={t("explorer.batchRunTitle")} icon={<ListChecks className="h-4 w-4 text-muted-foreground" />}>
                <div className="flex flex-col gap-3">
                    <div className="flex flex-wrap items-center gap-2">
                        <button
                            onClick={handleVerify}
                            disabled={verifying || running || ids.length === 0 || issues.length > 0}
                            className="flex items-center gap-2 px-4 py-1.5 rounded-md text-sm font-medium border border-border hover:bg-muted transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                            {verifying ? <><Loader2 className="h-3.5 w-3.5 animate-spin" /> {t("explorer.batchVerifying")}</> : t("explorer.batchVerify")}
                        </button>
                        {running ? (
                            <button
                                onClick={handleStop}
                                disabled={job?.cancel_requested}
                                className="flex items-center gap-2 px-4 py-1.5 rounded-md text-sm font-medium border border-destructive/40 text-destructive hover:bg-destructive/10 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                            >
                                {t("explorer.batchStop")}
                            </button>
                        ) : (
                            <button
                                onClick={handleRun}
                                disabled={!verification || todo.length === 0 || starting}
                                className="flex items-center gap-2 px-4 py-1.5 rounded-md text-sm font-medium bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                            >
                                {starting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                                {t("explorer.batchRun", { n: todo.length })}
                            </button>
                        )}
                        {job && !running && (
                            <button
                                onClick={handleClear}
                                className="px-2.5 py-1.5 rounded-md text-xs border border-border hover:bg-muted transition-colors"
                            >
                                {t("explorer.batchClearResults")}
                            </button>
                        )}
                    </div>

                    {error && (
                        <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                            {error}
                        </div>
                    )}

                    {verification && (
                        <div className="rounded-lg border border-border bg-muted/20 px-3 py-2.5 text-xs font-mono space-y-1">
                            <div className="text-muted-foreground">
                                {t("explorer.batchVerifyModel", {
                                    backend: verification.llmBackend,
                                    model: verification.llmModel || "—",
                                    numCtx: verification.numCtx,
                                })}
                            </div>
                            <div className="text-muted-foreground">
                                {t("explorer.batchVerifyBudget", { budget: verification.budgetChars.toLocaleString() })}
                            </div>
                            <div className="text-emerald-600 dark:text-emerald-400">
                                {t("explorer.batchVerifyReady", { ready: verification.ready.length, total: verification.ids.length })}
                            </div>
                            {verification.missing.length > 0 && (
                                <div className="text-amber-600 dark:text-amber-400">
                                    {t("explorer.batchVerifyMissing", { n: verification.missing.length })}
                                    <span className="text-muted-foreground"> — {verification.missing.slice(0, 5).join(", ")}
                                        {verification.missing.length > 5 && ` +${verification.missing.length - 5}`}</span>
                                </div>
                            )}
                            {verification.overBudget.length > 0 && (
                                <div className="text-amber-600 dark:text-amber-400">
                                    {t("explorer.batchVerifyOverBudget", { n: verification.overBudget.length })}
                                    <span className="text-muted-foreground"> — {verification.overBudget.slice(0, 5).join(", ")}
                                        {verification.overBudget.length > 5 && ` +${verification.overBudget.length - 5}`}</span>
                                </div>
                            )}
                            {verification.llmBackend === "ollama" && (
                                <div className="text-muted-foreground">{t("explorer.batchVerifyOllama")}</div>
                            )}
                        </div>
                    )}

                    {job && <BatchStatus job={job} />}
                </div>
            </Card>

            {/* Results */}
            {results.length > 0 && (
                <div className="rounded-xl border border-border bg-card shadow-xs overflow-hidden">
                    <div className="flex items-center gap-2 px-4 py-3 border-b border-border bg-muted/30">
                        <span className="text-sm font-medium">{t("explorer.batchResultsTitle")}</span>
                        <div className="ml-auto flex items-center gap-2">
                            <button
                                onClick={() => download("results.csv", toCsv(columns, exportHeaders, results), "text/csv;charset=utf-8")}
                                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs border border-border hover:bg-muted transition-colors"
                            >
                                <Download className="h-3.5 w-3.5" /> CSV
                            </button>
                            <button
                                onClick={() => download(
                                    "results.json",
                                    // Carries the questions alongside the answers — the CSV can only
                                    // fit them into the header, JSON can state them properly.
                                    JSON.stringify({ questions: jobQs, results }, null, 2),
                                    "application/json"
                                )}
                                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs border border-border hover:bg-muted transition-colors"
                            >
                                <Download className="h-3.5 w-3.5" /> JSON
                            </button>
                        </div>
                    </div>
                    <div className="overflow-x-auto">
                        <table className="w-full text-xs">
                            <thead>
                                <tr className="border-b border-border bg-muted/20">
                                    {columns.map((c, i) => (
                                        // Compact `questionN` on screen with the question on hover; the
                                        // downloads carry the spelled-out header instead.
                                        <th
                                            key={c}
                                            title={i >= meta.length ? jobQs[i - meta.length]?.question : undefined}
                                            className="text-left font-mono font-medium text-muted-foreground px-3 py-2 whitespace-nowrap"
                                        >
                                            {c}
                                        </th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {results.map((row) => (
                                    <tr key={row._id} className="border-b border-border/50 last:border-0 hover:bg-muted/20">
                                        {columns.map((c) => (
                                            <td
                                                key={c}
                                                className={cn(
                                                    "px-3 py-2 align-top max-w-md",
                                                    c === "_id" && "font-mono whitespace-nowrap",
                                                    c === "_status" && cn("font-mono whitespace-nowrap", statusTone(row._status))
                                                )}
                                            >
                                                {String(row[c] ?? "")}
                                            </td>
                                        ))}
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}
        </div>
    );
};
