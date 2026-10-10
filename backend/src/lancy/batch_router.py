"""
Lancy — Batch analysis jobs and saved question sets.

A batch job analyzes a list of documents one after another with the same prompt
and JSON schema. It runs as a background task in the backend process and records
each document's result in SQLite as it finishes, so the browser that started it can
leave, reload or close without losing the run — the UI polls `GET /job` to follow it.

Each owner has at most one job. Starting a run with the same definition (source,
id field, prompt, schema) as the existing job extends it: documents that already
have a result are skipped and only new or failed ones are analyzed. A different
definition would make the existing results answers to other questions, so it
replaces the job — only when the caller confirms with `replace: true`.

Endpoints (prefix /api/v1/batch):
    GET    /question-sets          — the caller's saved question sets
    PUT    /question-sets/{name}   — save or overwrite a set
    DELETE /question-sets/{name}   — delete a set
    GET    /job                    — the caller's job with its results, or null
    POST   /job                    — start, extend or replace the job
    POST   /job/cancel             — stop after the current document
    DELETE /job                    — discard the job and its results
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Awaitable, Callable, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from lancy.database import (
    add_batch_result,
    batch_cancel_requested,
    delete_batch_job,
    delete_question_set,
    finish_batch_job,
    get_batch_job,
    init_db,
    interrupt_running_batch_jobs,
    list_question_sets,
    replace_batch_job,
    request_batch_cancel,
    resume_batch_job,
    save_question_set,
    set_batch_current,
)
from lancy.piles_router import pile_owner_id
from lancy.rag_router import AnalyzeDocumentRequest, AnalyzeDocumentResponse

log = logging.getLogger("uvicorn")

_MAX_IDS = 5_000
_MAX_QUESTIONS = 50

AnalyzeOne = Callable[..., Awaitable[AnalyzeDocumentResponse]]


class QuestionSetBody(BaseModel):
    rows: list[dict[str, Any]] = Field(..., min_length=1, max_length=_MAX_QUESTIONS)


class BatchJobRequest(BaseModel):
    ids: list[str] = Field(..., min_length=1, max_length=_MAX_IDS)
    id_field: Literal["source_file", "document_id"] = "source_file"
    prompt: str = Field(..., min_length=1, max_length=20_000)
    response_schema: dict[str, Any]
    questions: list[dict[str, Any]] = Field(default_factory=list, max_length=_MAX_QUESTIONS)
    """Display form of the questions, returned with the job so its results can be
    labelled after the editor has moved on. Not part of the job's definition."""
    kb_id: str | None = Field(default=None, max_length=100)
    pile_id: str | None = Field(default=None, max_length=64)
    replace: bool = False
    """Confirms discarding an existing job whose definition differs."""

    @model_validator(mode="after")
    def one_source(self) -> "BatchJobRequest":
        if self.pile_id and self.kb_id:
            raise ValueError("Provide either pile_id or kb_id, not both.")
        return self


def _definition(kb_id: str | None, pile_id: str | None, id_field: str, prompt: str, schema: dict) -> tuple:
    """What decides whether two runs' results are interchangeable."""
    return (kb_id, pile_id, id_field, prompt, json.dumps(schema, sort_keys=True))


def _unique(ids: list[str]) -> list[str]:
    return list(dict.fromkeys(i for i in ids if i))


def create_batch_router(db_dir, analyze_one: AnalyzeOne) -> APIRouter:
    """
    Args:
        db_dir:      Directory holding user_config.db.
        analyze_one: rag_router's per-document analysis — async
                     (AnalyzeDocumentRequest, *, owner_id, role) -> AnalyzeDocumentResponse,
                     raising HTTPException on failure.
    """
    router = APIRouter(prefix="/api/v1/batch")
    sqlite_path = db_dir / "user_config.db"
    init_db(sqlite_path)
    interrupted = interrupt_running_batch_jobs(sqlite_path)
    if interrupted:
        log.info(f"Marked {interrupted} batch job(s) interrupted by the restart")

    # Owners whose job task is alive in this process. The database status alone
    # cannot guard a double click: both requests would read 'not running'.
    running: set[str] = set()

    async def _run(owner_id: str, role: str) -> None:
        try:
            job = get_batch_job(sqlite_path, owner_id)
            if job is None:
                return
            finished = {r["ident"] for r in job["results"] if not r["status"].startswith("error")}
            for ident in (i for i in job["ids"] if i not in finished):
                cancel = batch_cancel_requested(sqlite_path, owner_id)
                if cancel is None:
                    return  # discarded mid-run
                if cancel:
                    finish_batch_job(sqlite_path, owner_id, "cancelled")
                    return
                set_batch_current(sqlite_path, owner_id, ident)
                t0 = time.monotonic()
                req = AnalyzeDocumentRequest(
                    **{job["id_field"]: ident},
                    prompt=job["prompt"],
                    response_schema=job["schema"],
                    kb_id=job["kb_id"],
                    pile_id=job["pile_id"],
                )
                try:
                    resp = await analyze_one(req, owner_id=owner_id, role=role)
                    status = resp.skipped or "ok"
                    chunk_count, char_count, result = resp.chunk_count, resp.char_count, resp.result
                except HTTPException as exc:
                    status = f"error:HTTP {exc.status_code} {exc.detail}"[:300]
                    chunk_count, char_count, result = 0, 0, None
                except Exception as exc:
                    log.exception(f"Batch job ({owner_id}): analysis of {ident!r} failed")
                    status = f"error:{type(exc).__name__}"
                    chunk_count, char_count, result = 0, 0, None
                add_batch_result(
                    sqlite_path,
                    owner_id,
                    ident,
                    status=status,
                    chunk_count=chunk_count,
                    char_count=char_count,
                    result=result,
                    elapsed_s=round(time.monotonic() - t0, 2),
                )
            if batch_cancel_requested(sqlite_path, owner_id) is not None:
                finish_batch_job(sqlite_path, owner_id, "done")
        except Exception as exc:
            # Class name only — the message can carry a connection string or a path.
            log.exception(f"Batch job ({owner_id}) failed")
            finish_batch_job(sqlite_path, owner_id, "failed", type(exc).__name__)
        finally:
            running.discard(owner_id)

    # ── Question sets ─────────────────────────────────────────────────────────

    @router.get("/question-sets")
    async def get_question_sets(request: Request) -> list[dict]:
        return list_question_sets(sqlite_path, pile_owner_id(request))

    @router.put("/question-sets/{name:path}")
    async def put_question_set(name: str, body: QuestionSetBody, request: Request) -> dict:
        name = name.strip()
        if not name or len(name) > 100:
            raise HTTPException(400, "Name must be 1 to 100 characters")
        save_question_set(sqlite_path, pile_owner_id(request), name, body.rows)
        return {"saved": name}

    @router.delete("/question-sets/{name:path}")
    async def remove_question_set(name: str, request: Request) -> dict:
        if not delete_question_set(sqlite_path, pile_owner_id(request), name):
            raise HTTPException(404, "Question set not found")
        return {"deleted": name}

    # ── Job ───────────────────────────────────────────────────────────────────

    @router.get("/job")
    async def get_job(request: Request) -> dict | None:
        return get_batch_job(sqlite_path, pile_owner_id(request))

    @router.post("/job")
    async def start_job(req: BatchJobRequest, request: Request, background_tasks: BackgroundTasks) -> dict:
        owner_id = pile_owner_id(request)
        if owner_id in running:
            raise HTTPException(409, "A batch job is already running — stop it first")
        ids = _unique(req.ids)
        existing = get_batch_job(sqlite_path, owner_id)
        same = existing is not None and _definition(
            existing["kb_id"], existing["pile_id"], existing["id_field"], existing["prompt"], existing["schema"]
        ) == _definition(req.kb_id, req.pile_id, req.id_field, req.prompt, req.response_schema)

        if same:
            # Keep the existing order and append new identifiers, so a CSV exported
            # earlier and one exported now line up row for row.
            resume_batch_job(sqlite_path, owner_id, _unique(existing["ids"] + ids), req.questions)
        else:
            if existing is not None and existing["results"] and not req.replace:
                raise HTTPException(
                    409,
                    "The questions or source differ from the existing job; "
                    "starting replaces its results. Resend with replace=true to confirm.",
                )
            replace_batch_job(
                sqlite_path,
                owner_id,
                kb_id=req.kb_id,
                pile_id=req.pile_id,
                id_field=req.id_field,
                prompt=req.prompt,
                schema=req.response_schema,
                questions=req.questions,
                ids=ids,
            )

        running.add(owner_id)
        background_tasks.add_task(_run, owner_id, request.headers.get("x-user-role", "user"))
        return get_batch_job(sqlite_path, owner_id) or {}

    @router.post("/job/cancel")
    async def cancel_job(request: Request) -> dict:
        if not request_batch_cancel(sqlite_path, pile_owner_id(request)):
            raise HTTPException(409, "No batch job is running")
        return {"cancelling": True}

    @router.delete("/job")
    async def discard_job(request: Request) -> dict:
        owner_id = pile_owner_id(request)
        if owner_id in running:
            raise HTTPException(409, "Stop the running job before discarding it")
        delete_batch_job(sqlite_path, owner_id)
        return {"deleted": True}

    return router
