import React, { FunctionComponent, useEffect, useState } from "react";
import {
    Bar,
    BarChart,
    CartesianGrid,
    ComposedChart,
    Legend,
    Line,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from "recharts";

interface DayEntry {
    day: string;
    conversations: number;
    messages: number;
}

interface ModelPerf {
    model: string;
    count: number;
    tps_min: number | null;
    tps_avg: number | null;
    tps_max: number | null;
    dur_min: number | null;
    dur_avg: number | null;
    dur_max: number | null;
}

async function fetchUsageStats(days: number): Promise<DayEntry[]> {
    const res = await fetch(`/api/admin/stats/usage?days=${days}`);
    if (!res.ok) throw new Error("Failed to load usage stats");
    const data = await res.json();
    return (data.days as string[]).map((d: string, i: number) => ({
        day: d,
        conversations: data.conversations[i],
        messages: data.messages[i],
    }));
}

async function fetchPerfStats(days: number): Promise<ModelPerf[]> {
    const res = await fetch(`/api/admin/stats/performance?days=${days}`);
    if (!res.ok) throw new Error("Failed to load performance stats");
    const data = await res.json();
    return data.models as ModelPerf[];
}

function formatDay(day: string): string {
    const d = new Date(day + "T00:00:00");
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function tickFormatter(day: string, index: number, data: DayEntry[]): string {
    if (data.length <= 30) return formatDay(day);
    const step = Math.ceil(data.length / 20);
    return index % step === 0 ? formatDay(day) : "";
}

function shortModel(name: string): string {
    return name.split("/").pop()?.split(":")[0] ?? name;
}

function ChartShell({ title, children }: { title: string; children: React.ReactNode }) {
    return (
        <div className="rounded-lg border border-border p-4 bg-muted/20">
            <p className="text-xs font-medium text-muted-foreground mb-3">{title}</p>
            {children}
        </div>
    );
}

function Empty({ msg = "No data for this period." }: { msg?: string }) {
    return <div className="h-48 flex items-center justify-center text-sm text-muted-foreground">{msg}</div>;
}

export const UsageAnalytics: FunctionComponent = () => {
    const [data, setData] = useState<DayEntry[]>([]);
    const [perf, setPerf] = useState<ModelPerf[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [days, setDays] = useState(180);

    useEffect(() => {
        setLoading(true);
        setError(null);
        Promise.all([fetchUsageStats(days), fetchPerfStats(days)])
            .then(([u, p]) => { setData(u); setPerf(p); })
            .catch((e) => setError(e.message))
            .finally(() => setLoading(false));
    }, [days]);

    const totalConvs = data.reduce((s, d) => s + d.conversations, 0);
    const totalMsgs = data.reduce((s, d) => s + d.messages, 0);

    // Flatten perf into chart-friendly rows keyed by short model name
    const tpsRows = perf.map((m) => ({
        model: shortModel(m.model),
        Min: m.tps_min,
        Avg: m.tps_avg,
        Max: m.tps_max,
        count: m.count,
        full: m.model,
    }));
    const durRows = perf.map((m) => ({
        model: shortModel(m.model),
        Min: m.dur_min,
        Avg: m.dur_avg,
        Max: m.dur_max,
        full: m.model,
    }));

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold">Usage Analytics</h2>
                <select
                    value={days}
                    onChange={(e) => setDays(Number(e.target.value))}
                    className="text-xs [font-family:inherit] bg-muted border border-border text-foreground rounded px-2 py-1 focus:outline-none focus:border-blue-400 transition-colors"
                >
                    <option value={30}>Last 30 days</option>
                    <option value={90}>Last 90 days</option>
                    <option value={180}>Last 180 days</option>
                    <option value={365}>Last 365 days</option>
                </select>
            </div>

            {/* Summary cards */}
            <div className="grid grid-cols-2 gap-3">
                {[
                    { label: "Conversations", value: totalConvs },
                    { label: "User Messages", value: totalMsgs },
                ].map(({ label, value }) => (
                    <div key={label} className="rounded-lg border border-border p-4 bg-muted/20">
                        <p className="text-xs text-muted-foreground">{label}</p>
                        <p className="text-2xl font-semibold mt-1">{value.toLocaleString()}</p>
                        <p className="text-xs text-muted-foreground mt-0.5">last {days} days</p>
                    </div>
                ))}
            </div>

            {loading && <div className="h-64 flex items-center justify-center text-sm text-muted-foreground">Loading…</div>}
            {error && <div className="h-64 flex items-center justify-center text-sm text-destructive">{error}</div>}

            {!loading && !error && (
                <>
                    {/* Chart 1 — conversations & user messages over time */}
                    <ChartShell title="Conversations &amp; User Messages">
                        {data.length === 0 ? <Empty /> : (
                            <ResponsiveContainer width="100%" height={260}>
                                <ComposedChart data={data} margin={{ top: 4, right: 12, left: 0, bottom: 4 }}>
                                    <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                                    <XAxis dataKey="day" tickFormatter={(val, idx) => tickFormatter(val, idx, data)} tick={{ fontSize: 11 }} />
                                    <YAxis yAxisId="left" tick={{ fontSize: 11 }} />
                                    <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} />
                                    <Tooltip labelFormatter={(l) => formatDay(l as string)} contentStyle={{ fontSize: 12 }} />
                                    <Legend wrapperStyle={{ fontSize: 12 }} />
                                    <Bar yAxisId="left" dataKey="conversations" name="Conversations" fill="hsl(var(--primary))" opacity={0.7} radius={[2, 2, 0, 0]} />
                                    <Line yAxisId="right" type="monotone" dataKey="messages" name="User Messages" stroke="hsl(var(--chart-2, 200 80% 50%))" strokeWidth={2} dot={false} />
                                </ComposedChart>
                            </ResponsiveContainer>
                        )}
                    </ChartShell>

                    {/* Chart 2 — tokens/second by model */}
                    <ChartShell title="Tokens / second by model (min / avg / max)">
                        {tpsRows.length === 0 || tpsRows.every((r) => r.Avg === null) ? <Empty msg="No token speed data yet." /> : (
                            <ResponsiveContainer width="100%" height={220}>
                                <BarChart data={tpsRows} margin={{ top: 4, right: 12, left: 0, bottom: 4 }}>
                                    <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                                    <XAxis dataKey="model" tick={{ fontSize: 11 }} />
                                    <YAxis tick={{ fontSize: 11 }} unit=" t/s" />
                                    <Tooltip
                                        contentStyle={{ fontSize: 12 }}
                                        formatter={(val, name) => [`${val} t/s`, name]}
                                        labelFormatter={(l, items) => {
                                            const full = items?.[0]?.payload?.full ?? l;
                                            const count = items?.[0]?.payload?.count ?? "";
                                            return `${full}${count ? ` (${count} responses)` : ""}`;
                                        }}
                                    />
                                    <Legend wrapperStyle={{ fontSize: 12 }} />
                                    <Bar dataKey="Min" name="Min" fill="#6b7280" opacity={0.7} radius={[2, 2, 0, 0]} />
                                    <Bar dataKey="Avg" name="Avg" fill="hsl(var(--primary))" opacity={0.85} radius={[2, 2, 0, 0]} />
                                    <Bar dataKey="Max" name="Max" fill="#22c55e" opacity={0.7} radius={[2, 2, 0, 0]} />
                                </BarChart>
                            </ResponsiveContainer>
                        )}
                    </ChartShell>

                    {/* Chart 3 — response duration by model */}
                    <ChartShell title="Response duration by model in seconds (min / avg / max)">
                        {durRows.length === 0 || durRows.every((r) => r.Avg === null) ? <Empty msg="No duration data yet." /> : (
                            <ResponsiveContainer width="100%" height={220}>
                                <BarChart data={durRows} margin={{ top: 4, right: 12, left: 0, bottom: 4 }}>
                                    <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                                    <XAxis dataKey="model" tick={{ fontSize: 11 }} />
                                    <YAxis tick={{ fontSize: 11 }} unit=" s" />
                                    <Tooltip
                                        contentStyle={{ fontSize: 12 }}
                                        formatter={(val, name) => [`${val}s`, name]}
                                        labelFormatter={(l, items) => items?.[0]?.payload?.full ?? l}
                                    />
                                    <Legend wrapperStyle={{ fontSize: 12 }} />
                                    <Bar dataKey="Min" name="Min" fill="#6b7280" opacity={0.7} radius={[2, 2, 0, 0]} />
                                    <Bar dataKey="Avg" name="Avg" fill="hsl(var(--primary))" opacity={0.85} radius={[2, 2, 0, 0]} />
                                    <Bar dataKey="Max" name="Max" fill="#22c55e" opacity={0.7} radius={[2, 2, 0, 0]} />
                                </BarChart>
                            </ResponsiveContainer>
                        )}
                    </ChartShell>
                </>
            )}
        </div>
    );
};
