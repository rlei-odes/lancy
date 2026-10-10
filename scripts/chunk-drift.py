#!/usr/bin/env python3
"""Lancy — what a knowledge base holds, and whether re-chunking would change it.

Chunking changes are silent. A docling bump holds its API, raises nothing, and
shifts the markdown just enough to move boundaries; a fix to one of our own
chunkers does the same thing deliberately.

Neither reaches the documents already indexed. Cross-run dedup skips any file whose
hash is already in the store, so its chunks stay exactly as the code that first
ingested them produced — indefinitely, through every later re-index. `last_indexed`
advances anyway, which makes the corpus look current when parts of it are not. The
result is a store chunked several different ways at once, with nothing reporting it
and retrieval quality drifting for no visible reason.

`drift` is how you see it. Only `reset=True` re-chunks what is already there.

Four modes:

    kb       what a KB currently holds — per file, chunk counts and size shape
    chunk    chunk files from disk now and write a snapshot
    compare  diff two snapshots — run `chunk` either side of an upgrade
    drift    compare a KB's stored chunks against a fresh re-chunk of the same
             files, answering the upgrade question with no before-snapshot needed

Usage:
    # what is indexed right now
    python scripts/chunk-drift.py kb
    python scripts/chunk-drift.py kb --kb-id tenders

    # either side of a dependency bump
    python scripts/chunk-drift.py chunk data/report.pdf -o /tmp/before.json
    pip install -r requirements.txt
    python scripts/chunk-drift.py chunk data/report.pdf -o /tmp/after.json
    python scripts/chunk-drift.py compare /tmp/before.json /tmp/after.json

    # or against what the store already has, no before-snapshot required
    python scripts/chunk-drift.py drift --limit 5

Re-chunking goes through `load_chunks`, the same entry point ingestion uses, so
token splitting and low-quality merging match production — a reported difference
is the library's doing, not this script's.

Runs in-process against the configured stores. No server needs to be running, and
nothing is written to any store: every mode is read-only.
"""

import argparse
import asyncio
import hashlib
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DB_DIR = ROOT / "backend" / "src" / "lancy" / "db"
REGISTRY = DB_DIR / "knowledge_bases.json"

# A chunk this small is almost always a stray heading or an artefact of a boundary
# landing badly; worth counting because the number moving is itself a signal.
TINY_CHUNK_CHARS = 120


# ─── Registry ─────────────────────────────────────────────────────────────────


def load_kb(kb_id: str | None) -> dict:
    """The KB record from knowledge_bases.json — active KB when kb_id is None."""
    if not REGISTRY.exists():
        sys.exit(f"No KB registry at {REGISTRY}. Has the backend ever run?")
    reg = json.loads(REGISTRY.read_text())
    target = kb_id or reg.get("active")
    bases = reg.get("bases", {})
    if target not in bases:
        sys.exit(
            f"KB {target!r} not found. Available: {', '.join(sorted(bases)) or '(none)'}"
        )
    return bases[target]


def data_dirs(kb: dict) -> list[Path]:
    return [
        Path(d) if Path(d).is_absolute() else ROOT / d for d in kb.get("data_dirs", [])
    ]


def find_source(name: str, kb: dict) -> Path | None:
    """Locate an indexed document on disk by the filename stored in its metadata."""
    for d in data_dirs(kb):
        if not d.exists():
            continue
        for candidate in d.rglob(name):
            if candidate.is_file():
                return candidate
    return None


# ─── Store access ─────────────────────────────────────────────────────────────


def open_store(kb: dict) -> Any:
    from lancy.feature0_baseline_rag import make_vector_store

    vs_type = kb.get("vs_type") or "chromadb"
    return make_vector_store(
        vs_type=vs_type,
        db_path=Path(kb["vs_path"]) if vs_type == "chromadb" else None,
        embedding_model_name=kb.get("embedding_model", ""),
        vs_connection_string=kb.get("vs_connection_string", "") or "",
        table_name=f"rag_{kb['id'].replace('-', '_')}",
    )


async def read_store(kb: dict) -> list[Any]:
    """Every chunk in the KB's store. Disposes the connection pool on the way out."""
    vs = open_store(kb)
    try:
        return await vs.get_chunks_by_filter()
    finally:
        engine = getattr(vs, "engine", None)
        if engine is not None:
            await engine.dispose()


# ─── Chunk fingerprinting ─────────────────────────────────────────────────────


def digest(text: str) -> str:
    """Short content hash. Identical text gives an identical digest, so a moved
    boundary shows up as a digest that exists on one side and not the other."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]


def fingerprint(chunks: list[Any]) -> dict:
    """The comparable shape of one file's chunks."""
    texts = [c.content or "" for c in chunks]
    sizes = [len(t) for t in texts]
    return {
        "chunks": len(chunks),
        "chars": sum(sizes),
        "digests": [digest(t) for t in texts],
        "sizes": sizes,
        "images": sum(1 for c in chunks if (getattr(c, "mime_type", "") or "").startswith("image")),
        "tiny": sum(1 for s in sizes if s < TINY_CHUNK_CHARS),
    }


def size_shape(sizes: list[int]) -> str:
    if not sizes:
        return "—"
    return (
        f"min {min(sizes)} · p50 {int(statistics.median(sizes))} · max {max(sizes)}"
    )


def verdict(before: dict, after: dict) -> str:
    """How two fingerprints of the same file differ.

    Digest multisets are compared rather than counts alone: the same number of
    chunks with different content means the boundaries moved, which is the failure
    mode a count-only check misses entirely.
    """
    if sorted(before["digests"]) == sorted(after["digests"]):
        return "identical"
    if before["chunks"] == after["chunks"]:
        return "BOUNDARIES MOVED"
    return "COUNT CHANGED"


# ─── Re-chunking ──────────────────────────────────────────────────────────────


def rechunk(path: Path, kb: dict) -> list[Any]:
    """Chunk one file with the KB's own ingestion parameters.

    Goes through `load_chunks` so token splitting and low-quality merging are applied
    exactly as ingestion applies them. Calling the chunker directly would skip both
    and report differences this corpus would never actually see.
    """
    from lancy.feature0_baseline_rag import load_chunks

    return load_chunks(
        include_files=[path],
        pdf_ocr_enabled=bool(kb.get("pdf_ocr_enabled", True)),
        max_chunk_tokens=int(kb.get("max_chunk_tokens", 0) or 0),
        write_images=bool(kb.get("image_indexing_enabled", False)),
    )


def docling_version() -> str:
    try:
        from importlib.metadata import version

        return version("docling")
    except Exception:
        return "unknown"


# ─── Modes ────────────────────────────────────────────────────────────────────


def mode_kb(args: argparse.Namespace) -> int:
    kb = load_kb(args.kb_id)
    chunks = asyncio.run(read_store(kb))

    print(f"KB            {kb['name']}  (id={kb['id']})")
    print(f"store         {kb.get('vs_type', 'chromadb')}  {kb.get('vs_path', '')}")
    print(f"embedding     {kb.get('embedding_backend')}/{kb.get('embedding_model')}")
    print(
        f"chunking      ocr={kb.get('pdf_ocr_enabled')} "
        f"max_chunk_tokens={kb.get('max_chunk_tokens', 0)} "
        f"images={kb.get('image_indexing_enabled')}"
    )
    print(f"last indexed  {kb.get('last_indexed') or 'never'}")
    print()

    if not chunks:
        print("Store is empty.")
        return 0

    by_file: dict[str, list[Any]] = {}
    for c in chunks:
        by_file.setdefault((c.metadata or {}).get("source_file", "(unknown)"), []).append(c)

    # file_hash is what cross-run dedup keys on; fewer distinct hashes than files
    # means two source files carry identical content.
    hashes = {(c.metadata or {}).get("file_hash") for c in chunks}
    hashes.discard(None)

    rows = []
    for name, group in sorted(by_file.items()):
        fp = fingerprint(group)
        rows.append((name, fp))

    width = min(60, max(len(n) for n, _ in rows))
    print(f"{'document'.ljust(width)}  chunks   chars  tiny  img  size shape")
    print("─" * (width + 46))
    for name, fp in rows:
        label = name if len(name) <= width else "…" + name[-(width - 1):]
        print(
            f"{label.ljust(width)}  {fp['chunks']:>6}  {fp['chars']:>6}  "
            f"{fp['tiny']:>4}  {fp['images']:>3}  {size_shape(fp['sizes'])}"
        )

    all_sizes = [len(c.content or "") for c in chunks]
    tiny = sum(1 for s in all_sizes if s < TINY_CHUNK_CHARS)
    print()
    print(f"{len(chunks)} chunks across {len(by_file)} document(s), {len(hashes)} distinct file hash(es)")
    print(f"chunk size: {size_shape(all_sizes)}")
    if tiny:
        print(f"{tiny} chunk(s) under {TINY_CHUNK_CHARS} chars — usually stray headings")

    # Only meaningful once .eml documents are in the corpus.
    emails = {
        (c.metadata or {}).get("email_subject")
        for c in chunks
        if (c.metadata or {}).get("email_subject")
    }
    attachments = {
        (c.metadata or {}).get("email_attachment")
        for c in chunks
        if (c.metadata or {}).get("email_attachment")
    }
    if emails:
        print(f"{len(emails)} email(s) indexed, {len(attachments)} attachment(s) chunked")
    return 0


def mode_chunk(args: argparse.Namespace) -> int:
    kb = load_kb(args.kb_id)
    paths: list[Path] = []
    for raw in args.paths:
        p = Path(raw)
        if p.is_dir():
            paths.extend(sorted(f for f in p.rglob("*") if f.is_file()))
        elif p.is_file():
            paths.append(p)
        else:
            print(f"[skip] {p} — not found", file=sys.stderr)

    if not paths:
        sys.exit("Nothing to chunk.")

    snapshot: dict = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "docling": docling_version(),
        "kb_id": kb["id"],
        "params": {
            "pdf_ocr_enabled": kb.get("pdf_ocr_enabled"),
            "max_chunk_tokens": kb.get("max_chunk_tokens", 0),
            "write_images": kb.get("image_indexing_enabled", False),
        },
        "files": {},
    }

    print(f"docling {snapshot['docling']} · params from KB {kb['id']!r}\n")
    for p in paths:
        try:
            produced = rechunk(p, kb)
        except Exception as exc:
            print(f"  {p.name}: FAILED — {type(exc).__name__}: {exc}", file=sys.stderr)
            snapshot["files"][p.name] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        fp = fingerprint(produced)
        snapshot["files"][p.name] = fp
        print(f"  {p.name}: {fp['chunks']} chunks, {fp['chars']} chars, {size_shape(fp['sizes'])}")

    out = Path(args.out)
    out.write_text(json.dumps(snapshot, indent=2))
    print(f"\nSnapshot written to {out}")
    return 0


def mode_compare(args: argparse.Namespace) -> int:
    before = json.loads(Path(args.before).read_text())
    after = json.loads(Path(args.after).read_text())

    print(f"before   docling {before.get('docling', '?')}   {before.get('created_at', '')}")
    print(f"after    docling {after.get('docling', '?')}   {after.get('created_at', '')}")
    if before.get("params") != after.get("params"):
        # Otherwise a chunking-parameter change gets blamed on the library.
        print("\n⚠️  chunking parameters differ between the snapshots:")
        print(f"    before: {before.get('params')}")
        print(f"    after:  {after.get('params')}")
    print()

    names = sorted(set(before.get("files", {})) | set(after.get("files", {})))
    changed = 0
    for name in names:
        b = before["files"].get(name)
        a = after["files"].get(name)
        if b is None or a is None:
            print(f"  {name}: only in {'after' if b is None else 'before'}")
            changed += 1
            continue
        if "error" in b or "error" in a:
            print(f"  {name}: error — before={b.get('error', 'ok')} after={a.get('error', 'ok')}")
            changed += 1
            continue
        v = verdict(b, a)
        if v == "identical":
            print(f"  {name}: identical ({a['chunks']} chunks)")
            continue
        changed += 1
        kept = len(set(b["digests"]) & set(a["digests"]))
        print(
            f"  {name}: {v} — chunks {b['chunks']}→{a['chunks']}, "
            f"chars {b['chars']}→{a['chars']}, {kept}/{b['chunks']} chunks unchanged"
        )

    print()
    if changed:
        print(f"{changed} of {len(names)} document(s) changed — a re-index would alter the corpus")
    else:
        print(f"All {len(names)} document(s) chunk identically — safe to leave the corpus as it is")
    return 1 if changed else 0


def mode_drift(args: argparse.Namespace) -> int:
    kb = load_kb(args.kb_id)
    stored = asyncio.run(read_store(kb))
    if not stored:
        sys.exit("Store is empty — nothing to compare against.")

    by_file: dict[str, list[Any]] = {}
    for c in stored:
        by_file.setdefault((c.metadata or {}).get("source_file", "(unknown)"), []).append(c)

    names = sorted(by_file)
    if args.file:
        names = [n for n in names if args.file in n]
        if not names:
            sys.exit(f"No indexed document matching {args.file!r}")
    if args.limit:
        names = names[: args.limit]

    print(f"KB {kb['name']!r} · docling {docling_version()}")
    print(f"comparing {len(names)} of {len(by_file)} indexed document(s) against a fresh re-chunk\n")

    changed = missing = 0
    for name in names:
        group = by_file[name]
        path = find_source(name, kb)
        if path is None:
            print(f"  {name}: source file not found in data_dirs — cannot re-chunk")
            missing += 1
            continue

        stored_fp = fingerprint(group)
        # If the bytes changed since indexing, any difference is the file's doing and
        # says nothing about the library — worth separating out rather than counting.
        from lancy.feature0_baseline_rag import file_hash

        indexed_hash = (group[0].metadata or {}).get("file_hash")
        current_hash = file_hash(path)
        if indexed_hash and indexed_hash != current_hash:
            print(f"  {name}: file on disk has changed since indexing — skipped")
            missing += 1
            continue

        try:
            fresh_fp = fingerprint(rechunk(path, kb))
        except Exception as exc:
            print(f"  {name}: re-chunk FAILED — {type(exc).__name__}: {exc}")
            changed += 1
            continue

        v = verdict(stored_fp, fresh_fp)
        if v == "identical":
            print(f"  {name}: identical ({fresh_fp['chunks']} chunks)")
            continue
        changed += 1
        kept = len(set(stored_fp["digests"]) & set(fresh_fp["digests"]))
        print(
            f"  {name}: {v} — chunks {stored_fp['chunks']}→{fresh_fp['chunks']}, "
            f"chars {stored_fp['chars']}→{fresh_fp['chars']}, "
            f"{kept}/{stored_fp['chunks']} chunks unchanged"
        )

    print()
    if changed:
        print(
            f"{changed} document(s) would chunk differently than what is stored.\n"
            "Their chunks were produced by an older version of the chunking code and\n"
            "dedup has preserved them through every re-index since. Only reset=True\n"
            "re-chunks what is already in the store."
        )
    else:
        print("Stored chunks match a fresh re-chunk — the corpus is consistent.")
    if missing:
        print(f"{missing} document(s) could not be compared (see above).")
    return 1 if changed else 0


# ─── CLI ──────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="mode", required=True)

    p_kb = sub.add_parser("kb", help="what a KB currently holds")
    p_kb.add_argument("--kb-id", default=None, help="defaults to the active KB")
    p_kb.set_defaults(fn=mode_kb)

    p_chunk = sub.add_parser("chunk", help="chunk files now and write a snapshot")
    p_chunk.add_argument("paths", nargs="+", help="files or directories")
    p_chunk.add_argument("-o", "--out", required=True, help="snapshot JSON path")
    p_chunk.add_argument("--kb-id", default=None, help="KB whose chunking params to use")
    p_chunk.set_defaults(fn=mode_chunk)

    p_cmp = sub.add_parser("compare", help="diff two snapshots")
    p_cmp.add_argument("before")
    p_cmp.add_argument("after")
    p_cmp.set_defaults(fn=mode_compare)

    p_drift = sub.add_parser("drift", help="stored chunks vs a fresh re-chunk")
    p_drift.add_argument("--kb-id", default=None, help="defaults to the active KB")
    p_drift.add_argument("--file", default=None, help="only documents matching this substring")
    p_drift.add_argument("--limit", type=int, default=0, help="compare at most N documents")
    p_drift.set_defaults(fn=mode_drift)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
