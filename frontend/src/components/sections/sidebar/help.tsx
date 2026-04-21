import React, { FunctionComponent, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/lib/lorem";
import { useTranslation } from "react-i18next";

interface SectionProps {
    title: string;
    children: React.ReactNode;
    defaultOpen?: boolean;
}

const Section: FunctionComponent<SectionProps> = ({ title, children, defaultOpen = false }) => {
    const [open, setOpen] = useState(defaultOpen);
    return (
        <div className="border-b border-border last:border-0">
            <button
                className="flex w-full items-center justify-between px-4 py-2 text-xs font-semibold text-foreground hover:bg-muted/50 text-left"
                onClick={() => setOpen((v) => !v)}
            >
                {title}
                {open ? <ChevronDown className="w-3 h-3 shrink-0" /> : <ChevronRight className="w-3 h-3 shrink-0" />}
            </button>
            {open && <div className="px-4 pb-3 text-xs text-muted-foreground space-y-1">{children}</div>}
        </div>
    );
};

const Row: FunctionComponent<{ label: string; value: string }> = ({ label, value }) => (
    <div className="flex gap-1">
        <span className="font-medium shrink-0 text-foreground">{label}:</span>
        <span>{value}</span>
    </div>
);

interface Props {
    onClickBack: () => void;
}

export const Help: FunctionComponent<Props> = ({ onClickBack }) => {
    const { t } = useTranslation("app");
    return (
        <div className="text-foreground h-full flex flex-col">
            <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
                <button onClick={onClickBack} className="text-xs text-muted-foreground hover:text-foreground">{t("help.back")}</button>
                <span className="font-bold text-sm">{t("help.title")}</span>
            </div>
            <div className="flex-1 overflow-y-auto">

                <Section title={t("help.secRetrieval")} defaultOpen={true}>
                    <Row label="Top-K" value={t("help.topK")} />
                    <Row label="BM25" value={t("help.bm25")} />
                    <Row label="RRF k" value={t("help.rrfK")} />
                    <Row label="Query Expansion" value={t("help.queryExp")} />
                    <Row label="HyDE" value={t("help.hyde")} />
                    <Row label="Reranking" value={t("help.reranking")} />
                </Section>

                <Section title={t("help.secEmbedding")}>
                    <Row label="local" value={t("help.embLocal")} />
                    <Row label="nomic" value={t("help.embNomic")} />
                    <Row label="litellm" value={t("help.embOpenai")} />
                    <p className="mt-1 italic text-[11px]">{t("help.embNote")}</p>
                </Section>

                <Section title={t("help.secLlm")}>
                    <Row label="ollama" value={t("help.llmOllama")} />
                    <Row label="openai/litellm" value={t("help.llmOpenai")} />
                    <Row label="anthropic" value={t("help.llmAnthropic")} />
                </Section>

                <Section title={t("help.secKb")}>
                    <p>{t("help.kbIntro")}</p>
                    <Row label="+" value={t("help.kbCreate")} />
                    <Row label="Re-Index" value={t("help.reindexIncr")} />
                    <Row label="Reset + Re-Index" value={t("help.reindexFull")} />
                    <Row label="Formate" value={t("help.formats")} />
                    <p className="mt-1 italic text-[11px]">{t("help.kbExample")}</p>
                </Section>

            </div>
        </div>
    );
};
