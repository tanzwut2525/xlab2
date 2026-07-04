import json

from agent.config import config
from agent.providers import get_provider
from agent.providers.base import Message
from agent.tools import registry


def run_turn(history: list[Message], user_input: str, provider_name: str | None = None) -> str:
    provider = get_provider(provider_name)
    tools = registry.all()

    history.append(Message(role="user", content=user_input))

    for _ in range(config.max_tool_iterations):
        response = provider.chat(history, tools)
        history.append(
            Message(role="assistant", content=response.text, tool_calls=response.tool_calls)
        )

        if response.stop_reason != "tool_use" or not response.tool_calls:
            return response.text

        for call in response.tool_calls:
            try:
                result = registry.call(call.name, call.arguments)
            except Exception as exc:  # tool failed; let the model see why
                result = {"error": str(exc)}
            history.append(
                Message(
                    role="tool",
                    content=json.dumps(result),
                    tool_call_id=call.id,
                    tool_name=call.name,
                )
            )

    return "I couldn't finish that request within the allowed number of tool calls."
