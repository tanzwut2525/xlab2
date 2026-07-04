import json
import logging
from dataclasses import dataclass, field
from typing import Any

from agent.config import config
from agent.providers import get_provider
from agent.providers.base import Message
from agent.tools import registry

logger = logging.getLogger(__name__)


@dataclass
class ToolInvocation:
    name: str
    arguments: dict[str, Any]
    result: Any


@dataclass
class TurnResult:
    text: str
    tool_invocations: list[ToolInvocation] = field(default_factory=list)


def run_turn(
    history: list[Message],
    user_input: str,
    provider_name: str | None = None,
    system_prompt: str | None = None,
    exclude_tools: frozenset[str] = frozenset(),
) -> TurnResult:
    provider = get_provider(provider_name)
    tools = [tool for tool in registry.all() if tool.name not in exclude_tools]

    history.append(Message(role="user", content=user_input))
    invocations: list[ToolInvocation] = []

    for _ in range(config.max_tool_iterations):
        response = provider.chat(history, tools, system_prompt=system_prompt)
        history.append(
            Message(role="assistant", content=response.text, tool_calls=response.tool_calls)
        )

        if response.stop_reason != "tool_use" or not response.tool_calls:
            return TurnResult(text=response.text, tool_invocations=invocations)

        for call in response.tool_calls:
            try:
                result = registry.call(call.name, call.arguments)
            except Exception as exc:  # tool failed; let the model see why
                logger.exception("Tool '%s' failed", call.name)
                result = {"error": str(exc)}
            invocations.append(ToolInvocation(name=call.name, arguments=call.arguments, result=result))
            history.append(
                Message(
                    role="tool",
                    content=json.dumps(result, default=str),
                    tool_call_id=call.id,
                    tool_name=call.name,
                )
            )

    return TurnResult(
        text="I couldn't finish that request within the allowed number of tool calls.",
        tool_invocations=invocations,
    )
