import React, { createContext, FunctionComponent, useCallback, useContext, useEffect, useState } from "react";
import { config } from "@/config";

interface BrandingConfig {
    agent_name: string;
    agent_avatar_url: string | null;
}

interface BrandingContextValue {
    agentName: string;
    agentAvatarUrl: string;
    reload: () => void;
}

const BrandingContext = createContext<BrandingContextValue>({
    agentName: config.agent.name,
    agentAvatarUrl: config.app.logo,
    reload: () => {},
});

export const BrandingProvider: FunctionComponent<{ children: React.ReactNode }> = ({ children }) => {
    const [branding, setBranding] = useState<BrandingConfig | null>(null);

    const load = useCallback(async () => {
        try {
            const res = await fetch("/api/v1/branding");
            if (res.ok) setBranding(await res.json());
        } catch {}
    }, []);

    useEffect(() => { load(); }, [load]);

    return (
        <BrandingContext.Provider value={{
            agentName: branding?.agent_name || config.agent.name,
            agentAvatarUrl: branding?.agent_avatar_url || config.app.logo,
            reload: load,
        }}>
            {children}
        </BrandingContext.Provider>
    );
};

export const useBranding = () => useContext(BrandingContext);
