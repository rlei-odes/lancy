/**
 * Intent: the batch tab decides correctly which documents a run will analyze, and
 * saved question sets round-trip without picking up or losing anything.
 *
 *   - a job's results count as done only when the job answers the same questions
 *     from the same source — otherwise every ready document runs again
 *   - failed documents count as not done, so a rerun retries them
 *   - that comparison must not depend on schema key order, because the backend
 *     compares with sorted keys and the two would otherwise disagree on "same"
 *   - a stored set carries no React keys, and a malformed one loads as something
 *     usable rather than crashing the editor
 */

import { describe, expect, it } from "vitest";

import {
    type BatchJob,
    type JobDefinition,
    fromStoredRows,
    idsToRun,
    jobCounts,
    sameDefinition,
    sameQuestions,
    stableStringify,
    toResultRow,
    toStoredRows,
} from "@/components/sections/batch-analysis";
import { formatDuration } from "@/lib/duration";

const SCHEMA = { type: "object", properties: { question1: { type: "string" } } };

const DEF: JobDefinition = { kbId: "kb1", pileId: null, idField: "source_file", prompt: "question1: Q", schema: SCHEMA };

function job(results: [string, string][], overrides: Partial<BatchJob> = {}): BatchJob {
    return {
        status: "done",
        kb_id: "kb1",
        pile_id: null,
        id_field: "source_file",
        prompt: "question1: Q",
        schema: SCHEMA,
        questions: [],
        ids: results.map(([id]) => id),
        current_ident: "",
        cancel_requested: false,
        error: "",
        started_at: "2026-10-10T20:00:00+00:00",
        updated_at: "2026-10-10T20:01:00+00:00",
        results: results.map(([ident, status]) => ({ ident, status, chunk_count: 1, char_count: 10, result: null, elapsed_s: 2 })),
        ...overrides,
    };
}

describe("idsToRun", () => {
    it("runs everything ready when there is no job", () => {
        expect(idsToRun(null, ["a", "b"], DEF)).toEqual(["a", "b"]);
    });

    it("skips documents the same job already answered and retries failed ones", () => {
        const j = job([["a", "ok"], ["b", "error:HTTP 502 boom"], ["c", "over_budget"]]);
        expect(idsToRun(j, ["a", "b", "c", "new"], DEF)).toEqual(["b", "new"]);
    });

    it("runs everything again when the questions changed", () => {
        const j = job([["a", "ok"]]);
        expect(idsToRun(j, ["a"], { ...DEF, prompt: "question1: other" })).toEqual(["a"]);
    });

    it("runs everything again when the source changed", () => {
        const j = job([["a", "ok"]]);
        expect(idsToRun(j, ["a"], { ...DEF, kbId: "kb2" })).toEqual(["a"]);
        expect(idsToRun(j, ["a"], { ...DEF, kbId: null, pileId: "p1" })).toEqual(["a"]);
    });
});

describe("sameDefinition", () => {
    it("ignores schema key order", () => {
        const reordered = { properties: { question1: { type: "string" } }, type: "object" };
        expect(sameDefinition(job([]), { ...DEF, schema: reordered })).toBe(true);
    });

    it("treats a missing kb_id like null", () => {
        expect(sameDefinition(job([], { kb_id: null }), { ...DEF, kbId: null })).toBe(true);
    });
});

describe("stableStringify", () => {
    it("sorts keys at every level but keeps array order", () => {
        expect(stableStringify({ b: 1, a: { d: [2, 1], c: null } })).toBe('{"a":{"c":null,"d":[2,1]},"b":1}');
    });
});

describe("jobCounts", () => {
    it("splits results into ok, skipped and errors against the job's documents", () => {
        const j = job([["a", "ok"], ["b", "over_budget"], ["c", "no_chunks"], ["d", "error:x"]], {
            ids: ["a", "b", "c", "d", "e", "f"],
        });
        expect(jobCounts(j)).toEqual({ total: 6, done: 4, ok: 1, skipped: 2, errors: 1 });
    });
});

describe("toResultRow", () => {
    it("flattens the answers next to the meta columns and serialises nested values", () => {
        const row = toResultRow({
            ident: "a.pdf",
            status: "ok",
            chunk_count: 3,
            char_count: 99,
            result: { question1: "yes", question2: true, question3: { nested: 1 } },
            elapsed_s: 1,
        });
        expect(row).toEqual({
            _id: "a.pdf",
            _status: "ok",
            _chunk_count: 3,
            _char_count: 99,
            question1: "yes",
            question2: true,
            question3: '{"nested":1}',
        });
    });
});

describe("question sets", () => {
    const rows = [
        { key: "k1", question: "Augenschutz?", type: "text" as const, choices: "" },
        { key: "k2", question: "Status?", type: "choice" as const, choices: "ja, nein" },
    ];

    it("stores rows without React keys", () => {
        expect(toStoredRows(rows)).toEqual([
            { question: "Augenschutz?", type: "text", choices: "" },
            { question: "Status?", type: "choice", choices: "ja, nein" },
        ]);
    });

    it("round-trips: a loaded set equals the rows it was saved from", () => {
        const loaded = fromStoredRows(toStoredRows(rows));
        expect(loaded.map((r) => r.question)).toEqual(["Augenschutz?", "Status?"]);
        expect(sameQuestions(loaded, toStoredRows(rows))).toBe(true);
        expect(sameQuestions(rows.slice(0, 1), toStoredRows(rows))).toBe(false);
    });

    it("loads malformed data as usable rows instead of failing", () => {
        const loaded = fromStoredRows([{ question: "ok", type: "bogus" }, null, { type: "text" }, "x"]);
        expect(loaded).toHaveLength(1);
        expect(loaded[0]).toMatchObject({ question: "ok", type: "text", choices: "" });
        expect(fromStoredRows("not a list")).toHaveLength(1);
    });
});

describe("formatDuration", () => {
    it("stays compact across scales", () => {
        expect([0, 45, 65, 600, 3725].map(formatDuration)).toEqual(["0s", "45s", "1m 05s", "10m 00s", "1h 02m"]);
    });
});
