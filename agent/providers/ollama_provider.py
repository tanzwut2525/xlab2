import json
import logging

import httpx

from agent.providers.base import Message, ModelProvider, ModelResponse, ToolCall
from agent.tools.registry import Tool

logger = logging.getLogger(__name__)

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


class OllamaProvider(ModelProvider):
    def __init__(self, base_url: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model

    def chat(
        self,
        messages: list[Message],
        tools: list[Tool],
        system_prompt: str | None = None,
    ) -> ModelResponse:
        ollama_messages = [{"role": "system", "content": system_prompt or DEFAULT_SYSTEM_PROMPT}]
        ollama_messages += self._to_ollama_messages(messages)

        ollama_tools = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in tools
        ]

        payload = {
            "model": self._model,
            "messages": ollama_messages,
            "tools": ollama_tools,
            "stream": False,
        }

        response = httpx.post(f"{self._base_url}/api/chat", json=payload, timeout=120)
        response.raise_for_status()
        data = response.json()

        message = data.get("message", {})
        text = message.get("content", "") or ""
        raw_tool_calls = message.get("tool_calls") or []

        tool_calls = [
            ToolCall(
                id=f"call_{i}",
                name=call["function"]["name"],
                arguments=self._parse_arguments(call["function"].get("arguments", {})),
            )
            for i, call in enumerate(raw_tool_calls)
        ]

        stop_reason = "tool_use" if tool_calls else "end_turn"
        return ModelResponse(text=text, tool_calls=tool_calls, stop_reason=stop_reason)

    @staticmethod
    def _parse_arguments(arguments: dict | str) -> dict:
        if isinstance(arguments, dict):
            return arguments
        try:
            return json.loads(arguments)
        except (TypeError, json.JSONDecodeError):
            logger.warning("Could not parse tool call arguments as JSON: %r", arguments)
            return {}

    @staticmethod
    def _to_ollama_messages(messages: list[Message]) -> list[dict]:
        result: list[dict] = []
        for msg in messages:
            if msg.role == "user":
                result.append({"role": "user", "content": msg.content})
            elif msg.role == "assistant":
                entry: dict = {"role": "assistant", "content": msg.content}
                if msg.tool_calls:
                    entry["tool_calls"] = [
                        {"function": {"name": call.name, "arguments": call.arguments}}
                        for call in msg.tool_calls
                    ]
                result.append(entry)
            elif msg.role == "tool":
                result.append({"role": "tool", "content": msg.content})
        return result
