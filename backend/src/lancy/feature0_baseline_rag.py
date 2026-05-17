"""
Baseline RAG pipeline.

Each pipeline stage is an independent function so you can run and inspect individual steps without executing the full pipeline.

Steps at a glance:
    1  load_chunks(): Load PDFs, split into header-based chunks
    2  build_vector_store(): Embed chunks and persist to ChromaDB
    3  inspect_retrieval(): Run semantic search and print results
    4  build_agent(): Assemble the RAG agent from the vector store
    5  ask(): Send a query and return the answer

LLM backends (BACKEND must be set explicitly, there is no default):
    ollama: local Ollama server at http://localhost:11434
    openai: requires OPENAI_API_KEY (env var or /secrets/OPENAI_API_KEY file)

Embedding backends (EMBEDDING_BACKEND, default: local):
    local  — SentenceTransformer, no API key needed (default)
    openai — requires OPENAI_API_KEY

Data & vector store:
    PDFs are read from <project-root>/data/.
    The vector store is written to <project-root>/backend/db/data_vs.db by default.
    Override the location by setting the DB_DIR environment variable.
    Set reset_vs=True (or RESET_VS=1) to rebuild the store from scratch.
    Re-embedding is skipped on subsequent runs if the store already exists.

Usage:
    BACKEND must always be provided explicitly:
        BACKEND=ollama python -m lancy.feature0_baseline_rag
        BACKEND=openai python -m lancy.feature0_baseline_rag

    Override the query or model at runtime:
        QUERY="What is the carbon footprint of wood pallets?" \\
        BACKEND=openai python -m lancy.feature0_baseline_rag

        MODEL=gpt-4o   BACKEND=openai python -m lancy.feature0_baseline_rag
        MODEL=llama3.2 BACKEND=ollama python -m lancy.feature0_baseline_rag
"""

import asyncio
import hashlib
import os
import re
from collections import Counter
from pathlib import Path

from loguru import logger

from conversational_toolkit.embeddings.base import EmbeddingsModel
from conversational_toolkit.embeddings.openai import OpenAIEmbeddings
from conversational_toolkit.embeddings.sentence_transformer import (
    SentenceTransformerEmbeddings,
)


from conversational_toolkit.agents.base import QueryWithContext
from conversational_toolkit.agents.rag import RAG
from conversational_toolkit.chunking.base import Chunk
from conversational_toolkit.chunking.excel_chunker import ExcelChunker
from conversational_toolkit.chunking.markdown_chunker import MarkdownChunker
from conversational_toolkit.chunking.markitdown_chunker import MarkItDownChunker
from conversational_toolkit.chunking.pdf_chunker import PDFChunker
from conversational_toolkit.llms.base import LLM, LLMMessage
from conversational_toolkit.llms.local_llm import LocalLLM
from conversational_toolkit.llms.ollama import OllamaLLM
from conversational_toolkit.llms.openai import OpenAILLM
from conversational_toolkit.retriever.vectorstore_retriever import VectorStoreRetriever
from conversational_toolkit.vectorstores.base import ChunkMatch, VectorStore
from conversational_toolkit.vectorstores.chromadb import ChromaDBVectorStore


# Embedding dimension lookup — used when creating a pgvector table.
# All values are the default output dimensions for each model.
EMBEDDING_DIMS: dict[str, int] = {
    "nomic-ai/nomic-embed-text-v1": 768,
    "nomic-embed-text": 768,
    "all-MiniLM-L6-v2": 384,
    "all-minilm": 384,
    "BAAI/bge-m3": 1024,
    "intfloat/multilingual-e5-large": 1024,
    "mxbai-embed-large": 1024,
    "text-embedding-3-small": 1536,
    "text-embedding-ada-002": 1536,
    "text-embedding-3-large": 3072,
    "voyage/voyage-3": 1024,
}
_DEFAULT_EMBEDDING_DIM = 768


def make_vector_store(
    vs_type: str,
    db_path: Path | None,
    embedding_model_name: str,
    vs_connection_string: str = "",
    table_name: str = "rag_chunks",
) -> VectorStore:
    """Factory: create a ChromaDB or PGVector store from KB config."""
    vs_type = (vs_type or "chromadb").lower()
    if vs_type == "pgvector":
        from sqlalchemy.ext.asyncio import create_async_engine
        from conversational_toolkit.vectorstores.postgres import PGVectorStore

        conn = vs_connection_string.strip()
        if not conn:
            raise ValueError("vs_connection_string is required for pgvector")
        # Convert postgresql:// to postgresql+asyncpg://
        if conn.startswith("postgresql://"):
            conn = conn.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif conn.startswith("postgres://"):
            conn = conn.replace("postgres://", "postgresql+asyncpg://", 1)
        engine = create_async_engine(conn, pool_pre_ping=True)
        dim = EMBEDDING_DIMS.get(embedding_model_name, _DEFAULT_EMBEDDING_DIM)
        logger.info(
            f"PGVectorStore: table={table_name!r} dim={dim} conn={conn[:40]}..."
        )
        return PGVectorStore(engine=engine, table_name=table_name, embeddings_size=dim)
    else:
        if db_path is None:
            raise ValueError("db_path is required for chromadb")
        return ChromaDBVectorStore(db_path=str(db_path))


def _task_prefixes(model_name: str) -> tuple[str, str] | None:
    """Return (document_prefix, query_prefix) for models trained with task prefixes, else None.

    - nomic-embed-text: "search_document: " / "search_query: "
    - intfloat E5 family: "passage: " / "query: "
    """
    name = model_name.lower()
    if "nomic" in name:
        return ("search_document: ", "search_query: ")
    if "e5" in name:
        return ("passage: ", "query: ")
    return None


class TaskPrefixRetriever(VectorStoreRetriever):
    """VectorStoreRetriever that prepends a model-specific task prefix to queries."""

    def __init__(self, embedding_model, vector_store, top_k: int, query_prefix: str):
        super().__init__(embedding_model, vector_store, top_k=top_k)
        self._query_prefix = query_prefix

    async def retrieve(self, query: str) -> list[ChunkMatch]:
        return await super().retrieve(f"{self._query_prefix}{query}")


# Paths and defaults
_ROOT = Path(__file__).parents[3]  # <project-root>/
DATA_DIR = _ROOT / "data"
DB_DIR = Path(os.getenv("DB_DIR", str(_ROOT / "backend" / "db")))
VS_PATH = DB_DIR / "data_vs.db"

# Local embedding model — runs fully offline via sentence-transformers.
# nomic-embed-text produces 768-dim embeddings and handles long documents well.
# Switch to "all-MiniLM-L6-v2" for a lighter/faster alternative (384-dim).
LOCAL_EMBEDDING_MODEL = "nomic-ai/nomic-embed-text-v1"
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"

# Active embedding model name — resolved in build_embedding_model()
EMBEDDING_MODEL = LOCAL_EMBEDDING_MODEL

RETRIEVER_TOP_K = 5
SEED = 42
MAX_FILES = None  # None = all files; set to int for dev/debug
MAX_FILE_SIZE_MB = 20

SYSTEM_PROMPT = (
    "You are a helpful AI assistant specialised in sustainability and product compliance for PrimePack AG.\n\n"
    "You will receive document excerpts relevant to the user's question. "
    "Produce the best possible answer using only the information in those excerpts.\n\n"
    "Rules:\n"
    "- Use the provided excerpts as your only source of truth. Do not rely on outside knowledge.\n"
    "- Use all relevant excerpts when forming your answer.\n"
    "- If the answer cannot be found in the excerpts, clearly say that you do not know.\n"
    "- Always cite the source document for any claim you make.\n"
    "- If excerpts contain conflicting information, report both values and flag the conflict.\n"
    "- Distinguish between third-party verified claims (EPDs) and self-declared supplier claims."
)

_CHUNKERS: dict[
    str, PDFChunker | ExcelChunker | MarkdownChunker | MarkItDownChunker
] = {
    ".pdf": PDFChunker(),
    ".xlsx": ExcelChunker(),
    ".xls": ExcelChunker(),
    ".md": MarkdownChunker(),
    ".txt": MarkdownChunker(),
    ".docx": MarkItDownChunker(),
    ".doc": MarkItDownChunker(),
    ".epub": MarkItDownChunker(),
}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".tiff", ".bmp", ".webp"}


def _get_secret(name: str) -> str:
    secret_file = Path(f"/secrets/{name}")
    if secret_file.exists():
        return secret_file.read_text().strip()
    key = os.environ.get(name, "")
    if not key:
        raise ValueError(
            f"{name} not found. Either:\n"
            f"  - Add it as a Renku secret at /secrets/{name}, or\n"
            f"  - Set the {name} environment variable."
        )
    return key


def build_embedding_model(
    embedding_backend: str | None = None,
    model_name: str | None = None,
    ollama_host: str = "",
    custom_base_url: str = "",
    custom_api_key: str = "",
) -> EmbeddingsModel:
    """Instantiate the embedding model for the requested backend.

    local   → SentenceTransformer (fully offline, no API key needed).
    ollama  → Ollama embedding server via OpenAI-compat /v1/embeddings.
    litellm → OpenAI-compatible API at LITELLM_BASE_URL (requires LITELLM_API_KEY).
    custom  → direct OpenAI-compatible API with explicit base URL + API key.
    """
    backend = (
        (embedding_backend or os.getenv("EMBEDDING_BACKEND", "local")).lower().strip()
    )
    # backward-compat: 'openai' was renamed to 'custom'
    if backend == "openai":
        backend = "custom"

    if backend == "ollama":
        host = (ollama_host or os.getenv("OLLAMA_HOST", "localhost:11434")).strip()
        if not host.startswith("http"):
            host = f"http://{host}"
        base_url = f"{host}/v1"
        name = model_name or "nomic-embed-text"
        logger.info(f"Embedding backend: Ollama ({name}) @ {base_url}")
        return OpenAIEmbeddings(
            model_name=name, base_url=base_url, api_key="ollama", dimensions=None
        )
    elif backend == "litellm":
        name = model_name or "voyage/voyage-3"
        base_url = os.getenv("LITELLM_BASE_URL", "")
        api_key = os.getenv("LITELLM_API_KEY", "")
        logger.info(f"Embedding backend: LiteLLM ({name}) @ {base_url or 'default'}")
        return OpenAIEmbeddings(
            model_name=name,
            base_url=base_url or None,
            api_key=api_key or None,
            dimensions=None,
        )
    elif backend == "custom":
        name = model_name or OPENAI_EMBEDDING_MODEL
        url = custom_base_url.strip() or None
        key = custom_api_key.strip() or os.getenv("OPENAI_API_KEY", "") or None
        logger.info(f"Embedding backend: custom ({name}) @ {url or 'OpenAI default'}")
        return OpenAIEmbeddings(
            model_name=name, base_url=url, api_key=key, dimensions=None
        )
    elif backend == "qwen3vl":
        # Deferred import — loading torch + transformers at module level costs seconds
        # even when this backend is not used.
        from conversational_toolkit.embeddings.qwen_vl import Qwen3VLEmbeddings

        name = model_name or "Qwen/Qwen3-VL-Embedding-2B"
        logger.info(f"Embedding backend: Qwen3VL ({name})")
        return Qwen3VLEmbeddings(model_name_or_path=name)
    else:  # local
        name = model_name or LOCAL_EMBEDDING_MODEL
        logger.info(f"Embedding backend: local SentenceTransformer ({name})")
        return SentenceTransformerEmbeddings(
            model_name=name,
            trust_remote_code="nomic" in name.lower(),
        )


def build_llm(
    backend: str,
    model_name: str | None = None,
    temperature: float = 0.3,
    response_format=None,
    ollama_host: str | None = None,
    num_ctx: int = 8192,
    max_tokens: int | None = None,
    custom_base_url: str = "",
    custom_api_key: str = "",
) -> LLM:
    """Instantiate the LLM for the requested backend."""
    backend = backend.lower().strip()
    match backend:
        case "ollama":
            name = model_name or "mistral-nemo:12b"
            host = ollama_host or os.getenv("OLLAMA_HOST") or None
            logger.info(
                f"LLM backend: Ollama ({name}) host={host or 'localhost'} num_ctx={num_ctx}"
            )
            return OllamaLLM(
                model_name=name,
                temperature=temperature,
                seed=SEED,
                tools=None,
                tool_choice=None,
                response_format=response_format,
                host=host,
                num_ctx=num_ctx,
            )
        case "litellm":
            base_url = os.getenv("LITELLM_BASE_URL", "")
            api_key = os.getenv("LITELLM_API_KEY", "")
            if not base_url:
                raise ValueError("LITELLM_BASE_URL environment variable is not set")
            name = model_name or "claude-haiku-4-5"
            if base_url and not base_url.rstrip("/").endswith("/v1"):
                base_url = base_url.rstrip("/") + "/v1"
            logger.info(f"LLM backend: LiteLLM ({name}) @ {base_url or 'default'}")
            return LocalLLM(
                model_name=name,
                base_url=base_url or "",
                api_key=api_key or "",
                temperature=temperature,
                seed=SEED,
                response_format=response_format,
                display_name=f"litellm/{name}",
                max_tokens=max_tokens,
            )
        case "custom":
            # OpenAI-compatible custom endpoint (Anthropic, OpenAI, local vLLM, etc.)
            name = model_name or ""
            url = custom_base_url.strip()
            key = custom_api_key.strip()
            logger.info(f"LLM backend: Custom ({name}) @ {url or '(no url)'}")
            return OpenAILLM(
                model_name=name,
                temperature=temperature,
                seed=SEED,
                openai_api_key=key or None,
                base_url=url or None,
                response_format=response_format,
                max_tokens=max_tokens,
            )
        case "openai" | "anthropic" | "qwen":
            # Legacy / internal: route through custom with env-based credentials
            if backend == "openai":
                name = model_name or "gpt-4o-mini"
                key = _get_secret("OPENAI_API_KEY") or ""
                url = None
            elif backend == "anthropic":
                name = model_name or "claude-haiku-4-5-20251001"
                key = _get_secret("ANTHROPIC_API_KEY") or ""
                url = "https://api.anthropic.com/v1"
            else:  # qwen
                name = model_name or "Qwen/Qwen3-32B-AWQ"
                key = _get_secret("SDSC_QWEN3_32B_AWQ") or ""
                url = "https://vllm-gateway-runai-codev-llm.inference.compute.datascience.ch/v1"
            logger.info(f"LLM backend: {backend} ({name})")
            return OpenAILLM(
                model_name=name,
                temperature=temperature,
                seed=SEED,
                openai_api_key=key or None,
                base_url=url,
                response_format=response_format,
            )
        case _:
            raise ValueError(
                f"Unsupported backend {backend!r}. Choose 'ollama', 'litellm', or 'custom'."
            )


def _split_chunk_by_tokens(chunk: Chunk, max_tokens: int) -> list[Chunk]:
    """Split a chunk whose content exceeds max_tokens into smaller sub-chunks.

    Uses a simple character-based token estimate (4 chars ≈ 1 token).
    Splits at paragraph boundaries where possible, otherwise at word boundaries.
    """
    max_chars = max_tokens * 4
    text = chunk.content
    if len(text) <= max_chars:
        return [chunk]

    sub_chunks: list[Chunk] = []
    paragraphs = text.split("\n\n")
    current: list[str] = []
    current_len = 0

    def _flush() -> None:
        if current:
            sub_text = "\n\n".join(current).strip()
            if sub_text:
                sub_chunks.append(
                    Chunk(
                        title=chunk.title,
                        content=sub_text,
                        mime_type=chunk.mime_type,
                        metadata=chunk.metadata.copy(),
                    )
                )

    for para in paragraphs:
        if current_len + len(para) + 2 > max_chars and current:
            _flush()
            current = []
            current_len = 0
        # Paragraph itself is too long — split at word boundaries
        if len(para) > max_chars:
            words = para.split()
            word_buf: list[str] = []
            word_len = 0
            for word in words:
                if word_len + len(word) + 1 > max_chars and word_buf:
                    sub_chunks.append(
                        Chunk(
                            title=chunk.title,
                            content=" ".join(word_buf),
                            mime_type=chunk.mime_type,
                            metadata=chunk.metadata.copy(),
                        )
                    )
                    word_buf = []
                    word_len = 0
                word_buf.append(word)
                word_len += len(word) + 1
            if word_buf:
                para_text = " ".join(word_buf)
                current.append(para_text)
                current_len += len(para_text) + 2
        else:
            current.append(para)
            current_len += len(para) + 2

    _flush()
    return sub_chunks if sub_chunks else [chunk]


# ---------------------------------------------------------------------------
# Chunk quality filter
# ---------------------------------------------------------------------------

_MARKDOWN_JUNK_RE = re.compile(
    r"<!--.*?-->|"       # HTML comments  (<!-- image -->, etc.)
    r"!\[.*?\]\(.*?\)|"  # image embeds   ![]()
    r"\[.*?\]\(.*?\)|"   # links          [text](url)
    r"^#{1,6}\s*|"       # heading markers
    r"^[-*_]{3,}\s*$",   # horizontal rules  ---, ***, ___
    re.MULTILINE | re.DOTALL,
)

# Non-whitespace characters required after stripping Markdown syntax.
# Heading-only or image-placeholder-only chunks typically score ~15–25.
MIN_CONTENT_CHARS = 50


def _content_score(text: str) -> int:
    """Non-whitespace character count after stripping Markdown syntax."""
    return sum(1 for c in _MARKDOWN_JUNK_RE.sub("", text) if not c.isspace())


def _merge_low_quality_chunks(chunks: list) -> list:
    """Merge near-empty chunks forward into the next substantive chunk.

    Any chunk whose stripped content scores below MIN_CONTENT_CHARS is
    prepended to the following chunk.  A trailing low-quality chunk with no
    successor is dropped entirely.
    """
    result = []
    pending: str = ""
    n_merged = 0

    for chunk in chunks:
        if not chunk.mime_type.startswith("text"):
            # Non-text chunks (images) pass through unmodified; never merge
            # binary content with pending text or vice-versa.
            result.append(chunk)
            continue
        if _content_score(chunk.content) < MIN_CONTENT_CHARS:
            pending = (pending + "\n\n" + chunk.content).strip() if pending else chunk.content.strip()
            n_merged += 1
        else:
            if pending:
                chunk = Chunk(
                    title=chunk.title,
                    content=pending + "\n\n" + chunk.content,
                    mime_type=chunk.mime_type,
                    metadata=chunk.metadata.copy(),
                )
                pending = ""
            result.append(chunk)

    if n_merged:
        dropped = 1 if pending else 0
        logger.info(
            f"Chunk quality filter: {n_merged} low-quality chunk(s) merged forward"
            + (f", {dropped} dropped (no successor)" if dropped else "")
        )
    return result


def file_hash(path: Path) -> str:
    """SHA-256 fingerprint of a file's raw bytes.

    The hash is content-based: renaming a file produces the same hash;
    modifying it produces a different one.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _collect_candidate_files(
    data_dirs: list[Path],
    max_file_size_mb: float,
    max_files: int | None = None,
    write_images: bool = False,
) -> list[Path]:
    """Return the filtered list of ingestable files from the given directories.

    Applies the same rules as the ingestion loop: supported extensions only,
    no EVALUATION files, within the size limit. Used by both the dedup pre-pass
    and load_chunks (when include_files is not provided).

    When write_images=True, standalone image files are included alongside
    document files so they enter the dedup pre-pass and are passed to load_chunks.
    """
    all_files: list[Path] = []
    for d in data_dirs:
        if not d.exists():
            logger.warning(f"Data directory not found, skipping: {d}")
            continue
        all_files.extend(sorted(f for f in d.rglob("*") if f.is_file()))

    if max_files is not None:
        all_files = all_files[:max_files]

    for f in all_files:
        ext = f.suffix.lower()
        if ext not in _CHUNKERS and ext not in _IMAGE_EXTENSIONS:
            logger.warning(f"Skipping unsupported file type {ext!r}: {f.name}")
        elif ext in _IMAGE_EXTENSIONS and not write_images:
            logger.warning(f"Skipping image file (image indexing disabled): {f.name}")

    candidates = [
        f
        for f in all_files
        if (
            f.suffix.lower() in _CHUNKERS
            or (write_images and f.suffix.lower() in _IMAGE_EXTENSIONS)
        )
        and "EVALUATION" not in f.name
    ]

    result: list[Path] = []
    for f in candidates:
        size_mb = f.stat().st_size / (1024 * 1024)
        if size_mb > max_file_size_mb:
            logger.warning(
                f"Skipping {f.name}: file too large "
                f"({size_mb:.1f} MB > {max_file_size_mb} MB limit)"
            )
            continue
        result.append(f)

    return result


def load_chunks(
    data_dirs: list[Path] | None = None,
    max_files: int | None = None,
    max_file_size_mb: float = MAX_FILE_SIZE_MB,
    on_progress=None,  # callable(current_file, file_index, total_files, chunks_so_far)
    pdf_ocr_enabled: bool = True,
    max_chunk_tokens: int = 0,
    include_files: list[Path] | None = None,  # if set, skip data_dirs discovery
    file_hashes: dict[Path, str]
    | None = None,  # precomputed hashes for stamping chunks
    write_images: bool = False,  # extract images from PDFs and load standalone image files
) -> list[Chunk]:
    """Load documents and split them into chunks.

    Args:
        data_dirs: Directories to ingest. Defaults to [DATA_DIR]. Ignored when
            include_files is provided.
        max_files: Cap total files (useful for dev/debug). Ignored when
            include_files is provided.
        max_file_size_mb: Skip files larger than this limit. Ignored when
            include_files is provided (caller is responsible for pre-filtering).
        include_files: When provided, parse exactly these files instead of
            scanning data_dirs. The list is assumed to be pre-filtered
            (extension, size, dedup) by the caller.
        file_hashes: Map of path -> SHA-256 hash. When provided, each chunk is
            stamped with chunk.metadata["file_hash"]. If a file is not in the
            map, the hash is computed on the fly so that standalone / notebook
            use always produces stamped chunks.
        write_images: When True, extract embedded images from PDFs (as base64
            image/png chunks) and load standalone image files. When False
            (default), images are skipped — PDFChunker is called with
            write_images=False and image files are ignored.
    """
    import base64

    dirs = data_dirs if data_dirs else [DATA_DIR]
    all_chunks: list[Chunk] = []

    if include_files is not None:
        supported_files = include_files
        logger.info(
            f"Chunking {len(supported_files)} files (pre-filtered by dedup pre-pass)"
        )
    else:
        supported_files = _collect_candidate_files(
            dirs, max_file_size_mb, max_files, write_images=write_images
        )
        logger.info(
            f"Chunking {len(supported_files)} files from {[str(d) for d in dirs]}"
        )

    for file_idx, file_path in enumerate(supported_files):
        ext = file_path.suffix.lower()
        try:
            if on_progress:
                on_progress(
                    file_path.name, file_idx, len(supported_files), len(all_chunks)
                )

            if ext in _IMAGE_EXTENSIONS:
                # Standalone image file — load as a single base64 chunk
                b64 = base64.b64encode(file_path.read_bytes()).decode()
                file_chunks: list[Chunk] = [
                    Chunk(
                        content=b64,
                        mime_type="image/png",
                        title=file_path.name,
                        metadata={},
                    )
                ]
            else:
                chunker = _CHUNKERS[ext]
                kwargs: dict = {}
                if ext == ".pdf":
                    kwargs["do_ocr"] = pdf_ocr_enabled
                    kwargs["write_images"] = write_images
                file_chunks = chunker.make_chunks(str(file_path), **kwargs)
                if max_chunk_tokens > 0:
                    split: list[Chunk] = []
                    for c in file_chunks:
                        split.extend(_split_chunk_by_tokens(c, max_chunk_tokens))
                    file_chunks = split
                file_chunks = _merge_low_quality_chunks(file_chunks)

            h = (file_hashes or {}).get(file_path) or file_hash(file_path)
            for chunk in file_chunks:
                chunk.metadata["source_file"] = file_path.name
                chunk.metadata["source"] = file_path.name
                chunk.metadata["title"] = chunk.title
                chunk.metadata["file_hash"] = h
                chunk.metadata["chunk_chars"] = len(chunk.content)
            all_chunks.extend(file_chunks)
            logger.debug(f"  {file_path.name}: {len(file_chunks)} chunks")
            if on_progress:
                on_progress(
                    file_path.name, file_idx + 1, len(supported_files), len(all_chunks)
                )
        except Exception as exc:
            logger.warning(f"Skipping {file_path.name}: {exc}")

    logger.info(f"Done, {len(all_chunks)} chunks total")
    return all_chunks


def inspect_chunks(chunks: list[Chunk], sample_size: int = 5) -> None:
    counts = Counter(c.metadata.get("source_file", "unknown") for c in chunks)
    logger.info("------ Chunk inspection -------")
    logger.info(f"Total chunks: {len(chunks)}; Source files: {len(counts)}")
    for fname, n in sorted(counts.items()):
        logger.info(f"{fname}: {n} chunks")
    logger.info(f"Sample (first {sample_size}):")
    for chunk in chunks[:sample_size]:
        source = chunk.metadata.get("source_file", "?")
        logger.info(f"Source and title: [{source}] {chunk.title!r}")
        logger.info(f"Chunk content: {chunk.content[:200].strip()!r}")


async def build_vector_store(
    chunks: list[Chunk],
    embedding_model: EmbeddingsModel,
    db_path: Path = VS_PATH,
    reset: bool = False,
    batch_size: int = 50,
    on_embed_progress=None,  # callable(batch_index, total_batches)
    vector_store: VectorStore
    | None = None,  # if provided, use instead of creating ChromaDB
    existing_hashes: set[str]
    | None = None,  # precomputed from pre-pass; avoids second metadata scan
    use_task_prefix: bool = True,
) -> VectorStore:
    """Embed chunks and persist them in a vector store (ChromaDB or PGVector).

    Deduplication: chunks are grouped by file_hash. Groups whose hash is already
    present in the store are skipped. existing_hashes can be passed from the
    ingestion pre-pass to avoid a second full metadata scan; when None (e.g.
    standalone / notebook use), get_file_hashes() is called internally.
    """
    if vector_store is None:
        vector_store = ChromaDBVectorStore(db_path=str(db_path))

    if reset:
        if isinstance(vector_store, ChromaDBVectorStore):
            vector_store.client.delete_collection(vector_store.collection.name)
            vector_store.collection = vector_store.client.create_collection(
                name="default_collection"
            )
            logger.info(f"Reset ChromaDB collection at {db_path}")
        else:
            await vector_store.clear()
            logger.info("Reset vector store (cleared all rows)")

    if not chunks:
        logger.warning("No chunks to embed — vector store will be empty.")
        return vector_store

    # Validate that all chunks carry a file_hash — stamped by load_chunks.
    missing = [c for c in chunks if "file_hash" not in c.metadata]
    if missing:
        raise ValueError(
            f"{len(missing)} chunk(s) are missing 'file_hash' in metadata. "
            "Ensure load_chunks() stamped them before calling build_vector_store()."
        )

    # Resolve the set of hashes already in the store (belt-and-suspenders check).
    # When called from _run_ingestion the pre-pass passes existing_hashes so we
    # avoid a second full metadata scan.
    if existing_hashes is None:
        existing_hashes = await vector_store.get_file_hashes()
        if existing_hashes:
            logger.debug(
                f"build_vector_store: {len(existing_hashes)} hashes already in store"
            )

    # Group chunks by file_hash and filter out files already indexed.
    chunks_by_hash: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        h = chunk.metadata["file_hash"]
        chunks_by_hash.setdefault(h, []).append(chunk)

    chunks_to_embed: list[Chunk] = []
    for h, group in chunks_by_hash.items():
        fname = group[0].metadata.get("source_file", "?")
        if h in existing_hashes:
            logger.info(f"Skipping {fname!r} — already in store (hash={h[:8]}…)")
        else:
            chunks_to_embed.extend(group)

    if not chunks_to_embed:
        logger.info("All incoming chunks are already in the store — nothing to embed.")
        return vector_store

    logger.info(f"Embedding {len(chunks_to_embed)} chunks ...")

    doc_prefix: str | None = None
    if use_task_prefix:
        prefixes = _task_prefixes(getattr(embedding_model, "model_name", ""))
        if prefixes:
            doc_prefix = prefixes[0]

    for i in range(0, len(chunks_to_embed), batch_size):
        batch = chunks_to_embed[i : i + batch_size]

        if doc_prefix:
            texts = [f"{doc_prefix}{c.content}" for c in batch]
        else:
            texts = [c.content for c in batch]

        embeddings = await embedding_model.get_embeddings(texts)

        await vector_store.insert_chunks(chunks=batch, embedding=embeddings)
        batch_idx = i // batch_size + 1
        total_batches = (len(chunks_to_embed) - 1) // batch_size + 1
        logger.info(f"Processed batch {batch_idx}/{total_batches}")
        if on_embed_progress:
            on_embed_progress(batch_idx, total_batches)

    logger.info(f"Done! Vector store written to {db_path}")
    return vector_store


async def inspect_retrieval(
    query: str,
    vector_store: VectorStore,
    embedding_model: EmbeddingsModel,
    top_k: int = RETRIEVER_TOP_K,
) -> list[ChunkMatch]:
    retriever = _make_retriever(embedding_model, vector_store, top_k)
    results = await retriever.retrieve(query)

    logger.info(f"Retrieval for query: {query!r}")
    print(f"\nTop-{top_k} retrieved chunks (returned={len(results)}):")
    for i, r in enumerate(results, 1):
        src = r.metadata.get("source_file", "?")
        print(f"  [{i}] score={r.score:.4f}  file={src!r}  title={r.title!r}")
        print(f"       {r.content[:1000].strip()!r}")

    return results


def _make_retriever(
    embedding_model: EmbeddingsModel,
    vector_store: VectorStore,
    top_k: int,
    use_task_prefix: bool = True,
) -> VectorStoreRetriever:
    """Return a retriever with the correct query prefix for the embedding model."""
    if use_task_prefix:
        prefixes = _task_prefixes(getattr(embedding_model, "model_name", ""))
        if prefixes:
            return TaskPrefixRetriever(embedding_model, vector_store, top_k, query_prefix=prefixes[1])
    return VectorStoreRetriever(embedding_model, vector_store, top_k=top_k)


def build_agent(
    vector_store: VectorStore,
    embedding_model: EmbeddingsModel,
    llm: LLM,
    top_k: int,
    system_prompt: str,
    number_query_expansion: int = 0,
    enable_hyde: bool = False,
) -> RAG:
    retriever = _make_retriever(embedding_model, vector_store, top_k)
    agent = RAG(
        llm=llm,
        utility_llm=llm,
        system_prompt=system_prompt,
        retrievers=[retriever],
        number_query_expansion=number_query_expansion,
        enable_hyde=enable_hyde,
    )
    logger.info(
        f"RAG agent ready (top_k={top_k}  query_expansion={number_query_expansion})"
    )
    return agent


async def ask(
    agent: RAG,
    query: str,
    history: list[LLMMessage] | None = None,
) -> str:
    logger.info(f"Query: {query!r}")
    response = await agent.answer(QueryWithContext(query=query, history=history or []))

    answer_text = "".join(mc.text for mc in response.content if mc.text)

    logger.info("Answer:")
    print(answer_text)
    print(f"Sources ({len(response.sources)}):")
    for src in response.sources:
        source_file = src.metadata.get("source_file", "?")
        print(f"  {source_file!r}  |  {src.title!r}")

    return answer_text


async def run_pipeline(
    backend: str,
    model_name: str | None = None,
    query: str = "What sustainability certifications do the pallets have?",
    reset_vs: bool = False,
    embedding_backend: str | None = None,
) -> str:
    logger.info("Starting Baseline RAG pipeline")

    chunks = load_chunks(max_files=MAX_FILES)
    inspect_chunks(chunks)

    embedding_model = build_embedding_model(embedding_backend)
    vector_store = await build_vector_store(chunks, embedding_model, reset=reset_vs)

    await inspect_retrieval(query, vector_store, embedding_model)

    llm = build_llm(backend, model_name=model_name)
    agent = build_agent(
        vector_store,
        embedding_model,
        llm,
        top_k=RETRIEVER_TOP_K,
        system_prompt=SYSTEM_PROMPT,
    )

    answer = await ask(agent, query)
    logger.info("Baseline RAG pipeline done")
    return answer


if __name__ == "__main__":
    _backend = os.getenv("BACKEND")
    if not _backend:
        raise SystemExit(
            "The BACKEND environment variable is not set.\n"
            "Choose one of: ollama, openai, qwen\n"
            "Example: BACKEND=ollama python -m lancy.feature0_baseline_rag"
        )
    asyncio.run(
        run_pipeline(
            backend=_backend,
            model_name=os.getenv("MODEL") or None,
            query=os.getenv("QUERY", "What materials is the Lara pallet made out of?"),
            reset_vs=os.getenv("RESET_VS", "0") == "1",
            embedding_backend=os.getenv("EMBEDDING_BACKEND", "local"),
        )
    )
