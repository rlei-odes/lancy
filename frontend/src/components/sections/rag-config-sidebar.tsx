"use client";

import React, { FunctionComponent, useState } from "react";
import { Settings2 } from "lucide-react";
import { cn } from "@/lib/lorem";
import { RagConfigPanel } from "@/components/sections/rag-config-panel";

const PANEL_SIZE = 320;

export const RagConfigSidebar: FunctionComponent = () => {
    const [showPanel, setShowPanel] = useState(true);

    return (
        <>
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

            <div
                className="h-full flex-shrink-0 overflow-x-hidden transition-all duration-300 border-l border-border"
                style={{ width: showPanel ? `${PANEL_SIZE}px` : "0px" }}
            >
                <div className="h-full" style={{ minWidth: `${PANEL_SIZE}px` }}>
                    <RagConfigPanel />
                </div>
            </div>
        </>
    );
};
