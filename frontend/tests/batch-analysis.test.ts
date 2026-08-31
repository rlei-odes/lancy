/**
 * Intent: the questions the user types and the JSON Schema sent to the model are
 * generated together, so they cannot drift apart.
 *
 * The batch tab builds both artefacts from one list of rows. Three properties are
 * load-bearing for strict decoding on OpenAI-compatible backends:
 *
 *   - every property is `required` and `additionalProperties` is false, or the
 *     backend rejects the schema outright under strict mode
 *   - number fields are `["number", "null"]`, so "the document does not say" has a
 *     legal answer — without it strict decoding forces a number and the honesty
 *     rule turns "couldn't tell" into a confident 0
 *   - schema field names stay positional (`questionN`) while the CSV header spells
 *     the question out, so the columns stay in question order and remain readable
 */

import { describe, expect, it } from "vitest";

import {
    buildPrompt,
    buildSchema,
    csvCell,
    exportColumn,
    fieldName,
    parseChoices,
    parseIdList,
    toCsv,
    validateRows,
} from "@/components/sections/batch-analysis";

const row = (question: string, type = "text", choices = "") =>
    ({ key: question, question, type, choices }) as never;

describe("fieldName", () => {
    it("is positional and 1-based", () => {
        expect([0, 1, 2].map(fieldName)).toEqual(["question1", "question2", "question3"]);
    });
});

describe("buildSchema", () => {
    const rows = [
        row("Wie heisst der Auftraggeber?"),
        row("Ist das Projekt abgeschlossen?", "boolean"),
        row("Wie viele Türme werden erwähnt?", "number"),
        row("Wie ist der Status?", "choice", "yes, no, unclear"),
    ];

    it("marks every property required — strict mode rejects anything looser", () => {
        const schema = buildSchema(rows) as never as {
            required: string[];
            properties: Record<string, unknown>;
        };

        expect(schema.required).toEqual(["question1", "question2", "question3", "question4"]);
        expect(schema.required).toEqual(Object.keys(schema.properties));
    });

    it("closes the object with additionalProperties: false", () => {
        expect(buildSchema(rows)).toMatchObject({ type: "object", additionalProperties: false });
    });

    it("makes number fields nullable so 'not stated' is a legal answer", () => {
        const { properties } = buildSchema(rows) as never as {
            properties: Record<string, { type: unknown }>;
        };

        expect(properties.question3.type).toEqual(["number", "null"]);
    });

    it.each([
        ["text", "string"],
        ["boolean", "boolean"],
    ])("maps the %s row to type %s", (type, expected) => {
        const { properties } = buildSchema([row("Frage?", type)]) as never as {
            properties: Record<string, { type: unknown }>;
        };

        expect(properties.question1.type).toBe(expected);
    });

    it("turns a choice row into a string enum", () => {
        const { properties } = buildSchema([
            row("Status?", "choice", "yes, no, unclear"),
        ]) as never as { properties: Record<string, { type: string; enum: string[] }> };

        expect(properties.question1.type).toBe("string");
        expect(properties.question1.enum).toEqual(["yes", "no", "unclear"]);
    });

    it("carries the question text as the property description", () => {
        const { properties } = buildSchema([row("  Wer war beteiligt?  ")]) as never as {
            properties: Record<string, { description: string }>;
        };

        expect(properties.question1.description).toBe("Wer war beteiligt?");
    });

    it("produces a valid empty object for no rows", () => {
        expect(buildSchema([])).toEqual({
            type: "object",
            properties: {},
            required: [],
            additionalProperties: false,
        });
    });
});

describe("buildPrompt", () => {
    it("lines the questions up with the schema field names", () => {
        expect(buildPrompt([row("Erste Frage?"), row("Zweite Frage?")])).toBe(
            "question1: Erste Frage?\nquestion2: Zweite Frage?",
        );
    });

    it("trims the question text", () => {
        expect(buildPrompt([row("  Frage?  ")])).toBe("question1: Frage?");
    });
});

describe("parseChoices", () => {
    it("splits, trims and drops blanks", () => {
        expect(parseChoices(" yes ,no ,, unclear ")).toEqual(["yes", "no", "unclear"]);
    });

    it("collapses repeats to the first occurrence", () => {
        expect(parseChoices("yes, no, yes")).toEqual(["yes", "no"]);
    });

    it("returns nothing for an empty string", () => {
        expect(parseChoices("")).toEqual([]);
        expect(parseChoices("   ,  ,")).toEqual([]);
    });
});

describe("exportColumn", () => {
    it("keeps the position prefix and appends a readable slug", () => {
        expect(exportColumn(row("How many towers are mentioned?"), 2)).toBe(
            "question3_how_many_towers_are_mentioned",
        );
    });

    it("falls back to the bare field name when the question has no usable characters", () => {
        expect(exportColumn(row("???"), 0)).toBe("question1");
        expect(exportColumn(row(""), 0)).toBe("question1");
    });

    it("caps the slug and leaves no trailing underscore", () => {
        const column = exportColumn(row("a".repeat(80)), 0);

        expect(column.length).toBeLessThanOrEqual("question1_".length + 40);
        expect(column.endsWith("_")).toBe(false);
    });

    it("does not leave a trailing underscore when the cut lands on a separator", () => {
        // 40 chars of slug would end mid-separator here.
        const column = exportColumn(row("wort ".repeat(12)), 0);

        expect(column.endsWith("_")).toBe(false);
    });
});

describe("validateRows", () => {
    it("reports an empty question set", () => {
        expect(validateRows([])).toEqual([{ code: "noQuestions" }]);
    });

    it("reports a blank question, naming its column", () => {
        expect(validateRows([row("Erste?"), row("   ")])).toEqual([
            { code: "noQuestion", column: "question2" },
        ]);
    });

    it("requires a choice row to offer at least two options", () => {
        expect(validateRows([row("Status?", "choice", "yes")])).toEqual([
            { code: "choices", column: "question1" },
        ]);
        expect(validateRows([row("Status?", "choice", "yes, no")])).toEqual([]);
    });

    it("passes a well-formed set", () => {
        expect(validateRows([row("Frage?"), row("Zahl?", "number")])).toEqual([]);
    });
});

describe("parseIdList", () => {
    it("drops blank lines and # comments", () => {
        const { ids } = parseIdList("# Kommentar\n\na.pdf\n  \nb.pdf\n# noch einer");

        expect(ids).toEqual(["a.pdf", "b.pdf"]);
    });

    it("trims surrounding whitespace", () => {
        expect(parseIdList("  a.pdf  \n\tb.pdf\t").ids).toEqual(["a.pdf", "b.pdf"]);
    });

    it("collapses duplicates to the first occurrence and counts them", () => {
        const { ids, duplicates } = parseIdList("a.pdf\nb.pdf\na.pdf\na.pdf");

        expect(ids).toEqual(["a.pdf", "b.pdf"]);
        expect(duplicates).toBe(2);
    });

    it("returns an empty list for empty input", () => {
        expect(parseIdList("")).toEqual({ ids: [], duplicates: 0 });
        expect(parseIdList("\n\n#only a comment\n")).toEqual({ ids: [], duplicates: 0 });
    });

    it("keeps a # that is not at the start of the line", () => {
        expect(parseIdList("Bericht #3.pdf").ids).toEqual(["Bericht #3.pdf"]);
    });
});

describe("CSV export", () => {
    it("leaves a plain value unquoted", () => {
        expect(csvCell("Zürich")).toBe("Zürich");
        expect(csvCell(42)).toBe("42");
    });

    it.each([
        ["a comma", "a,b", '"a,b"'],
        ["a newline", "a\nb", '"a\nb"'],
        ["a carriage return", "a\rb", '"a\rb"'],
    ])("quotes a value containing %s", (_label, value, expected) => {
        expect(csvCell(value)).toBe(expected);
    });

    it("doubles an embedded quote", () => {
        expect(csvCell('sagte "ja"')).toBe('"sagte ""ja"""');
    });

    it("renders null and undefined as an empty cell, not the word", () => {
        expect(csvCell(null)).toBe("");
        expect(csvCell(undefined)).toBe("");
    });

    it("renders false rather than dropping it", () => {
        expect(csvCell(false)).toBe("false");
    });

    it("writes the header from the readable names and the rows from the schema keys", () => {
        const csv = toCsv(
            ["source_file", "question1"],
            ["source_file", "question1_wer"],
            [
                { source_file: "a.pdf", question1: "Meier" },
                { source_file: "b,pdf", question1: null },
            ] as never,
        );

        expect(csv).toBe('source_file,question1_wer\na.pdf,Meier\n"b,pdf",');
    });
});
