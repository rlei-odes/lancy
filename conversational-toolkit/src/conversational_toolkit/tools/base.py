"""
Tool abstractions for LLM function calling.

Tools are the mechanism by which an LLM can request external actions during an
agentic loop. Each 'Tool' exposes a JSON schema via 'json_schema()' that is
passed to the LLM API, and a 'call()' coroutine that executes the action when
the model requests it.

Tools whose response dict includes a '_sources' key will have those sources
automatically surfaced in the 'AgentAnswer' by 'ToolAgent'.

Concrete implementations: 'RetrieverTool', 'EmbeddingsTool'.
"""

from abc import ABC, abstractmethod
from typing import Any, TypedDict, Literal


class FunctionDescription(TypedDict):
    """JSON schema fragment describing a callable function for the LLM API."""

    name: str
    description: str
    parameters: dict[str, Any]


class ToolDescription(TypedDict):
    """Full tool descriptor in the format expected by OpenAI-compatible APIs."""

    type: Literal["function"]
    function: FunctionDescription


class Tool(ABC):
    """
    Abstract base class for LLM-callable tools.

    Subclasses declare 'name', 'description', and 'parameters' as class
    attributes so that 'json_schema()' can assemble the tool descriptor without
    any additional configuration. The ABC ensures every tool provides an async
    'call()' implementation.
    """

    name: str
    description: str
    parameters: dict[str, Any]

    @abstractmethod
    async def call(self, args: dict[str, Any]) -> dict[str, Any]:
        """Execute the tool with the given arguments and return a result dict.

        'ToolAgent' automatically passes '_query' and '_history' in addition to
        the LLM-supplied arguments, so tools can access the original user query
        for contextualisation even when the LLM does not include it explicitly.
        """
        pass

    def json_schema(self) -> ToolDescription:
        """Return the tool descriptor in OpenAI function-calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
