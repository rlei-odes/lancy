import React, { FunctionComponent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Avatar, AvatarImage } from "@/components/ui/avatar";
import { BookOpen, ChevronDown, ChevronRight, Settings, SquarePen, Tag, Trash2, Webhook } from "lucide-react";
import { SidebarButton } from "@/components/ui/sidebar-button";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { groupConversationsByDate } from "@/lib/conversation";
import { Footer } from "@/components/sections/sidebar/footer";
import { IndexingStatus } from "@/components/sections/sidebar/indexing-status";
import { config } from "@/config";
import { useMessaging } from "@/hooks/useMessaging";
import { cn } from "@/lib/lorem";
import { SearchBar } from "@/components/ui/search-bar";
import { Conversation } from "@/services/conversation";

interface Props {
    onClickSettings: () => void;
    onClickHelp: () => void;
    onClickWebhook: () => void;
    onChangeConversation?: (conversationId: string) => void;
}

export const History: FunctionComponent<Props> = (props: Props) => {
    const { onClickSettings, onClickHelp, onClickWebhook, onChangeConversation } = props;
    const { conversations, changeConversation, createNewConversation, activeConversationId, deleteAllConversations, deleteConversation } = useMessaging();
    const [isDeleteAllOpen, setIsDeleteAllOpen] = useState(false);
    const [pendingGroupIds, setPendingGroupIds] = useState<string[]>([]);
    const { t, i18n } = useTranslation("app");
    const { isMobile } = useMediaQuery();
    const [filter, setFilter] = useState<string>("");
    const [filteredConversations, setFilteredConversations] = useState<Conversation[]>(conversations);
    const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());

    const toggleGroup = (date: string) => {
        setCollapsedGroups((prev) => {
            const next = new Set(prev);
            if (next.has(date)) next.delete(date);
            else next.add(date);
            return next;
        });
    };

    const handleChangeConversation = (conversationId: string) => {
        if (onChangeConversation) {
            onChangeConversation(conversationId);
        }
        changeConversation(conversationId);
    };

    const handleNewConversation = () => {
        if (onChangeConversation) {
            onChangeConversation("");
        }
        createNewConversation();
    };

    const handleFilterConversations = (value: string) => {
        setFilteredConversations(conversations.filter((conversation) => conversation.title.toLowerCase().includes(value.toLowerCase())));
    };

    useEffect(() => {
        setFilteredConversations(conversations);
    }, [conversations]);

    // Group by session_label first, then within each session by date.
    const sessionLabels = Array.from(
        new Set(filteredConversations.map((c) => c.session_label || ""))
    ).sort((a, b) => {
        if (a === "" && b !== "") return 1;
        if (a !== "" && b === "") return -1;
        return a.localeCompare(b);
    });

    const groupedBySession = sessionLabels.map((label) => ({
        label,
        conversations: filteredConversations.filter((c) => (c.session_label || "") === label),
    }));

    // Within each session group, sub-group by date.
    const getSubGroups = (conversations: Conversation[]) => groupConversationsByDate(conversations, t);

    return (
        <div className="text-foreground h-full">
            <div className="flex flex-col h-full content-between">
                <div
                    className={cn("flex flex-row justify-between mt-4 mx-2 p-2", isMobile ? "" : "ripple rounded-md")}
                    onClick={() => {
                        if (!isMobile) {
                            handleNewConversation();
                        }
                    }}
                >
                    <div className="flex flex-row align-middle items-center">
                        <Avatar className="h-10 w-10 p-1 bg-white rounded-full border border-primary/10 flex items-center justify-center">
                            <AvatarImage src={config.app.logo} alt="Logo" loading="lazy" />
                        </Avatar>
                        <div className="text-xl flex pl-2 font-bold text-foreground cursor-default">{config.app.name}</div>
                    </div>
                    {!isMobile && (
                        <div className="flex flex-row align-middle items-center gap-2">
                            <Trash2
                                onClick={(e) => { e.stopPropagation(); setIsDeleteAllOpen(true); }}
                                className="w-4 h-4 mr-1 text-muted-foreground hover:text-destructive transition-colors"
                            />
                            <SquarePen onClick={createNewConversation} className="w-5 h-5 mr-2" />
                        </div>
                    )}
                </div>
                <SearchBar
                    onChange={(value: string) => {
                        handleFilterConversations(value);
                    }}
                    className="px-4"
                />
                <div className="flex flex-1 flex-col pt-3 overflow-scroll scrollbar-hide">
                    {groupedBySession.map(({ label, conversations: sessionConvs }) => {
                        const sessionKey = label || "__no_session__";
                        const subGroups = getSubGroups(sessionConvs);
                        const hasLabel = !!label;
                        return (
                            <div key={sessionKey}>
                                {hasLabel && (
                                    <div className="flex items-center group/session">
                                        <button
                                            onClick={() => toggleGroup("session:" + sessionKey)}
                                            className="flex flex-1 items-center gap-1.5 text-xs text-amber-700 dark:text-amber-300 hover:text-amber-600 dark:hover:text-amber-200 pl-4 pr-1 py-1.5 text-left transition-colors font-medium"
                                        >
                                            <Tag className="h-3 w-3 shrink-0" />
                                            <span className="truncate">{label}</span>
                                            {collapsedGroups.has("session:" + sessionKey)
                                                ? <ChevronRight className="h-3 w-3 shrink-0 ml-auto" />
                                                : <ChevronDown className="h-3 w-3 shrink-0 ml-auto" />
                                            }
                                        </button>
                                        <button
                                            onClick={() => setPendingGroupIds(sessionConvs.map((c) => c.id))}
                                            className="pr-3 py-1.5 opacity-0 group-hover/session:opacity-100 transition-opacity"
                                            title={`Alle Chats in «${label}» löschen`}
                                        >
                                            <Trash2 className="h-3 w-3 text-muted-foreground hover:text-destructive transition-colors" />
                                        </button>
                                    </div>
                                )}
                                {!collapsedGroups.has("session:" + sessionKey) && subGroups.map(
                                    ({ date, conversations: dateCons }) =>
                                        dateCons.length !== 0 && (
                                            <div key={date} className={cn("flex flex-col", hasLabel && "ml-2")}>
                                                <div className="flex items-center group/date">
                                                <button
                                                    onClick={() => toggleGroup(sessionKey + ":" + date)}
                                                    className="flex flex-1 items-center justify-between text-xs text-foreground opacity-50 hover:opacity-80 pl-4 pr-1 py-2 text-left transition-opacity"
                                                >
                                                    <span>{date}</span>
                                                    {collapsedGroups.has(sessionKey + ":" + date)
                                                        ? <ChevronRight className="h-3 w-3 shrink-0" />
                                                        : <ChevronDown className="h-3 w-3 shrink-0" />
                                                    }
                                                </button>
                                                <button
                                                    onClick={() => setPendingGroupIds(dateCons.map((c) => c.id))}
                                                    className="pr-3 py-2 opacity-0 group-hover/date:opacity-100 transition-opacity"
                                                    title={`Alle Chats von «${date}» löschen`}
                                                >
                                                    <Trash2 className="h-3 w-3 text-muted-foreground hover:text-destructive transition-colors" />
                                                </button>
                                                </div>
                                                {!collapsedGroups.has(sessionKey + ":" + date) && dateCons.map((conversation) => (
                                                    <SidebarButton
                                                        conversationId={conversation.id}
                                                        label={conversation.title}
                                                        key={conversation.id}
                                                        onClick={() => handleChangeConversation(conversation.id)}
                                                        isSelected={conversation.id === activeConversationId}
                                                        kbName={conversation.kb_name}
                                                        ragConfigSnapshot={conversation.rag_config_snapshot}
                                                    />
                                                ))}
                                            </div>
                                        )
                                )}
                            </div>
                        );
                    })}
                </div>
                <div className="pt-2 space-y-1">
                    <IndexingStatus />
                    <div className="flex gap-2 py-1 px-2 mx-2">
                        <div
                            className="flex flex-1 py-2 px-3 cursor-pointer border border-border rounded-md justify-center hover:bg-muted"
                            onClick={onClickWebhook}
                        >
                            <div className="flex text-sm items-center gap-1 text-muted-foreground">
                                <Webhook className="h-4 w-4" /> {t("workflows.title")}
                            </div>
                        </div>
                    </div>
                    <div className="flex gap-2 py-1 px-2 mx-2">
                        <div
                            className="flex flex-1 py-2 px-3 cursor-pointer border border-border rounded-md justify-center hover:bg-muted"
                            onClick={onClickHelp}
                        >
                            <div className="flex text-sm items-center gap-1 text-muted-foreground">
                                <BookOpen className="h-4 w-4" /> {t("help.title")}
                            </div>
                        </div>
                        <div
                            className="flex flex-1 py-2 px-3 cursor-pointer border border-border rounded-md justify-center hover:bg-muted"
                            onClick={onClickSettings}
                        >
                            <div className="flex text-sm items-center gap-1 text-muted-foreground">
                                <Settings className="h-4 w-4" /> {t("settings")}
                            </div>
                        </div>
                    </div>
                </div>
                <Footer />
            </div>
            {isDeleteAllOpen && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
                    <div className="bg-background border border-border rounded-lg p-6 mx-4 max-w-sm w-full shadow-xl">
                        <h3 className="text-base font-semibold text-foreground mb-2">{t("deleteAll")}</h3>
                        <p className="text-sm text-muted-foreground mb-5">{t("deleteAllDescription")}</p>
                        <div className="flex justify-end gap-2">
                            <button
                                className="px-4 py-2 text-sm rounded-md hover:bg-muted transition-colors"
                                onClick={() => setIsDeleteAllOpen(false)}
                            >{t("cancel")}</button>
                            <button
                                className="px-4 py-2 text-sm rounded-md bg-destructive text-destructive-foreground hover:bg-destructive/80 transition-colors"
                                onClick={() => { deleteAllConversations(); setIsDeleteAllOpen(false); }}
                            >{t("confirm")}</button>
                        </div>
                    </div>
                </div>
            )}
            {pendingGroupIds.length > 0 && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
                    <div className="bg-background border border-border rounded-lg p-6 mx-4 max-w-sm w-full shadow-xl">
                        <h3 className="text-base font-semibold text-foreground mb-2">{pendingGroupIds.length} Chats löschen</h3>
                        <p className="text-sm text-muted-foreground mb-5">Diese {pendingGroupIds.length} Chats werden unwiderruflich gelöscht.</p>
                        <div className="flex justify-end gap-2">
                            <button
                                className="px-4 py-2 text-sm rounded-md hover:bg-muted transition-colors"
                                onClick={() => setPendingGroupIds([])}
                            >{t("cancel")}</button>
                            <button
                                className="px-4 py-2 text-sm rounded-md bg-destructive text-destructive-foreground hover:bg-destructive/80 transition-colors"
                                onClick={() => {
                                    pendingGroupIds.forEach((id) => deleteConversation(id));
                                    setPendingGroupIds([]);
                                }}
                            >{t("confirm")}</button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};
