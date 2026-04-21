import React, { FunctionComponent, useState } from "react";
import { Source } from "@/services/message";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import { QuoteItem } from "@/components/ui/quote-item";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Markdown } from "@/components/ui/markdown";
import { ExternalLink } from "lucide-react";

const DOC_EXT = /\.(pdf|xlsx|xls|docx|doc|md|txt|csv)$/i;

interface SourceItemProps {
    source: Source;
    index: number;
}

export const SourceItem: FunctionComponent<SourceItemProps> = (props: SourceItemProps) => {
    const { source, index } = props;
    const [isSourceOpen, setIsSourceOpen] = useState(false);
    const { isMobile } = useMediaQuery();

    const { metadata, content } = source;

    const sourceFile = metadata?.source_file as string | undefined;
    const origin = metadata?.url || metadata?.origin || metadata.source;
    const sectionTitle = (metadata?.title || metadata?.heading || "") as string;
    const filename = sourceFile || (origin && DOC_EXT.test(String(origin)) ? String(origin) : null);
    const mimeType = metadata.mime_type || "";

    const fileUrl = sourceFile
        ? `/api/v1/files/${encodeURIComponent(sourceFile)}`
        : origin && DOC_EXT.test(String(origin)) && !String(origin).startsWith("http")
            ? `/api/v1/files/${encodeURIComponent(String(origin))}`
            : null;

    const renderContent = () => {
        if (mimeType === "image/png") {
            return (
                <div className="flex flex-col py-3">
                    <img src={`data:image/png;base64,${content}`} alt={filename || sectionTitle || ""} className="max-w-full h-auto" />
                    {!!origin && (
                        <div className="pt-1 flex flex-row justify-end italic">
                            <Markdown content={`- (${origin})`} />
                        </div>
                    )}
                </div>
            );
        }

        if (mimeType === "text/markdown") {
            return <QuoteItem content={content} origin={origin} />;
        }

        return <QuoteItem content={content} origin={origin} />;
    };

    return (
        <div className="px-1 py-1">
            <div
                className="flex flex-row border rounded-md hover:bg-muted hover:cursor-pointer"
                onClick={() => setIsSourceOpen(true)}
            >
                <div className="w-5 h-full py-1 bg-muted text-center rounded-l-md">{index + 1}</div>
                <div className="px-1.5 h-full py-1 overflow-hidden text-ellipsis whitespace-nowrap flex-1 min-w-0 flex items-center gap-1">
                    <span className="truncate">{filename ?? content}</span>
                    {sectionTitle && (
                        <span className="ml-1 text-xs text-muted-foreground truncate hidden sm:inline">
                            · {sectionTitle.replace(/^#+\s*/, "")}
                        </span>
                    )}
                </div>
            </div>
            <Sheet open={isSourceOpen} onOpenChange={(value) => setIsSourceOpen(value)}>
                <SheetContent className="w-full h-full overflow-scroll py-3 px-8" side={"bottom"}>
                    {renderContent()}
                </SheetContent>
            </Sheet>
        </div>
    );
};
