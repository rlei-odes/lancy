import { ApiService } from "./api";
import { Message, Reaction } from "@/services/message";

export interface RagConfigSnapshot {
    retriever_top_k?: number;
    rrf_k?: number;
    bm25_enabled?: boolean;
    reranking_enabled?: boolean;
    reranking_candidate_pool?: number;
    hyde_enabled?: boolean;
    query_expansion?: number;
    llm_backend?: string;
    llm_model?: string;
    llm_temperature?: number;
    utility_llm_model?: string;
    embedding_backend?: string;
    embedding_model?: string;
    vs_type?: string;
}

export interface Conversation {
    id: string;
    title: string;
    update_timestamp: number;
    create_timestamp?: number;
    messages?: Message[];
    kb_id?: string;
    kb_name?: string;
    rag_config_snapshot?: RagConfigSnapshot;
    session_label?: string;
}

class ConversationService extends ApiService {
    constructor(apiUrl: string) {
        super(apiUrl);
    }

    async getAll(): Promise<Conversation[]> {
        try {
            const getUrl = `${this.apiUrl}`;
            const response = await this.fetchApi(getUrl);
            return response.json();
        } catch {
            return [];
        }
    }

    async get(conversationId: Conversation["id"]): Promise<Conversation | null> {
        try {
            if (!conversationId) {
                return null;
            }
            const getUrl = `${this.apiUrl}/${conversationId}`;
            const response = await this.fetchApi(getUrl);
            return response.json();
        } catch {
            return null;
        }
    }

    async getMessages(conversation_id: string): Promise<Message[]> {
        try {
            if (!conversation_id) {
                return [];
            }
            const getUrl = `${this.apiUrl}/${conversation_id}/messages`;
            const response = await this.fetchApi(getUrl);
            return response.json();
        } catch {
            return [];
        }
    }

    async delete(conversationId: Conversation["id"]): Promise<boolean | null> {
        try {
            if (!conversationId) {
                return null;
            }
            const deleteUrl = `${this.apiUrl}/${conversationId}`;
            const response = await this.fetchApi(deleteUrl, { method: "DELETE" });
            return response.json();
        } catch {
            return null;
        }
    }

    async deleteAll(): Promise<number | null> {
        try {
            const response = await this.fetchApi(this.apiUrl, { method: "DELETE" });
            return response.json();
        } catch {
            return null;
        }
    }

    async rename(conversationId: Conversation["id"], conversationName: string): Promise<Conversation | null> {
        try {
            if (!conversationId) {
                return null;
            }
            const putUrl = `${this.apiUrl}/${conversationId}`;
            const response = await this.fetchApi(putUrl, { method: "PUT", body: JSON.stringify({ title: conversationName }) });
            return response.json();
        } catch {
            return null;
        }
    }

    async react(conversationId: Conversation["id"], messageId: Message["id"], content: Reaction["content"]): Promise<Message | null> {
        try {
            const postUrl = `${this.apiUrl}/${conversationId}/messages/${messageId}/reactions`;
            const response = await this.fetchApi(postUrl, {
                method: "POST",
                body: JSON.stringify({ content }),
            });

            return response.json();
        } catch (e) {
            console.log("error", e);
            return null;
        }
    }
}

export const conversationService = new ConversationService(process.env.SERVER_URL! + "/api/v1/conversations");
