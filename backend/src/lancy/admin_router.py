"""
Admin API — usage analytics, database stats, data cleanup.

All routes under /api/admin require the admin role.
Enforcement is handled at the Next.js middleware layer (x-user-role header).
"""
from __future__ import annotations

import json as _json_mod
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


class ModelPerfStats(BaseModel):
    model: str
    count: int
    tps_min: float | None
    tps_avg: float | None
    tps_max: float | None
    dur_min: float | None   # seconds
    dur_avg: float | None
    dur_max: float | None


class PerformanceStats(BaseModel):
    models: list[ModelPerfStats]


class ClearRequest(BaseModel):
    older_than_months: int


class ClearResult(BaseModel):
    deleted_conversations: int
    deleted_messages: int
    deleted_reactions: int
    deleted_sources: int


class AdminConfig(BaseModel):
    auto_cleanup_enabled: bool = True
    auto_cleanup_months: int = 12
    auto_cleanup_last_run: str | None = None


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _dir_size(path: str) -> int | None:
    try:
        return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())
    except Exception:
        return None


def _load_admin_cfg(db_dir: Path) -> AdminConfig:
    p = db_dir / "admin_config.json"
    if p.exists():
        try:
            return AdminConfig(**_json_mod.loads(p.read_text()))
        except Exception:
            pass
    return AdminConfig()


def _save_admin_cfg(db_dir: Path, cfg: AdminConfig) -> None:
    (db_dir / "admin_config.json").write_text(cfg.model_dump_json())


async def run_auto_cleanup(db_dir: Path, db_engine: AsyncEngine) -> int:
    """Delete conversations older than configured threshold. Returns deleted count (0 if disabled)."""
    cfg = _load_admin_cfg(db_dir)
    if not cfg.auto_cleanup_enabled:
        return 0
    cutoff_ms = int(
        (datetime.now(timezone.utc) - timedelta(days=cfg.auto_cleanup_months * 30)).timestamp() * 1000
    )
    old_convs = "SELECT id FROM conversations WHERE create_timestamp < :cutoff"
    old_msgs = f"SELECT id FROM messages WHERE conversation_id IN ({old_convs})"
    async with db_engine.begin() as conn:
        await conn.execute(text(f"DELETE FROM reactions WHERE message_id IN ({old_msgs})"), {"cutoff": cutoff_ms})
        await conn.execute(text(f"DELETE FROM sources WHERE message_id IN ({old_msgs})"), {"cutoff": cutoff_ms})
        await conn.execute(text(f"DELETE FROM messages WHERE conversation_id IN ({old_convs})"), {"cutoff": cutoff_ms})
        c = (await conn.execute(text("DELETE FROM conversations WHERE create_timestamp < :cutoff"), {"cutoff": cutoff_ms})).rowcount
    cfg.auto_cleanup_last_run = datetime.now(timezone.utc).isoformat()
    _save_admin_cfg(db_dir, cfg)
    if c:
        log.info(f"Auto-cleanup: {c} conversation(s) removed (>{cfg.auto_cleanup_months}mo old)")
    return c


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
                        " FROM messages WHERE role = 'user' AND create_timestamp >= :cutoff"
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

    @router.get("/stats/performance", response_model=PerformanceStats)
    async def get_performance_stats(days: int = 180) -> PerformanceStats:
        import json as _json
        cutoff_ms = int(
            (datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000
        )
        async with db_engine.connect() as conn:
            rows = (
                await conn.execute(
                    text("SELECT metadata FROM messages WHERE role = 'assistant' AND create_timestamp >= :cutoff"),
                    {"cutoff": cutoff_ms},
                )
            ).fetchall()

        by_model: dict[str, dict[str, list[float]]] = {}
        for (raw,) in rows:
            meta_list: list[dict] = _json.loads(raw) if raw else []
            tps = next((m.get("tokens_per_second") for m in reversed(meta_list) if m.get("tokens_per_second")), None)
            dur_ms = next((m.get("query_duration_ms") for m in reversed(meta_list) if m.get("query_duration_ms")), None)
            model = next((m.get("model") for m in reversed(meta_list) if m.get("model")), None)
            if model is None:
                continue  # no model info → LLM error response, exclude from stats
            if model not in by_model:
                by_model[model] = {"tps": [], "dur": []}
            if tps is not None:
                by_model[model]["tps"].append(float(tps))
            if dur_ms is not None:
                by_model[model]["dur"].append(float(dur_ms) / 1000)

        def _stats(vals: list[float]) -> tuple[float | None, float | None, float | None]:
            if not vals:
                return None, None, None
            return round(min(vals), 2), round(sum(vals) / len(vals), 2), round(max(vals), 2)

        return PerformanceStats(
            models=[
                ModelPerfStats(
                    model=model,
                    count=max(len(v["tps"]), len(v["dur"])),
                    tps_min=_stats(v["tps"])[0],
                    tps_avg=_stats(v["tps"])[1],
                    tps_max=_stats(v["tps"])[2],
                    dur_min=_stats(v["dur"])[0],
                    dur_avg=_stats(v["dur"])[1],
                    dur_max=_stats(v["dur"])[2],
                )
                for model, v in sorted(by_model.items())
            ]
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

    @router.get("/config", response_model=AdminConfig)
    async def get_admin_config() -> AdminConfig:
        return _load_admin_cfg(db_dir)

    @router.put("/config", response_model=AdminConfig)
    async def put_admin_config(cfg: AdminConfig) -> AdminConfig:
        existing = _load_admin_cfg(db_dir)
        cfg.auto_cleanup_months = max(1, min(99, cfg.auto_cleanup_months))
        cfg.auto_cleanup_last_run = existing.auto_cleanup_last_run
        _save_admin_cfg(db_dir, cfg)
        return cfg

    return router
