"""
Admin API — usage analytics, database stats, data cleanup.

All routes under /api/admin require the admin role.
Enforcement is handled at the Next.js middleware layer (x-user-role header).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

log = logging.getLogger("uvicorn")


# ─── Response models ──────────────────────────────────────────────────────────


class UsageStats(BaseModel):
    days: list[str]
    conversations: list[int]
    messages: list[int]


class ConvDbStats(BaseModel):
    db_type: str          # "sqlite" | "postgres"
    path: str | None      # file path for sqlite, None for postgres
    size_bytes: int | None
    conv_count: int
    message_count: int
    reaction_count: int
    user_count: int


class VsDbStats(BaseModel):
    vs_type: str          # "chromadb" | "pgvector"
    path: str | None      # directory path for chromadb, None for pgvector
    size_bytes: int | None
    chunk_count: int | None


class DbStats(BaseModel):
    conv_db: ConvDbStats
    vs_db: VsDbStats


class ClearRequest(BaseModel):
    older_than_months: int


class ClearResult(BaseModel):
    deleted_conversations: int
    deleted_messages: int
    deleted_reactions: int
    deleted_sources: int


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _dir_size(path: str) -> int | None:
    try:
        return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())
    except Exception:
        return None


# ─── Router factory ───────────────────────────────────────────────────────────


def create_admin_router(
    db_dir: Path,
    db_engine: AsyncEngine,
    is_sqlite: bool,
    get_active_kb: Callable,
) -> APIRouter:
    """
    get_active_kb: zero-arg callable returning the active KBInfo (from kb_router).
    """
    router = APIRouter(prefix="/api/admin")

    @router.get("/stats/usage", response_model=UsageStats)
    async def get_usage_stats(days: int = 180) -> UsageStats:
        cutoff_ms = int(
            (datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000
        )
        if is_sqlite:
            date_expr = "date(create_timestamp / 1000, 'unixepoch')"
        else:
            date_expr = "to_char(to_timestamp(create_timestamp / 1000.0), 'YYYY-MM-DD')"

        async with db_engine.connect() as conn:
            conv_rows = (
                await conn.execute(
                    text(
                        f"SELECT {date_expr} as day, COUNT(*) as cnt"
                        " FROM conversations WHERE create_timestamp >= :cutoff"
                        " GROUP BY day ORDER BY day"
                    ),
                    {"cutoff": cutoff_ms},
                )
            ).fetchall()

            msg_rows = (
                await conn.execute(
                    text(
                        f"SELECT {date_expr} as day, COUNT(*) as cnt"
                        " FROM messages WHERE create_timestamp >= :cutoff"
                        " GROUP BY day ORDER BY day"
                    ),
                    {"cutoff": cutoff_ms},
                )
            ).fetchall()

        conv_map = {row[0]: row[1] for row in conv_rows}
        msg_map = {row[0]: row[1] for row in msg_rows}
        all_days = sorted(set(conv_map) | set(msg_map))

        return UsageStats(
            days=all_days,
            conversations=[conv_map.get(d, 0) for d in all_days],
            messages=[msg_map.get(d, 0) for d in all_days],
        )

    @router.get("/stats/db", response_model=DbStats)
    async def get_db_stats() -> DbStats:
        async with db_engine.connect() as conn:
            conv_count = (await conn.execute(text("SELECT COUNT(*) FROM conversations"))).scalar() or 0
            msg_count = (await conn.execute(text("SELECT COUNT(*) FROM messages"))).scalar() or 0
            reaction_count = (await conn.execute(text("SELECT COUNT(*) FROM reactions"))).scalar() or 0
            user_count = (await conn.execute(text("SELECT COUNT(*) FROM users"))).scalar() or 0

        conv_path = str(db_dir / "conversations.db") if is_sqlite else None
        conv_size = os.path.getsize(conv_path) if conv_path and Path(conv_path).exists() else None

        try:
            kb = get_active_kb()
            vs_type = getattr(kb, "vs_type", "chromadb") or "chromadb"
            vs_path = getattr(kb, "vs_path", None) if vs_type == "chromadb" else None
            vs_size = _dir_size(vs_path) if vs_path else None
            # Chunk count from kb_stats file written by ingestion pipeline
            stats_file = db_dir / f"kb_stats_{kb.id}.json"
            vs_chunks: int | None = None
            if stats_file.exists():
                import json
                data = json.loads(stats_file.read_text())
                vs_chunks = data.get("total_chunks")
        except Exception:
            vs_type, vs_path, vs_size, vs_chunks = "chromadb", None, None, None

        return DbStats(
            conv_db=ConvDbStats(
                db_type="sqlite" if is_sqlite else "postgres",
                path=conv_path,
                size_bytes=conv_size,
                conv_count=int(conv_count),
                message_count=int(msg_count),
                reaction_count=int(reaction_count),
                user_count=int(user_count),
            ),
            vs_db=VsDbStats(
                vs_type=vs_type,
                path=vs_path,
                size_bytes=vs_size,
                chunk_count=vs_chunks,
            ),
        )

    @router.post("/clear", response_model=ClearResult)
    async def clear_old_records(req: ClearRequest) -> ClearResult:
        months = max(1, req.older_than_months)
        cutoff_ms = int(
            (datetime.now(timezone.utc) - timedelta(days=months * 30)).timestamp() * 1000
        )
        # Delete in FK order: reactions → sources → messages → conversations
        async with db_engine.begin() as conn:
            old_convs = (
                "SELECT id FROM conversations WHERE create_timestamp < :cutoff"
            )
            old_msgs = f"SELECT id FROM messages WHERE conversation_id IN ({old_convs})"

            r = (await conn.execute(
                text(f"DELETE FROM reactions WHERE message_id IN ({old_msgs})"),
                {"cutoff": cutoff_ms},
            )).rowcount

            s = (await conn.execute(
                text(f"DELETE FROM sources WHERE message_id IN ({old_msgs})"),
                {"cutoff": cutoff_ms},
            )).rowcount

            m = (await conn.execute(
                text(f"DELETE FROM messages WHERE conversation_id IN ({old_convs})"),
                {"cutoff": cutoff_ms},
            )).rowcount

            c = (await conn.execute(
                text("DELETE FROM conversations WHERE create_timestamp < :cutoff"),
                {"cutoff": cutoff_ms},
            )).rowcount

        log.info(
            f"Admin clear ({months}mo): {c} conversations, {m} messages, "
            f"{r} reactions, {s} sources deleted"
        )
        return ClearResult(
            deleted_conversations=c,
            deleted_messages=m,
            deleted_reactions=r,
            deleted_sources=s,
        )

    return router
