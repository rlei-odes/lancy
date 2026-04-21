import React, { FunctionComponent, useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, WifiOff } from "lucide-react";

const API_BASE = typeof window !== "undefined" ? "" : (process.env.SERVER_URL ?? "");
const POLL_INTERVAL_OK = 8000;
const POLL_INTERVAL_DOWN = 3000;

type Status = "ok" | "down" | "init";

export const BackendStatus: FunctionComponent = () => {
    const [status, setStatus] = useState<Status>("init");
    const [downSince, setDownSince] = useState<Date | null>(null);
    const [recovered, setRecovered] = useState(false);
    const recoveredTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

    useEffect(() => {
        let active = true;
        let timer: ReturnType<typeof setTimeout>;

        const poll = async () => {
            try {
                const r = await fetch(`${API_BASE}/api/v1/rag/reindex-status`, {
                    credentials: "include",
                    signal: AbortSignal.timeout(4000),
                });
                if (!active) return;
                if (r.ok) {
                    setStatus((prev) => {
                        if (prev === "down") {
                            setRecovered(true);
                            if (recoveredTimer.current) clearTimeout(recoveredTimer.current);
                            recoveredTimer.current = setTimeout(() => setRecovered(false), 5000);
                        }
                        return "ok";
                    });
                    setDownSince(null);
                    timer = setTimeout(poll, POLL_INTERVAL_OK);
                } else {
                    throw new Error("non-ok");
                }
            } catch {
                if (!active) return;
                setStatus((prev) => {
                    if (prev !== "down") setDownSince(new Date());
                    return "down";
                });
                timer = setTimeout(poll, POLL_INTERVAL_DOWN);
            }
        };

        poll();
        return () => {
            active = false;
            clearTimeout(timer);
            if (recoveredTimer.current) clearTimeout(recoveredTimer.current);
        };
    }, []);

    if (status === "init") return null;

    if (status === "down") {
        const since = downSince
            ? downSince.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
            : "";
        return (
            <div className="fixed bottom-4 left-4 z-[100] flex items-start gap-2 px-3 py-2.5 rounded-lg bg-red-950 border border-red-700 text-red-200 shadow-xl max-w-xs text-xs animate-pulse-slow">
                <WifiOff className="h-4 w-4 shrink-0 mt-0.5 text-red-400" />
                <div>
                    <div className="font-semibold text-red-300">Backend nicht erreichbar</div>
                    {since && <div className="opacity-70 mt-0.5">seit {since} · wird neu verbunden…</div>}
                </div>
            </div>
        );
    }

    if (recovered) {
        return (
            <div className="fixed bottom-4 left-4 z-[100] flex items-center gap-2 px-3 py-2 rounded-lg bg-green-950 border border-green-700 text-green-200 shadow-xl text-xs">
                <CheckCircle2 className="h-4 w-4 shrink-0 text-green-400" />
                <span className="font-medium">Backend wieder erreichbar</span>
            </div>
        );
    }

    return null;
};
