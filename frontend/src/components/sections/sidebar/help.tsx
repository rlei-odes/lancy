import React, { FunctionComponent, useState } from "react";
import { ChevronDown, ChevronRight, FlaskConical, Lightbulb, SlidersHorizontal, Target } from "lucide-react";
import { useTranslation } from "react-i18next";

interface SectionProps {
    title: string;
    icon: React.ReactNode;
    children: React.ReactNode;
    defaultOpen?: boolean;
}

const Section: FunctionComponent<SectionProps> = ({ title, icon, children, defaultOpen = false }) => {
    const [open, setOpen] = useState(defaultOpen);
    return (
        <div className="border-b border-border last:border-0">
            <button
                className="flex w-full items-center gap-2 px-4 py-2 text-xs font-semibold text-foreground hover:bg-muted/50 text-left"
                onClick={() => setOpen((v) => !v)}
            >
                <span className="text-muted-foreground shrink-0">{icon}</span>
                <span className="flex-1">{title}</span>
                {open ? <ChevronDown className="w-3 h-3 shrink-0" /> : <ChevronRight className="w-3 h-3 shrink-0" />}
            </button>
            {open && <div className="px-4 pb-3 text-xs text-muted-foreground leading-relaxed space-y-2.5">{children}</div>}
        </div>
    );
};

/** A named thing the user can find in the UI, and what it does for them. */
const Item: FunctionComponent<{ name: string; children: React.ReactNode }> = ({ name, children }) => (
    <div>
        <div className="font-medium text-foreground">{name}</div>
        <p>{children}</p>
    </div>
);

const iconCls = "w-3.5 h-3.5";

interface Props {
    onClickBack: () => void;
}

/** User-facing reference: how answers come about and what the settings a user can
 *  change actually do. Names are read from the keys the UI itself uses, so the
 *  reference cannot drift from the labels it explains. Administration is covered
 *  by the docs and manuals, not here. */
export const Help: FunctionComponent<Props> = ({ onClickBack }) => {
    const { t } = useTranslation("app");
    return (
        <div className="text-foreground h-full flex flex-col">
            <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
                <button onClick={onClickBack} className="text-xs text-muted-foreground hover:text-foreground">{t("help.back")}</button>
                <span className="font-bold text-sm">{t("help.title")}</span>
            </div>
            <div className="flex-1 overflow-y-auto">

                <Section title={t("help.secBasics")} icon={<Lightbulb className={iconCls} />} defaultOpen={true}>
                    <p>{t("help.basicsPassages")}</p>
                    <p>{t("help.basicsEmbedding")}</p>
                    <p>{t("help.basicsLimits")}</p>
                </Section>

                <Section title={t("help.secSettings")} icon={<SlidersHorizontal className={iconCls} />}>
                    <p>{t("help.settingsIntro")}</p>
                    <Item name={t("rag.fieldTopK")}>{t("help.topK")}</Item>
                    <Item name={t("rag.fieldBm25")}>{t("help.bm25")}</Item>
                    <Item name={t("rag.fieldRrfK")}>{t("help.rrfK")}</Item>
                    <Item name={t("rag.fieldQueryExp")}>{t("help.queryExp")}</Item>
                    <Item name={t("rag.fieldHyde")}>{t("help.hyde")}</Item>
                    <Item name={t("rag.fieldReranking")}>{t("help.reranking", { pool: t("rag.fieldRerankPool") })}</Item>
                </Section>

                <Section title={t("help.secTips")} icon={<Target className={iconCls} />}>
                    <Item name={t("help.tipSpecificName")}>{t("help.tipSpecific")}</Item>
                    <Item name={t("chatFilters.add")}>{t("help.tipFilter")}</Item>
                    <Item name={t("next.askWithSelected")}>{t("help.tipExpand")}</Item>
                    <Item name={t("chatOnly.label")}>{t("help.tipChatOnly")}</Item>
                    <Item name={t("help.tipSourcesName")}>{t("help.tipSources")}</Item>
                </Section>

                <Section title={t("help.secExplorer")} icon={<FlaskConical className={iconCls} />}>
                    <p>{t("help.explorerIntro", { page: t("explorer.pageTitle") })}</p>
                    <Item name={t("explorer.tabProbe")}>{t("help.explorerProbe")}</Item>
                    <Item name={t("explorer.tabBrowser")}>{t("help.explorerBrowser")}</Item>
                    <Item name={t("explorer.tabAnalytics")}>{t("help.explorerAnalytics")}</Item>
                    <Item name={t("explorer.tabPiles")}>{t("help.explorerPiles")}</Item>
                    <Item name={t("explorer.tabBatch")}>{t("help.explorerBatch")}</Item>
                </Section>

            </div>
        </div>
    );
};
