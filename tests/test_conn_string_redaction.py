"""Intent: a database password never reaches the log.

Both places that logged a connection string truncated it — `conn[:40]` for the
pgvector store, `_database_url[:40]` for the conversation DB. Truncation is not
redaction: the prefix ahead of the password has a fixed length, so a slice lands
*inside* the password. "postgresql+asyncpg://" plus a username and a colon is
around 32 characters, so a 40-character slice printed the password's first
characters into backend.log.

`safe_conn_str` masks the password instead, and must not fall back to echoing the
original when parsing fails — that would leak precisely for the malformed inputs
most worth logging.

Every value below is a placeholder. Never put a real host, user or password in a
test: it is committed permanently and defeats the point of the fix.
"""

import pytest

from lancy.feature0_baseline_rag import safe_conn_str

PASSWORD = "pw-placeholder-do-not-use"
USER = "dbuser"
HOST = "dbhost"
DB = "dbname"


@pytest.mark.parametrize(
    "conn",
    [
        f"postgresql://{USER}:{PASSWORD}@{HOST}:5432/{DB}",
        f"postgresql+asyncpg://{USER}:{PASSWORD}@{HOST}:5432/{DB}",
        f"postgres://{USER}:{PASSWORD}@10.0.0.1:5432/{DB}",
        # password with URL-encoded punctuation
        f"postgresql://{USER}:p%40ss%3Aword@{HOST}:5432/{DB}",
    ],
    ids=["postgresql", "asyncpg", "postgres-ip", "encoded"],
)
def test_password_is_never_rendered(conn):
    out = safe_conn_str(conn)

    assert PASSWORD not in out
    # The old truncation exposed a prefix, so assert on prefixes too — any leading
    # run of the password is a leak, not just the whole thing.
    for n in range(4, len(PASSWORD) + 1):
        assert PASSWORD[:n] not in out
    assert "p%40ss" not in out and "p@ss" not in out


def test_what_is_kept_is_still_useful():
    """Redaction has to leave enough to diagnose a wrong host or database."""
    out = safe_conn_str(f"postgresql+asyncpg://{USER}:{PASSWORD}@{HOST}:5432/{DB}")

    assert HOST in out
    assert "5432" in out
    assert DB in out
    assert USER in out  # username is not a secret
    assert "asyncpg" in out  # driver, so a missing +asyncpg rewrite stays visible


@pytest.mark.parametrize(
    "conn",
    [
        "",
        "not a url at all",
        f"::::{PASSWORD}",
        # A typo'd port raises during parsing, and the password sits in password
        # position — the case where echoing `conn` would leak.
        f"postgresql://{USER}:{PASSWORD}@{HOST}:notaport/{DB}",
    ],
    ids=["empty", "garbage", "colons", "bad-port"],
)
def test_unparseable_input_does_not_echo_the_original(conn):
    """The failure path must not degrade to returning `conn`."""
    out = safe_conn_str(conn)

    assert PASSWORD not in out
    assert out  # always something loggable
