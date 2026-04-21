from typing import Any, cast

from sqlalchemy import Column, String, ForeignKey, Text, BigInteger, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import relationship


from conversational_toolkit.conversation_database.data_models.message import Message, MessageDatabase
from conversational_toolkit.conversation_database.postgres.index import Base
from conversational_toolkit.utils.database import generate_uid
from conversational_toolkit.llms.base import Roles

from loguru import logger


class MessageTable(Base):
    __tablename__ = "messages"
    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id"))
    conversation_id = Column(String, ForeignKey("conversations.id"))
    content = Column(Text)
    role = Column(String)
    create_timestamp = Column(BigInteger)
    parent_id = Column(String, ForeignKey("messages.id"))
    metadata_ = Column("metadata", JSONB)
    conversation = relationship("ConversationTable", back_populates="messages")
    user = relationship("UserTable", back_populates="messages")
    sources = relationship("SourceTable", order_by="SourceTable.id", back_populates="message")
    reactions = relationship("ReactionTable", order_by="ReactionTable.id", back_populates="message")

    def to_model(self) -> Message:
        return Message(
            id=str(self.id),
            user_id=str(self.user_id) if self.user_id is not None else None,
            conversation_id=str(self.conversation_id),
            content=str(self.content),
            role=Roles(str(self.role)),
            create_timestamp=int(self.create_timestamp),  # type: ignore[arg-type]
            parent_id=str(self.parent_id) if self.parent_id is not None else None,
            metadata=cast(list[dict[str, Any]], self.metadata_),
        )


class PostgreSQLMessageDatabase(MessageDatabase):
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.make_session = async_sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    async def create_table(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(MessageTable.metadata.create_all)

    async def create_message(
        self,
        message: Message,
    ) -> Message:
        async with self.make_session() as session:
            async with session.begin():
                try:
                    db_message = MessageTable(
                        id=message.id or generate_uid(),
                        user_id=message.user_id,
                        conversation_id=message.conversation_id,
                        content=message.content,
                        role=message.role,
                        create_timestamp=message.create_timestamp,
                        metadata_=message.metadata,
                        parent_id=message.parent_id,
                    )
                    session.add(db_message)
                    await session.commit()
                    return db_message.to_model()
                except Exception as e:
                    logger.error(f"Error creating message: {e}")
                    await session.rollback()
                    raise

    async def get_messages_by_conversation_id(self, conversation_id: str) -> list[Message]:
        async with self.make_session() as session:
            try:
                result = await session.execute(select(MessageTable).filter_by(conversation_id=conversation_id))
                messages = result.scalars().all()
                return [msg.to_model() for msg in messages]
            except Exception as e:
                logger.error(f"Error retrieving messages for conversation {conversation_id}: {e}")
                await session.rollback()
                raise

    async def get_message_by_id(self, message_id: str) -> Message:
        async with self.make_session() as session:
            try:
                message: MessageTable | None = await session.get(MessageTable, message_id)
                if message is None:
                    raise ValueError(f"Message with id {message_id} not found")
                return message.to_model()
            except Exception as e:
                logger.error(f"Error retrieving conversation {message_id}: {e}")
                await session.rollback()
                raise

    async def delete_message(self, message_id: str) -> bool:
        async with self.make_session() as session:
            async with session.begin():
                try:
                    message = await session.get(MessageTable, message_id)
                    if message:
                        await session.delete(message)
                        await session.commit()
                        return True
                    return False
                except Exception as e:
                    logger.error(f"Error deleting message {message_id}: {e}")
                    await session.rollback()
                    raise
