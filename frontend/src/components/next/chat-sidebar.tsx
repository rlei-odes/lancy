import React, { FunctionComponent, useState } from "react";
import { useTranslation } from "react-i18next";
import { MoreHorizontal, Pencil, Search, SquarePen, Trash } from "lucide-react";
import { cn } from "@/lib/lorem";
import { config } from "@/config";
import { useMessaging } from "@/hooks/useMessaging";
import { groupConversationsByPeriod } from "@/lib/conversation";
import { intlLocale } from "@/lib/lang/i18n";
import { Conversation } from "@/services/conversation";
import { Avatar, AvatarImage } from "@/components/ui/avatar";
import { DropdownMenu,DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { RenameDialog } from "@/components/sections/dialogs/rename-dialog";
import { DeleteDialog } from "@/components/sections/dialogs/delete-dialog";
import { UserMenu } from "@/components/next/user-menu";

/** Time of day for today's chats, the date for older ones (with the year only when it differs). */
const formatWhen = (timestamp: number, locale: string): string => {
    const date = new Date(timestamp);
    const now = new Date();
    if (date.toDateString() === now.toDateString()) return date.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
    return date.toLocaleDateString(locale, {
        day: "numeric",
        month: "short",
        ...(date.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}),
    });
};

const ConversationItem: FunctionComponent<{ conversation: Conversation; selected: boolean; onOpen: () => void }> = ({ conversation, selected, onOpen }) => {
    const { t, i18n } = useTranslation("app");
    const [renameOpen, setRenameOpen] = useState(false);
    const [deleteOpen, setDeleteOpen] = useState(false);
    const details = [conversation.kb_name, formatWhen(conversation.update_timestamp, intlLocale(i18n.language))].filter(Boolean).join(" · ");

    return (
        <div className={cn("group flex items-center rounded-lg", selected ? "bg-muted" : "hover:bg-muted/70")}>
            <button
                type="button"
                onClick={onOpen}
                aria-current={selected ? "page" : undefined}
                className="flex min-w-0 flex-1 flex-col px-2.5 py-1.5 text-left"
            >
                <span className={cn("truncate text-sm", selected && "font-medium")}>{conversation.title}</span>
                <span className="truncate text-xs text-muted-foreground">{details}</span>
            </button>
            <DropdownMenu>
                <DropdownMenuTrigger asChild>
                    <button
                        type="button"
                        aria-label={t("next.conversationMenu")}
                        className="mr-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground opacity-0 hover:bg-background hover:text-foreground focus:opacity-100 group-hover:opacity-100 data-[state=open]:opacity-100"
                    >
                        <MoreHorizontal className="h-4 w-4" />
                    </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent side="bottom" align="start" className="w-44">
                    <DropdownMenuItem onClick={() => setRenameOpen(true)} className="gap-2 py-2.5">
                        <Pencil className="h-4 w-4" />
                        {t("rename")}
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => setDeleteOpen(true)} className="gap-2 py-2.5">
                        <Trash className="h-4 w-4" />
                        {t("delete")}
                    </DropdownMenuItem>
                </DropdownMenuContent>
            </DropdownMenu>
            <RenameDialog isRenameDialogOpen={renameOpen} setIsRenameDialogOpen={setRenameOpen} conversationId={conversation.id} />
            <DeleteDialog isDeleteDialogOpen={deleteOpen} setIsDeleteDialogOpen={setDeleteOpen} conversationId={conversation.id} />
        </div>
    );
};

export const ChatSidebar: FunctionComponent = () => {
    const { t, i18n } = useTranslation("app");
    const { conversations, activeConversationId, changeConversation, createNewConversation } = useMessaging();
    const [query, setQuery] = useState("");

    const matching = conversations.filter((c) => c.title.toLowerCase().includes(query.trim().toLowerCase()));
    const groups = groupConversationsByPeriod(
        matching,
        { today: t("dates.today"), yesterday: t("dates.yesterday"), previous7Days: t("next.previous7Days"), previous30Days: t("next.previous30Days") },
        intlLocale(i18n.language),
    );

    return (
        <nav aria-label={t("next.searchChats")} className="flex h-full w-[264px] shrink-0 flex-col gap-3 border-r border-border bg-muted/50 px-3 py-3.5">
            <div className="flex items-center gap-2.5 px-1.5 py-1">
                <Avatar className="h-7 w-7 rounded-md bg-transparent dark:bg-transparent">
                    <AvatarImage src={config.app.logo} alt="" className="object-contain" />
                </Avatar>
                <span className="text-base font-semibold">{config.app.name}</span>
            </div>
            <button
                type="button"
                onClick={createNewConversation}
                className="group/new flex h-11 items-center gap-2.5 rounded-lg border border-border bg-card px-3 text-sm font-medium shadow-sm transition-[background-color,border-color,box-shadow,transform] duration-150 hover:border-primary/40 hover:bg-primary/5 hover:shadow-md focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background active:scale-[0.98] active:shadow-sm motion-reduce:transition-none motion-reduce:active:scale-100"
            >
                <SquarePen className="h-[18px] w-[18px] text-primary transition-transform duration-200 group-hover/new:-rotate-12 motion-reduce:transition-none motion-reduce:group-hover/new:rotate-0" />
                {t("newChat")}
            </button>
            <label className="flex h-9 items-center gap-2 rounded-lg px-2.5 text-muted-foreground focus-within:bg-card">
                <Search className="h-4 w-4 shrink-0" />
                <input
                    type="search"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder={t("next.searchChats")}
                    aria-label={t("next.searchChats")}
                    className="w-full bg-transparent text-sm text-foreground placeholder:text-muted-foreground focus:outline-hidden"
                />
            </label>
            <div className="scrollbar-hide flex min-h-0 flex-1 flex-col gap-3.5 overflow-y-auto">
                {groups.map((group) => (
                    <div key={group.label} className="flex flex-col gap-0.5">
                        <div className="px-2.5 pb-1 text-xs font-medium text-muted-foreground">{group.label}</div>
                        {group.conversations.map((conversation) => (
                            <ConversationItem
                                key={conversation.id}
                                conversation={conversation}
                                selected={conversation.id === activeConversationId}
                                onOpen={() => changeConversation(conversation.id)}
                            />
                        ))}
                    </div>
                ))}
            </div>
            <div className="border-t border-border pt-2">
                <UserMenu />
            </div>
        </nav>
    );
};
