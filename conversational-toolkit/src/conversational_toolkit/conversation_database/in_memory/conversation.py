import json

from loguru import logger

from conversational_toolkit.conversation_database.data_models.conversation import (
    ConversationDatabase,
    Conversation,
)
from conversational_toolkit.utils.database import generate_uid


class InMemoryConversationDatabase(ConversationDatabase):
    def __init__(self, json_file_path: str):
        self.json_file_path = json_file_path
        self.conversations: dict[str, Conversation] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self.json_file_path, "r") as f:
                data = json.load(f)
                self.conversations = {k: Conversation(**v) for k, v in data.items()}
        except FileNotFoundError:
            self._save()

    def _save(self) -> None:
        with open(self.json_file_path, "w") as f:
            json.dump(
                {
                    conversation_id: conversation.model_dump()
                    for conversation_id, conversation in self.conversations.items()
                },
                f,
                indent=4,
            )

    async def create_conversation(self, conversation: Conversation) -> Conversation:
        if not conversation.id:
            conversation.id = generate_uid()
        self.conversations[conversation.id] = conversation
        self._save()
        logger.debug(f"Created conversation: {conversation}")
        return conversation

    async def get_conversations_by_user_id(self, user_id: str) -> list[Conversation]:
        return [conv for conv in self.conversations.values() if conv.user_id == user_id]

    async def get_conversation_by_id(self, conversation_id: str) -> Conversation:
        conversation = self.conversations.get(conversation_id)
        if conversation is None:
            raise ValueError(f"Conversation with id {conversation_id} not found")
        return conversation

    async def update_conversation(
        self,
        conversation: Conversation,
    ) -> Conversation:
        if self.conversations.get(conversation.id) is None:
            raise ValueError(f"Conversation with id {conversation.id} not found")
        self.conversations[conversation.id] = conversation
        self._save()
        logger.debug(f"Updated conversation: {conversation}")
        return conversation

    async def delete_conversation(self, conversation_id: str) -> bool:
        if conversation_id in self.conversations:
            del self.conversations[conversation_id]
            self._save()
            logger.debug(f"Deleted conversation: {conversation_id}")
            return True
        return False
