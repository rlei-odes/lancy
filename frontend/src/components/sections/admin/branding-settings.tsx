import React, { FunctionComponent, useRef, useState } from "react";
import { Loader2, Upload, X } from "lucide-react";
import { useBranding } from "@/hooks/useBranding";
import { config } from "@/config";

export const BrandingSettings: FunctionComponent = () => {
    const { agentName, agentAvatarUrl, reload } = useBranding();
    const [name, setName] = useState(agentName);
    const [previewUrl, setPreviewUrl] = useState<string | null>(null);
    const [file, setFile] = useState<File | null>(null);
    const [saving, setSaving] = useState(false);
    const [result, setResult] = useState<{ ok: boolean; msg: string } | null>(null);
    const fileRef = useRef<HTMLInputElement>(null);

    const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const f = e.target.files?.[0];
        if (!f) return;
        setFile(f);
        setPreviewUrl(URL.createObjectURL(f));
    };

    const handleRemoveAvatar = async () => {
        setSaving(true);
        setResult(null);
        try {
            const res = await fetch("/api/v1/branding/avatar", { method: "DELETE" });
            if (!res.ok) throw new Error("Failed to remove avatar");
            setPreviewUrl(null);
            setFile(null);
            reload();
            setResult({ ok: true, msg: "Avatar removed." });
        } catch (e: unknown) {
            setResult({ ok: false, msg: e instanceof Error ? e.message : "Failed to remove avatar." });
        } finally {
            setSaving(false);
        }
    };

    const handleSave = async () => {
        setSaving(true);
        setResult(null);
        try {
            const form = new FormData();
            form.append("agent_name", name.trim() || agentName);
            if (file) form.append("avatar", file);

            const res = await fetch("/api/v1/branding", { method: "PUT", body: form });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || "Save failed");
            }
            reload();
            setFile(null);
            setResult({ ok: true, msg: "Branding saved." });
        } catch (e: unknown) {
            setResult({ ok: false, msg: e instanceof Error ? e.message : "Save failed." });
        } finally {
            setSaving(false);
        }
    };

    const currentAvatar = previewUrl ?? agentAvatarUrl;

    return (
        <div className="space-y-6 max-w-lg">
            <h2 className="text-sm font-semibold">Branding</h2>

            {/* Agent name */}
            <div className="rounded-lg border border-border p-4 bg-muted/20 space-y-3">
                <h3 className="text-sm font-medium">Agent Name</h3>
                <p className="text-xs text-muted-foreground">
                    The name shown in the chat UI next to agent responses.
                </p>
                <input
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    maxLength={80}
                    placeholder={config.agent.name}
                    className="w-full text-sm border border-border rounded-md px-3 py-2 bg-background text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                />
            </div>

            {/* Agent avatar */}
            <div className="rounded-lg border border-border p-4 bg-muted/20 space-y-3">
                <h3 className="text-sm font-medium">Agent Avatar</h3>
                <p className="text-xs text-muted-foreground">
                    PNG, JPEG, WebP, or SVG — max 2 MB. Displayed next to agent messages and in the sidebar header.
                </p>

                {/* Preview */}
                <div className="flex items-center gap-4">
                    <div className="h-16 w-16 rounded-full border border-border bg-background flex items-center justify-center overflow-hidden shrink-0">
                        {currentAvatar ? (
                            <img src={currentAvatar} alt="Avatar preview" className="h-full w-full object-cover" />
                        ) : (
                            <span className="text-xs text-muted-foreground">None</span>
                        )}
                    </div>
                    <div className="flex flex-col gap-2">
                        <button
                            onClick={() => fileRef.current?.click()}
                            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md border border-border hover:bg-muted transition-colors"
                        >
                            <Upload className="h-3 w-3" /> Upload image
                        </button>
                        {(agentAvatarUrl !== config.app.logo || previewUrl) && (
                            <button
                                onClick={handleRemoveAvatar}
                                disabled={saving}
                                className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md border border-border text-destructive hover:bg-destructive/10 transition-colors"
                            >
                                <X className="h-3 w-3" /> Remove
                            </button>
                        )}
                    </div>
                </div>
                <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml" className="hidden" onChange={handleFileChange} />
                {file && <p className="text-xs text-muted-foreground">Selected: {file.name}</p>}
            </div>

            {/* Save */}
            <div className="flex items-center gap-3">
                <button
                    onClick={handleSave}
                    disabled={saving}
                    className="flex items-center gap-1.5 px-4 py-2 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
                >
                    {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                    Save
                </button>
                {result && (
                    <p className={`text-xs ${result.ok ? "text-green-600 dark:text-green-400" : "text-destructive"}`}>
                        {result.msg}
                    </p>
                )}
            </div>

            {/* Future scope note */}
            <div className="rounded-lg border border-border/50 p-4 bg-muted/10">
                <p className="text-xs text-muted-foreground font-medium mb-1">Coming later</p>
                <ul className="text-xs text-muted-foreground space-y-0.5 list-disc list-inside">
                    <li>Application name (browser tab title)</li>
                    <li>Favicon</li>
                </ul>
            </div>
        </div>
    );
};
