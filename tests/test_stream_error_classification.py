"""Intent: a retrieval outage is reported as one, and not blamed on the LLM.

With pgvector unreachable the chat showed "Cannot reach the LLM. Is it running?"
while the LLM was serving fine, which sends you to debug the wrong machine.

asyncpg lets a refused connection propagate as a bare `ConnectionRefusedError`:
module `builtins`, so matching on the exception's module misses it, and its text
varies by platform — "[Errno 111] Connection refused" on one host, "Connect call
failed ('10.0.0.1', 5432)" on another — so matching on text misses it too. Only
the second spelling was listed, which is why this reproduced on one machine and
not another.

The discriminator is structural: the LLM client never raises an unwrapped
OSError, because httpx/openai re-raise as their own types. So an unwrapped
OSError came from the vector store. Adding "connection refused" to the text list
instead would have reported a genuine LLM outage as a database fault.
"""

import pytest

from lancy.main import _classify_stream_error

DB_DOWN = "Retrieval failed. Is the vector database (PostgreSQL) running?"
CHROMA_BAD = "Retrieval failed. Could not read the ChromaDB store on disk."
LLM_DOWN = "Cannot reach the LLM. Is it running?"


class PGVectorStore:
    """Stands in for the real store — only the class name is read."""


class ChromaDBVectorStore:
    pass


def wrapped(outer_module, outer_name, text, cause):
    """An exception as httpx/openai raise it: their own type, OSError beneath."""
    exc_type = type(outer_name, (Exception,), {})
    exc_type.__module__ = outer_module
    try:
        try:
            raise cause
        except BaseException as inner:
            raise exc_type(text) from inner
    except BaseException as e:
        return e


def classify(exc, store=None):
    return _classify_stream_error(exc, store or PGVectorStore(), "some-model")


# ─── the vector database is down ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "[Errno 111] Connection refused",                    # the spelling that regressed
        "Connect call failed ('10.0.0.1', 5432)",
        "[Errno 111] Connect call failed ('10.0.0.1', 5432)",
        "[Errno -2] Name or service not known",
        "[Errno 113] No route to host",
    ],
    ids=["refused", "connect-call-failed", "errno-connect-call", "dns", "no-route"],
)
def test_an_unwrapped_oserror_is_a_retrieval_failure(text):
    assert classify(ConnectionRefusedError(text)) == DB_DOWN


def test_a_timeout_reaching_the_database_is_a_retrieval_failure():
    assert classify(TimeoutError("[Errno 110] Connection timed out")) == DB_DOWN


@pytest.mark.parametrize(
    "text",
    [
        "could not connect to server",
        "could not translate host name \"dbhost\" to address",
        "asyncpg.exceptions.InvalidPasswordError",
        "relation \"rag_kb1\" does not exist",  # names postgres via the driver below
    ],
    ids=["could-not-connect", "bad-host", "auth", "missing-table"],
)
def test_driver_errors_are_retrieval_failures(text):
    exc = wrapped("asyncpg.exceptions", "PostgresError", text, RuntimeError("inner"))

    assert classify(exc) == DB_DOWN


def test_a_sqlalchemy_wrapped_error_is_a_retrieval_failure():
    exc = wrapped("sqlalchemy.exc", "OperationalError", "connection failed", RuntimeError("x"))

    assert classify(exc) == DB_DOWN


# ─── the LLM is genuinely down: must NOT be blamed on the database ────────────


def test_a_refused_llm_connection_is_still_reported_as_an_llm_failure():
    """The case that a broader text match would have broken."""
    exc = wrapped(
        "openai", "APIConnectionError", "Connection error.",
        wrapped("httpx", "ConnectError", "[Errno 111] Connection refused",
                ConnectionRefusedError("[Errno 111] Connection refused")),
    )

    assert classify(exc) == LLM_DOWN


def test_an_httpx_connect_error_is_an_llm_failure():
    exc = wrapped("httpx", "ConnectError", "[Errno 111] Connection refused",
                  ConnectionRefusedError("[Errno 111] Connection refused"))

    assert classify(exc) == LLM_DOWN


def test_a_missing_model_names_the_model():
    exc = wrapped("openai", "NotFoundError", "404 model not found", RuntimeError("x"))

    assert "some-model" in _classify_stream_error(exc, PGVectorStore(), "some-model")


# ─── the message names the store actually in use ──────────────────────────────


def test_a_chromadb_file_error_does_not_name_postgresql():
    """ChromaDB is on disk, so its failures are OSErrors too — and 'is it
    running?' is the wrong question for an embedded store."""
    assert classify(FileNotFoundError("[Errno 2] No such file"), ChromaDBVectorStore()) == CHROMA_BAD


@pytest.mark.parametrize(
    "exc",
    [
        PermissionError("[Errno 13] Permission denied"),
        OSError("[Errno 28] No space left on device"),
        IsADirectoryError("[Errno 21] Is a directory"),
    ],
    ids=["permission", "disk-full", "is-a-directory"],
)
def test_chromadb_disk_failures_point_at_the_disk(exc):
    out = classify(exc, ChromaDBVectorStore())

    assert out == CHROMA_BAD
    assert "PostgreSQL" not in out


def test_an_agent_without_a_store_still_reports_a_retrieval_failure():
    """vector_store is Optional on the base RAG, so None must not crash."""
    assert _classify_stream_error(ConnectionRefusedError("refused"), None, "m") == DB_DOWN


# ─── unrelated failures stay verbatim ─────────────────────────────────────────


def test_an_unrelated_error_is_passed_through_for_debugging():
    out = classify(ValueError("prompt template is malformed"))

    assert "prompt template is malformed" in out


def test_a_cyclic_exception_chain_terminates():
    """__context__ can form a cycle; the walk must not hang."""
    a = ValueError("a")
    b = ValueError("b")
    a.__cause__ = b
    b.__cause__ = a

    assert classify(a)  # returns rather than looping forever
