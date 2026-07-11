import React, { FunctionComponent } from "react";
import { Markdown } from "@/components/ui/markdown";

interface Props {
    content: string;
    origin?: string;
    originHref?: string | null;
}

export const QuoteItem: FunctionComponent<Props> = (props: Props) => {
    const { content, origin, originHref } = props;

    const hasOrigin = origin !== undefined;

    return (
        <div className="quote">
            <div className="flex flex-col py-3">
                <Markdown content={content} />
                {hasOrigin && (
                    <div className="pt-1 flex flex-row justify-end italic">
                        {originHref ? (
                            <span>- (<a
                                href={originHref}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 underline decoration-blue-600/30 hover:decoration-blue-600 transition-colors underline-offset-2"
                            >{origin}</a>)</span>
                        ) : (
                            <span>- ({origin})</span>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
};
