import loguru
import re
from textwrap import dedent
from typing import Sequence

from conversational_toolkit.llms.base import LLM, LLMMessage, Roles, MessageContent
from conversational_toolkit.retriever.text_analysis import detect_language, tokenize
from conversational_toolkit.vectorstores.base import ChunkMatch, ChunkRecord


async def make_query_standalone(llm: LLM, history: list[LLMMessage], query: str) -> str:
    template_query_standalone = dedent(
        """
        Objective: Your task is to analyze the input query and the provided conversation history. When the user mentions something that was mentioned in the conversation, but not clearly said in the current query, rewrite it.

        Example:

            User Query: How much does it cost?
            Conversation History:
                - User: Can you tell me about the price of the new iPhone?
                - Assistant: The new iPhone costs around $999.
            Reformulated Query: How much does the new iPhone cost?


        Input:

            User Query: {query}
            Conversation History:
                {chat_history}

        Reformulated Query (ONLY the reformulated query in the same language as the user query, without any explanation):
    """
    )
    chat_history = "\n".join(f"- {message.role}: {_history_text(message)}" for message in history)

    conversation = [
        LLMMessage(
            role=Roles.SYSTEM,
            content=[
                MessageContent(
                    type="text",
                    text="You are a helpful assistant that transforms a message from a user to be independent from the conversation history given. Always produce the reformulated query in the same language as the original user query.",
                )
            ],
        ),
        LLMMessage(
            role=Roles.USER,
            content=[
                MessageContent(
                    type="text", text=template_query_standalone.format(query=query, chat_history=chat_history)
                )
            ],
        ),
    ]
    reply = await llm.generate(conversation)
    reformulated_query = ((reply.content[0].text if reply.content else "") or "").strip()

    loguru.logger.debug(f"Original query: {query}")
    loguru.logger.debug(f"Reformulated query: {reformulated_query}")

    # An empty rewrite would search for nothing; the user's own words are better.
    return reformulated_query or query


# Characters of one earlier message the query rewriter sees. It needs the subject
# of the conversation, not every detail of a long answer.
HISTORY_MESSAGE_CHARS = 1500


def _history_text(message: LLMMessage) -> str:
    """A message's text on one line, cut to HISTORY_MESSAGE_CHARS."""
    text = " ".join(" ".join(c.text or "" for c in message.content if c.type == "text").split())
    return text if len(text) <= HISTORY_MESSAGE_CHARS else text[:HISTORY_MESSAGE_CHARS] + " …"


def language_rule(languages: Sequence[str]) -> str:
    """The instruction that picks the language of generated search text.

    'languages' are the corpus languages, most common first; empty means unknown.
    Generated text is embedded and matched against the corpus, so it is written in
    a language the corpus has: the query's own when the corpus has it, otherwise
    the corpus's main one.
    """
    if not languages:
        return "Write in the same language as the original query."
    if len(languages) == 1:
        return f"Write in {languages[0]}, the language of the documents searched."
    return (
        f"The documents searched are written in {', '.join(languages)}. Write in the language of the "
        f"original query if it is one of these, otherwise in {languages[0]}."
    )


async def query_expansion(query: str, llm: LLM, expansion_number: int = 2, languages: Sequence[str] = ()) -> list[str]:
    # "Produce N queries total: some ... some ..." asked for one thing and
    # several in the same breath, and nothing told the model when to stop — at
    # N=1 that reliably sent it into a repetition loop. The count is stated once
    # now, with an explicit stop. The language follows the corpus: a spread of
    # languages costs one of N slots on a translation the corpus has no use for.
    template_query_expansion = """
        Generate alternative search queries for: {query}

        Rules:
        - Output exactly {expansion_number} line(s), one query per line.
        - No numbering, labels, quotes or explanation.
        - Each query must differ meaningfully from the others and from the original.
        - {language_rule}
        - Stop after {expansion_number} line(s).
    """
    conversation = [
        LLMMessage(
            role=Roles.SYSTEM,
            content=[
                MessageContent(
                    type="text",
                    text="You are a focused assistant that rewrites a search query into a fixed number of alternatives. Output only the queries, one per line, with no numbering, labels, or explanation. Never repeat a query.",
                )
            ],
        ),
        LLMMessage(
            role=Roles.USER,
            content=[
                MessageContent(
                    type="text",
                    text=template_query_expansion.format(
                        query=query, expansion_number=expansion_number, language_rule=language_rule(languages)
                    ),
                )
            ],
        ),
    ]

    # A query line runs ~8-12 tokens, so this is generous for the answer asked
    # for and still caps a runaway at a fraction of a second rather than the
    # utility LLM's full budget (one loop cost ~19s of a ~42s answer).
    budget = 32 * expansion_number + 32
    raw = ((await llm.generate(conversation, max_tokens=budget)).content[0].text or "").strip().split("\n")
    lines = [line.strip() for line in raw if line.strip()]
    # `expansion_number` is a request to the model, not a constraint on it: every
    # line returned here becomes its own embedding call and vector search, so a
    # model that repeats itself multiplies the retrieval work without bound (a
    # repetition loop once turned expansion=1 into 204 queries and exhausted the
    # vector store's connection pool). Deduplicate before truncating, or a loop
    # spends the whole budget searching the same text twice.
    generated_queries = list(dict.fromkeys(lines))[:expansion_number]

    loguru.logger.debug(f"Original query for expansion: {query}")
    if len(lines) > len(generated_queries):
        loguru.logger.warning(
            f"Query expansion asked for {expansion_number} queries and got {len(lines)} "
            f"({len(set(lines))} unique) — keeping the first {len(generated_queries)}."
        )
    for i, generated_query in enumerate(generated_queries, start=1):
        loguru.logger.debug(f"Generated query {i}: {generated_query}")

    return generated_queries


async def hyde_expansion(query: str, llm: LLM, languages: Sequence[str] = ()) -> str:
    conversation = [
        LLMMessage(
            role=Roles.SYSTEM,
            content=[
                MessageContent(
                    type="text",
                    text=(
                        "You are a helpful assistant. Provide a hypothetical example answer to the provided query. "
                        f"{language_rule(languages)} Concise, only a few sentences, without any introduction or conclusion."
                    ),
                )
            ],
        ),
        # A language rule in the system prompt alone loses to the language of the
        # query: models answer in the language they are asked in. Repeated next to it.
        LLMMessage(
            role=Roles.USER,
            content=[
                MessageContent(type="text", text=f"{query}\n\n{language_rule(languages)}" if languages else query)
            ],
        ),
    ]
    hyde_expansion_message = (await llm.generate(conversation)).content[0].text or ""

    loguru.logger.debug(f"Original query for HyDE expansion: {query}")
    loguru.logger.debug(f"HyDE expansion: {hyde_expansion_message}")

    return hyde_expansion_message


# Lines kept from the keyword extraction answer; a model that keeps going is cut here.
MAX_KEYWORDS = 20

_LIST_MARKER = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")


async def keyword_extraction(query: str, llm: LLM, languages: Sequence[str] = ()) -> list[str]:
    """Search terms for keyword search: the question's key terms, their synonyms and translations.

    Keyword search matches the question's own words, so it misses a document that
    says the same in other words or another language — "surrogate key" never
    matches "Ersatzschlüssel". The terms returned here widen that; the caller
    weighs them below the question's own words. Translations go into the corpus
    languages other than the question's own; with none left there are none, and
    the prompt does not mention translating — asked to translate into the
    question's own language, a model picks languages of its own.
    """
    question_language = detect_language(tokenize(query))
    targets = [lang for lang in languages if lang.lower() != question_language]
    translations = (
        f"- two or three translations of each key term into {', '.join(targets)}, and into no other language;\n"
        if targets
        else ""
    )
    # Every term returned adds score to the chunks containing it. A term for the field
    # around the question ("database", "definition") or a neighbouring concept ("foreign
    # key" for "surrogate key") lifts chunks that are not about the question at all, so
    # the prompt asks for the same meaning in other words and nothing wider.
    prompt = (
        f"Question: {query}\n\n"
        "List the words a document answering this question would use for what it asks about.\n\n"
        "Include:\n"
        "- the key terms of the question — the concepts it asks about;\n"
        "- words that mean the same as a key term, and its singular or plural form;\n"
        f"{translations}\n"
        "Leave out:\n"
        "- broader topics and the field the question belongs to;\n"
        "- related, neighbouring or contrasting concepts — they are other things, not other words;\n"
        '- filler such as question words, "definition", "vs".\n\n'
        "Rules:\n"
        "- One term per line, each a single word or a short phrase.\n"
        "- No numbering, labels, quotes or explanation.\n"
        f"- At most {MAX_KEYWORDS} lines, then stop."
    )
    conversation = [
        LLMMessage(
            role=Roles.SYSTEM,
            content=[
                MessageContent(
                    type="text",
                    text="You turn a question into search terms. Output only the terms, one per line. Never repeat a term.",
                )
            ],
        ),
        LLMMessage(role=Roles.USER, content=[MessageContent(type="text", text=prompt)]),
    ]
    # A term runs a few tokens; the cap stops a repetition loop early, as in query_expansion.
    raw = (await llm.generate(conversation, max_tokens=8 * MAX_KEYWORDS + 32)).content[0].text or ""
    terms: dict[str, str] = {}
    for line in raw.splitlines():
        term = _LIST_MARKER.sub("", line).strip().strip("\"'`").strip()
        if term and len(term) <= 80:
            terms.setdefault(term.lower(), term)
    keywords = list(terms.values())[:MAX_KEYWORDS]
    loguru.logger.debug(f"Keywords for {query!r}: {keywords}")
    return keywords


def reciprocal_rank_fusion(search_results: Sequence[Sequence[ChunkRecord]], k: int = 60) -> list[ChunkRecord]:
    """
    Applies Reciprocal Rank Fusion (RRF) to a list of search results from different sources.

    Parameters:
    - search_results: A list of tuples where each tuple contains a source name and a ranked list of ChunkRecord objects.
    - k: A constant that dampens the rank influence, default is 60.

    Returns:
    - A list of tuples, each containing a source name and a Chunk object, sorted by the fused score.
    """
    fused_scores = {}
    chunk_map = {}

    for chunks in search_results:
        for rank, chunk in enumerate(chunks, start=1):
            score = float(1 / (k + rank))
            if chunk.id not in fused_scores:
                fused_scores[chunk.id] = score
            else:
                fused_scores[chunk.id] += score
                if isinstance(chunk, ChunkMatch) and isinstance(chunk_map[chunk.id], ChunkMatch):
                    chunk = chunk.model_copy(update={"ranks": merge_ranks(chunk_map[chunk.id].ranks, chunk.ranks)})
            chunk_map[chunk.id] = chunk

    sorted_results = sorted(fused_scores.items(), key=lambda item: item[1], reverse=True)

    return [chunk_map[chunk_id] for chunk_id, _ in sorted_results]


def merge_ranks(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    """Combine two 'ChunkMatch.ranks', keeping the best (lowest) rank per method."""
    return {method: min(a.get(method, rank), rank) for method, rank in b.items()} | {
        method: rank for method, rank in a.items() if method not in b
    }


def build_query_with_chunks(user_query: str = "", chunks: list[ChunkRecord] | None = None) -> str:
    """
    Constructs a query string for an LLM, embedding relevant chunk content inside XML tags.

    Parameters:
    - user_query: The original user query string (optional).
    - chunks: List of Chunk objects containing source information and content.

    Returns:
    - A formatted string with the user query (if provided) and relevant sources in XML format.
    """
    if not chunks:
        sources_xml = "No sources found."
    else:
        sources_xml = "\n".join(f'<source id="{chunk.id}">\n{chunk.content}\n</source>' for chunk in chunks)

    return dedent(
        f"""
        User Query: {user_query}\n
        Here are the sources I found for you:
        {sources_xml}
        """
    )
