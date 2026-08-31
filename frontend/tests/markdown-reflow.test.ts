/**
 * Intent: rebuild tables that were flattened before the ingestion fix, but only
 * when the reconstruction is unambiguous.
 *
 * Chunks indexed before `8013923` had every newline inside a block replaced by a
 * space, so a table arrived as one line: `| A | B | |---|---| | 1 | 2 |`. Those
 * chunks stay flattened until the KB is re-ingested, so the renderer repairs them
 * at display time.
 *
 * The guard rail matters as much as the repair: splitting a row apart on a guess
 * shows the user a table that is subtly wrong, which is worse than showing the raw
 * pipes. Anything whose cell counts do not line up exactly must pass through
 * untouched — including tables that already have their newlines.
 */

import { describe, expect, it } from "vitest";

import {
    linkifyDocRefs,
    reflowFlattenedTable,
    reflowFlattenedTables,
} from "@/components/ui/markdown";

describe("reflowFlattenedTable", () => {
    it("rebuilds the canonical flattened table", () => {
        const flat = "| A | B | |---|---| | 1 | 2 |";

        expect(reflowFlattenedTable(flat)).toBe("| A | B |\n| --- | --- |\n| 1 | 2 |");
    });

    it("rebuilds a table with several body rows", () => {
        const flat = "| Region | Umsatz | |---|---| | Zürich | 120 | | Bern | 80 |";

        expect(reflowFlattenedTable(flat)).toBe(
            "| Region | Umsatz |\n| --- | --- |\n| Zürich | 120 |\n| Bern | 80 |",
        );
    });

    it("handles three columns", () => {
        const flat = "| A | B | C | |---|---|---| | 1 | 2 | 3 |";

        expect(reflowFlattenedTable(flat)).toBe("| A | B | C |\n| --- | --- | --- |\n| 1 | 2 | 3 |");
    });

    it("accepts aligned delimiter cells", () => {
        const flat = "| A | B | |:---|---:| | 1 | 2 |";

        expect(reflowFlattenedTable(flat)).toBe("| A | B |\n| :--- | ---: |\n| 1 | 2 |");
    });

    it.each([
        ["no pipes at all", "Just a sentence."],
        ["prose that merely contains a pipe", "a | b"],
        ["no delimiter row", "| A | B | | 1 | 2 |"],
        ["a single already-correct row", "| A | B |"],
        ["a bare delimiter row", "|---|---|"],
        ["a trailing cell count that does not divide", "| A | B | |---|---| | 1 |"],
        ["a delimiter row not directly after the header", "| A | B | | 1 | 2 | |---|---|"],
    ])("returns null for %s", (_label, line) => {
        expect(reflowFlattenedTable(line)).toBeNull();
    });
});

describe("reflowFlattenedTables", () => {
    it("leaves a table that already has its newlines untouched", () => {
        const proper = "| A | B |\n| --- | --- |\n| 1 | 2 |";

        expect(reflowFlattenedTables(proper)).toBe(proper);
    });

    it("leaves content without pipes untouched", () => {
        const text = "Ein Absatz ohne Tabelle.\n\nUnd noch einer.";

        expect(reflowFlattenedTables(text)).toBe(text);
    });

    it("separates a rebuilt table from adjacent text so GFM parses it as a block", () => {
        const content = "Hier die Zahlen:\n| A | B | |---|---| | 1 | 2 |\nSoweit die Übersicht.";
        const out = reflowFlattenedTables(content);
        const lines = out.split("\n");

        // A blank line before and after the reconstructed block.
        expect(lines[lines.indexOf("| A | B |") - 1]).toBe("");
        expect(lines[lines.indexOf("| 1 | 2 |") + 1]).toBe("");
        expect(out).toContain("| A | B |\n| --- | --- |\n| 1 | 2 |");
    });

    it("rebuilds more than one flattened table in the same chunk", () => {
        const content = "| A | B | |---|---| | 1 | 2 |\n\n| C | D | |---|---| | 3 | 4 |";
        const out = reflowFlattenedTables(content);

        expect(out).toContain("| A | B |\n| --- | --- |\n| 1 | 2 |");
        expect(out).toContain("| C | D |\n| --- | --- |\n| 3 | 4 |");
    });

    it("preserves the surrounding prose", () => {
        const content = "Einleitung.\n| A | B | |---|---| | 1 | 2 |\nSchluss.";
        const out = reflowFlattenedTables(content);

        expect(out).toContain("Einleitung.");
        expect(out).toContain("Schluss.");
    });

    it("is idempotent — running it twice changes nothing further", () => {
        const content = "Text\n| A | B | |---|---| | 1 | 2 |\nMehr";
        const once = reflowFlattenedTables(content);

        expect(reflowFlattenedTables(once)).toBe(once);
    });
});

/**
 * Intent: a filename the model mentions in prose becomes a clickable citation,
 * but only when that file is actually among the retrieved sources.
 *
 * The LLM writes "(Bericht.pdf)" in running text. Linking it opens the source
 * popover. Linking a filename that was *not* retrieved would offer a citation the
 * user cannot open — and would dress up a name the model may have invented as if
 * it were a real retrieved document.
 */

const source = (source_file: string) =>
    ({ id: source_file, message_id: "m", content: "", metadata: { source_file } }) as never;

describe("linkifyDocRefs", () => {
    it("links a reference that is among the sources", () => {
        expect(linkifyDocRefs("siehe (Bericht.pdf) dazu", [source("Bericht.pdf")])).toBe(
            "siehe [Bericht.pdf](<Bericht.pdf>) dazu",
        );
    });

    it("leaves a reference that is not among the sources as plain text", () => {
        expect(linkifyDocRefs("siehe (Erfunden.pdf)", [source("Bericht.pdf")])).toBe(
            "siehe (Erfunden.pdf)",
        );
    });

    it("links every recognised extension", () => {
        for (const ext of ["pdf", "xlsx", "xls", "docx", "doc", "md", "txt", "csv"]) {
            const name = `datei.${ext}`;
            expect(linkifyDocRefs(`(${name})`, [source(name)])).toBe(`[${name}](<${name}>)`);
        }
    });

    it("ignores a parenthesised name with no document extension", () => {
        expect(linkifyDocRefs("(notafile.exe)", [source("notafile.exe")])).toBe("(notafile.exe)");
    });

    it("links several references in one answer", () => {
        const out = linkifyDocRefs(
            "vgl. (A.pdf) und (B.pdf)",
            [source("A.pdf"), source("B.pdf")],
        );

        expect(out).toBe("vgl. [A.pdf](<A.pdf>) und [B.pdf](<B.pdf>)");
    });

    it("wraps the target in <> so a filename with spaces stays one link", () => {
        const name = "Bericht Zürich 2026.pdf";

        expect(linkifyDocRefs(`(${name})`, [source(name)])).toBe(`[${name}](<${name}>)`);
    });

    it.each([
        ["no sources array", undefined],
        ["an empty sources array", []],
    ])("links optimistically given %s — there is nothing to check against", (_label, sources) => {
        expect(linkifyDocRefs("(Bericht.pdf)", sources as never)).toBe(
            "[Bericht.pdf](<Bericht.pdf>)",
        );
    });

    it("leaves content without any reference untouched", () => {
        const text = "Ein Absatz ganz ohne Verweis.";

        expect(linkifyDocRefs(text, [source("A.pdf")])).toBe(text);
    });
});
