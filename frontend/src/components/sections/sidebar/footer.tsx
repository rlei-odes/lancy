import React, { FunctionComponent, useEffect, useState } from "react";
import { Theme, useTheme } from "@/hooks/useTheme";
import { config } from "@/config";
import { Trans, useTranslation } from "react-i18next";
import { cn } from "@/lib/lorem";

const API_BASE = "";

export const Footer: FunctionComponent = () => {
    const { theme } = useTheme();
    const { t } = useTranslation("app");
    const [startedAt, setStartedAt] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;
        let timer: ReturnType<typeof setTimeout>;

        const poll = () => {
            fetch(`${API_BASE}/api/v1/rag/status`, { credentials: "include" })
                .then((r) => r.ok ? r.json() : null)
                .then((d) => {
                    if (cancelled) return;
                    if (d?.started_at) {
                        const dt = new Date(d.started_at);
                        setStartedAt(dt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
                    } else {
                        timer = setTimeout(poll, 2000);
                    }
                })
                .catch(() => {
                    if (!cancelled) timer = setTimeout(poll, 2000);
                });
        };

        poll();
        return () => { cancelled = true; clearTimeout(timer); };
    }, []);

    const isDarkMode = theme === Theme.DARK;
    return (
        <div className="flex justify-center w-full py-1 px-2">
            <div className="flex flex-col items-center">
                <label className="text-xs opacity-50">
                    {t("version", { version: config.app.version })}
                    {startedAt && <span className="ml-1">· ↑ {startedAt}</span>}
                </label>
                <div className="text-xs opacity-50 text-center leading-tight">
                    <Trans
                        i18nKey="credits"
                        components={{
                            1: (
                                <a
                                    className={cn("cursor-pointer", isDarkMode ? "text-blue-400" : "text-blue-600")}
                                    href="https://www.datascience.ch"
                                    target="_blank"
                                    rel="noreferrer"
                                />
                            ),
                            2: <br />,
                            3: (
                                <a
                                    className={cn("cursor-pointer", isDarkMode ? "text-blue-400" : "text-blue-600")}
                                    href="https://www.vonlanthen.tv"
                                    target="_blank"
                                    rel="noreferrer"
                                />
                            ),
                            4: (
                                <a
                                    className={cn("cursor-pointer", isDarkMode ? "text-blue-400" : "text-blue-600")}
                                    href="https://lancy.tech"
                                    target="_blank"
                                    rel="noreferrer"
                                />
                            ),
                        }}
                    />
                </div>
            </div>
        </div>
    );
};
