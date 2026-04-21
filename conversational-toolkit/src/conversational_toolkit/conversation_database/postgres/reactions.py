from sqlalchemy import Column, String, ForeignKey, Text, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import relationship

from conversational_toolkit.conversation_database.data_models.reaction import Reaction, ReactionDatabase
from conversational_toolkit.conversation_database.postgres.index import Base

from loguru import logger

from conversational_toolkit.utils.database import generate_uid


class ReactionTable(Base):
    __tablename__ = "reactions"
    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id"))
    message_id = Column(String, ForeignKey("messages.id"))
    content = Column(Text)
    note = Column(Text, nullable=True)
    message = relationship("MessageTable", back_populates="reactions")

    def to_model(self) -> Reaction:
        return Reaction(
            id=str(self.id),
            message_id=str(self.message_id),
            content=str(self.content),
            note=str(self.note) if self.note is not None else None,
            user_id=str(self.user_id),
        )


class PostgreSQLReactionDatabase(ReactionDatabase):
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.make_session = async_sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    async def create_table(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(ReactionTable.metadata.create_all)

    async def create_reaction(self, reaction: Reaction) -> Reaction:
        async with self.make_session() as session:
            async with session.begin():
                try:
                    db_reaction = ReactionTable(
                        id=reaction.id or generate_uid(),
                        message_id=reaction.message_id,
                        content=reaction.content,
                        note=reaction.note,
                        user_id=reaction.user_id,
                    )
                    session.add(db_reaction)
                    await session.commit()
                    return db_reaction.to_model()
                except Exception as e:
                    logger.error(f"Error creating reaction: {e}")
                    await session.rollback()
                    raise

    async def get_reactions_by_message_id(self, message_id: str) -> list[Reaction]:
        async with self.make_session() as session:
            try:
                result = await session.execute(select(ReactionTable).filter_by(message_id=message_id))
                reactions = result.scalars().all()
                return [reaction.to_model() for reaction in reactions]
            except Exception as e:
                logger.error(f"Error retrieving reactions for message {message_id}: {e}")
                await session.rollback()
                raise

    async def delete_reactions(self, reaction_ids: list[str]) -> bool:
        async with self.make_session() as session:
            async with session.begin():
                try:
                    for reaction_id in reaction_ids:
                        reaction = await session.get(ReactionTable, reaction_id)
                        if reaction:
                            await session.delete(reaction)
                    await session.commit()
                    return True
                except Exception as e:
                    logger.error(f"Error deleting reactions {reaction_ids}: {e}")
                    await session.rollback()
                    raise
