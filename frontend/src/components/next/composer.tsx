import React, { FunctionComponent, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, ArrowUp, BookOpen, Filter, Lock, MessageSquareOff, Plus, Square, X } from "lucide-react";
import { cn } from "@/lib/lorem";
import { useMessaging } from "@/hooks/useMessaging";
import { MessageTypes } from "@/services/message";
import { Disclaimer } from "@/components/ui/disclaimer";
import { DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { FilterPopover, useActiveKbFilters } from "@/components/sections/chat-action-bar";
import { KbPicker } from "@/components/next/kb-picker";
import { useContextBudget } from "@/components/next/use-context-budget";

const MAX_TEXTAREA_PX = 208;

const OptionChip: FunctionComponent<{ icon: React.ReactNode; label: React.ReactNode; title?: string; onRemove?: () => void; tone?: "default" | "danger" }> = ({
    icon,
    label,
    title,
    onRemove,
    tone = "default",
}) => {
    const { t } = useTranslation("app");
    return (
        <span
            title={title}
            className={cn(
                "inline-flex max-w-full items-center gap-1.5 rounded-full py-0.5 pl-2.5 text-xs",
                onRemove ? "pr-1" : "pr-2.5",
                tone === "danger" ? "bg-destructive/15 text-destructive" : "bg-primary/10 text-primary",
            )}
        >
            {icon}
            <span className="truncate">{label}</span>
            {onRemove && (
                <button type="button" aria-label={t("next.removeOption")} onClick={onRemove} className="rounded-full p-0.5 hover:bg-primary/15">
                    <X className="h-3 w-3" />
                </button>
            )}
        </span>
    );
};

const ExpandChip: FunctionComponent<{ files: string[]; onRemove: () => void }> = ({ files, onRemove }) => {
    const { t } = useTranslation("app");
    const { tokens, budget, over } = useContextBudget(files);
    const budgetText = tokens !== null && budget !== null ? ` · ${t("next.budget", { tokens, budget })}` : "";
    return (
        <OptionChip
            icon={<BookOpen className="h-3.5 w-3.5 shrink-0" />}
            label={t("next.expandArmed", { count: files.length }) + budgetText}
            title={over ? t("next.overBudget") : files.join("\n")}
            tone={over ? "danger" : "default"}
            onRemove={onRemove}
        />
    );
};

export const Composer: FunctionComponent = () => {
    const { t } = useTranslation("app");
    const {
        sendMessage,
        stopSending,
        sending,
        cursor,
        activeConversationId,
        chatFilters,
        setChatFilters,
        frozenChatFilters,
        chatOnly,
        setChatOnly,
        expandContextFiles,
        setExpandContextFiles,
    } = useMessaging();
    const kb = useActiveKbFilters();
    const [draft, setDraft] = useState("");
    const [filterOpen, setFilterOpen] = useState(false);
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const filterRef = useRef<HTMLDivElement>(null);
    const [kbIncompatible, setKbIncompatible] = useState(() => typeof window !== "undefined" && sessionStorage.getItem("lancy_kb_incompatible") === "1");

    useEffect(() => {
        const handler = (e: Event) => setKbIncompatible(!(e as CustomEvent).detail?.compatible);
        window.addEventListener("lancy-kb-pool-state", handler);
        return () => window.removeEventListener("lancy-kb-pool-state", handler);
    }, []);

    // Grow with the text up to a cap, then scroll.
    useEffect(() => {
        const el = textareaRef.current;
        if (!el) return;
        el.style.height = "auto";
        el.style.height = `${Math.min(el.scrollHeight, MAX_TEXTAREA_PX)}px`;
        el.style.overflowY = el.scrollHeight > MAX_TEXTAREA_PX ? "auto" : "hidden";
    }, [draft]);

    // Arming documents from the panel is the moment the user turns to the composer.
    useEffect(() => {
        if (expandContextFiles.length > 0) textareaRef.current?.focus();
    }, [expandContextFiles]);

    useEffect(() => {
        if (!filterOpen) return;
        const onDoc = (e: MouseEvent) => {
            if (filterRef.current && !filterRef.current.contains(e.target as Node)) setFilterOpen(false);
        };
        document.addEventListener("mousedown", onDoc);
        return () => document.removeEventListener("mousedown", onDoc);
    }, [filterOpen]);

    const expandArmed = expandContextFiles.length > 0;
    const canFilter = !activeConversationId && !!kb?.chat_filters.enabled && kb.chat_filters.keys.length > 0 && !expandArmed;
    const disabled = !draft.trim() || sending || kbIncompatible;

    const send = () => {
        if (disabled) return;
        sendMessage(draft, MessageTypes.NEXT, cursor);
        setDraft("");
    };

    const hasChips = frozenChatFilters.length > 0 || chatFilters.length > 0 || chatOnly || expandArmed;

    return (
        <div className="shrink-0 px-6 pb-4">
            <div className="mx-auto flex max-w-[760px] flex-col gap-2">
                {kbIncompatible && (
                    <div className="flex items-start gap-1.5 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                        <span>{t("kbIncompatibleWarning")}</span>
                    </div>
                )}
                <div ref={filterRef} className="relative">
                    {filterOpen && kb && (
                        <FilterPopover
                            kb={kb}
                            initial={Object.fromEntries(chatFilters.map((f) => [f.key, f.value]))}
                            onApply={(next) => {
                                setChatFilters(next);
                                setFilterOpen(false);
                            }}
                            onCancel={() => setFilterOpen(false)}
                        />
                    )}
                    <div className="flex flex-col gap-2 rounded-2xl border border-border bg-card px-3 pb-2.5 pt-3 transition-colors focus-within:border-primary/60">
                        {hasChips && (
                            <div className="flex flex-wrap gap-1.5 px-1">
                                {activeConversationId &&
                                    frozenChatFilters.map((f) => (
                                        <OptionChip
                                            key={`frozen-${f.key}`}
                                            icon={<Lock className="h-3 w-3 shrink-0" />}
                                            label={`${f.key}: ${f.value}`}
                                            title={t("next.filterLocked")}
                                        />
                                    ))}
                                {chatFilters.map((f) => (
                                    <OptionChip
                                        key={f.key}
                                        icon={<Filter className="h-3 w-3 shrink-0" />}
                                        label={`${f.key}: ${f.value}`}
                                        onRemove={() => setChatFilters(chatFilters.filter((x) => x.key !== f.key))}
                                    />
                                ))}
                                {chatOnly && (
                                    <OptionChip
                                        icon={<MessageSquareOff className="h-3.5 w-3.5 shrink-0" />}
                                        label={t("chatOnly.label")}
                                        title={t("chatOnly.tooltipOn")}
                                        onRemove={() => setChatOnly(false)}
                                    />
                                )}
                                {expandArmed && <ExpandChip files={expandContextFiles} onRemove={() => setExpandContextFiles([])} />}
                            </div>
                        )}
                        <textarea
                            ref={textareaRef}
                            rows={1}
                            value={draft}
                            maxLength={32000}
                            placeholder={t("messagePlaceholder")}
                            aria-label={t("messagePlaceholder")}
                            onChange={(e) => setDraft(e.target.value)}
                            onKeyDown={(e) => {
                                if (e.key === "Enter" && !e.shiftKey) {
                                    e.preventDefault();
                                    send();
                                }
                            }}
                            className="w-full resize-none bg-transparent px-1 text-[15px] leading-6 placeholder:text-muted-foreground focus:outline-hidden"
                        />
                        <div className="flex items-center gap-2">
                            <DropdownMenu>
                                <DropdownMenuTrigger asChild>
                                    <button
                                        type="button"
                                        aria-label={t("next.options")}
                                        title={t("next.options")}
                                        className="flex h-8 w-8 items-center justify-center rounded-lg border border-border text-muted-foreground hover:bg-muted hover:text-foreground"
                                    >
                                        <Plus className="h-4 w-4" />
                                    </button>
                                </DropdownMenuTrigger>
                                <DropdownMenuContent side="top" align="start" className="min-w-56">
                                    {canFilter && (
                                        <DropdownMenuItem onClick={() => setFilterOpen(true)} className="gap-2">
                                            <Filter className="h-4 w-4" />
                                            {chatFilters.length > 0 ? t("chatFilters.edit") : t("chatFilters.add")}
                                        </DropdownMenuItem>
                                    )}
                                    <DropdownMenuCheckboxItem
                                        checked={chatOnly}
                                        disabled={expandArmed}
                                        onCheckedChange={(v) => setChatOnly(!!v)}
                                        title={chatOnly ? t("chatOnly.tooltipOn") : t("chatOnly.tooltipOff")}
                                    >
                                        {t("chatOnly.label")}
                                    </DropdownMenuCheckboxItem>
                                </DropdownMenuContent>
                            </DropdownMenu>
                            <KbPicker />
                            <div className="flex-1" />
                            {sending ? (
                                <button
                                    type="button"
                                    aria-label={t("next.stop")}
                                    title={t("next.stop")}
                                    onClick={stopSending}
                                    className="flex h-9 w-9 items-center justify-center rounded-full bg-foreground text-background hover:bg-foreground/85"
                                >
                                    <Square className="h-3.5 w-3.5 fill-current" />
                                </button>
                            ) : (
                                <button
                                    type="button"
                                    aria-label={t("send")}
                                    title={t("send")}
                                    disabled={disabled}
                                    onClick={send}
                                    className="flex h-9 w-9 items-center justify-center rounded-full bg-primary text-primary-foreground hover:bg-primary/90 disabled:cursor-default disabled:bg-muted disabled:text-muted-foreground"
                                >
                                    <ArrowUp className="h-[18px] w-[18px]" />
                                </button>
                            )}
                        </div>
                    </div>
                </div>
                <Disclaimer />
            </div>
        </div>
    );
};
