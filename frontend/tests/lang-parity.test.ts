/**
 * Intent: every string the UI can show exists in all four languages.
 *
 * i18next falls back silently. A key present in en.ts but missing from de.ts does
 * not throw, log, or fail a build — the German UI just shows English text, or the
 * raw key. Nothing surfaces it, so a feature shipped with three of four languages
 * updated looks finished and stays broken until a user reports it.
 *
 * English is the reference: it is the language every string is authored in first.
 * The other three must match it exactly — no missing keys, and no orphans left
 * behind when an English key is renamed or removed.
 *
 * These import the modules rather than parsing the files, so nested objects and
 * interpolation placeholders are compared as the app actually sees them.
 */

import { describe, expect, it } from "vitest";

import * as de from "@/lib/lang/de";
import * as en from "@/lib/lang/en";
import * as fr from "@/lib/lang/fr";
// Aliased: a bare `it` would shadow vitest's own `it`.
import * as itIT from "@/lib/lang/it";

type Bundle = Record<string, unknown>;

const TRANSLATIONS: [string, Bundle][] = [
    ["de", de as Bundle],
    ["fr", fr as Bundle],
    ["it", itIT as Bundle],
];

/** Flatten nested namespaces to dot paths: `app.chatFilters.apply`. */
function flatten(value: unknown, prefix = ""): Map<string, string> {
    const out = new Map<string, string>();
    if (value === null || typeof value !== "object") return out;

    for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
        const path = prefix ? `${prefix}.${key}` : key;
        if (child !== null && typeof child === "object") {
            for (const [k, v] of flatten(child, path)) out.set(k, v);
        } else if (typeof child === "string") {
            out.set(path, child);
        }
    }
    return out;
}

/** `{{count}}` style placeholders, which must survive translation. */
function placeholders(text: string): string[] {
    return (text.match(/\{\{\s*[\w.]+\s*\}\}/g) ?? [])
        .map((p) => p.replace(/\s/g, ""))
        .sort();
}

const EN = flatten(en);

describe("the English reference bundle", () => {
    it("is non-empty", () => {
        expect(EN.size).toBeGreaterThan(300);
    });

    it("has no blank strings", () => {
        const blank = [...EN].filter(([, v]) => v.trim() === "").map(([k]) => k);

        expect(blank).toEqual([]);
    });
});

describe.each(TRANSLATIONS)("%s", (lang, bundle) => {
    const keys = flatten(bundle);

    it("defines every key English defines", () => {
        const missing = [...EN.keys()].filter((k) => !keys.has(k)).sort();

        expect(missing, `${lang}.ts is missing ${missing.length} key(s)`).toEqual([]);
    });

    it("defines no key English does not — an orphan from a rename", () => {
        const orphaned = [...keys.keys()].filter((k) => !EN.has(k)).sort();

        expect(orphaned, `${lang}.ts has ${orphaned.length} stale key(s)`).toEqual([]);
    });

    it("has no blank strings", () => {
        const blank = [...keys].filter(([, v]) => v.trim() === "").map(([k]) => k);

        expect(blank).toEqual([]);
    });

    it("keeps the same interpolation placeholders as English", () => {
        const mismatched: string[] = [];

        for (const [key, english] of EN) {
            const translated = keys.get(key);
            if (translated === undefined) continue; // reported by the missing-keys test

            const expected = placeholders(english);
            const actual = placeholders(translated);
            if (expected.join() !== actual.join()) {
                mismatched.push(`${key}: expected ${expected.join(",") || "none"}, got ${actual.join(",") || "none"}`);
            }
        }

        expect(mismatched).toEqual([]);
    });
});
