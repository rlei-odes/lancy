"use client";

import Head from "next/head";
import Link from "next/link";
import React, { useState } from "react";
import { ArrowLeft } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useTheme } from "@/hooks/useTheme";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { cn } from "@/lib/lorem";
import { RagConfigSidebar } from "@/components/sections/rag-config-sidebar";
import { RetrievalProbe } from "@/components/sections/retrieval-probe";
import { ChunkBrowser } from "@/components/sections/chunk-browser";
import { KbAnalytics } from "@/components/sections/kb-analytics";

type Tab = "probe" | "browser" | "analytics";

export default function ExplorerPage() {
    const { cssClass } = useTheme();
    const { isMobile } = useMediaQuery();
    const { t } = useTranslation("app");
    const [activeTab, setActiveTab] = useState<Tab>("probe");
    const tabLabels: Record<Tab, string> = {
        probe: t("explorer.tabProbe"),
        browser: t("explorer.tabBrowser"),
        analytics: t("explorer.tabAnalytics"),
    };

    return (
        <>
            <Head><title>{t("explorer.pageTitle")}</title></Head>
            <main
                className={cn(cssClass, "bg-background flex w-full overflow-hidden")}
                style={{ height: "100dvh" }}
            >
                {/* Main content */}
                <div className="flex flex-col h-full w-full min-w-0">
                    {/* Header */}
                    <div className="flex items-center gap-3 px-5 py-3.5 border-b border-border shrink-0">
                        <Link
                            href="/"
                            className="flex items-center justify-center h-7 w-7 rounded-md hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
                            title={t("explorer.backToChat")}
                        >
                            <ArrowLeft className="h-4 w-4" />
                        </Link>
                        <div className="h-4 w-px bg-border" />
                        <h1 className="text-sm font-semibold tracking-tight">{t("explorer.pageTitle")}</h1>
                        <span className="text-xs text-muted-foreground/60 font-mono">
                            {t("explorer.subtitle")}
                        </span>
                    </div>

                    {/* Tab strip */}
                    <div className="flex items-center gap-1 px-5 py-2 border-b border-border bg-muted/20 shrink-0">
                        {(["probe", "browser", "analytics"] as Tab[]).map(tab => (
                            <button
                                key={tab}
                                onClick={() => setActiveTab(tab)}
                                className={cn(
                                    "px-3 py-1.5 rounded-md text-xs font-medium transition-colors",
                                    activeTab === tab
                                        ? "bg-background text-foreground shadow-sm border border-border"
                                        : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
                                )}
                            >
                                {tabLabels[tab]}
                            </button>
                        ))}
                    </div>

                    {/* Scrollable content area */}
                    <div className="flex-1 overflow-y-auto">
                        <div className={cn(activeTab === "probe" ? "max-w-3xl" : "max-w-6xl", "mx-auto px-5 py-6")}>
                            {activeTab === "probe" && <RetrievalProbe />}
                            {activeTab === "browser" && <ChunkBrowser active={activeTab === "browser"} />}
                            {activeTab === "analytics" && <KbAnalytics active={activeTab === "analytics"} />}
                        </div>
                    </div>
                </div>

                {/* RAG config sidebar */}
                {!isMobile && <RagConfigSidebar />}
            </main>
        </>
    );
}
