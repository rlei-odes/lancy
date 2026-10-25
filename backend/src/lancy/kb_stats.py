from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from conversational_toolkit.chunking.base import Chunk
from conversational_toolkit.vectorstores.base import VectorStore
from lancy.language import detect_document

log = logging.getLogger("uvicorn")

_STEP = 200
_MAX_VAL = 2000
_BUCKET_KEYS = [f"{i}-{i + _STEP}" for i in range(0, _MAX_VAL, _STEP)] + [f"{_MAX_VAL}+"]


def _size_buckets(chunks: list[Chunk]) -> dict[str, int]:
    buckets: dict[str, int] = dict.fromkeys(_BUCKET_KEYS, 0)
    for c in chunks:
        s = len(c.content)
        idx = min(s // _STEP, _MAX_VAL // _STEP)
        buckets[_BUCKET_KEYS[idx]] += 1
    return buckets


def _per_hash_entries(chunks: list[Chunk], now_iso: str) -> dict[str, dict]:
    per_hash: dict[str, dict] = {}
    for c in chunks:
        h = c.metadata.get("file_hash", "unknown")
        if h not in per_hash:
            per_hash[h] = {
                "source_file": c.metadata.get("source_file", "?"),
                "chunk_count": 0,
                "indexed_at": now_iso,
                # None: too little text to tell. An entry written before languages were
                # detected has no key at all, which is how backfill_languages finds it.
                "language": c.metadata.get("language"),
            }
        per_hash[h]["chunk_count"] += 1
    return per_hash


def _cpd_distribution(chunks_per_document: dict) -> dict[str, int]:
    """Tally chunk counts per source_file, bucket into integer keys 1–100 plus '100+'."""
    file_totals: dict[str, int] = {}
    for entry in chunks_per_document.values():
        sf = entry["source_file"]
        file_totals[sf] = file_totals.get(sf, 0) + entry["chunk_count"]

    dist: dict[str, int] = {str(i): 0 for i in range(1, 101)}
    dist["100+"] = 0
    for count in file_totals.values():
        if count <= 100:
            dist[str(count)] += 1
        else:
            dist["100+"] += 1
    return dist


def language_distribution(chunks_per_document: dict) -> dict[str, int]:
    """Documents per detected language code. A document indexed in several versions counts once."""
    by_file = {
        entry["source_file"]: entry["language"] for entry in chunks_per_document.values() if entry.get("language")
    }
    counts: dict[str, int] = {}
    for code in by_file.values():
        counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))


def read_language_distribution(db_dir: Path, kb_id: str) -> dict[str, int]:
    """The KB's documents per language from its stats file; empty when there is none yet."""
    stats_path = db_dir / f"kb_stats_{kb_id}.json"
    if not stats_path.exists():
        return {}
    return json.loads(stats_path.read_text()).get("language_distribution", {})


async def backfill_languages(db_dir: Path, kb_id: str, vs: VectorStore) -> int:
    """Detect the language of documents indexed before languages were, from their chunks in the store.

    Returns how many documents were detected. Reads only the chunks of those
    documents; a KB whose documents all carry a language costs one file read.
    """
    stats_path = db_dir / f"kb_stats_{kb_id}.json"
    if not stats_path.exists():
        return 0
    stats = json.loads(stats_path.read_text())
    cpd = stats.get("chunks_per_document", {})
    missing = [h for h, entry in cpd.items() if "language" not in entry]
    if not missing:
        return 0

    log.info(f"Detecting the language of {len(missing)} document(s) in KB '{kb_id}'")
    chunks = await vs.get_chunks_by_filter(filters={"file_hash": missing})
    texts: dict[str, list[str]] = {}
    for chunk in chunks:
        if chunk.mime_type.startswith("text"):
            texts.setdefault(chunk.metadata.get("file_hash", ""), []).append(chunk.content)
    loop = asyncio.get_running_loop()
    for h in missing:
        cpd[h]["language"] = await loop.run_in_executor(None, detect_document, texts.get(h, []))

    stats["language_distribution"] = language_distribution(cpd)
    stats_path.write_text(json.dumps(stats, indent=2))
    return len(missing)


def failed_files_path(db_dir: Path, kb_id: str) -> Path:
    return db_dir / f"kb_failed_{kb_id}.json"


def load_failed_files(db_dir: Path, kb_id: str) -> dict[str, dict]:
    """Files of the KB that could not be indexed, by file hash: {source_file, reason, error, at}.

    A file that yields no chunks stores nothing, so the store cannot tell it from a
    new file; without this list every incremental run would chunk it again.
    Incremental runs skip these files; a full re-index, or forgetting one, retries it.
    """
    path = failed_files_path(db_dir, kb_id)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def remember_failed(db_dir: Path, kb_id: str, h: str, name: str, reason: str, error: str = "") -> None:
    failed = load_failed_files(db_dir, kb_id)
    failed[h] = {
        "source_file": name,
        "reason": reason,
        "error": error,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    failed_files_path(db_dir, kb_id).write_text(json.dumps(failed, indent=2))


def forget_failed(db_dir: Path, kb_id: str, h: str | None = None) -> int:
    """Drop one failed file (by hash), or all of them, so the next incremental run tries them again.

    Returns how many are left.
    """
    failed = {} if h is None else load_failed_files(db_dir, kb_id)
    failed.pop(h or "", None)
    path = failed_files_path(db_dir, kb_id)
    if failed:
        path.write_text(json.dumps(failed, indent=2))
    else:
        path.unlink(missing_ok=True)
    return len(failed)


def _approx_percentile(buckets: dict[str, int], total: int, pct: float) -> int:
    if total == 0:
        return 0
    target = total * pct
    cumulative = 0
    for key in _BUCKET_KEYS:
        count = buckets.get(key, 0)
        if key.endswith("+"):
            return _MAX_VAL
        lo = int(key.split("-")[0])
        cumulative += count
        if cumulative >= target:
            return lo + _STEP // 2
    return _MAX_VAL


def _summary_stats(dist: dict[str, int]) -> tuple[int, int, int, int]:
    """Return (total_chunks, avg_chars, p50_chars, p95_chars) approximated from histogram."""
    total = sum(dist.values())
    if total == 0:
        return 0, 0, 0, 0
    cumulative_sum = 0
    for key in _BUCKET_KEYS:
        count = dist.get(key, 0)
        mid = (_MAX_VAL + _STEP // 2) if key.endswith("+") else int(key.split("-")[0]) + _STEP // 2
        cumulative_sum += mid * count
    avg = cumulative_sum // total
    p50 = _approx_percentile(dist, total, 0.50)
    p95 = _approx_percentile(dist, total, 0.95)
    return total, avg, p50, p95


def write_kb_stats(
    db_dir: Path,
    kb_id: str,
    chunks: list[Chunk],
    was_reset: bool,
    files_added: int,
    files_skipped_store: int,
    files_skipped_batch: int,
) -> None:
    """Compute and persist kb_stats_{kb_id}.json. Merges with existing data on incremental runs."""
    stats_path = db_dir / f"kb_stats_{kb_id}.json"
    now_iso = datetime.now(timezone.utc).isoformat()

    new_buckets = _size_buckets(chunks)
    new_per_hash = _per_hash_entries(chunks, now_iso)
    history_entry = {
        "timestamp": now_iso,
        "chunks_added": len(chunks),
        "files_added": files_added,
        "files_skipped_store": files_skipped_store,
        "files_skipped_batch": files_skipped_batch,
        "was_reset": was_reset,
    }

    if was_reset or not stats_path.exists():
        combined_dist = new_buckets
        combined_cpd = new_per_hash
        combined_history = [history_entry]
        scope = "full"
    else:
        try:
            existing = json.loads(stats_path.read_text())
        except Exception as exc:
            log.warning(f"Could not read existing stats for KB '{kb_id}': {exc}. Recomputing from current chunks.")
            existing = {}

        existing_dist = existing.get("chunk_size_distribution", {})
        combined_dist = dict(new_buckets)
        for k in combined_dist:
            combined_dist[k] += existing_dist.get(k, 0)

        combined_cpd = dict(existing.get("chunks_per_document", {}))
        for h, entry in new_per_hash.items():
            if h not in combined_cpd:
                combined_cpd[h] = entry

        combined_history = list(existing.get("ingestion_history", []))
        combined_history.append(history_entry)
        scope = "incremental"

    total, avg, p50, p95 = _summary_stats(combined_dist)
    total_documents = len({e["source_file"] for e in combined_cpd.values()})

    stats = {
        "kb_id": kb_id,
        "computed_at": now_iso,
        "scope": scope,
        "total_chunks": total,
        "total_documents": total_documents,
        "avg_chunk_chars": avg,
        "p50_chunk_chars": p50,
        "p95_chunk_chars": p95,
        "chunk_size_distribution": combined_dist,
        "chunks_per_document": combined_cpd,
        "chunks_per_document_distribution": _cpd_distribution(combined_cpd),
        "language_distribution": language_distribution(combined_cpd),
        "ingestion_history": combined_history,
    }

    stats_path.write_text(json.dumps(stats, indent=2))
    log.info(f"KB stats written for '{kb_id}' (scope={scope}, {total} chunks, {total_documents} docs)")
