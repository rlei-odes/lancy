from typing import Any

import numpy as np
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from conversational_toolkit.chunking.base import Chunk
from conversational_toolkit.utils.database import generate_uid
from conversational_toolkit.vectorstores.base import VectorStore, ChunkMatch, ChunkRecord

from sqlalchemy import text, and_, func
from sqlalchemy import MetaData
from sqlalchemy import Table, Column, String, JSON
from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]
from numpy.typing import NDArray

from sqlalchemy import insert, select


class PGVectorStore(VectorStore):
    def __init__(
        self,
        engine: AsyncEngine,
        table_name: str,
        embeddings_size: int,
    ):
        self.table_name = table_name
        self.engine = engine
        self.SessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.embeddings_size = embeddings_size
        self._initialized = False

        self.metadata = MetaData()
        self.table = Table(
            table_name,
            self.metadata,
            Column("id", String, primary_key=True, index=True),
            Column("title", String, index=False),
            Column("content", String, index=False),
            Column("mime_type", String, index=False),
            Column("embedding", Vector(self.embeddings_size)),
            Column("chunk_metadata", JSON, nullable=True),
        )

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        await self.enable_vector_extension()
        await self.create_table()
        self._initialized = True

    async def enable_vector_extension(self) -> None:
        async with self.engine.begin() as session:
            await session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

    async def create_table(self) -> None:
        async with self.engine.begin() as session:
            await session.run_sync(self.metadata.create_all)

    async def clear(self) -> None:
        """Remove all rows from the table (keeps table structure intact)."""
        await self._ensure_initialized()
        async with self.engine.begin() as session:
            await session.execute(text(f"TRUNCATE TABLE {self.table_name}"))

    async def count(self) -> int:
        """Return the total number of chunks stored."""
        await self._ensure_initialized()
        async with self.SessionLocal() as session:
            result = await session.execute(select(func.count()).select_from(self.table))
            return result.scalar() or 0

    async def insert_chunks(self, chunks: list[Chunk], embedding: NDArray[np.float64]) -> None:
        await self._ensure_initialized()
        data_to_insert = [
            {
                "id": generate_uid(),
                "title": chunk.title,
                "content": chunk.content,
                "embedding": emb,
                "mime_type": chunk.mime_type,
                "chunk_metadata": chunk.metadata,
            }
            for chunk, emb in zip(chunks, embedding)
        ]

        async with self.SessionLocal() as session:
            async with session.begin():
                stmt = insert(self.table)
                await session.execute(stmt, data_to_insert)

    async def get_chunks_by_embedding(
        self,
        embedding: NDArray[np.float64],
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[ChunkMatch]:
        await self._ensure_initialized()
        async with self.SessionLocal() as session:
            query = select(self.table, (1 - self.table.columns.embedding.cosine_distance(embedding)).label("score"))

            if filters:
                conditions = [self.table.c.chunk_metadata[key].astext == str(value) for key, value in filters.items()]
                query = query.where(and_(*conditions))

            query = query.order_by(text("score DESC")).limit(top_k)

            chunks = await session.execute(query)
            results = [
                ChunkMatch(
                    id=chunk.id,
                    title=chunk.title,  # type: ignore[reportCallIssue]
                    content=chunk.content,  # type: ignore[reportCallIssue]
                    embedding=list(chunk.embedding) if chunk.embedding is not None else [],
                    mime_type=chunk.mime_type,  # type: ignore[reportCallIssue]
                    metadata=chunk.chunk_metadata or {},
                    score=chunk.score,
                )
                for chunk in chunks
            ]
        return results

    async def get_chunks_by_ids(self, chunk_ids: int | list[int]) -> list[Chunk]:
        await self._ensure_initialized()
        if isinstance(chunk_ids, int):
            chunk_ids = [chunk_ids]

        if not chunk_ids:
            return []

        async with self.SessionLocal() as session:
            results = await session.execute(select(self.table).where(self.table.columns.id.in_(chunk_ids)))

            chunks = [
                Chunk(
                    title=result.title,
                    content=result.content,
                    mime_type="text/plain",
                    metadata=result.chunk_metadata or {},
                )
                for result in results
            ]
        return chunks

    async def get_chunks_by_filter(self, filters: dict[str, Any] | None = None) -> list[ChunkRecord]:
        """Return all chunks matching the given metadata filters."""
        await self._ensure_initialized()
        async with self.SessionLocal() as session:
            query = select(self.table)
            if filters:
                conditions = [self.table.c.chunk_metadata[key].astext == str(value) for key, value in filters.items()]
                query = query.where(and_(*conditions))

            result = await session.execute(query)
            return [
                ChunkRecord(
                    id=row.id,
                    title=row.title or "",
                    content=row.content or "",
                    mime_type=row.mime_type or "text/plain",
                    metadata=row.chunk_metadata or {},
                    embedding=[],
                )
                for row in result
            ]

    async def get_source_files(self) -> list[str]:
        """Return a sorted list of unique source file names via a DISTINCT SQL query."""
        await self._ensure_initialized()
        async with self.SessionLocal() as session:
            result = await session.execute(
                select(self.table.c.chunk_metadata["source_file"].astext)
                .distinct()
                .where(self.table.c.chunk_metadata["source_file"].astext.isnot(None))
            )
            return sorted({row[0] for row in result if row[0]})

    async def get_file_hashes(self) -> set[str]:
        """Return the set of file_hash values present in this collection's chunk metadata."""
        await self._ensure_initialized()
        async with self.SessionLocal() as session:
            result = await session.execute(
                select(self.table.c.chunk_metadata["file_hash"].astext)
                .distinct()
                .where(self.table.c.chunk_metadata["file_hash"].astext.isnot(None))
            )
            return {row[0] for row in result if row[0]}
