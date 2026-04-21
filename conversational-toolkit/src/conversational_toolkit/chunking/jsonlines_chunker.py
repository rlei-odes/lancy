import json

from conversational_toolkit.chunking.base import Chunk, Chunker

from loguru import logger


class JSONLinesChunker(Chunker):
    def make_chunks(self, file_path: str, title_key: str, content_key: str, source_key: str) -> list[Chunk]:
        try:
            with open(file_path, "r") as f:
                data = [json.loads(line) for line in f]
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Error reading {file_path}: {e}")
            data = []

        return [
            Chunk(
                title=doc.get(title_key, ""),
                content=doc.get(content_key, ""),
                mime_type="text/plain",
                metadata={"source": doc.get(source_key, "")},
            )
            for doc in data
        ]
