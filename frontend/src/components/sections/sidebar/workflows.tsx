import React, { FunctionComponent } from "react";
import { ExternalLink } from "lucide-react";
import { useTranslation } from "react-i18next";

interface WorkflowItem {
    id: string;
    name: string;
    description: string;
    type: "n8n" | "dify" | "flowise" | "custom";
    url: string;
}

// Configure your webhook URLs here or load from environment/config
const WORKFLOWS: WorkflowItem[] = [
    // Example — replace with your actual webhook URL:
    // {
    //     id: "example",
    //     name: "My Workflow",
    //     description: "Trigger via webhook.",
    //     type: "n8n",
    //     url: "http://localhost:5678/webhook/your-webhook-id",
    // },
];

const TYPE_LABELS: Record<WorkflowItem["type"], string> = {
    n8n: "n8n",
    dify: "dify",
    flowise: "Flowise",
    custom: "Custom",
};

export const WorkflowList: FunctionComponent = () => {
    const { t } = useTranslation("app");
    return (
        <div className="flex-1 overflow-y-auto px-3 py-3 space-y-2">
            {WORKFLOWS.map((wf) => (
                <a
                    key={wf.id}
                    href={wf.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex flex-col gap-1 px-3 py-2.5 rounded-md border border-border hover:bg-muted transition-colors no-underline"
                >
                    <div className="flex items-center justify-between gap-2">
                        <span className="text-sm font-semibold text-foreground">{wf.name}</span>
                        <div className="flex items-center gap-1.5 shrink-0">
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground font-mono">
                                {TYPE_LABELS[wf.type]}
                            </span>
                            <ExternalLink className="w-3 h-3 text-muted-foreground" />
                        </div>
                    </div>
                    <span className="text-xs text-muted-foreground">{wf.description}</span>
                </a>
            ))}
            {WORKFLOWS.length === 0 && (
                <p className="text-xs text-muted-foreground px-1">{t("workflows.empty")}</p>
            )}
        </div>
    );
};

interface Props {
    onClickBack: () => void;
}

export const Workflows: FunctionComponent<Props> = ({ onClickBack }) => {
    const { t } = useTranslation("app");
    return (
        <div className="text-foreground h-full flex flex-col">
            <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
                <button onClick={onClickBack} className="text-xs text-muted-foreground hover:text-foreground">
                    {t("help.back")}
                </button>
                <span className="font-bold text-sm">{t("workflows.title")}</span>
            </div>
            <WorkflowList />
        </div>
    );
};
