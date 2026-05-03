import React, { FunctionComponent } from "react";
import { useTranslation } from "react-i18next";
import { useBranding } from "@/hooks/useBranding";

export const Disclaimer: FunctionComponent = () => {
    const { t } = useTranslation("app");
    const { agentName } = useBranding();

    return <label className="text-center text-xs opacity-50 text-foreground pt-2">{t("disclaimer", { name: agentName })}</label>;
};
