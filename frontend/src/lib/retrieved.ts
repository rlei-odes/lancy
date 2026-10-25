import { Message, RetrievalInfo, Source } from "@/services/message";

export interface RetrievedChunk {
    id: string;
    title: string;
    /** Position in the LLM context, 1-based. */
    position: number;
    /** False for a chunk the reranker discarded: found, but never shown to the LLM. */
    sent: boolean;
    cited: boolean;
    ranks: RetrievalInfo["ranks"];
    content: string;
    mimeType: string;
    metadata: Source["metadata"];
}

export interface RetrievedDocument {
    file: string;
    chunks: RetrievedChunk[];
    sentCount: number;
    citedCount: number;
    semantic: boolean;
    keyword: boolean;
}

export interface AnswerDocuments {
    /** Documents with a chunk sent to the LLM, the one the LLM saw first first. */
    documents: RetrievedDocument[];
    /** Documents found only in chunks the reranker discarded, best-ranked first. */
    discarded: RetrievedDocument[];
    /** Chunks sent to the LLM. */
    chunkCount: number;
    /** False when the answer predates provenance: only its cited sources are known, without ranks. */
    recorded: boolean;
}

const fileOf = (source: Source) => (source.metadata?.source_file as string | undefined) ?? "";

const toChunk = (source: Source, fallbackPosition: number): RetrievedChunk => {
    const info = source.metadata?._retrieval;
    return {
        id: source.id,
        title: info?.title || (source.metadata?.header as string | undefined) || "",
        position: info?.position ?? fallbackPosition,
        sent: info?.sent ?? true,
        cited: info?.cited ?? true,
        ranks: info?.ranks ?? {},
        content: source.content,
        mimeType: (source.metadata?.mime_type as string | undefined) ?? "text/markdown",
        metadata: source.metadata,
    };
};

/** An answer's documents for the panel: retrieved chunks grouped by file, ordered by their best chunk. */
export function documentsForAnswer(message: Message): AnswerDocuments {
    const recorded = (message.retrieved?.length ?? 0) > 0;
    const sources = recorded ? message.retrieved! : (message.sources ?? []);

    const byFile = new Map<string, RetrievedChunk[]>();
    sources.forEach((source, index) => {
        const file = fileOf(source);
        byFile.set(file, [...(byFile.get(file) ?? []), toChunk(source, index + 1)]);
    });

    const all = [...byFile.entries()]
        .map(([file, chunks]) => {
            chunks.sort((a, b) => a.position - b.position);
            return {
                file,
                chunks,
                sentCount: chunks.filter((c) => c.sent).length,
                citedCount: chunks.filter((c) => c.cited).length,
                semantic: chunks.some((c) => c.ranks.semantic != null),
                keyword: chunks.some((c) => c.ranks.bm25 != null),
            };
        })
        .sort((a, b) => a.chunks[0].position - b.chunks[0].position);

    return {
        documents: all.filter((d) => d.sentCount > 0),
        discarded: all.filter((d) => d.sentCount === 0),
        chunkCount: all.reduce((n, d) => n + d.sentCount, 0),
        recorded,
    };
}
