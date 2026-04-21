from sqlalchemy import Column, String, BigInteger, select, ForeignKey
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import relationship


from conversational_toolkit.conversation_database.data_models.conversation import (
    Conversation,
    ConversationDatabase,
)
from conversational_toolkit.conversation_database.postgres.index import Base
from conversational_toolkit.utils.database import generate_uid

from loguru import logger


class ConversationTable(Base):
    __tablename__ = "conversations"
    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id"))
    create_timestamp = Column(BigInteger)
    update_timestamp = Column(BigInteger)
    title = Column(String)
    user = relationship("UserTable", back_populates="conversations")
    messages = relationship("MessageTable", order_by="MessageTable.id", back_populates="conversation")

    def to_model(self) -> Conversation:
        return Conversation(
            id=str(self.id),
            user_id=str(self.user_id),
            create_timestamp=int(self.create_timestamp),  # type: ignore[arg-type]
            update_timestamp=int(self.update_timestamp),  # type: ignore[arg-type]
            title=str(self.title),
        )


class PostgreSQLConversationDatabase(ConversationDatabase):
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.make_session = async_sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    async def create_table(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(ConversationTable.metadata.create_all)

    async def create_conversation(self, conversation: Conversation) -> Conversation:
        async with self.make_session() as session:
            async with session.begin():
                try:
                    db_conversation = ConversationTable(
                        id=conversation.id or generate_uid(),
                        user_id=conversation.user_id,
                        create_timestamp=conversation.create_timestamp,
                        update_timestamp=conversation.update_timestamp,
                        title=conversation.title,
                    )
                    session.add(db_conversation)
                    await session.commit()
                    return db_conversation.to_model()
                except Exception as e:
                    logger.error(f"Error creating conversation: {e}")
                    await session.rollback()
                    raise

    async def get_conversations_by_user_id(self, user_id: str) -> list[Conversation]:
        async with self.make_session() as session:
            try:
                result = await session.execute(select(ConversationTable).filter_by(user_id=user_id))
                conversations = result.scalars().all()
                return [conversation.to_model() for conversation in conversations]
            except Exception as e:
                logger.error(f"Error retrieving conversations for user {user_id}: {e}")
                await session.rollback()
                raise

    async def get_conversation_by_id(self, conversation_id: str) -> Conversation:
        async with self.make_session() as session:
            try:
                conversation: ConversationTable | None = await session.get(ConversationTable, conversation_id)
                if conversation is None:
                    raise ValueError(f"Conversation with id {conversation_id} not found")
                return conversation.to_model()
            except Exception as e:
                logger.error(f"Error retrieving conversation {conversation_id}: {e}")
                await session.rollback()
                raise

    async def update_conversation(
        self,
        conversation: Conversation,
    ) -> Conversation:
        async with self.make_session() as session:
            async with session.begin():
                try:
                    db_conversation: ConversationTable | None = await session.get(ConversationTable, conversation.id)
                    if db_conversation:
                        setattr(db_conversation, "user_id", conversation.user_id)
                        setattr(db_conversation, "create_timestamp", conversation.create_timestamp)
                        setattr(db_conversation, "update_timestamp", conversation.update_timestamp)
                        setattr(db_conversation, "title", conversation.title)
                        await session.commit()
                        return db_conversation.to_model()
                    raise ValueError(f"Conversation with id {conversation.id} not found")
                except Exception as e:
                    logger.error(f"Error updating conversation {conversation.id}: {e}")
                    await session.rollback()
                    raise

    async def delete_conversation(self, conversation_id: str) -> bool:
        async with self.make_session() as session:
            async with session.begin():
                try:
                    conversation = await session.get(ConversationTable, conversation_id)
                    if conversation:
                        await session.delete(conversation)
                        await session.commit()
                        return True
                    return False
                except Exception as e:
                    logger.error(f"Error deleting conversation {conversation_id}: {e}")
                    await session.rollback()
                    raise
