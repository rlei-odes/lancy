import json

from sqlalchemy import Column, String, ForeignKey, Text, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import relationship

from conversational_toolkit.conversation_database.data_models.source import Source, SourceDatabase
from conversational_toolkit.conversation_database.postgres.index import Base
from conversational_toolkit.utils.database import generate_uid

from loguru import logger


class SourceTable(Base):
    __tablename__ = "sources"
    id = Column(String, primary_key=True)
    message_id = Column(String, ForeignKey("messages.id"))
    content = Column(Text)
    metadata_ = Column("metadata", Text)  # Store JSON as text for simplicity
    message = relationship("MessageTable", back_populates="sources")

    def to_model(self) -> Source:
        return Source(
            id=str(self.id),
            message_id=str(self.message_id),
            content=str(self.content),
            metadata=json.loads(str(self.metadata_)),
        )


class PostgreSQLSourceDatabase(SourceDatabase):
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.make_session = async_sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    async def create_table(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(SourceTable.metadata.create_all)

    async def create_source(self, source: Source) -> Source:
        async with self.make_session() as session:
            async with session.begin():
                try:
                    db_source = SourceTable(
                        id=source.id or generate_uid(),
                        message_id=source.message_id,
                        content=source.content,
                        metadata_=json.dumps(source.metadata),
                    )
                    session.add(db_source)
                    await session.commit()
                    return db_source.to_model()
                except Exception as e:
                    logger.error(f"Error creating source: {e}")
                    await session.rollback()
                    raise

    async def get_sources_by_message_id(self, message_id: str) -> list[Source]:
        async with self.make_session() as session:
            try:
                result = await session.execute(select(SourceTable).filter_by(message_id=message_id))
                sources = result.scalars().all()
                return [source.to_model() for source in sources]
            except Exception as e:
                logger.error(f"Error retrieving sources for message {message_id}: {e}")
                await session.rollback()
                raise

    async def delete_sources(self, source_ids: list[str]) -> bool:
        async with self.make_session() as session:
            async with session.begin():
                try:
                    for source_id in source_ids:
                        source = await session.get(SourceTable, source_id)
                        if source:
                            await session.delete(source)
                    await session.commit()
                    return True
                except Exception as e:
                    logger.error(f"Error deleting sources {source_ids}: {e}")
                    await session.rollback()
                    raise
