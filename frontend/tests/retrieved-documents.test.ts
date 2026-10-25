/**
 * Intent: the document panel shows each answer's search result per document.
 *
 * The backend returns retrieved chunks as a flat list in context order, each with
 * its provenance. The panel groups them by source file, so a document appears
 * once with all of its chunks, and orders documents by their best chunk — the
 * document the LLM saw first comes first.
 *
 * Answers saved before provenance was recorded carry only their cited sources.
 * Those still show, as cited chunks without ranks, and say so.
 */

import { describe, expect, it } from "vitest";

import { documentsForAnswer } from "@/lib/retrieved";
import { Message, RetrievalInfo, Source } from "@/services/message";

const chunk = (file: string, info: Partial<RetrievalInfo> & { position: number }): Source => ({
    id: `src-${file}-${info.position}`,
    message_id: "m",
    content: `content ${info.position}`,
    metadata: {
        source_file: file,
        _retrieval: { chunk_id: `c${info.position}`, title: `title ${info.position}`, cited: false, ranks: {}, ...info },
    },
});

const message = (fields: Partial<Message>): Message => ({
    id: "m",
    content: "",
    role: "assistant",
    create_timestamp: 0,
    conversation_id: "c",
    ...fields,
});

describe("documentsForAnswer", () => {
    it("groups chunks by file and orders documents by their best position", () => {
        const result = documentsForAnswer(
            message({
                retrieved: [
                    chunk("b.pdf", { position: 2 }),
                    chunk("a.pdf", { position: 3 }),
                    chunk("b.pdf", { position: 1 }),
                ],
            }),
        );

        expect(result.documents.map((d) => d.file)).toEqual(["b.pdf", "a.pdf"]);
        expect(result.documents[0].chunks.map((c) => c.position)).toEqual([1, 2]);
        expect(result.chunkCount).toBe(3);
        expect(result.recorded).toBe(true);
    });

    it("counts cited chunks and notes which search methods found the document", () => {
        const [doc] = documentsForAnswer(
            message({
                retrieved: [
                    chunk("a.pdf", { position: 1, cited: true, ranks: { semantic: 2 } }),
                    chunk("a.pdf", { position: 2, ranks: { bm25: 1, pre_rerank: 5 } }),
                ],
            }),
        ).documents;

        expect(doc.citedCount).toBe(1);
        expect(doc.semantic).toBe(true);
        expect(doc.keyword).toBe(true);
        expect(doc.chunks[1].ranks).toEqual({ bm25: 1, pre_rerank: 5 });
    });

    it("keeps documents only the reranker discarded apart, and out of the sent counts", () => {
        const result = documentsForAnswer(
            message({
                retrieved: [
                    chunk("a.pdf", { position: 1, sent: true }),
                    chunk("a.pdf", { position: 3, sent: false }),
                    chunk("b.pdf", { position: 2, sent: false }),
                ],
            }),
        );

        expect(result.documents.map((d) => [d.file, d.sentCount, d.chunks.length])).toEqual([["a.pdf", 1, 2]]);
        expect(result.discarded.map((d) => d.file)).toEqual(["b.pdf"]);
        expect(result.chunkCount).toBe(1);
    });

    it("treats chunks saved before discards were kept as sent", () => {
        const [doc] = documentsForAnswer(message({ retrieved: [chunk("a.pdf", { position: 1 })] })).documents;

        expect(doc.chunks[0].sent).toBe(true);
    });

    it("takes the chunk title from the retrieval record", () => {
        const [doc] = documentsForAnswer(message({ retrieved: [chunk("a.pdf", { position: 1, title: "A.12.3.1" })] })).documents;

        expect(doc.chunks[0].title).toBe("A.12.3.1");
    });

    it("falls back to the cited sources for answers saved without provenance", () => {
        const legacy: Source = { id: "s1", message_id: "m", content: "old", metadata: { source_file: "a.pdf", header: "## Intro" } };

        const result = documentsForAnswer(message({ sources: [legacy] }));

        expect(result.recorded).toBe(false);
        expect(result.documents[0].chunks[0]).toMatchObject({ cited: true, ranks: {}, title: "## Intro", position: 1 });
    });

    it("is empty for an answer without any documents", () => {
        expect(documentsForAnswer(message({})).documents).toEqual([]);
    });
});
