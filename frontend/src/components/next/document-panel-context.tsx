import React, { createContext, useContext, useState } from "react";
import { useMessaging } from "@/hooks/useMessaging";

interface DocumentPanelState {
    open: boolean;
    toggleOpen: () => void;
    /** The answer whose documents are shown; null follows the latest answer. */
    messageId: string | null;
    expandedFile: string | null;
    setExpandedFile: (file: string | null) => void;
    selected: string[];
    toggleSelected: (file: string) => void;
    clearSelection: () => void;
    /** Show an answer's documents, optionally with one of them expanded. */
    showDocuments: (messageId: string, file?: string) => void;
    /** Step the panel to another answer and point it out in the chat. */
    stepTo: (messageId: string) => void;
    /** The answer to point out; `key` restarts the hint when the same answer is stepped to again. */
    flash: { id: string; key: number } | null;
}

const DocumentPanelContext = createContext<DocumentPanelState | null>(null);

export const useDocumentPanel = () => {
    const state = useContext(DocumentPanelContext);
    if (!state) throw new Error("useDocumentPanel must be used inside DocumentPanelProvider");
    return state;
};

export const DocumentPanelProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const { sending, activeConversationId, thread } = useMessaging();
    const [open, setOpen] = useState(true);
    const [messageId, setMessageId] = useState<string | null>(null);
    const [expandedFile, setExpandedFile] = useState<string | null>(null);
    const [selected, setSelected] = useState<string[]>([]);
    const [flash, setFlash] = useState<{ id: string; key: number } | null>(null);
    const [wasSending, setWasSending] = useState(sending);
    const [shownConversation, setShownConversation] = useState(activeConversationId);
    const empty = thread.length === 0;
    const [wasEmpty, setWasEmpty] = useState(empty);

    const reset = () => {
        setMessageId(null);
        setExpandedFile(null);
        setSelected([]);
    };

    // A new question, another conversation or a fresh chat moves the panel back to
    // the latest answer and drops what was picked for the previous one. Adjusted
    // during render, React's documented way to reset state when an input changes.
    if (sending !== wasSending) {
        setWasSending(sending);
        if (sending) reset();
    }
    if (activeConversationId !== shownConversation) {
        setShownConversation(activeConversationId);
        reset();
    }
    if (empty !== wasEmpty) {
        setWasEmpty(empty);
        if (empty) reset();
    }

    const showDocuments = (id: string, file?: string) => {
        if (id !== messageId) setSelected([]);
        setMessageId(id);
        setExpandedFile(file ?? null);
        setOpen(true);
    };

    return (
        <DocumentPanelContext.Provider
            value={{
                open,
                toggleOpen: () => setOpen((v) => !v),
                messageId,
                expandedFile,
                setExpandedFile,
                selected,
                toggleSelected: (file) => setSelected((s) => (s.includes(file) ? s.filter((f) => f !== file) : [...s, file])),
                clearSelection: () => setSelected([]),
                showDocuments,
                stepTo: (id) => {
                    showDocuments(id);
                    setFlash((f) => ({ id, key: (f?.key ?? 0) + 1 }));
                },
                flash,
            }}
        >
            {children}
        </DocumentPanelContext.Provider>
    );
};
