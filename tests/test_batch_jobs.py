"""Intent: a batch run lives on the server, survives the browser, and only redoes what it must.

The contract pinned down here:

  - a job analyzes every identifier and records each result, in order
  - rerunning the same definition skips finished documents and retries failed ones,
    so adding a document and pressing Run analyzes just that document
  - a different definition (questions, source, id field) would turn the existing
    answers into answers to other questions, so it is refused until confirmed and
    then replaces the job
  - a cancel stops the job between documents
  - a job left running by a restart is marked interrupted and resumes where it stopped
  - jobs and question sets are per owner: another session sees neither
"""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from lancy.batch_router import create_batch_router
from lancy.database import get_batch_job, interrupt_running_batch_jobs, replace_batch_job, request_batch_cancel
from lancy.rag_router import AnalyzeDocumentResponse

ME = {"x-session-id": "session-aaa", "x-user-role": "user"}
THEM = {"x-session-id": "session-bbb"}

SCHEMA = {"type": "object", "properties": {"question1": {"type": "string"}}, "required": ["question1"]}


def body(ids, prompt="Q1", **extra):
    return {"ids": ids, "prompt": prompt, "response_schema": SCHEMA, "questions": [{"question": prompt}], **extra}


@pytest.fixture
def ctx(tmp_path):
    state: dict = {"calls": [], "fail": set(), "on_call": None}

    async def analyze_one(req, *, owner_id, role):
        ident = req.source_file or req.document_id
        state["calls"].append({"ident": ident, "owner": owner_id, "role": role, "kb": req.kb_id, "pile": req.pile_id})
        if state["on_call"]:
            state["on_call"](ident)
        if ident in state["fail"]:
            raise HTTPException(status_code=502, detail="LLM call failed: boom")
        skipped = "over_budget" if ident.startswith("big") else None
        return AnalyzeDocumentResponse(
            document_id=None,
            source_file=ident,
            chunk_count=3,
            char_count=100,
            result=None if skipped else {"question1": f"answer for {ident}"},
            skipped=skipped,
        )

    def build() -> TestClient:
        app = FastAPI()
        app.include_router(create_batch_router(tmp_path, analyze_one))
        return TestClient(app)

    state["build"] = build
    state["client"] = build()
    state["db"] = tmp_path / "user_config.db"
    return state


def _idents(ctx):
    return [c["ident"] for c in ctx["calls"]]


def test_job_analyzes_every_document_and_records_results(ctx):
    resp = ctx["client"].post("/api/v1/batch/job", json=body(["a.pdf", "big.pdf", "c.pdf"]), headers=ME)
    assert resp.status_code == 200, resp.text

    job = ctx["client"].get("/api/v1/batch/job", headers=ME).json()
    assert job["status"] == "done"
    assert [(r["ident"], r["status"]) for r in job["results"]] == [
        ("a.pdf", "ok"),
        ("big.pdf", "over_budget"),
        ("c.pdf", "ok"),
    ]
    assert job["results"][0]["result"] == {"question1": "answer for a.pdf"}
    assert job["questions"] == [{"question": "Q1"}]


def test_job_passes_source_and_identity_to_the_analysis(ctx):
    ctx["client"].post("/api/v1/batch/job", json=body(["a.pdf"], kb_id="other"), headers=ME)
    assert ctx["calls"] == [{"ident": "a.pdf", "owner": "session-aaa", "role": "user", "kb": "other", "pile": None}]


def test_failure_is_recorded_per_document_and_the_job_continues(ctx):
    ctx["fail"] = {"a.pdf"}
    ctx["client"].post("/api/v1/batch/job", json=body(["a.pdf", "b.pdf"]), headers=ME)
    job = ctx["client"].get("/api/v1/batch/job", headers=ME).json()
    assert job["status"] == "done"
    assert job["results"][0]["status"] == "error:HTTP 502 LLM call failed: boom"
    assert job["results"][1]["status"] == "ok"


def test_rerun_analyzes_only_new_and_failed_documents(ctx):
    ctx["fail"] = {"b.pdf"}
    ctx["client"].post("/api/v1/batch/job", json=body(["a.pdf", "b.pdf"]), headers=ME)
    ctx["calls"].clear()
    ctx["fail"] = set()

    ctx["client"].post("/api/v1/batch/job", json=body(["a.pdf", "b.pdf", "new.pdf"]), headers=ME)

    assert _idents(ctx) == ["b.pdf", "new.pdf"]
    job = ctx["client"].get("/api/v1/batch/job", headers=ME).json()
    assert job["ids"] == ["a.pdf", "b.pdf", "new.pdf"]
    assert {r["ident"]: r["status"] for r in job["results"]} == {"a.pdf": "ok", "b.pdf": "ok", "new.pdf": "ok"}


def test_changed_questions_need_confirmation_then_replace_the_job(ctx):
    ctx["client"].post("/api/v1/batch/job", json=body(["a.pdf"]), headers=ME)
    ctx["calls"].clear()

    refused = ctx["client"].post("/api/v1/batch/job", json=body(["a.pdf"], prompt="Q2"), headers=ME)
    assert refused.status_code == 409
    assert ctx["calls"] == []

    ok = ctx["client"].post("/api/v1/batch/job", json=body(["a.pdf"], prompt="Q2", replace=True), headers=ME)
    assert ok.status_code == 200
    assert _idents(ctx) == ["a.pdf"]
    job = ctx["client"].get("/api/v1/batch/job", headers=ME).json()
    assert job["prompt"] == "Q2"
    assert len(job["results"]) == 1


def test_changed_source_counts_as_a_different_job(ctx):
    ctx["client"].post("/api/v1/batch/job", json=body(["a.pdf"]), headers=ME)
    resp = ctx["client"].post("/api/v1/batch/job", json=body(["a.pdf"], kb_id="other"), headers=ME)
    assert resp.status_code == 409


def test_cancel_stops_between_documents(ctx):
    ctx["on_call"] = lambda ident: request_batch_cancel(ctx["db"], "session-aaa")
    ctx["client"].post("/api/v1/batch/job", json=body(["a.pdf", "b.pdf", "c.pdf"]), headers=ME)

    job = ctx["client"].get("/api/v1/batch/job", headers=ME).json()
    assert job["status"] == "cancelled"
    assert _idents(ctx) == ["a.pdf"]


def test_cancel_without_a_running_job_is_409(ctx):
    assert ctx["client"].post("/api/v1/batch/job/cancel", headers=ME).status_code == 409


def _running_job(db):
    replace_batch_job(
        db,
        "session-aaa",
        kb_id=None,
        pile_id=None,
        id_field="source_file",
        prompt="Q1",
        schema=SCHEMA,
        questions=[{"question": "Q1"}],
        ids=["a.pdf", "b.pdf"],
    )


def test_building_the_router_leaves_a_running_job_alone(ctx):
    """Spawned chunking workers re-import lancy.main and rebuild every router while
    the server runs, so construction must not touch a job that is in progress."""
    _running_job(ctx["db"])
    ctx["build"]()  # what a spawned chunking worker does on import
    assert get_batch_job(ctx["db"], "session-aaa")["status"] == "running"


def test_startup_reset_marks_running_job_interrupted_and_resume_continues(ctx):
    _running_job(ctx["db"])
    assert interrupt_running_batch_jobs(ctx["db"]) == 1  # main.py's startup hook

    assert get_batch_job(ctx["db"], "session-aaa")["status"] == "interrupted"
    ctx["client"].post("/api/v1/batch/job", json=body(["a.pdf", "b.pdf"]), headers=ME)
    assert _idents(ctx) == ["a.pdf", "b.pdf"]
    assert ctx["client"].get("/api/v1/batch/job", headers=ME).json()["status"] == "done"


def test_discard_removes_job_and_results(ctx):
    ctx["client"].post("/api/v1/batch/job", json=body(["a.pdf"]), headers=ME)
    assert ctx["client"].delete("/api/v1/batch/job", headers=ME).status_code == 200
    assert ctx["client"].get("/api/v1/batch/job", headers=ME).json() is None


def test_job_is_invisible_to_another_owner(ctx):
    ctx["client"].post("/api/v1/batch/job", json=body(["a.pdf"]), headers=ME)
    assert ctx["client"].get("/api/v1/batch/job", headers=THEM).json() is None


def test_question_sets_save_overwrite_list_delete(ctx):
    c = ctx["client"]
    rows = [{"question": "Augenschutz?", "type": "text"}]
    assert c.put("/api/v1/batch/question-sets/SDB", json={"rows": rows}, headers=ME).status_code == 200
    c.put("/api/v1/batch/question-sets/SDB", json={"rows": rows * 2}, headers=ME)
    c.put("/api/v1/batch/question-sets/alpha", json={"rows": rows}, headers=ME)

    sets = c.get("/api/v1/batch/question-sets", headers=ME).json()
    assert [s["name"] for s in sets] == ["alpha", "SDB"]
    assert len(sets[1]["rows"]) == 2

    assert c.get("/api/v1/batch/question-sets", headers=THEM).json() == []
    assert c.delete("/api/v1/batch/question-sets/SDB", headers=THEM).status_code == 404
    assert c.delete("/api/v1/batch/question-sets/SDB", headers=ME).status_code == 200
    assert [s["name"] for s in c.get("/api/v1/batch/question-sets", headers=ME).json()] == ["alpha"]


def test_question_set_name_must_not_be_blank(ctx):
    resp = ctx["client"].put("/api/v1/batch/question-sets/%20", json={"rows": [{"question": "x"}]}, headers=ME)
    assert resp.status_code == 400


def test_question_set_name_may_contain_a_slash(ctx):
    c = ctx["client"]
    assert (
        c.put("/api/v1/batch/question-sets/SDB%2FPSA", json={"rows": [{"question": "x"}]}, headers=ME).status_code
        == 200
    )
    assert [s["name"] for s in c.get("/api/v1/batch/question-sets", headers=ME).json()] == ["SDB/PSA"]
    assert c.delete("/api/v1/batch/question-sets/SDB%2FPSA", headers=ME).status_code == 200
