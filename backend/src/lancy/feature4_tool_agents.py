from typing import Any
from conversational_toolkit.tools.base import Tool
from conversational_toolkit.chunking.base import Chunk


def chunks_to_text(chunks: list[Chunk]) -> str:
    text = ""

    for chunk in chunks:
        text += (
            f"## Chunk {chunk.title}:\n```\n{chunk.content}\n```\n" + "-" * 30 + "\n\n"
        )

    text = text[:-4]

    return text


class RetrieveRelevantChunks(Tool):
    def __init__(
        self, name: str, description: str, parameters: dict[str, Any], retriever
    ):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.retriever = retriever

    async def call(self, args: dict[str, Any]) -> dict[str, Any]:
        query_with_history = args.get("query")

        retrieved = [await self.retriever.retrieve(q) for q in [query_with_history]]

        retrieved_as_text = [chunks_to_text(r) for r in retrieved]

        return {"result": retrieved_as_text}


class SumTwoNumbers(Tool):
    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
    ):
        self.name = name
        self.description = description
        self.parameters = parameters

    async def call(self, args: dict[str, Any]) -> dict[str, Any]:
        number_1 = args.get("number_1")
        number_2 = args.get("number_2")

        if not isinstance(number_1, (int, float)) or not isinstance(
            number_2, (int, float)
        ):
            raise ValueError("Both number_1 and number_2 must be int or float.")

        result = number_1 + number_2

        return {"result": result}


class HalfBoldText(Tool):
    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
    ):
        self.name = name
        self.description = description
        self.parameters = parameters

    async def call(self, args: dict[str, Any]) -> dict[str, Any]:
        text = args.get("text")

        if not isinstance(text, str):
            raise ValueError("The 'text' parameter must be a string.")

        words = text.split()
        if len(words) < 2:
            raise ValueError("The input text must contain at least two words.")

        # Bold every other word: yes, no, yes, no...
        bolded_text = " ".join(
            [f"**{word}**" if i % 2 == 0 else word for i, word in enumerate(words)]
        )

        return {"result": bolded_text}
