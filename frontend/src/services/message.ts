import { ApiService } from "./api";

export interface RetrievalStats {
    candidates_retrieved: number;
    chunks_to_llm: number;
    reranker_active: boolean;
    reranker_swaps: number;
    reranker_fallback: boolean;
}

export interface Message {
    id: string;
    content: string;
    role: "user" | "assistant" | "system";
    create_timestamp: number;
    conversation_id: string;
    parent_id?: string;
    user_id?: string;
    sources?: Source[];
    reaction?: Reaction;
    follow_up_questions?: string[];
    query_duration_ms?: number;
    tokens_per_second?: number;
    llm_model?: string;
    kb_name?: string;
    emb_model?: string;
    retrieval_stats?: RetrievalStats;
}

export interface Source {
    id: string;
    message_id: string;
    content: string;
    metadata: {
        title?: string;
        source?: string;
        origin?: string;
        url?: string;
        header?: string;
        mime_type?: string;
        [key: string]: string | number | boolean | string[] | null | undefined;
    };
}

export enum MessageTypes {
    NEXT = "next",
    REDO = "redo",
}

export interface UserInput {
    content: string;
    conversation_id?: string;
    parent_id?: string;
    type: MessageTypes;
    kb_id?: string;
    kb_name?: string;
}

export interface Reaction {
    id: string;
    message_id: string;
    content: string;
    note?: string;
}

export type ThumbsReaction = ":thumbsup:" | ":thumbsdown:";

class MessageService extends ApiService {
    constructor(apiUrl: string) {
        super(apiUrl);
    }

    async create(input: UserInput): Promise<Message | null> {
        try {
            const postUrl = `${this.apiUrl}/`;
            const response = await this.fetchApi(postUrl, {
                method: "POST",
                body: JSON.stringify(input),
            });

            return response.json();
        } catch (e) {
            console.log("error", e);
            return null;
        }
    }

    async createStream(input: UserInput, onMessage: (message: Message) => void, onError: () => void, onEnd: () => void, signal?: AbortSignal) {
        try {
            const postUrl = `${this.apiUrl}/stream`;
            const response = await this.fetchApi(postUrl, {
                method: "POST",
                body: JSON.stringify(input),
                signal,
            });

            const reader = response.body?.getReader();
            const decoder = new TextDecoder();
            let buffer = "";

            if (reader) {
                let doneReading = false;
                while (!doneReading) {
                    const { value, done } = await reader.read();
                    doneReading = done;
                    if (value) {
                        buffer += decoder.decode(value, { stream: true });
                        const lines = buffer.split("\n");
                        buffer = lines.pop() ?? "";
                        for (const line of lines) {
                            if (!line.trim()) continue;
                            try {
                                onMessage(JSON.parse(line) as Message);
                            } catch {}
                        }
                    }
                }
                if (buffer.trim()) {
                    try {
                        onMessage(JSON.parse(buffer) as Message);
                    } catch {}
                }
                onEnd();
            }
        } catch (e: any) {
            if (e?.name === "AbortError") {
                onEnd();  // user stopped — keep partial message, don't show error
            } else {
                console.log("error", e);
                onError();
            }
            return null;
        }
    }
}

export const messageService = new MessageService("/api/v1/messages");
