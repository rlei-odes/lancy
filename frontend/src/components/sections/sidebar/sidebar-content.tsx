import React, { FunctionComponent, useState } from "react";
import { History } from "@/components/sections/sidebar/history";
import { Settings } from "@/components/sections/sidebar/settings";
import { Help } from "@/components/sections/sidebar/help";
import { Workflows } from "@/components/sections/sidebar/workflows";

interface Props {
    onChangeConversation?: (conversationId: string) => void;
}

enum States {
    HISTORY,
    SETTINGS,
    HELP,
    WORKFLOWS,
}

export const SidebarContent: FunctionComponent<Props> = ({ onChangeConversation }) => {
    const [state, setState] = useState(States.HISTORY);

    const onClickBack = () => setState(States.HISTORY);

    if (state === States.HISTORY) {
        return (
            <History
                onClickSettings={() => setState(States.SETTINGS)}
                onClickHelp={() => setState(States.HELP)}
                onClickWebhook={() => setState(States.WORKFLOWS)}
                onChangeConversation={onChangeConversation}
            />
        );
    }

    if (state === States.SETTINGS) {
        return <Settings onClickBack={onClickBack} />;
    }

    if (state === States.HELP) {
        return <Help onClickBack={onClickBack} />;
    }

    if (state === States.WORKFLOWS) {
        return <Workflows onClickBack={onClickBack} />;
    }
};
