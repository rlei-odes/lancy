# batch-analyze — example inputs

Starter files for `scripts/batch-analyze.py`. Copy them elsewhere and adapt to
your KB and analysis task.

## Files

- **`docs.txt`** — one identifier per line. See "Which id to use?" below.
- **`prompt.txt`** — the natural-language questions you want answered per doc.
  Free-form; label the questions so it's obvious which schema field each maps to.
- **`schema.json`** — the JSON Schema the LLM answers must match. On
  OpenAI-compatible backends (custom / vLLM / OpenAI / Anthropic via LiteLLM)
  the schema is enforced at decode time; on Ollama it guides via the prompt.

## Which id to use?

Every ingested chunk carries two identifiers in metadata:

| Field | What it is | Example |
|---|---|---|
| `source_file` | Display filename (as ingested) | `contract-2025-01.pdf` |
| `document_id` | Stable id used for versioning / DMS integration | `contract-2025-01` (from `upload-docs.sh`, which strips the extension) or `CTR-2025-001` (from a DMS) |

Either works to look a document up. Pick the one you actually have in hand:

- **Uploaded via `scripts/upload-docs.sh` from a local folder** → use `--id-field source_file`
  (the default) and put filenames in `docs.txt`. Simplest for the common case.
- **Driven by a DMS or upload pipeline that assigns stable ids** → use
  `--id-field document_id` and put those ids in `docs.txt`. The batch results
  can then be joined back to your DMS records without a filename lookup step.

## Quick run

```bash
# 1. Adapt the three input files to your KB
cp -r scripts/examples/batch-analyze/ /tmp/my-analysis/
# ... edit /tmp/my-analysis/{docs.txt,prompt.txt,schema.json} ...

# 2. Make sure the target KB is ingested and either active OR pass --kb <id>
./scripts/batch-analyze.py \
    --files  /tmp/my-analysis/docs.txt \
    --prompt /tmp/my-analysis/prompt.txt \
    --schema /tmp/my-analysis/schema.json \
    --output /tmp/my-analysis/results.csv
```

The CSV is appended row-by-row and safe to interrupt — re-running skips ids
already present. Use `--force` to overwrite from scratch.
