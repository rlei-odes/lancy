"use client";

import { SendBar } from "@/components/sections/send-bar";
import { Welcome } from "@/components/sections/welcome";
import React from "react";
import { useMessaging } from "@/hooks/useMessaging";
import { useTheme } from "@/hooks/useTheme";
import { cn } from "@/lib/lorem";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { Sidebar } from "@/components/sections/sidebar/sidebar";
import { Header } from "@/components/sections/header";
import { MessageList } from "@/components/sections/message-list";
import { RagConfigSidebar } from "@/components/sections/rag-config-sidebar";

export default function Home() {
    const { thread, loading } = useMessaging();
    const { cssClass } = useTheme();
    const { isMobile } = useMediaQuery();

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

            {!isMobile && <RagConfigSidebar />}
        </main>
    );
}
