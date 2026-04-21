import json

from loguru import logger

from conversational_toolkit.conversation_database.data_models.user import UserDatabase, User
from conversational_toolkit.utils.database import generate_uid


class InMemoryUserDatabase(UserDatabase):
    def __init__(self, json_file_path: str):
        self.json_file_path = json_file_path
        self.users: dict[str, User] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self.json_file_path, "r") as f:
                data = json.load(f)
                self.users = {k: User(**v) for k, v in data.items()}
        except FileNotFoundError:
            self._save()

    def _save(self) -> None:
        with open(self.json_file_path, "w") as f:
            json.dump({user_id: user.model_dump() for user_id, user in self.users.items()}, f, indent=4)

    async def create_user(self, user: User) -> User:
        if not user.id:
            user.id = generate_uid()
        self.users[user.id] = user
        self._save()
        logger.debug(f"Created user: {user}")
        return user

    async def get_user_by_id(self, user_id: str) -> User | None:
        user = self.users.get(user_id)
        if not user:
            return None
        return user
