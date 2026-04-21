import json
import chromadb
from typing import Any
import numpy as np
from numpy.typing import NDArray

from conversational_toolkit.chunking.base import Chunk
from conversational_toolkit.utils.database import generate_uid
from conversational_toolkit.vectorstores.base import VectorStore, ChunkMatch, ChunkRecord


class ChromaDBVectorStore(VectorStore):
    def __init__(self, db_path: str, collection_name: str = "default_collection"):
        """
        Initialize the ChromaDB vector store.

        :param db_path: Path to store the ChromaDB database.
        :param collection_name: Name of the collection within the database.
        """
        self.client = chromadb.PersistentClient(path=db_path)
        self.collection = self.client.get_or_create_collection(name=collection_name)

    async def insert_chunks(self, chunks: list[Chunk], embedding: NDArray[np.float64]) -> None:
        """
        Insert chunks into ChromaDB.

        :param chunks: List of document chunks
        :param embedding: Corresponding embedding vectors
        """
        documents = []
        metadatas = []
        ids = []

        for chunk, _ in zip(chunks, embedding):
            doc_id = str(generate_uid())
            documents.append(chunk.content)
            raw_meta = {"title": chunk.title, "mime_type": chunk.mime_type, **chunk.metadata}
            # ChromaDB only accepts str/int/float/bool — serialize anything else to JSON
            safe_meta = {k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in raw_meta.items()}
            metadatas.append(safe_meta)
            ids.append(doc_id)

        self.collection.add(
            ids=ids,
            embeddings=embedding.tolist(),  # type: ignore
            metadatas=metadatas,  # type: ignore
            documents=documents,
        )

    async def get_chunks_by_embedding(
        self, embedding: NDArray[np.float64], top_k: int, filters: dict[str, Any] | None = None
    ) -> list[ChunkMatch]:
        """
        Retrieve chunks most similar to the given embedding.

        :param embedding: Query embedding
        :param top_k: Number of results to return
        :param filters: Optional filters for metadata
        """
        import asyncio
        import logging as _log

        _log.getLogger("uvicorn").debug("chromadb: get_chunks_by_embedding START")
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None, lambda: self.collection.query(query_embeddings=embedding.tolist(), n_results=top_k, where=filters)
        )  # type: ignore
        _log.getLogger("uvicorn").debug(
            f"chromadb: get_chunks_by_embedding DONE, {len(results['ids'][0]) if results and results['ids'] else 0} results"
        )

        chunk_matches = []
        if results and results["ids"]:
            for i in range(len(results["ids"][0])):
                metadata: dict[str, Any] = dict(results["metadatas"][0][i]) if results["metadatas"] else {}
                chunk_matches.append(
                    ChunkMatch(
                        id=results["ids"][0][i],
                        title=str(metadata.get("title", "")),  # type: ignore[reportCallIssue]
                        mime_type=str(metadata.get("mime_type", "")),  # type: ignore[reportCallIssue]
                        metadata=metadata,
                        content=results["documents"][0][i] if results["documents"] else "",  # type: ignore[reportCallIssue]
                        embedding=[],
                        score=results["distances"][0][i] if results["distances"] else 0.0,
                    )
                )

        return chunk_matches

    async def get_chunks_by_filter(self, filters: dict[str, Any] | None = None) -> list[ChunkRecord]:
        """
        Return all chunks matching the given metadata filters (no embedding needed).

        Uses ChromaDB's 'collection.get(where=filters)'. Supports ChromaDB filter
        operators: '$eq', '$ne', '$gt', '$lt', '$gte', '$lte', '$and', '$or'.

        Example — fetch all chunks from a specific file at a given index:
            filters = {
                "$and": [
                    {"source_file": {"$eq": "report.pdf"}},
                    {"chunk_index": {"$eq": 3}},
                ]
            }
        """
        import asyncio
        import logging as _log

        _log.getLogger("uvicorn").debug("chromadb: get_chunks_by_filter START")
        loop = asyncio.get_running_loop()
        if not filters:
            results = await loop.run_in_executor(None, self.collection.get)
        else:
            results = await loop.run_in_executor(None, lambda: self.collection.get(where=filters))  # type: ignore[arg-type]
        _log.getLogger("uvicorn").debug(
            f"chromadb: get_chunks_by_filter DONE, {len(results['ids']) if results and results['ids'] else 0} results"
        )

        chunk_records = []
        if results and results["ids"]:
            for i in range(len(results["ids"])):
                metadata: dict[str, Any] = dict(results["metadatas"][i]) if results["metadatas"] else {}
                chunk_records.append(
                    ChunkRecord(
                        id=results["ids"][i],
                        title=str(metadata.get("title", "")),  # type: ignore[reportCallIssue]
                        mime_type=str(metadata.get("mime_type", "")),  # type: ignore[reportCallIssue]
                        content=results["documents"][i] if results["documents"] else "",  # type: ignore[reportCallIssue]
                        metadata=metadata,
                        embedding=[],
                    )
                )
        return chunk_records

    async def count(self) -> int:
        """Return the total number of chunks in the collection."""
        return self.collection.count()

    async def get_source_files(self) -> list[str]:
        """Return a sorted list of unique source file names (metadata-only fetch, no embeddings)."""
        import asyncio

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, lambda: self.collection.get(include=["metadatas"]))
        return sorted({m.get("source_file", "") for m in (result.get("metadatas") or []) if m and m.get("source_file")})

    async def get_file_hashes(self) -> set[str]:
        """Return the set of file_hash values present in this collection's chunk metadata."""
        import asyncio

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, lambda: self.collection.get(include=["metadatas"]))
        return {m["file_hash"] for m in (result.get("metadatas") or []) if m and "file_hash" in m}

    async def get_chunks_by_ids(self, chunk_ids: int | list[int]) -> list[Chunk]:
        """
        Retrieve chunks by their IDs.

        :param chunk_ids: A single ID or a list of IDs
        :return: List of retrieved chunks
        """
        if isinstance(chunk_ids, int):
            chunk_ids = [str(chunk_ids)]  # type: ignore
        else:
            chunk_ids = [str(cid) for cid in chunk_ids]  # type: ignore

        results = self.collection.get(ids=chunk_ids)  # type: ignore

        chunks = []
        if results and results["ids"]:
            for i in range(len(results["ids"])):
                metadata: dict[str, Any] = dict(results["metadatas"][i]) if results["metadatas"] else {}
                chunks.append(
                    Chunk(
                        title=str(metadata.get("title", "")),
                        mime_type=str(metadata.get("mime_type", "")),
                        content=results["documents"][i] if results["documents"] else "",
                        metadata=metadata,
                    )
                )

        return chunks
