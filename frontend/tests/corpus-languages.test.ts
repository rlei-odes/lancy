/**
 * Intent: the KB picker names the languages the search writes in, as the backend picks them.
 *
 * The backend counts a detected language once it holds 10 % of the documents
 * (`lancy/language.py`), and an admin-set list replaces the detected one. The
 * picker shows the same list, so a stray language never appears there.
 */

import { describe, expect, it } from "vitest";

import { corpusLanguageShares } from "@/lib/corpus-languages";

describe("corpusLanguageShares", () => {
    it("drops a language below the threshold and puts the main one first", () => {
        expect(corpusLanguageShares({ en: 98, de: 700, it: 2 }, [])).toEqual([
            { code: "de", share: 0.875 },
            { code: "en", share: 0.1225 },
        ]);
    });

    it("shows an admin-set list as it is, without shares", () => {
        expect(corpusLanguageShares({ de: 700 }, ["fr", "de"])).toEqual([
            { code: "fr", share: null },
            { code: "de", share: null },
        ]);
    });

    it("shows nothing for a KB not yet detected", () => {
        expect(corpusLanguageShares(undefined, undefined)).toEqual([]);
    });
});
