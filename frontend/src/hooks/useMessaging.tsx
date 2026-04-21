import React, { createContext, useContext, useEffect, useRef, useState } from "react";
import { messageService as mockMessageService } from "@/services/mock-message";
import { Message, messageService as realMessageService, MessageTypes, Reaction } from "@/services/message";
import { useTranslation } from "react-i18next";
import { conversationService as mockConversationService } from "@/services/mock-conversation";
import { Conversation, conversationService as realConversationService } from "@/services/conversation";
import { useRouter } from "next/router";
import { getLastChild, getMessageThread } from "@/lib/thread";

const shouldUseMock = Boolean(process.env.USE_MOCK_SERVICES);

const conversationService = shouldUseMock ? mockConversationService : realConversationService;
const messageService = shouldUseMock ? mockMessageService : realMessageService;

export const LOADING_ID = "loading";
export const ERROR_ID = "error";

const makeLoadingMessage = (conversationId: string, content: string): Message => ({
    id: LOADING_ID,
    content,
    role: "assistant",
    create_timestamp: new Date().getTime(),
    conversation_id: conversationId,
});

const makeErrorMessage = (conversation_id: string, error: string): Message => ({
    id: ERROR_ID,
    content: error,
    role: "assistant",
    create_timestamp: new Date().getTime(),
    conversation_id: conversation_id,
});

const MessagingContext = createContext<{
    thread: Message[];
    messages: Message[];
    conversations: Conversation[];
    activeConversationId: Conversation["id"];
    cursor: Message["id"];
    sending: boolean;
    loading: boolean;
    sessionLabel: string;
    setSessionLabel: (label: string) => void;
    createNewConversation: () => void;
    changeConversation: (conversationId: string) => void;
    changeThread: (messageId: string) => void;
    sendMessage: (input: string, type: MessageTypes, parentId?: string) => void;
    stopSending: () => void;
    renameConversation: (conversationId: Conversation["id"], conversationName: string) => void;
    deleteConversation: (conversationId: Conversation["id"]) => void;
    deleteAllConversations: () => void;
    reactToMessage: (messageId: Message["id"], reaction: Reaction["content"]) => void;
}>({
    thread: [],
    messages: [],
    conversations: [],
    activeConversationId: "",
    cursor: "",
    sending: false,
    loading: false,
    sessionLabel: "",
    setSessionLabel: () => {},
    createNewConversation: () => {},
    changeConversation: () => {},
    changeThread: () => {},
    sendMessage: () => {},
    stopSending: () => {},
    renameConversation: () => {},
    deleteConversation: () => {},
    deleteAllConversations: () => {},
    reactToMessage: () => {},
});

export const useMessaging = () => useContext(MessagingContext);

interface Props {
    children: React.ReactNode;
}

export const MessagingProvider: React.FC<Props> = ({ children }) => {
    const router = useRouter();
    const activeConversationId = (router?.query?.conversationId as string) || "";
    const [messages, setMessages] = useState<Message[]>([]);
    const [sending, setSending] = useState<boolean>(false);
    const [initialLoading, setInitialLoading] = useState<boolean>(true);
    const [conversations, setConversations] = useState<Conversation[]>([]);
    const [cursor, setCursor] = useState<Message["id"]>("");
    const [thread, setThread] = useState<Message[]>([]);
    const [sessionLabel, setSessionLabelState] = useState<string>(() => {
        if (typeof window !== "undefined") {
            return localStorage.getItem("sessionLabel") || "";
        }
        return "";
    });

    const setSessionLabel = (label: string) => {
        setSessionLabelState(label);
        if (typeof window !== "undefined") {
            localStorage.setItem("sessionLabel", label);
        }
    };

    const _saveSessionLabelToHistory = (label: string) => {
        if (!label || typeof window === "undefined") return;
        const prev: string[] = JSON.parse(localStorage.getItem("sessionLabelHistory") || "[]");
        const updated = [label, ...prev.filter((l) => l !== label)].slice(0, 10);
        localStorage.setItem("sessionLabelHistory", JSON.stringify(updated));
    };
    const abortControllerRef = useRef<AbortController | null>(null);
    const { t } = useTranslation("app");

    const setConversationId = (conversationId: Conversation["id"]) => {
        router.push(conversationId ? `/c/${conversationId}` : "/", undefined, { shallow: true });
    };

    const setMessagesAndThread = (messages: Message[], cursor?: Message["id"]) => {
        setMessages(messages);
        setThread(getMessageThread(messages, cursor));
    };

    useEffect(() => {
        // Always call /auth/refresh on startup so the backend can migrate old cookies
        // to the current stable user_id (deterministic hash of API_KEY).
        fetch(`${process.env.SERVER_URL}/auth/refresh`, { method: "POST", credentials: "include" })
            .catch(() => {})
            .finally(() => {
                if (activeConversationId) {
                    _changeConversation(activeConversationId).then(() => setInitialLoading(false));
                } else {
                    setInitialLoading(false);
                }
                conversationService.getAll().then((response) => {
                    if (response) {
                        setConversations(response);
                    }
                });
            });
    }, []);

    const createNewConversation = () => {
        setConversationId("");
        setCursor("");
        setMessagesAndThread([], "");
        // session label intentionally kept — user wants to keep labelling the same test session
    };

    const changeConversation = (conversationId: string) => {
        _changeConversation(conversationId);
    };

    const _changeConversation = (conversationId: string) => {
        return conversationService.getMessages(conversationId).then((response) => {
            if (response && response.length > 0) {
                const cur = response[response.length - 1].id;
                setCursor(cur);
                setMessagesAndThread(response, cur);
                setConversationId(conversationId);
            }
        });
    };

    const changeThread = (cursor: Message["id"]) => {
        const lastChild = getLastChild(messages, cursor);
        if (!lastChild) {
            return;
        }
        setCursor(lastChild.id);
        setThread(getMessageThread(messages, lastChild.id));
    };

    const _updateConversation = (conversationId: Conversation["id"], conversation: Conversation) => {
        setConversations((prev) => {
            const index = prev.findIndex((s) => s.id === conversationId);
            if (index !== -1) {
                prev[index] = conversation;
            }
            return [conversation, ...prev.filter((s) => s.id !== conversationId)];
        });
    };

    const sendMessage = (content: string, type: MessageTypes, parentId?: Message["id"]) => {
        const userMessage: Message = {
            id: String(messages.length),
            content,
            role: "user",
            create_timestamp: new Date().getTime(),
            conversation_id: activeConversationId ? activeConversationId : "",
            parent_id: parentId,
        };

        const userInput = {
            content,
            type,
            ...(activeConversationId ? { conversation_id: activeConversationId } : {}),
            ...(parentId ? { parent_id: parentId } : {}),
            ...(!activeConversationId && sessionLabel ? { session_label: sessionLabel } : {}),
        };

        if (type === MessageTypes.REDO) {
            setThread((prev) => [...getMessageThread(prev, parentId), makeLoadingMessage(activeConversationId, "")]);
        } else {
            setThread((prev) => [...getMessageThread(prev, parentId), userMessage, makeLoadingMessage(activeConversationId, "")]);
        }
        setSending(true);
        messageService
            .create(userInput)
            .then((response) => {
                if (response) {
                    if (!activeConversationId && response.conversation_id) {
                        setConversationId(response.conversation_id);
                        conversationService.get(response.conversation_id).then((conversation) => {
                            if (conversation) {
                                _updateConversation(response.conversation_id, conversation);
                            }
                        });
                    }
                    setThread((prev) => [...prev.slice(0, prev.length - 1), response]);
                    setCursor(response.id);
                    setSending(false);
                } else {
                    setThread((prev) => [...prev.slice(0, prev.length - 1), makeErrorMessage(activeConversationId, t("error"))]);
                    setSending(false);
                }
            })
            .catch(() => {
                // TODO: Add error message
                setThread((prev) => [...prev.slice(0, prev.length - 1), makeErrorMessage(activeConversationId, t("error"))]);
                setSending(false);
            });
    };

    const sendMessageStream = (content: string, type: MessageTypes, parentId?: Message["id"]) => {
        const userMessage: Message = {
            id: String(thread.length),
            content,
            role: "user",
            create_timestamp: new Date().getTime(),
            conversation_id: activeConversationId ? activeConversationId : "",
            parent_id: parentId,
        };
        const userInput = {
            content,
            type,
            ...(activeConversationId ? { conversation_id: activeConversationId } : {}),
            ...(parentId ? { parent_id: parentId } : {}),
            ...(!activeConversationId && sessionLabel ? { session_label: sessionLabel } : {}),
        };

        if (type === MessageTypes.REDO) {
            setThread((prev) => [...getMessageThread(prev, parentId), makeLoadingMessage(activeConversationId, "")]);
        } else {
            setThread((prev) => [...getMessageThread(prev, parentId), userMessage, makeLoadingMessage(activeConversationId, "")]);
        }
        setSending(true);
        const abortController = new AbortController();
        abortControllerRef.current = abortController;

        let loadedConversation = activeConversationId;
        let newCursor = cursor;

        messageService.createStream(
            userInput,
            (streamedMessage) => {
                // Update the last message (which is a loading message) with the new streamed message
                setThread((prev) => [...prev.slice(0, -1), streamedMessage]);

                if (cursor !== streamedMessage.id) {
                    newCursor = streamedMessage.id;
                    setCursor(newCursor);
                }

                // Handle conversation update if a new conversation was created
                if (!activeConversationId && streamedMessage.conversation_id && !loadedConversation) {
                    loadedConversation = streamedMessage.conversation_id;
                    _saveSessionLabelToHistory(sessionLabel);
                    setConversationId(streamedMessage.conversation_id);
                    // Add placeholder with user input as title (avoids race condition with done callback)
                    _updateConversation(streamedMessage.conversation_id, {
                        id: streamedMessage.conversation_id,
                        title: content.substring(0, 60),
                        update_timestamp: new Date().getTime(),
                        session_label: sessionLabel || undefined,
                    });
                }
            },
            () => {
                console.log("error");
                setThread((prev) => [...prev.slice(0, prev.length - 1), makeErrorMessage(activeConversationId, t("error"))]);
                setSending(false);
            },
            () => {
                abortControllerRef.current = null;
                // Remove loading placeholder immediately — don't wait for async refresh
                setThread((prev) => prev.filter((m) => m.id !== LOADING_ID));
                setSending(false);
                if (!activeConversationId) {
                    const titleFromContent = content.substring(0, 60);
                    conversationService.get(loadedConversation).then((conversation) => {
                        if (conversation) {
                            _updateConversation(loadedConversation, { ...conversation, title: titleFromContent });
                            conversationService.rename(loadedConversation, titleFromContent);
                            setMessages(conversation?.messages || []);
                            setThread(conversation?.messages || []);
                        }
                    });
                } else {
                    conversationService.get(activeConversationId).then((conversation) => {
                        setMessages(conversation?.messages || []);
                        setThread(getMessageThread(conversation?.messages || [], newCursor));
                    });
                }
            },
            abortController.signal,
        );
    };

    const stopSending = () => {
        abortControllerRef.current?.abort();
        abortControllerRef.current = null;
        setSending(false);
    };

    const renameConversation = (conversationId: Conversation["id"], conversationName: string) => {
        conversationService.rename(conversationId, conversationName).then((response) => {
            if (response) {
                _updateConversation(conversationId, response);
            }
        });
    };

    const deleteConversation = (conversationId: Conversation["id"]) => {
        conversationService.delete(conversationId).then((response) => {
            if (response) {
                if (activeConversationId === conversationId) {
                    createNewConversation();
                }
                setConversations((prev) => prev.filter((s) => s.id !== conversationId));
            }
        });
    };

    const deleteAllConversations = () => {
        conversationService.deleteAll().then((count) => {
            if (count !== null) {
                createNewConversation();
                setConversations([]);
            }
        });
    };

    const reactToMessage = (messageId: Message["id"], reactionContent: Reaction["content"]) => {
        if (activeConversationId) {
            conversationService.react(activeConversationId, messageId, reactionContent);
        }
    };

    return (
        <MessagingContext.Provider
            value={{
                messages,
                thread,
                conversations,
                activeConversationId,
                cursor,
                sending,
                loading: initialLoading,
                sessionLabel,
                setSessionLabel,
                createNewConversation,
                changeConversation,
                changeThread,
                sendMessage: sendMessageStream,
                stopSending,
                renameConversation,
                deleteConversation,
                deleteAllConversations,
                reactToMessage,
            }}
        >
            {children}
        </MessagingContext.Provider>
    );
};
