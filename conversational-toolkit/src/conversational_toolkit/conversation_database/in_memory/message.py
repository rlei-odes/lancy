import json

from loguru import logger

from conversational_toolkit.conversation_database.data_models.message import MessageDatabase, Message
from conversational_toolkit.utils.database import generate_uid


class InMemoryMessageDatabase(MessageDatabase):
    def __init__(self, json_file_path: str):
        self.json_file_path = json_file_path
        self.messages: dict[str, Message] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self.json_file_path, "r") as f:
                data = json.load(f)
                self.messages = {k: Message(**v) for k, v in data.items()}
        except FileNotFoundError:
            self._save()

    def _save(self) -> None:
        with open(self.json_file_path, "w") as f:
            json.dump({message_id: message.model_dump() for message_id, message in self.messages.items()}, f, indent=4)

    async def create_message(
        self,
        message: Message,
    ) -> Message:
        if not message.id:
            message.id = generate_uid()
        self.messages[message.id] = message
        self._save()
        logger.debug(f"Created message: {message}")
        return message

    async def get_messages_by_conversation_id(self, conversation_id: str) -> list[Message]:
        return [msg for msg in self.messages.values() if msg.conversation_id == conversation_id]

    async def get_message_by_id(self, message_id: str) -> Message:
        message = self.messages.get(message_id)
        if message is None:
            raise ValueError(f"Message with id {message_id} not found")
        return message

    async def delete_message(self, message_id: str) -> bool:
        if message_id in self.messages:
            del self.messages[message_id]
            self._save()
            logger.debug(f"Deleted message: {message_id}")
            return True
        return False
