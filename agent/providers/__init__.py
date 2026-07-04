from agent.config import config
from agent.providers.anthropic_provider import AnthropicProvider
from agent.providers.base import ModelProvider
from agent.providers.ollama_provider import OllamaProvider


def get_provider(name: str | None = None) -> ModelProvider:
    provider_name = (name or config.default_provider).lower()

    if provider_name == "anthropic":
        return AnthropicProvider(api_key=config.anthropic_api_key, model=config.anthropic_model)
    if provider_name == "ollama":
        return OllamaProvider(base_url=config.ollama_base_url, model=config.ollama_model)

    raise ValueError(f"Unknown provider: {provider_name}")


__all__ = ["get_provider", "ModelProvider"]
