import time
from typing import AsyncGenerator

from loguru import logger
from conversational_toolkit.llms.base import LLM, LLMMessage, Roles
from conversational_toolkit.utils.metadata_provider import MetadataProvider
from openai import AsyncOpenAI


class LocalLLM(LLM):
    def __init__(
        self,
        model_name: str = "bartowski/gemma-2-9b-it-GGUF",
        temperature: float = 0.5,
        seed: int = 42,
        base_url: str = "",
        api_key: str = "",
        response_format: dict | None = None,
        display_name: str = "",
    ):
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self.model = model_name
        self.display_name = display_name or model_name
        self.temperature = temperature
        self.seed = seed
        self.response_format = response_format
        logger.debug(f"Local LLM loaded: {model_name}; temperature: {temperature}; seed: {seed}")

    async def generate(self, conversation: list[LLMMessage]) -> LLMMessage:
        """Generate a completion for the given conversation."""
        # Convert Pydantic models to plain dicts to avoid OpenAI SDK serialisation issues
        # (TypeError: argument 'by_alias': 'NoneType' cannot be converted to PyBool)
        api_messages = [
            {"role": str(m.role), "content": "".join(c.text or "" for c in m.content if c.type == "text")}
            for m in conversation
        ]
        kwargs: dict = {
            "model": self.model,
            "messages": api_messages,
            "temperature": self.temperature,
            "seed": self.seed,
        }
        if self.response_format:
            kwargs["response_format"] = self.response_format

        t_start = time.monotonic()
        completion = await self.client.chat.completions.create(**kwargs)  # type: ignore
        duration_s = time.monotonic() - t_start
        logger.debug(f"Completion: {completion}")

        # Publish stats so the controller can include them in message metadata
        usage = completion.usage
        if usage and usage.completion_tokens and duration_s > 0:
            MetadataProvider.add_metadata(
                {
                    "model": self.display_name,
                    "tokens_per_second": round(usage.completion_tokens / duration_s, 1),
                }
            )

        from conversational_toolkit.llms.base import MessageContent

        raw_content = completion.choices[0].message.content or ""
        return LLMMessage(
            content=[MessageContent(type="text", text=raw_content)],
            role=Roles(completion.choices[0].message.role),
            tool_calls=completion.choices[0].message.tool_calls,  # type: ignore
        )

    async def generate_stream(self, conversation: list[LLMMessage]) -> AsyncGenerator[LLMMessage, None]:
        msg = await self.generate(conversation)
        yield msg
