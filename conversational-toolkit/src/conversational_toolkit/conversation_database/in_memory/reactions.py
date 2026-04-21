import json

from loguru import logger

from conversational_toolkit.conversation_database.data_models.reaction import ReactionDatabase, Reaction
from conversational_toolkit.utils.database import generate_uid


class InMemoryReactionDatabase(ReactionDatabase):
    def __init__(self, json_file_path: str):
        self.json_file_path = json_file_path
        self.reactions: dict[str, Reaction] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self.json_file_path, "r") as f:
                data = json.load(f)
                self.reactions = {k: Reaction(**v) for k, v in data.items()}
        except FileNotFoundError:
            self._save()

    def _save(self) -> None:
        with open(self.json_file_path, "w") as f:
            json.dump(
                {reaction_id: reaction.model_dump() for reaction_id, reaction in self.reactions.items()}, f, indent=4
            )

    async def create_reaction(self, reaction: Reaction) -> Reaction:
        if not reaction.id:
            reaction.id = generate_uid()
        self.reactions[reaction.id] = reaction
        self._save()
        logger.debug(f"Created reaction: {reaction}")
        return reaction

    async def get_reactions_by_message_id(self, message_id: str) -> list[Reaction]:
        return [react for react in self.reactions.values() if react.message_id == message_id]

    async def delete_reactions(self, reaction_ids: list[str]) -> bool:
        for reaction_id in reaction_ids:
            if reaction_id in self.reactions:
                del self.reactions[reaction_id]
        self._save()
        logger.debug(f"Deleted reactions: {reaction_ids}")
        return True
