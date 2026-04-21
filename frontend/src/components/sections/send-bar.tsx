import React, { FunctionComponent, useEffect, useState } from "react";
import { NewMessageInput } from "@/components/ui/new-message-input";
import { useTranslation } from "react-i18next";
import { Suggestion, Suggestions } from "@/components/ui/suggestion";
import { Disclaimer } from "@/components/ui/disclaimer";
import { SendHorizonal, Square, Tag, X } from "lucide-react";
import { cn } from "@/lib/lorem";
import { useMessaging } from "@/hooks/useMessaging";
import { MessageTypes } from "@/services/message";

const API_BASE = typeof window !== "undefined" ? "" : (process.env.SERVER_URL ?? "");

// Strip common prefixes and extension from filenames for readable display
function cleanFilename(filename: string): string {
    return filename.replace(/\.(pdf|xlsx|xls|docx|doc|md|txt|csv)$/i, "").replace(/^[A-Z]+_/, "").replace(/_/g, " ");
}

export const SendBar: FunctionComponent = () => {
    const { thread, sendMessage: sendMessageStream, stopSending, sending, loading, cursor, sessionLabel, setSessionLabel, activeConversationId } = useMessaging();
    const [message, setMessage] = React.useState<string>("");
    const { t } = useTranslation("app");
    const [kbFiles, setKbFiles] = useState<string[]>([]);
    const [pastLabels, setPastLabels] = useState<string[]>(() =>
        typeof window !== "undefined" ? JSON.parse(localStorage.getItem("sessionLabelHistory") || "[]") : []
    );

    // Refresh past labels from localStorage whenever sessionLabel changes (catches newly saved labels)
    useEffect(() => {
        if (typeof window !== "undefined") {
            setPastLabels(JSON.parse(localStorage.getItem("sessionLabelHistory") || "[]"));
        }
    }, [sessionLabel]);

    useEffect(() => {
        fetch(`${API_BASE}/api/v1/rag/store-info`, { credentials: "include" })
            .then((r) => r.ok ? r.json() : null)
            .then((d) => { if (d?.file_list?.length) setKbFiles(d.file_list); })
            .catch(() => {});
    }, []);

    const showSuggestions = !loading && !thread.length;

    const handleSendMessage = (message: string) => {
        if (!sending) {
            sendMessageStream(message, MessageTypes.NEXT, cursor);
            setMessage("");
        }
    };

    // Build dynamic suggestions from KB files (2 file-specific + 2 generic)
    const buildSuggestions = (): Suggestion[] => {
        const templates: Suggestion[] = t("suggestionTemplates", { returnObjects: true }) as Suggestion[];
        const fallback: Suggestion[] = t("suggestions", { returnObjects: true }) as Suggestion[];
        if (!kbFiles.length || !Array.isArray(templates)) return fallback;

        const picked = kbFiles.slice(0, 2);
        const dynamic: Suggestion[] = picked.map((file, i) => {
            const tpl = templates[i % templates.length];
            const label = cleanFilename(file);
            return {
                text: tpl.text,
                subtext: (tpl.subtext ?? "").replace("{{file}}", label),
            };
        });
        return [...dynamic, ...fallback.slice(0, 2)];
    };

    const suggestions = buildSuggestions();

    const disabled = message.length === 0 || sending;

    return (
        <div className="w-full flex justify-center">
            <div className="w-full m-2 md:w-3/4 max-w-[700px]">
                <div className="flex justify-center w-full flex-col px-2 sm:px-0">
                    {showSuggestions ? (
                        <div className="px:0 pb-2 sm:p-2 overflow-scroll scrollbar-hide">
                            <Suggestions suggestions={suggestions} onClick={handleSendMessage} />
                        </div>
                    ) : null}
                    <div className="relative flex items-center">
                        <NewMessageInput
                            className="pr-12 w-full text-base"
                            value={message}
                            onChange={(e) => {
                                setMessage(e.target.value);
                            }}
                            placeholder={t("messagePlaceholder")}
                            onPressEnter={() => handleSendMessage(message)}
                        />
                        {sending ? (
                            <Square
                                className="absolute right-0 bottom-0 mb-3 mr-3 p-2 h-9 w-9 border text-white rounded cursor-pointer bg-red-500 hover:bg-red-400 border-red-400 transition ease-in-out duration-300"
                                onClick={stopSending}
                            />
                        ) : (
                            <SendHorizonal
                                className={cn(
                                    "absolute right-0 bottom-0 mb-3 mr-3 p-2 h-9 w-9 border text-white rounded transition ease-in-out duration-300",
                                    disabled ? "bg-gray-400 cursor-default" : "cursor-pointer bg-secondary hover:bg-secondary/85 border-secondary/20",
                                )}
                                onClick={() => !disabled && handleSendMessage(message)}
                            />
                        )}
                    </div>
                                    {/* Session label — only show when starting a new conversation */}
                    {!activeConversationId && (
                        <div className="flex flex-col gap-1 mt-1.5 px-1">
                            <div className="flex items-center gap-2">
                                <Tag className="h-3.5 w-3.5 text-amber-400/70 shrink-0" />
                                <input
                                    type="text"
                                    value={sessionLabel}
                                    onChange={(e) => setSessionLabel(e.target.value)}
                                    placeholder="Test-Session (optional)"
                                    className="flex-1 text-xs bg-transparent border-b border-foreground/10 focus:border-amber-400/50 outline-none text-muted-foreground placeholder:text-foreground/20 py-0.5 transition-colors"
                                />
                                {sessionLabel && (
                                    <X
                                        className="h-3 w-3 text-muted-foreground hover:text-foreground cursor-pointer shrink-0"
                                        onClick={() => setSessionLabel("")}
                                    />
                                )}
                            </div>
                            {pastLabels.length > 0 && (
                                <div className="flex flex-wrap gap-1 pl-5 items-center">
                                    {pastLabels.map((l) => (
                                        <span
                                            key={l}
                                            className={`inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded-full border transition-colors ${
                                                sessionLabel === l
                                                    ? "border-amber-400/60 bg-amber-400/15 text-amber-300"
                                                    : "border-foreground/15 text-muted-foreground"
                                            }`}
                                        >
                                            <button onClick={() => setSessionLabel(l)} className="hover:text-amber-300/80 transition-colors">{l}</button>
                                            <button
                                                onClick={() => {
                                                    const updated = pastLabels.filter((x) => x !== l);
                                                    localStorage.setItem("sessionLabelHistory", JSON.stringify(updated));
                                                    setPastLabels(updated);
                                                    if (sessionLabel === l) setSessionLabel("");
                                                }}
                                                className="opacity-50 hover:opacity-100 hover:text-destructive transition-colors ml-0.5"
                                            >×</button>
                                        </span>
                                    ))}
                                    <button
                                        onClick={() => {
                                            localStorage.removeItem("sessionLabelHistory");
                                            setPastLabels([]);
                                        }}
                                        className="text-[10px] text-muted-foreground/40 hover:text-muted-foreground transition-colors"
                                    >clear all</button>
                                </div>
                            )}
                        </div>
                    )}
                    <Disclaimer />
                </div>
            </div>
        </div>
    );
};
