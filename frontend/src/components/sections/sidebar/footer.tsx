import React, { FunctionComponent, useEffect, useState } from "react";
import { Theme, useTheme } from "@/hooks/useTheme";
import { config } from "@/config";
import { Trans, useTranslation } from "react-i18next";
import { cn } from "@/lib/lorem";

const API_BASE = "";

const RETRY_MS = 2_000;     // backend not answering yet — keep trying
const REFRESH_MS = 300_000; // re-read started_at, so a restart resets the clock
const TICK_MS = 30_000;     // recompute the rendered duration

type Translate = ReturnType<typeof useTranslation>["t"];

/**
 * Elapsed time since the backend started, in the coarsest two units that fit.
 *
 * The duration is measured against the viewer's clock, which can sit behind the
 * server's — most visibly right after a restart. A skewed clock yields 0 rather
 * than a negative or absurd figure.
 */
const formatUptime = (startedAt: string, now: number, t: Translate): string | null => {
    const started = new Date(startedAt).getTime();
    if (Number.isNaN(started)) return null;

    const minutes = Math.max(0, Math.floor((now - started) / 60_000));
    if (minutes < 60) return t("uptime.minutes", { minutes });

    const hours = Math.floor(minutes / 60);
    if (hours < 24) return t("uptime.hours", { hours, minutes: minutes % 60 });

    return t("uptime.days", { days: Math.floor(hours / 24), hours: hours % 24 });
};

export const Footer: FunctionComponent = () => {
    const { theme } = useTheme();
    const { t } = useTranslation("app");
    const [startedAt, setStartedAt] = useState<string | null>(null);
    const [now, setNow] = useState(() => Date.now());

    useEffect(() => {
        let cancelled = false;
        let timer: ReturnType<typeof setTimeout>;

        const poll = () => {
            fetch(`${API_BASE}/api/v1/rag/status`, { credentials: "include" })
                .then((r) => r.ok ? r.json() : null)
                .then((d) => {
                    if (cancelled) return;
                    setStartedAt(d?.started_at ?? null);
                    // Paired with the read, so the first render is not measured
                    // against a mount time that may be many retries old.
                    setNow(Date.now());
                    timer = setTimeout(poll, d?.started_at ? REFRESH_MS : RETRY_MS);
                })
                .catch(() => {
                    if (!cancelled) timer = setTimeout(poll, RETRY_MS);
                });
        };

        poll();
        return () => { cancelled = true; clearTimeout(timer); };
    }, []);

    // Only ticks while there is something to count from.
    useEffect(() => {
        if (!startedAt) return;
        const ticker = setInterval(() => setNow(Date.now()), TICK_MS);
        return () => clearInterval(ticker);
    }, [startedAt]);

    const uptime = startedAt ? formatUptime(startedAt, now, t) : null;
    const isDarkMode = theme === Theme.DARK;
    return (
        <div className="flex justify-center w-full py-1 px-2">
            <div className="flex flex-col items-center">
                <label className="text-xs opacity-50">
                    {t("version", { version: config.app.version })}
                    {startedAt && uptime && (
                        <span className="ml-1" title={new Date(startedAt).toLocaleString()}>· ↑ {uptime}</span>
                    )}
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
