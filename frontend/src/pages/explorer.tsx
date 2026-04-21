"use client";

import Head from "next/head";
import Link from "next/link";
import React from "react";
import { ArrowLeft } from "lucide-react";
import { useTheme } from "@/hooks/useTheme";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { cn } from "@/lib/lorem";
import { RagConfigSidebar } from "@/components/sections/rag-config-sidebar";
import { RetrievalProbe } from "@/components/sections/retrieval-probe";

export default function ExplorerPage() {
    const { cssClass } = useTheme();
    const { isMobile } = useMediaQuery();

    return (
        <>
            <Head><title>Retrieval Explorer</title></Head>
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
                            title="Back to chat"
                        >
                            <ArrowLeft className="h-4 w-4" />
                        </Link>
                        <div className="h-4 w-px bg-border" />
                        <h1 className="text-sm font-semibold tracking-tight">Retrieval Explorer</h1>
                        <span className="text-xs text-muted-foreground/60 font-mono">
                            — probe the retrieval pipeline without the LLM
                        </span>
                    </div>

                    {/* Scrollable probe area */}
                    <div className="flex-1 overflow-y-auto">
                        <div className="max-w-3xl mx-auto px-5 py-6">
                            <RetrievalProbe />
                        </div>
                    </div>
                </div>

                {/* RAG config sidebar */}
                {!isMobile && <RagConfigSidebar />}
            </main>
        </>
    );
}
