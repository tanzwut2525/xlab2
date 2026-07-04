from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from agent.tools.registry import Tool

Role = Literal["user", "assistant", "tool"]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None


@dataclass
class ModelResponse:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: Literal["end_turn", "tool_use"]


class ModelProvider(ABC):
    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        tools: list[Tool],
        system_prompt: str | None = None,
    ) -> ModelResponse:
        """Send the conversation to the model and return a normalized response.

        `system_prompt`, when given, replaces the provider's default system
        prompt for this call (used e.g. by the monitoring loop's ops persona).
        """
