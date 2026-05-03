import React, { FunctionComponent, useEffect, useState } from "react";
import {
    Bar,
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

function formatDay(day: string): string {
    const d = new Date(day + "T00:00:00");
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

// Thin out labels so the axis isn't crowded
function tickFormatter(day: string, index: number, data: DayEntry[]): string {
    if (data.length <= 30) return formatDay(day);
    const step = Math.ceil(data.length / 20);
    return index % step === 0 ? formatDay(day) : "";
}

export const UsageAnalytics: FunctionComponent = () => {
    const [data, setData] = useState<DayEntry[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [days, setDays] = useState(180);

    useEffect(() => {
        setLoading(true);
        setError(null);
        fetchUsageStats(days)
            .then(setData)
            .catch((e) => setError(e.message))
            .finally(() => setLoading(false));
    }, [days]);

    const totalConvs = data.reduce((s, d) => s + d.conversations, 0);
    const totalMsgs = data.reduce((s, d) => s + d.messages, 0);

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold">Conversations &amp; Messages</h2>
                <select
                    value={days}
                    onChange={(e) => setDays(Number(e.target.value))}
                    className="text-xs border border-border rounded px-2 py-1 bg-background text-foreground"
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
                    { label: "Messages", value: totalMsgs },
                ].map(({ label, value }) => (
                    <div key={label} className="rounded-lg border border-border p-4 bg-muted/20">
                        <p className="text-xs text-muted-foreground">{label}</p>
                        <p className="text-2xl font-semibold mt-1">{value.toLocaleString()}</p>
                        <p className="text-xs text-muted-foreground mt-0.5">last {days} days</p>
                    </div>
                ))}
            </div>

            {/* Chart */}
            <div className="rounded-lg border border-border p-4 bg-muted/20">
                {loading && (
                    <div className="h-64 flex items-center justify-center text-sm text-muted-foreground">
                        Loading…
                    </div>
                )}
                {error && (
                    <div className="h-64 flex items-center justify-center text-sm text-destructive">
                        {error}
                    </div>
                )}
                {!loading && !error && data.length === 0 && (
                    <div className="h-64 flex items-center justify-center text-sm text-muted-foreground">
                        No data for this period.
                    </div>
                )}
                {!loading && !error && data.length > 0 && (
                    <ResponsiveContainer width="100%" height={280}>
                        <ComposedChart data={data} margin={{ top: 4, right: 12, left: 0, bottom: 4 }}>
                            <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                            <XAxis
                                dataKey="day"
                                tickFormatter={(val, idx) => tickFormatter(val, idx, data)}
                                tick={{ fontSize: 11 }}
                                className="fill-muted-foreground"
                            />
                            <YAxis yAxisId="left" tick={{ fontSize: 11 }} className="fill-muted-foreground" />
                            <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} className="fill-muted-foreground" />
                            <Tooltip
                                labelFormatter={(l) => formatDay(l as string)}
                                contentStyle={{ fontSize: 12 }}
                            />
                            <Legend wrapperStyle={{ fontSize: 12 }} />
                            <Bar yAxisId="left" dataKey="conversations" name="Conversations" fill="hsl(var(--primary))" opacity={0.7} radius={[2, 2, 0, 0]} />
                            <Line yAxisId="right" type="monotone" dataKey="messages" name="Messages" stroke="hsl(var(--chart-2, 200 80% 50%))" strokeWidth={2} dot={false} />
                        </ComposedChart>
                    </ResponsiveContainer>
                )}
            </div>
        </div>
    );
};
