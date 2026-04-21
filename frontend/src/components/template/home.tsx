"use client";

import { SendBar } from "@/components/sections/send-bar";
import { Welcome } from "@/components/sections/welcome";
import React, { useState } from "react";
import { useMessaging } from "@/hooks/useMessaging";
import { useTheme } from "@/hooks/useTheme";
import { cn } from "@/lib/lorem";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { Sidebar } from "@/components/sections/sidebar/sidebar";
import { Header } from "@/components/sections/header";
import { DisclaimerDialog } from "@/components/sections/dialogs/disclaimer-dialog";
import { RagConfigPanel } from "@/components/sections/rag-config-panel";
import { BackendStatus } from "@/components/sections/backend-status";
import { MessageList } from "@/components/sections/message-list";
import { Settings2 } from "lucide-react";

const PANEL_SIZE = 320;

export default function Home() {
    const { thread, loading } = useMessaging();
    const { cssClass } = useTheme();
    const { isMobile } = useMediaQuery();
    const [showPanel, setShowPanel] = useState(true);

    return (
        <main className={cn(cssClass, "bg-background flex w-full overflow-hidden")} style={{ height: "100dvh" }}>
            {isMobile ? <Header /> : <Sidebar />}

            <div className="flex flex-col h-full w-full min-w-0">
                {loading ? (
                    <div className="flex flex-1 flex-col overflow-hidden" />
                ) : thread.length ? (
                    <MessageList />
                ) : (
                    <Welcome />
                )}
                <SendBar />
            </div>

            {!isMobile && (
                <>
                    {/* Toggle strip */}
                    <button
                        onClick={() => setShowPanel((v) => !v)}
                        title="RAG Parameters"
                        className={cn(
                            "flex items-center justify-center w-7 self-stretch border-l border-border hover:bg-muted transition-colors",
                            showPanel ? "text-primary" : "text-muted-foreground"
                        )}
                    >
                        <Settings2 className="w-4 h-4" />
                    </button>

                    {/* RAG config panel */}
                    <div
                        className="h-full flex-shrink-0 overflow-x-hidden transition-all duration-300 border-l border-border"
                        style={{ width: showPanel ? `${PANEL_SIZE}px` : "0px" }}
                    >
                        <div className="h-full" style={{ minWidth: `${PANEL_SIZE}px` }}>
                            <RagConfigPanel />
                        </div>
                    </div>
                </>
            )}

            <DisclaimerDialog />
            <BackendStatus />
        </main>
    );
}
