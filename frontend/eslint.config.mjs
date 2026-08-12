// ESLint flat config.
//
// Replaces .eslintrc.json, which could never have worked here: eslint-config-next 16
// ships flat config only, and the legacy loader serialises what it extends — those
// entries hold plugin objects that reference themselves, so every `next lint` run
// died with "Converting circular structure to JSON" before linting a single file.
// `next lint` is also deprecated in Next 15 and gone in 16, so package.json calls
// eslint directly now.
import next from "eslint-config-next/core-web-vitals";

// Named rather than exported anonymously — import/no-anonymous-default-export is
// part of the config-next ruleset, and this file lints itself.
const config = [
    ...next,
    {
        // next.config.mjs sets distDir to "dist"; config-next's own ignore list only
        // knows the default .next/out/build, so without this `eslint .` walks the
        // build output.
        ignores: ["dist/**"],
    },
    {
        // Carried over from .eslintrc.json. Its "max-len": [0, 160] is dropped —
        // severity 0 meant the rule was switched off anyway.
        rules: {
            quotes: ["error", "double", { avoidEscape: true }],
            "arrow-body-style": ["error", "as-needed"],
        },
    },
];

export default config;
