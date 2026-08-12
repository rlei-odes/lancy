"use client";
import { createContext, FunctionComponent, useContext, useEffect, useState, useSyncExternalStore } from "react";

export enum Theme {
    SYSTEM = "system",
    LIGHT = "light",
    DARK = "dark",
}

const ThemeContext = createContext<{
    theme: Theme;
    cssClass: string;
    changeTheme: (theme: Theme) => void;
}>({
    theme: Theme.LIGHT,
    cssClass: "",
    changeTheme: (theme: Theme) => {},
});

interface Props {
    children: React.ReactNode;
}

// The OS colour scheme is an external store, so it is subscribed to rather than
// mirrored into state from an effect. Module scope keeps these identities stable
// across renders, which useSyncExternalStore requires.
const subscribeToColorScheme = (onChange: () => void) => {
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
};
const getColorSchemeSnapshot = () => window.matchMedia("(prefers-color-scheme: dark)").matches;
const getServerColorSchemeSnapshot = () => false;

export const useTheme = () => useContext(ThemeContext);

export const ThemeProvider: FunctionComponent<Props> = (props: Props) => {
    const { children } = props;

    const [theme, setTheme] = useState(() => {
        if (typeof window !== "undefined") {
            const savedTheme = window.localStorage.getItem("theme");
            return savedTheme ? (savedTheme as Theme) : Theme.SYSTEM;
        }
        return Theme.SYSTEM; // Default: follow system preference
    });

    const systemPrefersDark = useSyncExternalStore(
        subscribeToColorScheme,
        getColorSchemeSnapshot,
        getServerColorSchemeSnapshot,
    );

    // Fully derived, so there is no effect to keep in step. This also fixes a case the
    // effect missed: it only wrote a new class on a media-query *change* event, so
    // switching from an explicit theme back to SYSTEM kept the explicit class until the
    // OS preference next flipped. The snapshot is re-read on every render instead.
    const cssClass = theme === Theme.SYSTEM ? (systemPrefersDark ? "dark" : "") : theme === Theme.DARK ? "dark" : "";

    const changeTheme = (theme: Theme) => {
        setTheme(() => theme);
        window.localStorage.setItem("theme", theme);
    };

    useEffect(() => {
        const body = document.querySelector("body");
        if (body) {
            body.className = cssClass;
        }
    }, [cssClass]);

    return (
        <ThemeContext.Provider
            value={{
                theme,
                cssClass,
                changeTheme,
            }}
        >
            {children}
        </ThemeContext.Provider>
    );
};
