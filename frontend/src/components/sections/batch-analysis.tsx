"use client";

import React, { FunctionComponent, useCallback, useEffect, useRef, useState } from "react";
import {
    AlertTriangle,
    ChevronDown,
    ChevronRight,
    ChevronUp,
    Download,
    FileStack,
    ListChecks,
    Loader2,
    Plus,
    Trash2,
    Upload,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/lorem";

const API_BASE = "";
const STORAGE_KEY = "batchAnalysisQuestions";
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
export function exportColumn(row: QuestionRow, index: number): string {
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

async function fetchDocStats(ids: string[], idField: IdField): Promise<DocumentStat[]> {
    const out: DocumentStat[] = [];
    for (let i = 0; i < ids.length; i += STATS_BATCH) {
        const r = await fetch(`${API_BASE}/api/v1/rag/document-stats`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ source_files: ids.slice(i, i + STATS_BATCH), id_field: idField }),
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({}));
            throw new Error(err.detail ?? "Document stats failed");
        }
        out.push(...((await r.json()).stats ?? []));
    }
    return out;
}

async function analyzeOne(
    ident: string,
    idField: IdField,
    prompt: string,
    schema: Record<string, unknown>
): Promise<ResultRow> {
    let r: Response;
    try {
        r = await fetch(`${API_BASE}/api/v1/rag/analyze-document`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ [idField]: ident, prompt, response_schema: schema }),
        });
    } catch (e) {
        return { _id: ident, _status: `error:${e instanceof Error ? e.message : "network"}`, _chunk_count: 0, _char_count: 0 };
    }
    if (!r.ok) {
        const detail = (await r.text()).slice(0, 200).trim();
        return { _id: ident, _status: `error:HTTP ${r.status} ${detail}`, _chunk_count: 0, _char_count: 0 };
    }
    const data = await r.json();
    const row: ResultRow = {
        _id: ident,
        _status: data.skipped ?? "ok",
        _chunk_count: data.chunk_count ?? 0,
        _char_count: data.char_count ?? 0,
    };
    for (const [k, v] of Object.entries(data.result ?? {})) {
        row[k] = typeof v === "object" && v !== null ? JSON.stringify(v) : (v as string | number | boolean);
    }
    return row;
}

// ─── Layout ───────────────────────────────────────────────────────────────────

const Card: FunctionComponent<{ step: number; title: string; icon: React.ReactNode; children: React.ReactNode }> = ({
    step, title, icon, children,
}) => (
    <div className="rounded-xl border border-border bg-card shadow-sm overflow-hidden">
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

const inputCls =
    "bg-background border border-border rounded-md px-2 py-1.5 text-xs outline-none focus:border-muted-foreground/50 transition-colors";

// ─── Component ────────────────────────────────────────────────────────────────

export const BatchAnalysis: FunctionComponent = () => {
    const { t } = useTranslation("app");

    const [idField, setIdField] = useState<IdField>("source_file");
    const [docText, setDocText] = useState("");
    const [rows, setRows] = useState<QuestionRow[]>([]);
    const [template, setTemplate] = useState("");
    const [showPrompt, setShowPrompt] = useState(false);

    const [verifying, setVerifying] = useState(false);
    const [verification, setVerification] = useState<Verification | null>(null);
    const [error, setError] = useState<string | null>(null);

    const [running, setRunning] = useState(false);
    const [results, setResults] = useState<ResultRow[]>([]);
    const [elapsed, setElapsed] = useState<number[]>([]);
    const cancelRef = useRef(false);
    const fileRef = useRef<HTMLInputElement>(null);

    // Restore the question set — without raw mode a reload would otherwise lose it
    useEffect(() => {
        try {
            const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
            if (Array.isArray(saved) && saved.length) { setRows(saved); return; }
        } catch { /* corrupt entry — fall through to the seed row */ }
        setRows([newRow()]);
    }, []);

    useEffect(() => {
        if (rows.length) localStorage.setItem(STORAGE_KEY, JSON.stringify(rows));
    }, [rows]);

    useEffect(() => { fetchTemplate().then(setTemplate).catch(() => {}); }, []);

    // Warn before a reload throws away an in-flight run
    useEffect(() => {
        if (!running) return;
        const handler = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
        window.addEventListener("beforeunload", handler);
        return () => window.removeEventListener("beforeunload", handler);
    }, [running]);

    const { ids, duplicates } = parseIdList(docText);
    const issues = validateRows(rows);
    const schema = buildSchema(rows);
    const prompt = buildPrompt(rows);
    const meta = ["_id", "_status", "_chunk_count", "_char_count"];
    const columns = [...meta, ...rows.map((_, i) => fieldName(i))];
    const exportHeaders = [...meta, ...rows.map(exportColumn)];

    const done = new Set(results.map((r) => r._id));
    const todo = (verification?.ready ?? []).filter((i) => !done.has(i));
    const avgSeconds = elapsed.length ? elapsed.reduce((a, b) => a + b, 0) / elapsed.length : 0;

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
            const stats = await fetchDocStats(ids, idField);
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
    }, [ids, duplicates, idField]);

    const handleRun = useCallback(async () => {
        cancelRef.current = false;
        setRunning(true);
        setError(null);
        for (const ident of todo) {
            if (cancelRef.current) break;
            const t0 = performance.now();
            const row = await analyzeOne(ident, idField, prompt, schema);
            setResults((prev) => [...prev, row]);
            setElapsed((prev) => [...prev, (performance.now() - t0) / 1000]);
        }
        setRunning(false);
    }, [todo, idField, prompt, schema]);

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
                        <label className="text-xs text-muted-foreground">{t("explorer.batchIdFieldLabel")}</label>
                        <select
                            value={idField}
                            onChange={(e) => { setIdField(e.target.value as IdField); setVerification(null); }}
                            className={inputCls}
                        >
                            <option value="source_file">source_file</option>
                            <option value="document_id">document_id</option>
                        </select>
                        <span className="text-xs text-muted-foreground/70">
                            {t(idField === "source_file" ? "explorer.batchIdFieldHintFile" : "explorer.batchIdFieldHintDoc")}
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
                        className="w-full resize-y bg-background border border-border rounded-md px-3 py-2 text-xs font-mono outline-none focus:border-muted-foreground/50 placeholder:text-muted-foreground/40"
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
                                    className="w-full resize-y bg-background border border-border rounded-md px-2 py-1.5 text-xs outline-none focus:border-muted-foreground/50 placeholder:text-muted-foreground/40"
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
                                onClick={() => { cancelRef.current = true; }}
                                className="flex items-center gap-2 px-4 py-1.5 rounded-md text-sm font-medium border border-destructive/40 text-destructive hover:bg-destructive/10 transition-colors"
                            >
                                {t("explorer.batchStop")}
                            </button>
                        ) : (
                            <button
                                onClick={handleRun}
                                disabled={!verification || todo.length === 0}
                                className="flex items-center gap-2 px-4 py-1.5 rounded-md text-sm font-medium bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                            >
                                {t("explorer.batchRun", { n: todo.length })}
                            </button>
                        )}
                        {results.length > 0 && !running && (
                            <button
                                onClick={() => { setResults([]); setElapsed([]); }}
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

                    {(running || results.length > 0) && (
                        <div className="flex items-center gap-3 text-xs text-muted-foreground font-mono">
                            {running && <Loader2 className="h-3.5 w-3.5 animate-spin shrink-0" />}
                            <span>
                                {t("explorer.batchProgress", {
                                    done: results.length,
                                    total: results.length + todo.length,
                                })}
                                {running && avgSeconds > 0 &&
                                    ` · ${t("explorer.batchEta", { seconds: Math.round(avgSeconds * todo.length) })}`}
                            </span>
                        </div>
                    )}
                </div>
            </Card>

            {/* Results */}
            {results.length > 0 && (
                <div className="rounded-xl border border-border bg-card shadow-sm overflow-hidden">
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
                                    JSON.stringify({
                                        questions: rows.map((r, i) => ({
                                            field: fieldName(i),
                                            question: r.question.trim(),
                                            type: r.type,
                                            ...(r.type === "choice" ? { choices: parseChoices(r.choices) } : {}),
                                        })),
                                        results,
                                    }, null, 2),
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
                                            title={i >= meta.length ? rows[i - meta.length]?.question : undefined}
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
