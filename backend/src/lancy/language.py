"""
Which languages a knowledge base is written in.

Each document's language is detected at ingestion and stored in its chunks'
metadata (`language`); the KB keeps a count of documents per language. HyDE,
query expansion and keyword extraction write in the corpus languages, because
what they generate is matched against the corpus.

Corpora mix languages — German course material with English terms, standards in
several languages — so a KB has a list of corpus languages, not one. A language
counts once it holds `MIN_SHARE` of the documents, so two Italian documents in
an 800-document KB do not earn Italian translations. An admin-set list replaces
the detected one.

`py3langid` detects, as in the sister project lcf: its model ships inside the
package, so nothing is downloaded on first use or needs fetching on an
installation without egress. Keyword search keeps its own stop-word detection
per chunk (`text_analysis.detect_language`), which needs no model and covers
exactly the languages it can stem.
"""

from __future__ import annotations

from functools import lru_cache

# Below this many characters a detection is a guess; the document stays unlabelled.
MIN_CHARS = 60

# Detection reads this much of a document; more changes nothing but the time it takes.
MAX_CHARS = 20_000

# Share of the documents a language needs to count as a corpus language.
MIN_SHARE = 0.10

_NAMES = {
    "de": "German",
    "en": "English",
    "fr": "French",
    "it": "Italian",
    "es": "Spanish",
    "pt": "Portuguese",
    "nl": "Dutch",
    "sv": "Swedish",
    "no": "Norwegian",
    "da": "Danish",
    "fi": "Finnish",
    "pl": "Polish",
    "cs": "Czech",
    "ru": "Russian",
    "tr": "Turkish",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
}


@lru_cache(maxsize=1)
def _identifier():
    from py3langid.langid import MODEL_FILE, LanguageIdentifier

    # `from_model_file` resolves the name against the package directory, where the
    # bundled model lives; `from_modelpath` would resolve it against the working directory.
    return LanguageIdentifier.from_model_file(MODEL_FILE)


def detect(text: str) -> str | None:
    """ISO 639-1 code of the language 'text' is written in; None when there is too little text."""
    body = " ".join((text or "")[: MAX_CHARS * 2].split())[:MAX_CHARS]
    if len(body) < MIN_CHARS:
        return None
    code, _ = _identifier().classify(body)
    return str(code)


def detect_document(texts: list[str]) -> str | None:
    """The language of a document given its chunks' texts, read up to 'MAX_CHARS'."""
    parts, size = [], 0
    for text in texts:
        parts.append(text)
        size += len(text)
        if size >= MAX_CHARS:
            break
    return detect("\n".join(parts))


def corpus_languages(counts: dict[str, int], override: list[str] | None = None) -> list[str]:
    """Corpus language codes, most documents first: the override when set, else those over 'MIN_SHARE'."""
    if override:
        return list(override)
    total = sum(counts.values())
    if total == 0:
        return []
    ranked = sorted(counts, key=lambda code: counts[code], reverse=True)
    return [code for code in ranked if counts[code] / total >= MIN_SHARE]


def language_name(code: str) -> str:
    """English name of a language code, for prompts; the code itself when it has none here."""
    return _NAMES.get(code.lower(), code)
