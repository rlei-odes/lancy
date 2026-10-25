import React, { FunctionComponent, useLayoutEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { PanelRight } from "lucide-react";
import { cn } from "@/lib/lorem";
import { config } from "@/config";
import { useMessaging } from "@/hooks/useMessaging";
import { useTheme } from "@/hooks/useTheme";
import { Avatar, AvatarImage } from "@/components/ui/avatar";
import { Suggestion, Suggestions } from "@/components/ui/suggestion";
import { MessageTypes } from "@/services/message";
import { ChatSidebar } from "@/components/next/chat-sidebar";
import { ChatMessage } from "@/components/next/chat-message";
import { Composer } from "@/components/next/composer";
import { DocumentPanel } from "@/components/next/document-panel";
import { DocumentPanelProvider, useDocumentPanel } from "@/components/next/document-panel-context";

const Thread: FunctionComponent<{ kbName?: string }> = ({ kbName }) => {
    const { thread, activeConversationId } = useMessaging();
    const endRef = useRef<HTMLDivElement>(null);
    const previousConversationId = useRef<string | null>(null);

    // Jump when another conversation opens, glide while the current one grows.
    useLayoutEffect(() => {
        const sameConversation = previousConversationId.current === activeConversationId;
        endRef.current?.scrollIntoView({ behavior: sameConversation ? "smooth" : "auto" });
        previousConversationId.current = activeConversationId;
    }, [thread, activeConversationId]);

    const messages = thread.filter((m) => m.role !== "system");
    return (
        <div className="flex-1 overflow-y-auto px-6 py-8">
            <div className="mx-auto flex max-w-[760px] flex-col gap-7">
                {messages.map((message, index) => (
                    <ChatMessage key={message.id || `streaming-${index}`} message={message} isLast={index === messages.length - 1} kbName={kbName} />
                ))}
                <div ref={endRef} />
            </div>
        </div>
    );
};

const Welcome: FunctionComponent = () => {
    const { t } = useTranslation("app");
    const { sendMessage, cursor, sending } = useMessaging();
    const suggestions = t("suggestions", { returnObjects: true }) as Suggestion[];

    return (
        <div className="flex flex-1 flex-col items-center justify-center gap-6 overflow-y-auto px-6">
            <Avatar className="h-16 w-16 bg-transparent dark:bg-transparent">
                <AvatarImage src={config.app.logo} alt="" className="object-contain" />
            </Avatar>
            <h1 className="text-2xl font-medium">{t("welcome")}</h1>
            {Array.isArray(suggestions) && (
                <div className="w-full max-w-[760px]">
                    <Suggestions suggestions={suggestions} onClick={(text) => !sending && sendMessage(text, MessageTypes.NEXT, cursor)} />
                </div>
            )}
        </div>
    );
};

const Layout: FunctionComponent = () => {
    const { t } = useTranslation("app");
    const { cssClass } = useTheme();
    const { thread, loading, conversations, activeConversationId } = useMessaging();
    const { open, toggleOpen } = useDocumentPanel();
    const conversation = conversations.find((c) => c.id === activeConversationId);

    return (
        <main className={cn(cssClass, "flex h-dvh w-full overflow-hidden bg-background text-[15px] text-foreground")}>
            <ChatSidebar />
            <section className="flex h-full min-w-0 flex-1 flex-col">
                <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-5">
                    <h1 className="min-w-0 flex-1 truncate font-medium">{conversation?.title ?? ""}</h1>
                    <button
                        type="button"
                        onClick={toggleOpen}
                        aria-pressed={open}
                        aria-label={t("next.togglePanel")}
                        title={t("next.togglePanel")}
                        className={cn(
                            "flex h-9 w-9 items-center justify-center rounded-lg",
                            open ? "text-foreground hover:bg-muted" : "text-muted-foreground hover:bg-muted hover:text-foreground",
                        )}
                    >
                        <PanelRight className="h-[18px] w-[18px]" />
                    </button>
                </header>
                {loading ? <div className="flex-1" /> : thread.length ? <Thread kbName={conversation?.kb_name} /> : <Welcome />}
                <Composer />
            </section>
            {open && <DocumentPanel />}
        </main>
    );
};

/** The redesigned chat: conversations on the left, the chat in the middle, the documents behind each answer on the right. */
export const NextChat: FunctionComponent = () => (
    <DocumentPanelProvider>
        <Layout />
    </DocumentPanelProvider>
);
