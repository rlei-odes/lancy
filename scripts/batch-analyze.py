#!/usr/bin/env python3
"""Lancy — Batch document analysis.

Runs POST /api/v1/rag/analyze-document once per document in a list, collects the
structured JSON results into a CSV that can be opened as a table.

Each doc's full text (all chunks) is fed to the main LLM together with the
questions from --prompt and the schema from --schema. The LLM answers strictly
in JSON matching the schema; each answer becomes one CSV row.

Prompt and schema must line up:
    - The prompt asks natural-language questions ("Q1: does this doc mention X?").
    - The schema declares one field per answer ("question1": {"type": "string"}).
    - The backend prompt template (prompts/batch_analyze.default.md) shows both
      the prompt and the schema to the LLM, so keep the field names and question
      order aligned or the LLM won't know which answer belongs where.

Ready-to-adapt examples ship in scripts/examples/batch-analyze/.

Usage:
    ./scripts/batch-analyze.py \\
        --frontend http://localhost:3000 \\
        --files docs.txt \\
        --prompt prompt.txt \\
        --schema schema.json \\
        --output results.csv

Optional:
    --kb <kb_id>          Target a specific KB (default: currently active KB).
    --id-field ID_FIELD   Which metadata field to match entries in docs.txt against.
                          Every chunk carries BOTH of these — pick whichever you have.
                            'source_file' (default) — the ingested filename, e.g.
                              'contract-2025-01.pdf'. Right when you uploaded via
                              upload-docs.sh from a local folder.
                            'document_id' — the stable id set at upload time.
                              upload-docs.sh sets this to the filename stem
                              ('contract-2025-01'); a DMS integration would use its
                              own record id ('CTR-2025-001'). Right when your batch
                              is driven by DMS ids you want to join back to.
    --password PASSWORD   Login password; else $LANCY_PASSWORD; else interactive.
    --force               Re-run ids already in the output CSV instead of skipping.

Input files:
    docs.txt     One identifier per line. Interpreted per --id-field.
                 Blank lines and lines starting with '#' are ignored.
    prompt.txt   Natural-language questions for the LLM. Free-form text; keep
                 question numbering aligned with the schema field names.
    schema.json  JSON Schema for the LLM output. The schema's top-level
                 'properties' become the CSV columns (order preserved).

Output CSV columns:
    _id, _status, _chunk_count, _char_count, <field1>, <field2>, ...

    _status is one of:
      'ok'          — LLM returned a parsed result
      'no_chunks'   — identifier not found in the KB
      'over_budget' — doc exceeds ~60% of num_ctx, skipped (see backend logs
                      for budget_chars; increase num_ctx in the RAG panel or
                      pre-summarise the doc if you want it in scope)
      'error:<msg>' — network, HTTP, or LLM error

    Rows are appended one at a time — safe to interrupt and re-run (already-
    processed ids are skipped unless --force is passed).

Auth:
    The script POSTs to /api/auth/login on the frontend, which sets an
    HttpOnly 'rag_auth' cookie carried on subsequent /api/v1/* calls.
    Password source (in priority order):
      1. --password on the command line
      2. LANCY_PASSWORD env var
      3. Interactive prompt
    LDAP mode (Mode 3) is not supported here — use the admin escape hatch by
    setting LANCY_ADMIN=1 to send {password, adminEscape: true}.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import json
import os
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("This script needs 'requests'. Install with: pip install requests")


def login(session: requests.Session, frontend: str, password: str, admin_escape: bool) -> None:
    payload: dict = {"password": password}
    if admin_escape:
        payload["adminEscape"] = True
    r = session.post(f"{frontend}/api/auth/login", json=payload, timeout=15)
    if r.status_code != 200:
        sys.exit(f"Login failed ({r.status_code}): {r.text.strip()}")
    role = r.json().get("role", "?")
    print(f"Logged in as role={role}")


def read_id_list(path: Path) -> list[str]:
    ids: list[str] = []
    for line in path.read_text().splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            ids.append(s)
    if not ids:
        sys.exit(f"No identifiers in {path}")
    return ids


def load_processed(output: Path) -> set[str]:
    if not output.exists():
        return set()
    with output.open() as f:
        return {row["_id"] for row in csv.DictReader(f) if row.get("_id")}


def analyze_one(
    session: requests.Session, frontend: str, id_field: str, ident: str,
    prompt: str, schema: dict, kb_id: str | None,
) -> dict:
    body: dict = {id_field: ident, "prompt": prompt, "response_schema": schema}
    if kb_id:
        body["kb_id"] = kb_id
    try:
        r = session.post(
            f"{frontend}/api/v1/rag/analyze-document",
            json=body,
            timeout=125,  # backend times out at 110s; give the proxy a few extra seconds
        )
    except requests.RequestException as exc:
        return {"_status": f"error:{exc}", "_chunk_count": 0, "_char_count": 0}

    if r.status_code != 200:
        detail = r.text.strip()[:200]
        return {"_status": f"error:HTTP {r.status_code} {detail}", "_chunk_count": 0, "_char_count": 0}

    data = r.json()
    if data.get("skipped"):
        return {
            "_status": data["skipped"],
            "_chunk_count": data.get("chunk_count", 0),
            "_char_count": data.get("char_count", 0),
        }
    row: dict = {
        "_status": "ok",
        "_chunk_count": data.get("chunk_count", 0),
        "_char_count": data.get("char_count", 0),
    }
    for k, v in (data.get("result") or {}).items():
        row[k] = json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
    return row


def main() -> None:
    ap = argparse.ArgumentParser(description="Batch-analyze documents in a Lancy KB.")
    ap.add_argument("--frontend", default=os.getenv("LANCY_FRONTEND", "http://localhost:3000"),
                    help="Frontend base URL (default: http://localhost:3000).")
    ap.add_argument("--files", required=True, type=Path,
                    help="Path to a text file listing one identifier per line.")
    ap.add_argument("--prompt", required=True, type=Path,
                    help="Path to a text file with the analysis prompt.")
    ap.add_argument("--schema", required=True, type=Path,
                    help="Path to a JSON Schema file for the LLM output.")
    ap.add_argument("--output", required=True, type=Path,
                    help="CSV output path. Appended row-by-row; safe to resume.")
    ap.add_argument("--id-field", choices=["source_file", "document_id"], default="source_file",
                    help="How to interpret entries in --files (default: source_file).")
    ap.add_argument("--kb", default=None,
                    help="KB id to target. Defaults to the currently active KB.")
    ap.add_argument("--password", default=None,
                    help="Login password. Falls back to $LANCY_PASSWORD, then interactive prompt.")
    ap.add_argument("--force", action="store_true",
                    help="Re-analyze ids already present in the output CSV.")
    args = ap.parse_args()

    prompt = args.prompt.read_text().strip()
    schema = json.loads(args.schema.read_text())
    ids = read_id_list(args.files)

    columns = ["_id", "_status", "_chunk_count", "_char_count"] + list(schema.get("properties", {}).keys())

    password = args.password or os.getenv("LANCY_PASSWORD") or getpass.getpass("Lancy password: ")
    admin_escape = os.getenv("LANCY_ADMIN") == "1"

    session = requests.Session()
    login(session, args.frontend.rstrip("/"), password, admin_escape)

    processed = set() if args.force else load_processed(args.output)
    todo = [i for i in ids if i not in processed]
    print(f"{len(ids)} total, {len(processed)} already done, {len(todo)} to process.")

    write_header = not args.output.exists() or args.force
    mode = "w" if args.force else "a"
    with args.output.open(mode, newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        if write_header:
            writer.writeheader()
            fh.flush()

        for i, ident in enumerate(todo, 1):
            t0 = time.monotonic()
            row = analyze_one(
                session, args.frontend.rstrip("/"), args.id_field, ident, prompt, schema, args.kb,
            )
            row["_id"] = ident
            writer.writerow(row)
            fh.flush()
            elapsed = time.monotonic() - t0
            print(f"  [{i}/{len(todo)}] {ident} → {row['_status']}  ({elapsed:.1f}s)")

    print(f"Done. Wrote {args.output}")


if __name__ == "__main__":
    main()
