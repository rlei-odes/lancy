#!/usr/bin/env python3
"""Lancy — live smoke test against a running dev deployment.

The unit suite runs entirely against fakes. That is deliberate, but it means a
dependency bump is exactly the class of breakage it cannot see: a vector store
client whose signature moved, a pydantic release that serialises differently, an
httpx update that changes streaming. Everything stays green while the running
system is broken. This exercises the real thing end to end.

What it does, through the frontend proxy so middleware auth is covered too:

    1. probe the deployment and record what it is running
    2. check the auth boundary (anonymous is refused, secrets are masked)
    3. create a throwaway KB, matching the pool's embedding model
    4. upload documents into it and wait for ingestion
    5. describe what chunking produced, and flag degenerate shapes
    6. prove bm25 and reranking actually change retrieval, and measure
       expected-source recall for each variant
    7. round-trip hyde and query_expansion through the config API
    8. ask the ground-truth questions from data/EVALUATION_qa_ground_truth.md
    9. exercise filters and expand_context
   10. repeat for each configured store (chromadb, pgvector)
   11. restore the original active KB and session config, delete what it created
   12. write a markdown report

Structural problems (bad status, malformed response, a leaked secret, a KB that
would not build) fail the run. Answer content never does: an LLM phrases things
differently every time, and a suite that cries wolf stops being read. Content
checks are reported for a human to judge.

DEV ONLY. It changes the active KB while it runs and deletes the KBs it made.

    cp scripts/live-test.config.example.json scripts/live-test.config.json
    scripts/live-test.py
    scripts/live-test.py --keep        # leave the test KBs behind to poke at
    scripts/live-test.py --quick       # one question instead of the full set
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO / "scripts" / "live-test.config.json"

# Every KB this script creates carries this prefix, and it refuses to delete
# anything without it. A typo in a config must not be able to drop a real KB.
TEST_KB_PREFIX = "zz-livetest-"

PASS, FAIL, WARN, INFO = "PASS", "FAIL", "WARN", "INFO"


# ─── Report ───────────────────────────────────────────────────────────────────


class Report:
    """Collects results. Structural checks gate the exit code; content does not."""

    def __init__(self) -> None:
        self.started = datetime.now(timezone.utc)
        self.environment: dict = {}
        self.checks: list[dict] = []
        self.answers: list[dict] = []
        self.sections: list[str] = []

    def check(self, status: str, name: str, detail: str = "", *, gating: bool = True) -> bool:
        self.checks.append(
            {"status": status, "name": name, "detail": detail, "gating": gating}
        )
        icon = {PASS: "  ok  ", FAIL: " FAIL ", WARN: " warn ", INFO: "  ..  "}[status]
        print(f"[{icon}] {name}" + (f" — {detail}" if detail else ""), flush=True)
        return status != FAIL

    def expect(self, name: str, ok: bool, detail: str = "", *, fail_detail: str = "") -> bool:
        """`detail` is shown either way; `fail_detail` only when the check fails."""
        return self.check(PASS if ok else FAIL, name, detail if ok else (fail_detail or detail))

    def note(self, name: str, ok: bool, detail: str = "") -> None:
        """A content observation: recorded and shown, but never fails the run."""
        self.check(PASS if ok else WARN, name, detail, gating=False)

    @property
    def failed(self) -> list[dict]:
        return [c for c in self.checks if c["status"] == FAIL and c["gating"]]

    @property
    def warned(self) -> list[dict]:
        return [c for c in self.checks if c["status"] == WARN]

    def write(self, path: Path) -> Path:
        lines = [
            "# Lancy live test",
            "",
            f"Run {self.started.isoformat(timespec='seconds')} · "
            f"{len(self.checks)} checks · {len(self.failed)} failed · {len(self.warned)} to review",
            "",
            "## Environment",
            "",
            "| | |",
            "|---|---|",
        ]
        lines += [f"| {k} | {v} |" for k, v in self.environment.items()]

        lines += ["", "## Checks", "", "| | Check | Detail |", "|---|---|---|"]
        for c in self.checks:
            mark = {PASS: "ok", FAIL: "**FAIL**", WARN: "review", INFO: "·"}[c["status"]]
            detail = str(c["detail"]).replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {mark} | {c['name']} | {detail} |")

        if self.answers:
            lines += [
                "",
                "## Answers",
                "",
                "Read these. The checks above confirm the pipeline responded; only you",
                "can tell whether it responded *well*. Each question links to the",
                "expected answer and documented failure mode in",
                "`data/EVALUATION_qa_ground_truth.md`.",
                "",
            ]
            for a in self.answers:
                lines += [
                    f"### {a['id']} · {a['store']}",
                    "",
                    f"**Question:** {a['question']}",
                    "",
                    f"*Watch for:* {a.get('why', '—')}",
                    "",
                    f"*Latency:* {a['latency']:.1f}s · *Sources cited:* "
                    f"{', '.join(a['sources']) if a['sources'] else 'none'}",
                    "",
                    "```",
                    a["answer"].strip() or "(empty)",
                    "```",
                    "",
                ]
                if a.get("misses"):
                    lines += [f"> Content notes: {'; '.join(a['misses'])}", ""]

        lines += self.sections
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")
        return path


# ─── Client ───────────────────────────────────────────────────────────────────


class Lancy:
    def __init__(self, base_url: str, token: str, timeout: float) -> None:
        self.base = base_url.rstrip("/")
        self.auth = {"Authorization": f"Bearer {token}"} if token else {}
        self.http = httpx.Client(timeout=timeout, follow_redirects=False)

    def get(self, path: str, *, anon: bool = False, **kw) -> httpx.Response:
        return self.http.get(
            f"{self.base}{path}", headers={} if anon else self.auth, **kw
        )

    def post(self, path: str, *, anon: bool = False, **kw) -> httpx.Response:
        return self.http.post(
            f"{self.base}{path}", headers={} if anon else self.auth, **kw
        )

    def delete(self, path: str, **kw) -> httpx.Response:
        return self.http.delete(f"{self.base}{path}", headers=self.auth, **kw)

    def ask(self, question: str, model: str, **extra) -> httpx.Response:
        body = {"model": model, "messages": [{"role": "user", "content": question}]}
        body.update(extra)
        return self.post("/v1/chat/completions", json=body)

    def close(self) -> None:
        self.http.close()


# ─── Phases ───────────────────────────────────────────────────────────────────


def probe(api: Lancy, rep: Report) -> tuple[dict, dict]:
    """Record what is running, and return (pool status, active KB config)."""
    pool = api.get("/api/v1/kb/pool")
    if not rep.expect("pool status reachable", pool.status_code == 200, f"HTTP {pool.status_code}"):
        raise SystemExit("cannot reach the deployment — is it running?")
    pool = pool.json()

    registry = api.get("/api/v1/kb")
    rep.expect("KB registry reachable", registry.status_code == 200, f"HTTP {registry.status_code}")
    registry = registry.json()

    cfg = api.get("/api/v1/rag/config")
    cfg = cfg.json() if cfg.status_code == 200 else {}

    active_id = registry.get("active", "")
    active = registry.get("bases", {}).get(active_id, {})
    emb = pool.get("emb_key") or {}

    rep.environment = {
        "base_url": api.base,
        "active KB": f"{active.get('name', '?')} (`{active_id}`)",
        "active store": active.get("vs_type", "?"),
        "loaded in pool": ", ".join(pool.get("loaded", [])) or "none",
        "embedding": f"{emb.get('backend', '?')} / {emb.get('model', '?')}",
        "LLM": f"{cfg.get('llm_backend', '?')} / {cfg.get('llm_model', '?')}",
        "retriever_top_k": cfg.get("retriever_top_k", "?"),
        "bm25": cfg.get("bm25_enabled", "?"),
        "reranking": cfg.get("reranking_enabled", "?"),
    }
    rep.check(INFO, "deployment probed", f"active={active_id}, store={active.get('vs_type')}")
    return pool, active


def check_auth_boundary(api: Lancy, rep: Report) -> None:
    """The security boundary: anonymous is refused, and secrets never ship."""
    anon = api.get("/api/v1/kb", anon=True)
    rep.expect(
        "anonymous read is refused",
        anon.status_code in (401, 403),
        f"HTTP {anon.status_code} (expected 401/403)",
    )

    anon_write = api.post("/api/v1/kb", anon=True, json={"name": "should-never-exist"})
    rep.expect(
        "anonymous KB creation is refused",
        anon_write.status_code in (401, 403),
        f"HTTP {anon_write.status_code} (expected 401/403)",
    )

    body = api.get("/api/v1/kb").text
    leaked = [
        m
        for m in ("postgresql://", "postgres://")
        if m in body and "***" not in body.split(m, 1)[1][:120]
    ]
    rep.expect(
        "KB API masks stored credentials",
        not leaked,
        "a connection string appears unmasked" if leaked else "passwords render as ***",
    )


def kb_payload(active: dict, name: str, vs_type: str, conn: str = "") -> dict:
    """A test KB mirroring the active KB, differing only in name and store.

    The embedding backend/model must match what the pool already holds, or
    activating this KB raises EmbeddingConflict and would evict the real one.
    """
    inherit = (
        "embedding_backend", "embedding_model", "embedding_ollama_host",
        "embedding_custom_base_url", "nomic_prefix", "max_file_size_mb",
        "embedding_batch_size", "pdf_ocr_enabled", "max_chunk_tokens",
    )
    payload = {k: active[k] for k in inherit if k in active}
    payload.update({"name": name, "vs_type": vs_type, "vs_connection_string": conn})
    return payload


def wait_for_ingestion(api: Lancy, rep: Report, expected: list[str], timeout: float) -> bool:
    """Poll the active KB's store until every uploaded file has chunks.

    Uploads are background tasks with no completion signal, so the store itself
    is the only honest source of truth.
    """
    deadline = time.time() + timeout
    seen: set[str] = set()
    last_report = 0.0
    while time.time() < deadline:
        info = api.get("/api/v1/rag/store-info")
        if info.status_code == 200:
            data = info.json()
            seen = set(data.get("file_list", []))
            if seen >= set(expected):
                rep.check(
                    INFO, "ingestion finished",
                    f"{data.get('chunks', 0)} chunks from {data.get('files', 0)} files",
                )
                return True
            if time.time() - last_report > 20:
                print(f"         … {len(seen)}/{len(expected)} files indexed", flush=True)
                last_report = time.time()
        time.sleep(3)
    rep.check(FAIL, "ingestion finished", f"timed out; missing {sorted(set(expected) - seen)}")
    return False


def grade(answer: str, sources: list[str], spec: dict) -> list[str]:
    """Content observations. Never fatal — see the module docstring."""
    low = answer.lower()
    misses = []
    for s in spec.get("expect_all", []):
        if s.lower() not in low:
            misses.append(f"missing {s!r}")
    anys = spec.get("expect_any", [])
    if anys and not any(s.lower() in low for s in anys):
        misses.append(f"none of {anys} present")
    for s in spec.get("forbid", []):
        if s.lower() in low:
            misses.append(f"contains forbidden {s!r}")
    cited = " ".join(sources).lower()
    for s in spec.get("expect_sources", []):
        if s.lower() not in cited and s.lower() not in low:
            misses.append(f"did not cite {s}")
    return misses


def parse_answer(payload: dict) -> tuple[str, list[str]]:
    content = payload["choices"][0]["message"]["content"]
    body, _, tail = content.partition("**Sources:**")
    sources = [
        line.lstrip("- ").strip()
        for line in tail.splitlines()
        if line.strip().startswith("-")
    ]
    return body.strip(), sources


def run_questions(api: Lancy, rep: Report, model: str, store: str, qs: list[dict]) -> None:
    for spec in qs:
        started = time.time()
        try:
            r = api.ask(spec["question"], model)
        except httpx.RequestError as exc:
            rep.expect(f"{store}: {spec['id']} answered", False, f"request failed: {exc}")
            continue
        latency = time.time() - started

        if not rep.expect(
            f"{store}: {spec['id']} answered", r.status_code == 200, f"HTTP {r.status_code}"
        ):
            continue
        answer, sources = parse_answer(r.json())
        rep.expect(f"{store}: {spec['id']} is not empty", bool(answer.strip()))

        misses = grade(answer, sources, spec)
        rep.note(
            f"{store}: {spec['id']} content",
            not misses,
            "; ".join(misses) if misses else f"{len(sources)} sources, {latency:.1f}s",
        )
        rep.answers.append(
            {
                "id": spec["id"], "store": store, "question": spec["question"],
                "why": spec.get("why", ""), "answer": answer, "sources": sources,
                "latency": latency, "misses": misses,
            }
        )


def run_probes(api: Lancy, rep: Report, model: str, store: str, probes: list[dict]) -> None:
    """filters and expand_context — the Lancy extensions to the OpenAI schema."""
    for spec in probes:
        extra = (
            {"expand_context": spec["documents"]}
            if spec["mode"] == "expand_context"
            else {"filters": spec["filters"]}
        )
        started = time.time()
        r = api.ask(spec["question"], model, **extra)
        latency = time.time() - started
        if not rep.expect(
            f"{store}: {spec['id']} accepted", r.status_code == 200, f"HTTP {r.status_code}"
        ):
            continue
        answer, sources = parse_answer(r.json())
        misses = grade(answer, sources, spec)
        rep.note(
            f"{store}: {spec['id']} content", not misses,
            "; ".join(misses) if misses else f"{len(sources)} sources, {latency:.1f}s",
        )
        rep.answers.append(
            {
                "id": spec["id"], "store": store, "question": spec["question"],
                "why": spec.get("why", ""), "answer": answer, "sources": sources,
                "latency": latency, "misses": misses,
            }
        )

    # Combining the two is refused, because expand_context bypasses retrieval.
    clash = api.ask("anything", model, filters={"a": "b"}, expand_context=["x.pdf"])
    rep.expect(
        f"{store}: filters+expand_context refused",
        clash.status_code == 400,
        f"HTTP {clash.status_code} (expected 400)",
    )


# Retrieval variants. bm25 and reranking are per-request overrides on
# /rag/retrieve, so these need no global config change.
RETRIEVE_VARIANTS = [
    ("semantic only", {"bm25_enabled": False, "reranking_enabled": False}),
    ("hybrid bm25+rrf", {"bm25_enabled": True, "reranking_enabled": False}),
    ("hybrid + reranking", {"bm25_enabled": True, "reranking_enabled": True}),
]


def sources_of(chunks: list[dict]) -> list[str]:
    return [c.get("metadata", {}).get("source_file", "?") for c in chunks]


def check_settings(api: Lancy, rep: Report, store: str, questions: list[dict]) -> None:
    """Prove each retrieval setting actually changes what comes back.

    The scores in the response make this checkable rather than merely plausible:
    bm25 populates bm25_score/rrf_score, reranking populates pre_rerank_rank. A
    setting that is wired up but inert shows as identical output, which is the
    failure mode worth catching after a dependency bump.
    """
    probe_q = questions[0]["question"]
    baseline_ids: list[str] = []
    recall: dict[str, str] = {}

    for label, opts in RETRIEVE_VARIANTS:
        r = api.post("/api/v1/rag/retrieve", json={"query": probe_q, **opts})
        if not rep.expect(
            f"{store}: retrieve [{label}]", r.status_code == 200, f"HTTP {r.status_code}"
        ):
            continue
        data = r.json()
        chunks = data.get("chunks", [])
        if not rep.expect(f"{store}: [{label}] returned chunks", bool(chunks), f"{len(chunks)} chunks"):
            continue

        has_bm25 = any(c["scores"].get("bm25_score") is not None for c in chunks)
        has_rerank = any(c["scores"].get("pre_rerank_rank") is not None for c in chunks)

        if opts["bm25_enabled"]:
            rep.expect(f"{store}: [{label}] bm25 scored the results", has_bm25)
        else:
            rep.expect(
                f"{store}: [{label}] bm25 is genuinely off", not has_bm25,
                "bm25_score present despite bm25_enabled=false" if has_bm25 else "",
            )

        if opts["reranking_enabled"]:
            rep.expect(
                f"{store}: [{label}] reranking ran", has_rerank and not data.get("reranking_skipped"),
                f"skipped={data.get('reranking_skipped')}, pre_rerank_rank present={has_rerank}",
            )
            moved = [c for c in chunks if c["scores"].get("pre_rerank_rank") != c["final_rank"]]
            rep.note(
                f"{store}: [{label}] reranking reordered results", bool(moved),
                f"{len(moved)}/{len(chunks)} chunks moved"
                if moved else "order identical to pre-rerank — reranker had no effect here",
            )

        ids = [c["id"] for c in chunks]
        if label == "semantic only":
            baseline_ids = ids
        else:
            differing = set(ids) ^ set(baseline_ids)
            if differing:
                detail = f"{len(differing)} chunks differ from semantic only"
            elif ids != baseline_ids:
                detail = "same chunks as semantic only, reordered"
            else:
                detail = "identical to semantic only — setting had no effect"
            rep.note(f"{store}: [{label}] changed the result set", ids != baseline_ids, detail)

        # Recall of the expected sources, per variant — the number that says
        # whether a setting helps rather than merely does something.
        hits = 0
        wanted = 0
        for q in questions:
            expected = q.get("expect_sources", [])
            if not expected:
                continue
            rr = api.post("/api/v1/rag/retrieve", json={"query": q["question"], **opts})
            if rr.status_code != 200:
                continue
            found = set(sources_of(rr.json().get("chunks", [])))
            wanted += len(expected)
            hits += sum(1 for e in expected if e in found)
        recall[label] = f"{hits}/{wanted}" if wanted else "n/a"

    rep.sections += [
        "",
        f"## Retrieval settings · {store}",
        "",
        "Expected-source recall across the ground-truth questions. Not a quality",
        "score — it counts whether the document the answer should rest on was",
        "retrieved at all.",
        "",
        "| Variant | Expected sources retrieved |",
        "|---|---|",
    ] + [f"| {k} | {v} |" for k, v in recall.items()]


def check_session_settings(api: Lancy, rep: Report, store: str, model: str, question: str) -> None:
    """hyde and query_expansion: verify they persist, and show their effect.

    These cannot be A/B'd through /rag/retrieve — that endpoint embeds the query
    directly and never runs the agent's preprocessing, so both settings would
    always look inert. They apply only on the chat path, where the answer is
    non-deterministic. So: assert the round-trip through the config API (which
    does catch config plumbing breaking), and put the two answers side by side
    in the report for a human to compare.
    """
    original = api.get("/api/v1/rag/config")
    if original.status_code != 200:
        rep.check(WARN, f"{store}: session settings", "could not read config", gating=False)
        return
    original = original.json()

    def apply(**overrides) -> bool:
        r = api.post("/api/v1/rag/config", json={**original, **overrides})
        if r.status_code != 200:
            return False
        time.sleep(4)  # the agent rebuild runs as a background task
        got = api.get("/api/v1/rag/config")
        return got.status_code == 200 and all(
            got.json().get(k) == v for k, v in overrides.items()
        )

    variants = [
        ("baseline", {"hyde_enabled": False, "query_expansion": 0}),
        ("hyde", {"hyde_enabled": True, "query_expansion": 0}),
        ("query_expansion=2", {"hyde_enabled": False, "query_expansion": 2}),
    ]
    observed: list[tuple[str, list[str]]] = []
    try:
        for label, overrides in variants:
            rep.expect(
                f"{store}: {label} settings persisted",
                apply(**overrides),
                fail_detail="config did not round-trip through the API",
            )
            r = api.ask(question, model)
            if not rep.expect(
                f"{store}: {label} query answered", r.status_code == 200, f"HTTP {r.status_code}"
            ):
                continue
            _, sources = parse_answer(r.json())
            observed.append((label, sources))
    finally:
        r = api.post("/api/v1/rag/config", json=original)
        rep.expect(f"{store}: session config restored", r.status_code == 200, f"HTTP {r.status_code}")
        time.sleep(3)

    if observed:
        rep.sections += [
            "",
            f"## Preprocessing settings · {store}",
            "",
            "hyde and query expansion act on the chat path only, so their effect",
            "shows up as different sources being cited. LLM output varies between",
            "runs, so a difference here is an observation, not a verdict.",
            "",
            "| Setting | Sources cited |",
            "|---|---|",
        ] + [
            f"| {label} | {', '.join(s) if s else 'none'} |".replace("|", "\\|", 0)
            for label, s in observed
        ]


# Chunking thresholds. Deliberately crude — a starting point to expand, not a
# claim about what good chunking is.
TINY_CHUNK_CHARS = 100
TINY_CHUNK_RATIO = 0.20
DUPLICATE_RATIO = 0.05


def check_chunking(api: Lancy, rep: Report, store: str, uploaded: list[str]) -> None:
    """Describe what ingestion actually produced, and flag degenerate shapes."""
    chunks: list[dict] = []
    offset = 0
    while True:
        r = api.post("/api/v1/rag/chunks", json={"filters": [], "limit": 200, "offset": offset})
        if r.status_code != 200:
            rep.check(WARN, f"{store}: chunk browse", f"HTTP {r.status_code}", gating=False)
            return
        page = r.json()
        chunks += page.get("chunks", [])
        if not page.get("has_more") or len(chunks) > 5000:
            break
        offset += 200

    if not rep.expect(f"{store}: chunks are browsable", bool(chunks), f"{len(chunks)} chunks"):
        return

    by_file: dict[str, list[int]] = {}
    for c in chunks:
        by_file.setdefault(c.get("metadata", {}).get("source_file", "?"), []).append(
            len(c.get("content", ""))
        )

    # Every uploaded file must have produced something. A file that ingests to
    # zero chunks is invisible to retrieval while looking successfully indexed.
    missing = [f for f in uploaded if f not in by_file]
    rep.expect(
        f"{store}: every uploaded file produced chunks",
        not missing,
        f"no chunks for {missing}" if missing else f"{len(by_file)} files",
    )

    empty = [c for c in chunks if not c.get("content", "").strip()]
    rep.expect(f"{store}: no empty chunks", not empty, f"{len(empty)} empty")

    tiny = [n for sizes in by_file.values() for n in sizes if n < TINY_CHUNK_CHARS]
    ratio = len(tiny) / len(chunks)
    rep.note(
        f"{store}: few undersized chunks", ratio <= TINY_CHUNK_RATIO,
        f"{len(tiny)}/{len(chunks)} ({ratio:.0%}) under {TINY_CHUNK_CHARS} chars",
    )

    texts = [c.get("content", "").strip() for c in chunks]
    dupes = len(texts) - len(set(texts))
    rep.note(
        f"{store}: few duplicate chunks", dupes / len(chunks) <= DUPLICATE_RATIO,
        f"{dupes}/{len(chunks)} duplicates",
    )

    rows = []
    for f in sorted(by_file):
        sizes = sorted(by_file[f])
        median = sizes[len(sizes) // 2]
        small = sum(1 for n in sizes if n < TINY_CHUNK_CHARS)
        rows.append(
            f"| {f} | {len(sizes)} | {min(sizes)} | {median} | {max(sizes)} | {small} |"
        )

    rep.sections += [
        "",
        f"## Chunking · {store}",
        "",
        f"{len(chunks)} chunks from {len(by_file)} files. Character counts, not tokens.",
        "A file with very few chunks relative to its size, or a large share of",
        "undersized chunks, is worth opening in the Chunk Browser.",
        "",
        "| File | Chunks | Min | Median | Max | < 100 chars |",
        "|---|---|---|---|---|---|",
    ] + rows


def exercise_store(
    api: Lancy, rep: Report, cfg: dict, active: dict, store: str, conn: str, questions: dict
) -> str | None:
    """Build a throwaway KB on `store`, fill it, interrogate it. Returns its id."""
    name = f"{TEST_KB_PREFIX}{store}"
    created = api.post("/api/v1/kb", json=kb_payload(active, name, store, conn))
    # Only echo the body on failure: a KB record carries credential fields, and
    # this detail string ends up in the written report.
    if not rep.expect(
        f"{store}: KB created",
        created.status_code == 200,
        f"HTTP {created.status_code}" if created.status_code == 200 else f"HTTP {created.status_code} {created.text[:200]}",
    ):
        return None
    kb_id = created.json()["id"]
    if not kb_id.startswith(TEST_KB_PREFIX):
        rep.check(FAIL, f"{store}: KB id carries the test prefix", kb_id)
        return kb_id

    act = api.post(f"/api/v1/kb/{kb_id}/activate")
    if not rep.expect(
        f"{store}: KB activated",
        act.status_code == 200,
        f"HTTP {act.status_code}" if act.status_code == 200 else f"HTTP {act.status_code} {act.text[:200]}",
    ):
        return kb_id

    models = api.get("/v1/models")
    ids = [m["id"] for m in models.json().get("data", [])]
    rep.expect(f"{store}: KB is addressable via /v1/models", kb_id in ids, f"got {ids}")

    docs_dir = REPO / cfg["documents_dir"]
    uploaded = []
    for doc in cfg["documents"]:
        path = docs_dir / doc["file"]
        if not path.exists():
            rep.check(WARN, f"{store}: {doc['file']} present", "not found — skipped", gating=False)
            continue
        with path.open("rb") as fh:
            r = api.post(
                f"/api/v1/kb/{kb_id}/documents",
                files={"file": (path.name, fh, "application/octet-stream")},
                data={
                    "metadata": json.dumps(
                        {"document_id": f"livetest-{path.stem}", "livetest_group": doc["group"]}
                    )
                },
            )
        if rep.expect(f"{store}: uploaded {doc['file']}", r.status_code == 200, f"HTTP {r.status_code}"):
            uploaded.append(path.name)

    if not uploaded:
        rep.check(FAIL, f"{store}: anything to query", "no documents were accepted")
        return kb_id

    if not wait_for_ingestion(api, rep, uploaded, cfg["ingest_timeout_s"]):
        return kb_id

    check_chunking(api, rep, store, uploaded)
    check_settings(api, rep, store, questions["questions"])
    check_session_settings(api, rep, store, kb_id, questions["questions"][0]["question"])

    run_questions(api, rep, kb_id, store, questions["questions"])
    run_probes(api, rep, kb_id, store, questions.get("retrieval_probes", []))

    unknown = api.ask("hello", f"{TEST_KB_PREFIX}does-not-exist")
    rep.expect(
        f"{store}: unloaded KB is refused", unknown.status_code == 404,
        f"HTTP {unknown.status_code} (expected 404)",
    )
    return kb_id


# ─── Main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--keep", action="store_true", help="leave the test KBs in place")
    ap.add_argument("--quick", action="store_true", help="one question instead of all")
    ap.add_argument("--report", type=Path, default=None)
    args = ap.parse_args()

    if not args.config.exists():
        print(
            f"No config at {args.config}\n"
            f"  cp scripts/live-test.config.example.json {args.config}\n"
            f"then set base_url and bearer_token.",
            file=sys.stderr,
        )
        return 2
    cfg = json.loads(args.config.read_text())
    questions = json.loads((REPO / cfg["questions_file"]).read_text())
    if args.quick:
        questions["questions"] = questions["questions"][:1]
        questions["retrieval_probes"] = questions.get("retrieval_probes", [])[:1]

    api = Lancy(cfg["base_url"], cfg.get("bearer_token", ""), cfg["request_timeout_s"])
    rep = Report()
    created: list[str] = []
    original_active = None

    print("─── Lancy live test ─────────────────────────────────────────────────")
    print(f"    {cfg['base_url']} · DEV ONLY · creates and deletes KBs\n")
    try:
        pool, active = probe(api, rep)
        original_active = pool.get("active")
        check_auth_boundary(api, rep)

        for store, opts in cfg["stores"].items():
            if not opts.get("enabled"):
                rep.check(INFO, f"{store}: skipped", "disabled in config")
                continue
            if store == "pgvector" and not opts.get("connection_string"):
                rep.check(WARN, f"{store}: skipped", "no connection_string configured", gating=False)
                continue
            print(f"\n─── {store} ─────────────────────────────────────────────")
            kb_id = exercise_store(
                api, rep, cfg, active, store, opts.get("connection_string", ""), questions
            )
            if kb_id:
                created.append(kb_id)
    except KeyboardInterrupt:
        rep.check(FAIL, "run completed", "interrupted")
    except Exception as exc:  # noqa: BLE001 — the report must survive any failure
        rep.check(FAIL, "run completed", f"{type(exc).__name__}: {exc}")
    finally:
        print("\n─── cleanup ─────────────────────────────────────────────────")
        if original_active:
            r = api.post(f"/api/v1/kb/{original_active}/activate")
            rep.expect(
                f"restored active KB to {original_active}", r.status_code == 200,
                f"HTTP {r.status_code}",
            )
        if args.keep or cfg.get("keep_test_kbs"):
            rep.check(INFO, "test KBs kept", ", ".join(created) or "none")
        else:
            for kb_id in created:
                # Belt and braces: never delete anything this run did not create.
                if not kb_id.startswith(TEST_KB_PREFIX):
                    rep.check(FAIL, f"refused to delete {kb_id}", "missing test prefix")
                    continue
                r = api.delete(f"/api/v1/kb/{kb_id}")
                rep.expect(f"deleted {kb_id}", r.status_code == 200, f"HTTP {r.status_code}")

        stamp = rep.started.strftime("%Y%m%d-%H%M%S")
        path = args.report or REPO / "logs" / f"live-test-{stamp}.md"
        rep.write(path)
        api.close()

        print("\n─── summary ─────────────────────────────────────────────────")
        print(f"    {len(rep.checks)} checks · {len(rep.failed)} failed · {len(rep.warned)} to review")
        for c in rep.failed:
            print(f"    FAIL  {c['name']} — {c['detail']}")
        for c in rep.warned:
            print(f"    review  {c['name']} — {c['detail']}")
        print(f"\n    Report: {path}")
        print("    Read the Answers section — the checks prove it replied, not that it replied well.\n")

    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
