import anthropic

from agent.providers.base import Message, ModelProvider, ModelResponse, ToolCall
from agent.tools.registry import Tool

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful agent running inside a Docker container. "
    "Use the available tools when a question requires real-world information "
    "you don't otherwise have, such as the current date or time.\n\n"
    "Never invent a metric value, status, or other factual detail. Only state "
    "a value you actually received from a tool result. If a tool call errors, "
    "or returns empty/no data, say so explicitly (e.g. 'that query returned no "
    "data' or 'the tool call failed: <error>') instead of guessing a plausible-"
    "sounding answer."
)


class AnthropicProvider(ModelProvider):
    def __init__(self, api_key: str, model: str) -> None:
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set")
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def chat(
        self,
        messages: list[Message],
        tools: list[Tool],
        system_prompt: str | None = None,
    ) -> ModelResponse:
        anthropic_messages = self._to_anthropic_messages(messages)
        anthropic_tools = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in tools
        ]

        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=system_prompt or DEFAULT_SYSTEM_PROMPT,
            messages=anthropic_messages,
            tools=anthropic_tools,
        )

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=block.input))

        stop_reason = "tool_use" if response.stop_reason == "tool_use" else "end_turn"
        return ModelResponse(text="".join(text_parts), tool_calls=tool_calls, stop_reason=stop_reason)

    @staticmethod
    def _to_anthropic_messages(messages: list[Message]) -> list[dict]:
        result: list[dict] = []
        for msg in messages:
            if msg.role == "user":
                result.append({"role": "user", "content": msg.content})
            elif msg.role == "assistant":
                content: list[dict] = []
                if msg.content:
                    content.append({"type": "text", "text": msg.content})
                for call in msg.tool_calls:
                    content.append(
                        {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                    )
                result.append({"role": "assistant", "content": content})
            elif msg.role == "tool":
                result.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.tool_call_id,
                                "content": msg.content,
                            }
                        ],
                    }
                )
        return result
