"use client";

import Head from "next/head";
import Link from "next/link";
import { useRouter } from "next/router";
import React, { useEffect, useState } from "react";
import { ArrowLeft, BarChart2, Database, Palette } from "lucide-react";
import { useTheme } from "@/hooks/useTheme";
import { useRole } from "@/hooks/useRole";
import { cn } from "@/lib/lorem";
import { UsageAnalytics } from "@/components/sections/admin/usage-analytics";
import { DatabaseStats } from "@/components/sections/admin/database-stats";
import { BrandingSettings } from "@/components/sections/admin/branding-settings";

type Tab = "usage" | "database" | "branding";

const TABS: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: "usage",    label: "Usage Analytics", icon: <BarChart2 className="h-3.5 w-3.5" /> },
    { id: "database", label: "Database",         icon: <Database className="h-3.5 w-3.5" /> },
    { id: "branding", label: "Branding",          icon: <Palette className="h-3.5 w-3.5" /> },
];

export default function AdminPage() {
    const { cssClass } = useTheme();
    const { role, loading } = useRole();
    const router = useRouter();
    const [activeTab, setActiveTab] = useState<Tab>("usage");

    // Redirect non-admins away
    useEffect(() => {
        if (!loading && role !== "admin") {
            router.replace("/");
        }
    }, [role, loading, router]);

    if (loading || role !== "admin") return null;

    return (
        <>
            <Head><title>Admin — Lancy</title></Head>
            <main className={cn(cssClass, "bg-background flex w-full overflow-hidden")} style={{ height: "100dvh" }}>
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
                        <h1 className="text-sm font-semibold tracking-tight">Admin</h1>
                        <span className="text-xs text-muted-foreground/60 font-mono leading-none">system management</span>
                    </div>

                    {/* Tab strip */}
                    <div className="flex items-center gap-1 px-5 py-2 border-b border-border bg-muted/20 shrink-0">
                        {TABS.map((tab) => (
                            <button
                                key={tab.id}
                                onClick={() => setActiveTab(tab.id)}
                                className={cn(
                                    "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors",
                                    activeTab === tab.id
                                        ? "bg-background border border-border text-foreground shadow-sm"
                                        : "text-muted-foreground hover:text-foreground hover:bg-background/50"
                                )}
                            >
                                {tab.icon}
                                {tab.label}
                            </button>
                        ))}
                    </div>

                    {/* Content */}
                    <div className="flex-1 overflow-y-auto">
                        <div className="max-w-3xl mx-auto px-5 py-6">
                            {activeTab === "usage"    && <UsageAnalytics />}
                            {activeTab === "database" && <DatabaseStats />}
                            {activeTab === "branding" && <BrandingSettings />}
                        </div>
                    </div>
                </div>
            </main>
        </>
    );
}
