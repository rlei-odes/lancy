import json

from loguru import logger

from conversational_toolkit.conversation_database.data_models.source import SourceDatabase, Source
from conversational_toolkit.utils.database import generate_uid


class InMemorySourceDatabase(SourceDatabase):
    def __init__(self, json_file_path: str):
        self.json_file_path = json_file_path
        self.sources: dict[str, Source] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self.json_file_path, "r") as f:
                data = json.load(f)
                self.sources = {k: Source(**v) for k, v in data.items()}
        except FileNotFoundError:
            self._save()

    def _save(self) -> None:
        with open(self.json_file_path, "w") as f:
            json.dump({source_id: source.model_dump() for source_id, source in self.sources.items()}, f, indent=4)

    async def create_source(self, source: Source) -> Source:
        if not source.id:
            source.id = generate_uid()
        self.sources[source.id] = source
        self._save()
        logger.debug(f"Created source: {source}")
        return source

    async def get_sources_by_message_id(self, message_id: str) -> list[Source]:
        return [src for src in self.sources.values() if src.message_id == message_id]

    async def delete_sources(self, source_ids: list[str]) -> bool:
        for source_id in source_ids:
            if source_id in self.sources:
                del self.sources[source_id]
        self._save()
        logger.debug(f"Deleted sources: {source_ids}")
        return True
