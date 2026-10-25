import React, { FunctionComponent, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import { BookOpen, FlaskConical, History as HistoryIcon, Settings as SettingsIcon, ShieldCheck, SlidersHorizontal, User } from "lucide-react";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Settings } from "@/components/sections/sidebar/settings";
import { Help } from "@/components/sections/sidebar/help";
import { useBackendUptime } from "@/components/sections/sidebar/footer";
import { config } from "@/config";

type OpenSheet = "settings" | "reference" | null;

const initials = (name: string) =>
    name
        .split(/\s+/)
        .filter(Boolean)
        .slice(0, 2)
        .map((part) => part[0]!.toUpperCase())
        .join("");

export const UserMenu: FunctionComponent = () => {
    const { t } = useTranslation("app");
    const [me, setMe] = useState<{ role?: string; display_name?: string } | null>(null);
    const [sheet, setSheet] = useState<OpenSheet>(null);

    useEffect(() => {
        fetch("/api/auth/me")
            .then((r) => r.json())
            .then(setMe)
            .catch((e) => console.warn("[user-menu] could not read the signed-in user:", e));
    }, []);

    // Only single sign-on provides a name; with password login the role is all there is.
    const name = me?.display_name ?? "";
    const label = name || (me?.role === "admin" ? t("next.roleAdmin") : me?.role ? t("next.roleUser") : t("next.userMenu"));
    const { startedAt, uptime } = useBackendUptime();
    const close = () => setSheet(null);

    return (
        <>
            <DropdownMenu>
                <DropdownMenuTrigger asChild>
                    <button type="button" className="flex w-full items-center gap-2.5 rounded-lg px-1.5 py-1.5 text-left hover:bg-muted">
                        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted text-[13px] font-semibold">
                            {name ? initials(name) : <User className="h-4 w-4" />}
                        </span>
                        <span className="flex min-w-0 flex-1 flex-col">
                            <span className="truncate text-sm font-medium">{label}</span>
                            <span className="truncate text-[11px] text-muted-foreground" title={startedAt ? new Date(startedAt).toLocaleString() : undefined}>
                                {t("version", { version: config.app.version })}
                                {uptime && ` · ↑ ${uptime}`}
                            </span>
                        </span>
                        <SlidersHorizontal className="h-4 w-4 shrink-0 text-muted-foreground" />
                    </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent side="top" align="start" className="w-60">
                    <DropdownMenuItem onClick={() => setSheet("settings")} className="gap-2 py-2.5">
                        <SettingsIcon className="h-4 w-4" />
                        {t("settings")}
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => setSheet("reference")} className="gap-2 py-2.5">
                        <BookOpen className="h-4 w-4" />
                        {t("help.title")}
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem asChild className="gap-2 py-2.5">
                        <Link href="/explorer">
                            <FlaskConical className="h-4 w-4" />
                            {t("next.advanced")}
                        </Link>
                    </DropdownMenuItem>
                    {me?.role === "admin" && (
                        <DropdownMenuItem asChild className="gap-2 py-2.5">
                            <Link href="/admin">
                                <ShieldCheck className="h-4 w-4" />
                                {t("next.admin")}
                            </Link>
                        </DropdownMenuItem>
                    )}
                    <DropdownMenuItem asChild className="gap-2 py-2.5">
                        <Link href="/">
                            <HistoryIcon className="h-4 w-4" />
                            {t("next.classicView")}
                        </Link>
                    </DropdownMenuItem>
                </DropdownMenuContent>
            </DropdownMenu>

            <Sheet open={sheet !== null} onOpenChange={(open) => !open && close()}>
                <SheetContent side="left" className="w-[340px] overflow-y-auto bg-background p-0 sm:max-w-[340px]">
                    <SheetTitle className="sr-only">{sheet === "reference" ? t("help.title") : t("settings")}</SheetTitle>
                    {sheet === "settings" && <Settings onClickBack={close} />}
                    {sheet === "reference" && <Help onClickBack={close} />}
                </SheetContent>
            </Sheet>
        </>
    );
};
