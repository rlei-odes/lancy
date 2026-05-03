import React, { FunctionComponent } from "react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Trans, useTranslation } from "react-i18next";
import { useDisclaimer } from "@/hooks/useDisclaimer";
import { config } from "@/config";
import { useBranding } from "@/hooks/useBranding";
import { cn } from "@/lib/lorem";
import { Theme, useTheme } from "@/hooks/useTheme";

export const DisclaimerDialog: FunctionComponent = () => {
    const { t } = useTranslation("app");
    const { theme } = useTheme();
    const isDarkMode = theme === Theme.DARK;

    const { isOpen, setDisclaimerIsOpen } = useDisclaimer();
    const { agentName } = useBranding();

    return (
        <Dialog open={isOpen} onOpenChange={setDisclaimerIsOpen}>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>{t("informationDialog.title")}</DialogTitle>
                </DialogHeader>
                <DialogDescription>
                    <Trans
                        i18nKey="informationDialog.goal"
                        values={{ name: agentName }}
                        components={{
                            1: <a className="font-bold" />,
                        }}
                    />
                    <br />
                    <br />
                    <Trans i18nKey="informationDialog.document" />
                    <br />
                    <br />
                    {t("informationDialog.languages")}
                    <br />
                    <br />
                    {t("informationDialog.license")}
                </DialogDescription>

                <div className="text-xs opacity-50 text-center">
                    <Trans
                        i18nKey="credits"
                        values={{ name: config.app.name }}
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
            </DialogContent>
        </Dialog>
    );
};
