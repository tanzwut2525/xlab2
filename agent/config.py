import os


class Config:
    default_provider: str = os.environ.get("DEFAULT_PROVIDER", "anthropic")

    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

    ollama_base_url: str = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")
    ollama_model: str = os.environ.get("OLLAMA_MODEL", "llama3.1")

    max_tool_iterations: int = int(os.environ.get("MAX_TOOL_ITERATIONS", "8"))


config = Config()
