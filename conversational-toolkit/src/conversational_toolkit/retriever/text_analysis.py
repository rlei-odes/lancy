"""
Text analysis for keyword (BM25) search: stop words and stemming for German, English, French and Italian.

Chunks and questions go through matching analysis, so "keys" matches "key" and "Schlüsseln" matches "Schlüssel".
A chunk is stemmed in its own language, detected from the stop words it contains. A question word is expanded
to its stem in every supported language plus the word itself, because the corpus mixes languages: a German chunk
may use an English term, which the German stemmer reduces differently from the English one.

The expansion alone does not cover an inflected English term inside other-language text: the German stemmer
leaves "keys" as "keys", while the question's "key" stems to "key" in every language. So a chunk that is not
English also indexes each word's English stem — English is the language of loanwords in these corpora. The
extra terms are not counted in the chunk's length, which BM25 normalises by.
"""

import re
import threading
from collections.abc import Iterable
from functools import lru_cache

import snowballstemmer  # type: ignore[import-untyped]

# Function words only. "it" and "us" are left out of English: as IT and US they are search terms.
STOP_WORDS: dict[str, frozenset[str]] = {
    "english": frozenset(
        "a an the and or but if of at by for with about to from in on into over under as is are was were be been "
        "being am do does did have has had i me my we our you your he him his she her its they them their this "
        "that these those what which who whom whose when where why how there here not no nor so than too very can "
        "could would should will shall may might must all any both each few more most some such only own same just "
        "also then out up down off again further once between through during before after above below because "
        "until while".split()
    ),
    "german": frozenset(
        "der die das den dem des ein eine einer eines einem einen und oder aber wenn als wie ist sind war waren "
        "sein seine seiner seinen ihr ihre ihrer ihren es er sie wir ich du man nicht kein keine keinen zu zum zur "
        "von vom mit bei beim aus auf an am im in ins für über unter nach vor durch um auch noch nur so dass da "
        "dann denn doch hat haben hatte wird werden wurde wurden kann können muss müssen soll sollen was wer wo "
        "warum welche welcher welches dies diese dieser dieses sich mich mir dir uns euch ihm ihn ihnen bis ob "
        "sehr schon hier dort alle alles".split()
    ),
    "french": frozenset(
        "le la les l un une des du de d et ou mais si que qu qui quoi quel quelle quels quelles est sont été être "
        "a ont avoir ce cet cette ces il elle ils elles je j tu nous vous on ne n pas plus au aux en dans par pour "
        "sur avec sans sous comme se s sa son ses leur leurs y c lui mon ma mes ton ta tes notre nos votre vos".split()
    ),
    "italian": frozenset(
        "il lo la i gli le l un uno una di del della dei degli delle da dal dalla in nel nella nei su sul sulla "
        "per con tra fra e ed o ma se che chi cosa come dove quando perché è sono era essere ha hanno avere non "
        "si ci mi ti vi questo questa questi queste quello quella anche più al alla ai agli alle suo sua loro io "
        "tu lui lei noi voi".split()
    ),
}

LANGUAGES = tuple(STOP_WORDS)

_WORD = re.compile(r"\b\w+\b")
_local = threading.local()


def _stemmer(language: str):
    # Stemmer objects keep internal state and are not thread-safe; the index is built
    # in a worker thread while questions are analysed on the event loop.
    stemmers = getattr(_local, "stemmers", None)
    if stemmers is None:
        stemmers = _local.stemmers = {lang: snowballstemmer.stemmer(lang) for lang in LANGUAGES}
    return stemmers[language]


@lru_cache(maxsize=1_000_000)
def _stem(language: str, word: str) -> str:
    return _stemmer(language).stemWord(word)


def tokenize(text: str) -> list[str]:
    """Lowercase words."""
    return _WORD.findall(text.lower())


def detect_language(words: list[str]) -> str | None:
    """The supported language with the most stop words among 'words'; None when there are none."""
    counts = {lang: sum(1 for w in words if w in STOP_WORDS[lang]) for lang in LANGUAGES}
    best = max(LANGUAGES, key=lambda lang: counts[lang])
    return best if counts[best] > 0 else None


def analyze_document(text: str) -> list[str]:
    """Index terms for a chunk: its language's stop words dropped, the rest stemmed in that language.

    Text with no recognisable language (a table of figures, a bare title) keeps its words unchanged.
    """
    words = tokenize(text)
    language = detect_language(words)
    if language is None:
        return words
    stop = STOP_WORDS[language]
    return [_stem(language, w) for w in words if w not in stop]


def index_document(text: str) -> tuple[list[str], int]:
    """Index terms for a chunk, and its length for BM25.

    The terms are 'analyze_document''s, plus each word's English stem where the chunk is
    in another language and the stem differs. The length counts the words, not the extra
    stems, so indexing them does not make a chunk look longer than it is.
    """
    words = tokenize(text)
    language = detect_language(words)
    if language is None:
        return words, len(words)
    stop = STOP_WORDS[language]
    content = [w for w in words if w not in stop]
    terms = [_stem(language, w) for w in content]
    if language != "english":
        extra = [e for w, t in zip(content, terms) if (e := _stem("english", w)) != t]
        return terms + extra, len(terms)
    return terms, len(terms)


def query_words(text: str) -> list[list[str]]:
    """Search terms for a question, grouped by word: each word with its stems in every supported language.

    The question's stop words are dropped, in its own language only. A question made only of
    stop words keeps its words. Keyword search scores a word once, by its best-matching form.
    """
    words = tokenize(text)
    language = detect_language(words)
    if language is not None:
        content = [w for w in words if w not in STOP_WORDS[language]]
        words = content or words
    return [list(dict.fromkeys([word, *(_stem(lang, word) for lang in LANGUAGES)])) for word in words]


def analyze_query(text: str) -> list[str]:
    """Search terms for a question, as one list: every word's forms from 'query_words'."""
    return [term for forms in query_words(text) for term in forms]


def term_spans(text: str, query_terms: Iterable[str]) -> list[tuple[int, int]]:
    """Character spans of the words in 'text' that keyword search matches against 'query_terms'.

    A word matches when one of its index terms, as 'index_document' produces them — the
    word stemmed in the text's language, or its English stem in other-language text — is
    one of the search terms. Stop words never match.
    """
    terms = set(query_terms)
    matches = list(_WORD.finditer(text))
    words = [m.group().lower() for m in matches]
    language = detect_language(words)
    stop = STOP_WORDS[language] if language else frozenset()

    def _matches(w: str) -> bool:
        if language is None:
            return w in terms
        return _stem(language, w) in terms or (language != "english" and _stem("english", w) in terms)

    return [m.span() for m, w in zip(matches, words) if w not in stop and _matches(w)]


def matched_words(text: str, query_terms: Iterable[str]) -> list[str]:
    """The distinct words of 'text', lowercased, that keyword search matches against 'query_terms'."""
    return list(dict.fromkeys(text[s:e].lower() for s, e in term_spans(text, query_terms)))
