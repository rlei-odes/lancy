/**
 * Intent: the pile progress bar moves steadily and never overshoots.
 *
 * Ingestion handles one file at a time — chunk it, then embed its chunks in batches
 * — so the share done is the files already finished plus the current file's
 * embedding batches. Before ingestion has listed the files there is nothing to
 * measure, which must read as 0 rather than as a division by zero.
 */

import { describe, expect, it } from "vitest";

import { type PileProgress, progressFraction } from "@/components/sections/pile-manager";

const p = (overrides: Partial<PileProgress>): PileProgress => ({
    phase: "chunking",
    current_file: "",
    file_index: 0,
    total_files: 0,
    embed_batch: 0,
    embed_total_batches: 0,
    started_at: null,
    ...overrides,
});

describe("progressFraction", () => {
    it("is 0 before ingestion has listed the files", () => {
        expect(progressFraction(p({ phase: "preparing" }))).toBe(0);
    });

    it("counts finished files whole while the current one is being chunked", () => {
        expect(progressFraction(p({ file_index: 3, total_files: 10 }))).toBeCloseTo(0.2);
    });

    it("adds the current file's embedding batches", () => {
        const half = p({ phase: "embedding", file_index: 3, total_files: 10, embed_batch: 1, embed_total_batches: 2 });
        expect(progressFraction(half)).toBeCloseTo(0.25);
    });

    it("reaches 1 on the last file's last batch and never passes it", () => {
        const last = p({ phase: "embedding", file_index: 9, total_files: 9, embed_batch: 2, embed_total_batches: 2 });
        expect(progressFraction(last)).toBe(1);
        expect(progressFraction({ ...last, file_index: 10 })).toBe(1);
    });
});
