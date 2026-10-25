import * as i18nModule from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import * as en from "./en";
import * as fr from "./fr";
import * as it from "./it";
import * as de from "./de";

const i18n = i18nModule;

export enum Languages {
    EN = "en",
    FR = "fr",
    DE = "de",
    IT = "it",
}

export const DisplayLanguages = {
    [Languages.EN]: "English",
    [Languages.FR]: "Français",
    [Languages.DE]: "Deutsch",
    [Languages.IT]: "Italiano",
};

export const defaultNS = "app";

export const resources = {
    en,
    fr,
    it,
    de,
};

export namespace Translation {
    export const init = () => {
        const instance = i18n.use(LanguageDetector).use(initReactI18next);
        instance.init({
            detection: {
                order: ["localStorage", "navigator"],
                caches: ["localStorage"],
            },
            defaultNS,
            resources,
            ns: ["app"],
            fallbackLng: "en",
            returnEmptyString: false,
            interpolation: {
                escapeValue: false,
            },
            returnNull: false,
        });
        // `{{count, number}}` in a string formats with the interface's locale, Swiss for German.
        instance.services.formatter?.add("number", (value, lng, options) =>
            new Intl.NumberFormat(intlLocale(lng ?? "en"), options).format(value),
        );
    };
}

/** The Intl locale for an interface language: German is written the Swiss way (12’345, 25.10.2026). */
export const intlLocale = (lng: string): string => (lng.startsWith("de") ? "de-CH" : lng);

export const translate = i18n.t;
