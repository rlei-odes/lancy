from sqlalchemy import Column, String
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import relationship

from conversational_toolkit.conversation_database.data_models.user import User, UserDatabase
from conversational_toolkit.conversation_database.postgres.index import Base
from conversational_toolkit.utils.database import generate_uid

from loguru import logger


class UserTable(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True)
    conversations = relationship("ConversationTable", order_by="ConversationTable.id", back_populates="user")
    messages = relationship("MessageTable", order_by="MessageTable.id", back_populates="user")

    def to_model(self) -> User:
        return User(
            id=str(self.id),
        )


class PostgreSQLUserDatabase(UserDatabase):
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.make_session = async_sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    async def create_table(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(UserTable.metadata.create_all)

    async def create_user(self, user: User) -> User:
        async with self.make_session() as session:
            async with session.begin():
                try:
                    if not user.id:
                        user.id = generate_uid()
                    db_user = UserTable(id=user.id)
                    session.add(db_user)
                    await session.commit()
                    return db_user.to_model()
                except Exception as e:
                    logger.error(f"Error creating user: {e}")
                    await session.rollback()
                    raise

    async def get_user_by_id(self, user_id: str) -> User | None:
        async with self.make_session() as session:
            try:
                user: UserTable | None = await session.get(UserTable, user_id)
                if not user:
                    return None
                return user.to_model()
            except Exception as e:
                logger.error(f"Error retrieving user {user_id}: {e}")
                await session.rollback()
                return None
