from sqlalchemy import Column, String, BigInteger, select, ForeignKey, text
from sqlalchemy.types import JSON
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import relationship

from conversational_toolkit.conversation_database.data_models.conversation import Conversation, ConversationDatabase
from conversational_toolkit.conversation_database.sqlite.index import Base
from conversational_toolkit.utils.database import generate_uid

from loguru import logger


class ConversationTable(Base):
    __tablename__ = "conversations"
    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id"))
    create_timestamp = Column(BigInteger)
    update_timestamp = Column(BigInteger)
    title = Column(String)
    kb_id = Column(String, nullable=True)
    kb_name = Column(String, nullable=True)
    rag_config_snapshot = Column(JSON, nullable=True)
    session_label = Column(String, nullable=True)
    user = relationship("UserTable", back_populates="conversations")
    messages = relationship("MessageTable", order_by="MessageTable.id", back_populates="conversation")

    def to_model(self) -> Conversation:
        return Conversation(
            id=str(self.id),
            user_id=str(self.user_id),
            create_timestamp=int(self.create_timestamp),  # type: ignore[arg-type]
            update_timestamp=int(self.update_timestamp),  # type: ignore[arg-type]
            title=str(self.title),
            kb_id=self.kb_id,
            kb_name=self.kb_name,
            rag_config_snapshot=self.rag_config_snapshot,
            session_label=self.session_label,
        )


class SQLiteConversationDatabase(ConversationDatabase):
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.make_session = async_sessionmaker(bind=self.engine, expire_on_commit=False, class_=AsyncSession)

    async def create_table(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(ConversationTable.metadata.create_all)

    async def create_conversation(self, conversation: Conversation) -> Conversation:
        async with self.make_session() as session:
            async with session.begin():
                try:
                    db_conv = ConversationTable(
                        id=conversation.id or generate_uid(),
                        user_id=conversation.user_id,
                        create_timestamp=conversation.create_timestamp,
                        update_timestamp=conversation.update_timestamp,
                        title=conversation.title,
                        kb_id=conversation.kb_id,
                        kb_name=conversation.kb_name,
                        rag_config_snapshot=conversation.rag_config_snapshot,
                        session_label=conversation.session_label,
                    )
                    session.add(db_conv)
                    await session.commit()
                    return db_conv.to_model()
                except Exception as e:
                    logger.error(f"Error creating conversation: {e}")
                    await session.rollback()
                    raise

    async def get_conversations_by_user_id(self, user_id: str) -> list[Conversation]:
        async with self.make_session() as session:
            try:
                result = await session.execute(select(ConversationTable).filter_by(user_id=user_id))
                return [c.to_model() for c in result.scalars().all()]
            except Exception as e:
                logger.error(f"Error retrieving conversations for user {user_id}: {e}")
                await session.rollback()
                raise

    async def get_conversation_by_id(self, conversation_id: str) -> Conversation:
        async with self.make_session() as session:
            try:
                conv: ConversationTable | None = await session.get(ConversationTable, conversation_id)
                if conv is None:
                    raise ValueError(f"Conversation with id {conversation_id} not found")
                return conv.to_model()
            except Exception as e:
                logger.error(f"Error retrieving conversation {conversation_id}: {e}")
                await session.rollback()
                raise

    async def update_conversation(self, conversation: Conversation) -> Conversation:
        async with self.make_session() as session:
            async with session.begin():
                try:
                    db_conv: ConversationTable | None = await session.get(ConversationTable, conversation.id)
                    if db_conv:
                        setattr(db_conv, "user_id", conversation.user_id)
                        setattr(db_conv, "create_timestamp", conversation.create_timestamp)
                        setattr(db_conv, "update_timestamp", conversation.update_timestamp)
                        setattr(db_conv, "title", conversation.title)
                        setattr(db_conv, "kb_id", conversation.kb_id)
                        setattr(db_conv, "kb_name", conversation.kb_name)
                        setattr(db_conv, "rag_config_snapshot", conversation.rag_config_snapshot)
                        setattr(db_conv, "session_label", conversation.session_label)
                        await session.commit()
                        return db_conv.to_model()
                    raise ValueError(f"Conversation with id {conversation.id} not found")
                except Exception as e:
                    logger.error(f"Error updating conversation {conversation.id}: {e}")
                    await session.rollback()
                    raise

    async def delete_conversation(self, conversation_id: str) -> bool:
        async with self.make_session() as session:
            async with session.begin():
                try:
                    conv = await session.get(ConversationTable, conversation_id)
                    if conv:
                        await session.delete(conv)
                        await session.commit()
                        return True
                    return False
                except Exception as e:
                    logger.error(f"Error deleting conversation {conversation_id}: {e}")
                    await session.rollback()
                    raise
