import React, { FunctionComponent, useState } from "react";
import { MoreHorizontal, Pencil, Trash } from "lucide-react";
import { cn } from "@/lib/lorem";
import { DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuTrigger } from "./dropdown-menu";
import { useTranslation } from "react-i18next";
import { RenameDialog } from "@/components/sections/dialogs/rename-dialog";
import { DeleteDialog } from "@/components/sections/dialogs/delete-dialog";
import { RagConfigSnapshot } from "@/services/conversation";

interface Props {
    conversationId: string;
    label: string;
    isSelected: boolean;
    onClick: () => void;
    className?: string;
    kbName?: string;
    ragConfigSnapshot?: RagConfigSnapshot;
}

export const SidebarButton: FunctionComponent<Props> = (props: Props) => {
    const { className, label, onClick, isSelected, conversationId, kbName, ragConfigSnapshot } = props;
    const [isHover, setIsHover] = useState(false);
    const [isDropdownOpen, setIsDropdownOpen] = useState(false);
    const [isRenameDialogOpen, setIsRenameDialogOpen] = useState(false);
    const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);

    const { t } = useTranslation("app");

    const isActive = isHover || isSelected || isDropdownOpen;

    const handleClickDelete = (e: React.MouseEvent<HTMLDivElement, MouseEvent>) => {
        setIsDeleteDialogOpen(true);
    };
    const handleClickRename = (e: React.MouseEvent<HTMLDivElement, MouseEvent>) => {
        setIsRenameDialogOpen(true);
    };

    const badges: string[] = [];
    if (ragConfigSnapshot) {
        if (ragConfigSnapshot.llm_model) badges.push(ragConfigSnapshot.llm_model.split("/").pop()!.split(":")[0]);
        if (ragConfigSnapshot.llm_temperature != null) badges.push(`T=${ragConfigSnapshot.llm_temperature}`);
        if (ragConfigSnapshot.embedding_model) badges.push(`emb:${ragConfigSnapshot.embedding_model.split("/").pop()!}`);
        if (ragConfigSnapshot.retriever_top_k !== undefined) badges.push(`k=${ragConfigSnapshot.retriever_top_k}`);
        if (ragConfigSnapshot.bm25_enabled) badges.push("BM25");
        if (ragConfigSnapshot.reranking_enabled) badges.push("Rerank");
        if (ragConfigSnapshot.hyde_enabled) badges.push("HyDE");
        if (ragConfigSnapshot.query_expansion) badges.push(`QExp×${ragConfigSnapshot.query_expansion}`);
    }

    const tooltip = [
        kbName,
        ...(ragConfigSnapshot ? [
            `k=${ragConfigSnapshot.retriever_top_k}`,
            ragConfigSnapshot.bm25_enabled ? "BM25" : null,
            ragConfigSnapshot.reranking_enabled ? `Rerank(${ragConfigSnapshot.reranking_candidate_pool ?? 15})` : null,
            ragConfigSnapshot.hyde_enabled ? "HyDE" : null,
            ragConfigSnapshot.query_expansion ? `QExp×${ragConfigSnapshot.query_expansion}` : null,
            ragConfigSnapshot.llm_model ? `LLM: ${ragConfigSnapshot.llm_model}` : null,
            ragConfigSnapshot.llm_temperature != null ? `T=${ragConfigSnapshot.llm_temperature}` : null,
            ragConfigSnapshot.utility_llm_model ? `Util: ${ragConfigSnapshot.utility_llm_model}` : null,
            ragConfigSnapshot.embedding_backend ? `Emb: ${ragConfigSnapshot.embedding_backend}` : null,
            ragConfigSnapshot.embedding_model ? `  → ${ragConfigSnapshot.embedding_model}` : null,
            ragConfigSnapshot.vs_type ? `VS: ${ragConfigSnapshot.vs_type}` : null,
        ].filter(Boolean) : []),
    ].filter(Boolean).join("\n");

    return (
        <div>
            <div
                className={cn("flex flex-col py-1.5 px-4 mx-2 cursor-pointer rounded-md", className, isSelected ? "bg-foreground/15" : isActive ? "bg-foreground/5" : "")}
                onClick={() => {
                    if (!isDropdownOpen) {
                        onClick();
                    }
                }}
                onMouseEnter={() => setIsHover(true)}
                onMouseLeave={() => setIsHover(false)}
            >
                <div className="flex flex-row justify-between w-full items-center">
                    <div className="flex text-foreground text-sm text-ellipsis text-nowrap whitespace-nowrap overflow-hidden w-full">{label}</div>
                    <DropdownMenu onOpenChange={setIsDropdownOpen}>
                        <DropdownMenuTrigger asChild>{isActive && <MoreHorizontal className="h-4 w-7 shrink-0 rounded-md hover:opacity-55" />}</DropdownMenuTrigger>
                        <DropdownMenuContent className="w-48" side="bottom" align="start">
                            <DropdownMenuGroup>
                                <DropdownMenuItem onClick={handleClickRename} className="py-3">
                                    <Pencil className="h-4 w-4 mr-2" />
                                    <span>{t("rename")}</span>
                                </DropdownMenuItem>
                                <DropdownMenuItem onClick={handleClickDelete} className="py-3">
                                    <Trash className="h-4 w-4 mr-2" />
                                    <span>{t("delete")}</span>
                                </DropdownMenuItem>
                            </DropdownMenuGroup>
                        </DropdownMenuContent>
                    </DropdownMenu>
                </div>
                {(kbName || badges.length > 0) && (
                    <div className="flex flex-row flex-wrap gap-x-1 gap-y-0.5 mt-0.5" title={tooltip}>
                        {kbName && (
                            <span className="text-[10px] leading-tight px-1 py-px rounded bg-blue-700/70 text-blue-100">
                                {kbName}
                            </span>
                        )}
                        {badges.map((b) => (
                            <span key={b} className="text-[10px] leading-tight px-1 py-px rounded bg-zinc-600/80 text-zinc-200">
                                {b}
                            </span>
                        ))}
                    </div>
                )}
            </div>
            <RenameDialog isRenameDialogOpen={isRenameDialogOpen} setIsRenameDialogOpen={setIsRenameDialogOpen} conversationId={conversationId} />
            <DeleteDialog isDeleteDialogOpen={isDeleteDialogOpen} setIsDeleteDialogOpen={setIsDeleteDialogOpen} conversationId={conversationId} />
        </div>
    );
};
