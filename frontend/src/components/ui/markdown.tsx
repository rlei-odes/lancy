import React, { FunctionComponent, useState } from "react";
import ReactMarkdown from "react-markdown";
import RemarkMath from "remark-math";
import RemarkBreaks from "remark-breaks";
import RehypeKatex from "rehype-katex";
import RemarkGfm from "remark-gfm";
import RehypeHighlight from "rehype-highlight";
import RehypeSanitize from "rehype-sanitize";
import RehypeRaw from "rehype-raw";
import { defaultSchema } from "hast-util-sanitize";
import { cn } from "@/lib/lorem";
import { Source } from "@/services/message";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ExternalLink } from "lucide-react";
import { resolveSourceUrl } from "@/lib/sources";

const DOC_EXT = /\.(pdf|xlsx|xls|docx|doc|md|txt|csv)$/i;

// Allow source:// protocol so the Markdown a-handler can intercept citation links
const sanitizeSchema = {
    ...defaultSchema,
    protocols: {
        ...defaultSchema.protocols,
        href: [...(defaultSchema.protocols?.href ?? []), "source"],
    },
};

const DELIM_CELL = /^\s*:?-{3,}:?\s*$/;

// Chunks indexed before the ingestion fix had every newline inside a block
// replaced by a space, which leaves a whole markdown table on one line:
//   | A | B | |---|---| | 1 | 2 |
// Rebuild the rows, but only when the cell counts line up exactly — splitting a
// row apart on a guess is worse than showing the raw text.
function reflowFlattenedTable(line: string): string | null {
    const trimmed = line.trim();
    if (!trimmed.startsWith("|") || !trimmed.endsWith("|") || !trimmed.includes("---")) return null;

    const cells = trimmed.split("|").slice(1, -1);
    const start = cells.findIndex((c) => DELIM_CELL.test(c));
    if (start < 0) return null;

    // The delimiter row gives the column count.
    let cols = 0;
    while (start + cols < cells.length && DELIM_CELL.test(cells[start + cols])) cols++;
    // It must sit directly after the header row's cells plus the collapsed newline.
    if (start !== cols + 1) return null;

    const rows: string[][] = [];
    for (let i = 0; i < cells.length;) {
        if (cells.length - i < cols) return null;
        rows.push(cells.slice(i, i + cols));
        i += cols;
        if (i < cells.length) {
            if (cells[i].trim()) return null; // must be the space a newline became
            i++;
        }
    }
    return rows.map((r) => `| ${r.map((c) => c.trim()).join(" | ")} |`).join("\n");
}

// Tables that already have their newlines are left untouched: their individual
// lines fail the reconstruction above and pass straight through.
function reflowFlattenedTables(content: string): string {
    if (!content.includes("|")) return content;

    const out: string[] = [];
    const lines = content.split("\n");
    lines.forEach((line, i) => {
        const table = reflowFlattenedTable(line);
        if (!table) {
            out.push(line);
            return;
        }
        // GFM only parses the table as a block if it is not glued to adjacent text.
        if (out.length > 0 && out[out.length - 1].trim()) out.push("");
        out.push(table);
        if (lines[i + 1]?.trim()) out.push("");
    });
    return out.join("\n");
}

// Convert (filename.pdf) plain-text references to markdown links,
// but only for filenames that actually exist in the provided sources.
const DOC_REF_RE = /\(([^()\s][^()]*\.(pdf|xlsx|xls|docx|doc|md|txt|csv))\)/gi;
function linkifyDocRefs(content: string, sources?: Source[]): string {
    const knownFiles = new Set(
        (sources ?? []).map((s) => s.metadata?.source_file as string).filter(Boolean)
    );
    return content.replace(DOC_REF_RE, (match, filename) => {
        if (knownFiles.size > 0 && !knownFiles.has(filename)) return match;
        return `[${filename}](<${filename}>)`;
    });
}

// Inline citation link that shows a source-content popup on click
const SourceCitationLink: FunctionComponent<{ filename: string; sources?: Source[] }> = ({ filename, sources }) => {
    const [open, setOpen] = useState(false);
    const source = sources?.find((s) => (s.metadata?.source_file as string) === filename);
    const fileUrl = resolveSourceUrl(filename, source?.metadata);

    return (
        <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
                <button className="inline text-blue-600 dark:text-blue-400 underline underline-offset-2 decoration-blue-600/30 hover:decoration-blue-600 transition-colors cursor-pointer text-[0.9em] font-medium">
                    {filename}
                </button>
            </PopoverTrigger>
            <PopoverContent className="w-[520px] max-h-[420px] flex flex-col p-0 overflow-hidden" align="start">
                <div className="px-3 py-2 border-b flex items-center justify-between">
                    <span className="text-xs font-semibold truncate pr-2">{filename}</span>
                    <a
                        href={fileUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-1 text-xs text-blue-600 dark:text-blue-400 hover:underline shrink-0"
                        onClick={(e) => e.stopPropagation()}
                    >
                        <ExternalLink className="w-3 h-3" />
                        Öffnen
                    </a>
                </div>
                {source && (
                    <div className="px-3 py-2 overflow-auto text-xs text-muted-foreground whitespace-pre-wrap leading-relaxed">
                        {source.content}
                    </div>
                )}
            </PopoverContent>
        </Popover>
    );
};

export const Markdown: FunctionComponent<{ content: string; sources?: Source[] }> = ({ content, sources }) => (
    <ReactMarkdown
        remarkPlugins={[RemarkMath, RemarkGfm, RemarkBreaks]}
        rehypePlugins={[
            RehypeKatex,
            RehypeRaw,
            [RehypeSanitize, sanitizeSchema],
            [
                RehypeHighlight,
                {
                    detect: false,
                    ignoreMissing: true,
                },
            ],
        ]}
        components={{
            h1: ({ node, ...props }) => <h1 className="text-4xl font-bold mt-8 mb-4 text-foreground border-b pb-2" {...props} />,
            h2: ({ node, ...props }) => <h2 className="text-3xl font-bold mt-6 mb-3 text-foreground" {...props} />,
            h3: ({ node, ...props }) => <h3 className="text-2xl font-semibold mt-5 mb-2 text-foreground" {...props} />,
            h4: ({ node, ...props }) => <h4 className="text-xl font-semibold mt-4 mb-2 text-foreground" {...props} />,
            h5: ({ node, ...props }) => <h5 className="text-lg font-semibold mt-3 mb-1 text-foreground" {...props} />,
            h6: ({ node, ...props }) => <h6 className="text-base font-semibold mt-2 mb-1 text-muted-foreground" {...props} />,
            p: (pProps) => <p {...pProps} className={cn(pProps.className, "mb-4 leading-7 text-foreground")} dir="auto" />,
            a: (aProps) => {
                const href = aProps.href || "";

                // source:// links are in-text citations → show content popup
                if (href.startsWith("source://")) {
                    const filename = decodeURIComponent(href.replace("source://", ""));
                    return <SourceCitationLink filename={filename} sources={sources} />;
                }

                const isHttp = /^https?:\/\//i.test(href);
                const isInternal = /^\/#/i.test(href);
                const isDocFile = DOC_EXT.test(href);

                // LLM sometimes embeds filenames as relative links → rewrite to backend file endpoint
                // (or to the source's external_url if one was supplied at ingestion)
                if (!isHttp && !isInternal && isDocFile && href !== "") {
                    const source = sources?.find((s) => (s.metadata?.source_file as string) === href);
                    const fileUrl = resolveSourceUrl(href, source?.metadata);
                    return (
                        <a
                            href={fileUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className={cn(
                                aProps.className,
                                "text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 underline decoration-blue-600/30 hover:decoration-blue-600 transition-colors underline-offset-2",
                            )}
                        >
                            {aProps.children}
                        </a>
                    );
                }
                if (!isHttp && !isInternal && href !== "") {
                    return <span className="text-foreground">{aProps.children}</span>;
                }
                const target = isInternal ? "_self" : "_blank";
                return (
                    <a
                        {...aProps}
                        href={href}
                        target={target}
                        rel="noopener noreferrer"
                        className={cn(
                            aProps.className,
                            "text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 underline decoration-blue-600/30 hover:decoration-blue-600 transition-colors underline-offset-2",
                        )}
                    />
                );
            },
            code: (codeProps: any) => {
                const { inline, className: codeClass, children, ...props } = codeProps;
                if (inline) {
                    return (
                        <code {...props} className={cn(codeClass, "bg-muted text-foreground text-[0.875em] px-1.5 py-0.5 rounded font-mono")}>
                            {children}
                        </code>
                    );
                } else {
                    return (
                        <pre className={cn(codeClass, "bg-muted text-foreground text-sm p-4 rounded-lg overflow-x-auto my-4 border")}>
                            <code {...props} className={codeClass}>
                                {children}
                            </code>
                        </pre>
                    );
                }
            },
            pre: (preProps) => (
                <pre {...preProps} className={cn(preProps.className, "bg-muted text-foreground text-sm p-4 my-4 rounded-lg overflow-x-auto border")} />
            ),
            blockquote: (bqProps) => (
                <blockquote {...bqProps} className="border-l-4 border-muted-foreground/30 pl-4 py-1 italic my-4 text-muted-foreground bg-muted/30 rounded-r" />
            ),
            hr: () => <hr className="my-8 border-t border-border" />,
            ul: (ulProps) => <ul {...ulProps} className="list-disc list-outside ml-6 my-3 text-foreground space-y-1" />,
            ol: (olProps) => <ol {...olProps} className="list-decimal list-outside ml-6 my-3 text-foreground space-y-1" />,
            li: (liProps) => <li {...liProps} className="text-foreground leading-7 pl-1" />,
            input: ({ node, ...props }) => <input {...props} disabled className="mr-2 cursor-not-allowed accent-primary" />,
            strong: ({ node, ...props }) => <strong {...props} className="font-semibold text-foreground" />,
            em: ({ node, ...props }) => <em {...props} className="italic text-foreground" />,
            del: ({ node, ...props }) => <del {...props} className="line-through text-muted-foreground" />,
            table: (tableProps) => (
                <div className="my-6 overflow-x-auto">
                    <table {...tableProps} className="w-full border-collapse border border-border rounded-lg overflow-hidden text-sm" />
                </div>
            ),
            thead: (theadProps) => <thead {...theadProps} className="bg-muted" />,
            tbody: (tbodyProps) => <tbody {...tbodyProps} className="divide-y divide-border" />,
            tr: (trProps) => <tr {...trProps} className="border-b border-border last:border-0" />,
            th: (thProps) => <th {...thProps} className="px-4 py-3 font-semibold text-left text-foreground" />,
            td: (tdProps) => <td {...tdProps} className="px-4 py-3 text-left text-foreground" />,
        }}
    >
        {reflowFlattenedTables(linkifyDocRefs(content, sources))}
    </ReactMarkdown>
);
