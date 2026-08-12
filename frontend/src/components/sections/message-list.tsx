import React, { FunctionComponent, useLayoutEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { MessageItem } from "@/components/ui/message-item";
import { Conversation } from "@/services/conversation";
import { useMessaging } from "@/hooks/useMessaging";

export const MessageList: FunctionComponent = () => {
    const { thread, activeConversationId, cursor } = useMessaging();

    const { t } = useTranslation("app");
    const messagesEndRef = useRef<HTMLDivElement>(null);
    // A ref, not state: this is only read to compare against the current id, never
    // rendered. As state it re-rendered the whole list on every thread change purely
    // to record a value the render output does not use.
    const previousConversationId = useRef<Conversation["id"] | null>(null);

    useLayoutEffect(() => {
        // Read before writing — scrolling smoothly means "same conversation as the
        // previous render", which is what the state version captured by setting it
        // first and then comparing against the not-yet-updated value.
        const isSameConversationId =
            previousConversationId.current && previousConversationId.current === activeConversationId;
        messagesEndRef.current?.scrollIntoView({ behavior: isSameConversationId ? "smooth" : "auto" });
        previousConversationId.current = activeConversationId;
    }, [thread]);

    return (
        <div className="flex flex-1 flex-col overflow-hidden">
            <div className={"flex flex-shrink-0 h-[70px]"} />
            <div className="flex justify-center overflow-y-auto overflow-x-hidden">
                <div className="w-full m-2 md:w-3/4 max-w-[800px]">
                    <div className="flex flex-col gap-2">
                        {thread
                            .filter((m) => m.role !== "system")
                            .map((message, index, msgs) => (
                                <MessageItem key={message.id} isLastMessage={index === msgs.length - 1} {...message} />
                            ))}
                        <div ref={messagesEndRef} />
                    </div>
                </div>
            </div>
        </div>
    );
};
