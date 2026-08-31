/**
 * Intent: a cited source resolves either to its DMS deep link or to the backend
 * file endpoint, with the filename encoded so it survives the proxy.
 *
 * The fallback URL is consumed by the `/api/:path*` rewrite in next.config.mjs,
 * which forwards the path to the backend verbatim. Anything the encoding gets
 * wrong here — a space, an umlaut, a `+`, a `#` — turns into a 404 or the wrong
 * file on a real document set.
 */

import { describe, expect, it } from "vitest";

import { resolveSourceUrl } from "@/lib/sources";

describe("external_url wins when present", () => {
    it("uses a DMS deep link as-is, without encoding it", () => {
        const url = "dms://open?doc=4711&view=full";
        expect(resolveSourceUrl("Bericht.pdf", { external_url: url })).toBe(url);
    });

    it("passes a custom protocol through untouched", () => {
        const url = "cs://Client/Open?id=99";
        expect(resolveSourceUrl("x.pdf", { external_url: url })).toBe(url);
    });

    it.each([
        ["missing metadata", undefined],
        ["null metadata", null],
        ["no external_url key", {}],
        ["empty external_url", { external_url: "" }],
        ["non-string external_url", { external_url: 42 }],
    ])("falls back to the file endpoint for %s", (_label, metadata) => {
        expect(resolveSourceUrl("report.pdf", metadata as never)).toBe(
            "/api/v1/files/report.pdf",
        );
    });
});

describe("filename encoding on the fallback path", () => {
    it("encodes spaces and umlauts", () => {
        expect(resolveSourceUrl("Bericht Zürich.pdf", null)).toBe(
            "/api/v1/files/Bericht%20Z%C3%BCrich.pdf",
        );
    });

    it.each([
        ["Bericht + Anhang.pdf", "Bericht%20%2B%20Anhang.pdf"],
        ["a&b.pdf", "a%26b.pdf"],
        ["100%.pdf", "100%25.pdf"],
        ["frage?.pdf", "frage%3F.pdf"],
        ["teil#2.pdf", "teil%232.pdf"],
        ["a/b.pdf", "a%2Fb.pdf"],
    ])("encodes the reserved characters in %s", (filename, encoded) => {
        expect(resolveSourceUrl(filename, null)).toBe(`/api/v1/files/${encoded}`);
    });

    it("keeps a path separator from escaping the files endpoint", () => {
        // Encoded, `..` cannot climb out of /api/v1/files/ at the proxy layer.
        const url = resolveSourceUrl("../../etc/passwd", null);

        expect(url).toBe("/api/v1/files/..%2F..%2Fetc%2Fpasswd");
        expect(url.startsWith("/api/v1/files/")).toBe(true);
        expect(url.split("/").length).toBe(5);
    });

    it("leaves an already-safe filename unchanged", () => {
        expect(resolveSourceUrl("report_2026-01.pdf", null)).toBe(
            "/api/v1/files/report_2026-01.pdf",
        );
    });
});
