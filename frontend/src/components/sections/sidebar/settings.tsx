import React, { FunctionComponent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { DisplayLanguages, Languages } from "@/lib/lang/i18n";
import { Theme, useTheme } from "@/hooks/useTheme";
import { ArrowLeft, Sun, Moon, ShieldCheck, LogIn } from "lucide-react";
import { Footer } from "@/components/sections/sidebar/footer";
import { useDisclaimer } from "@/hooks/useDisclaimer";
import { useRole } from "@/hooks/useRole";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { useRouter } from "next/router";

interface Props {
    onClickBack: () => void;
}

function RoleSeparationSection() {
    const { role } = useRole();
    const router = useRouter();

    const [mode, setMode] = useState<"1" | "2" | null>(null);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [adminPw, setAdminPw] = useState("");
    const [adminPwConfirm, setAdminPwConfirm] = useState("");
    const [error, setError] = useState("");
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        fetch("/api/auth/admin-config")
            .then((r) => r.json())
            .then((d) => setMode(d.mode ?? "1"))
            .catch(() => setMode("1"));
    }, []);

    async function activate() {
        setError("");
        if (adminPw.length < 8) {
            setError("Password must be at least 8 characters.");
            return;
        }
        if (adminPw !== adminPwConfirm) {
            setError("Passwords do not match.");
            return;
        }
        setSaving(true);
        try {
            const res = await fetch("/api/auth/admin-config", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ admin_password: adminPw }),
            });
            const data = await res.json();
            if (!res.ok) {
                setError(data.error ?? "Failed to save.");
                return;
            }
            setMode("2");
            setDialogOpen(false);
            setAdminPw("");
            setAdminPwConfirm("");
        } catch {
            setError("Connection error.");
        } finally {
            setSaving(false);
        }
    }

    if (mode === null) return null;

    return (
        <div className="pb-10">
            <div className="flex flex-row items-center gap-2 pb-2">
                <ShieldCheck className="h-4 w-4 text-muted-foreground" />
                <span className="text-sm font-medium">Role Separation</span>
            </div>

            {mode === "1" && (
                <div>
                    <p className="text-xs text-muted-foreground pb-3">
                        All logged-in users are currently admins. Set an admin password to restrict
                        configuration access to admins only.
                    </p>
                    <button
                        onClick={() => setDialogOpen(true)}
                        className="text-xs px-3 py-1.5 rounded-md bg-muted hover:bg-muted/80 transition-colors"
                    >
                        Set admin password…
                    </button>
                </div>
            )}

            {mode === "2" && role === "user" && (
                <div>
                    <p className="text-xs text-muted-foreground pb-3">
                        Role separation is active. You are logged in as a regular user.
                    </p>
                    <button
                        onClick={() => router.push("/login?redirect=" + encodeURIComponent(router.asPath))}
                        className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-md bg-muted hover:bg-muted/80 transition-colors"
                    >
                        <LogIn className="h-3.5 w-3.5" />
                        Admin Login
                    </button>
                </div>
            )}

            {mode === "2" && role === "admin" && (
                <p className="text-xs text-muted-foreground">
                    Role separation is active. You are logged in as admin.
                </p>
            )}

            <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Enable Role Separation</DialogTitle>
                    </DialogHeader>
                    <div className="text-sm text-muted-foreground space-y-3 py-2">
                        <p>
                            Once activated, users who log in with the regular password will see a
                            read-only view. Only users who log in with the admin password can access
                            configuration, reindexing, and preset management.
                        </p>
                        <p>Keep the admin password safe — there is no recovery mechanism.</p>
                    </div>
                    <div className="space-y-3 py-2">
                        <div>
                            <label className="text-xs text-muted-foreground block mb-1">Admin password</label>
                            <input
                                type="password"
                                value={adminPw}
                                onChange={(e) => setAdminPw(e.target.value)}
                                className="w-full rounded-md bg-muted border border-border text-foreground px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                                placeholder="min. 8 characters"
                            />
                        </div>
                        <div>
                            <label className="text-xs text-muted-foreground block mb-1">Confirm password</label>
                            <input
                                type="password"
                                value={adminPwConfirm}
                                onChange={(e) => setAdminPwConfirm(e.target.value)}
                                className="w-full rounded-md bg-muted border border-border text-foreground px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                                placeholder="repeat password"
                            />
                        </div>
                        {error && <p className="text-xs text-red-500">{error}</p>}
                    </div>
                    <DialogFooter>
                        <button
                            onClick={() => { setDialogOpen(false); setAdminPw(""); setAdminPwConfirm(""); setError(""); }}
                            className="text-sm px-4 py-2 rounded-md hover:bg-muted transition-colors"
                        >
                            Cancel
                        </button>
                        <button
                            onClick={activate}
                            disabled={saving}
                            className="text-sm px-4 py-2 rounded-md bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white transition-colors"
                        >
                            {saving ? "Saving…" : "Activate"}
                        </button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}

export const Settings: FunctionComponent<Props> = (props: Props) => {
    const { onClickBack } = props;
    const { t, i18n } = useTranslation("app");
    const { theme, changeTheme } = useTheme();
    const { setDisclaimerIsOpen } = useDisclaimer();
    const isDarkMode = theme === Theme.DARK;

    const currentLanguage = i18n.language;

    return (
        <div className="text-foreground flex flex-col justify-between h-full">
            <div className="px-6 pt-6">
                <div className="flex flex-row items-center">
                    <ArrowLeft onClick={onClickBack} className="cursor-pointer hover:opacity-70" />
                    <div className="pl-4 text-2xl font-bold">{t("settings")}</div>
                </div>
                <div className="flex flex-col pt-10">
                    <div className="flex flex-row align-middle items-center pb-10">
                        <label className="text-sm pr-2">{`${t("language")}:`}</label>
                        <Select value={currentLanguage} onValueChange={(language) => i18n.changeLanguage(language)}>
                            <SelectTrigger className="w-[180px] transition-all duration-300">
                                <SelectValue defaultValue={"en"} />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectGroup>
                                    {Object.values(Languages).map((language) => (
                                        <SelectItem key={language} value={language}>
                                            {DisplayLanguages[language]}
                                        </SelectItem>
                                    ))}
                                </SelectGroup>
                            </SelectContent>
                        </Select>
                    </div>
                    <div className="flex flex-row align-middle items-center pb-10">
                        <label className="text-sm pr-2">{`${t("darkMode")}:`}</label>
                        <button
                            onClick={() => changeTheme(isDarkMode ? Theme.LIGHT : Theme.DARK)}
                            className="p-1.5 rounded-md hover:bg-muted transition-colors"
                            title={isDarkMode ? t("switchToLight") : t("switchToDark")}
                        >
                            {isDarkMode ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
                        </button>
                    </div>
                    <div className="flex flex-row align-middle items-center pb-10">
                        <label className="text-sm pr-2 hover:cursor-pointer underline text-blue-500" onClick={() => setDisclaimerIsOpen(true)}>{`${t(
                            "about",
                        )}`}</label>
                    </div>
                    <RoleSeparationSection />
                </div>
            </div>
            <Footer />
        </div>
    );
};
