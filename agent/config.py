import os


def _bool_env(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _list_env(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


class Config:
    # --- LLM providers / chat ---
    default_provider: str = os.environ.get("DEFAULT_PROVIDER", "anthropic")

    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

    ollama_base_url: str = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")
    ollama_model: str = os.environ.get("OLLAMA_MODEL", "llama3.1")

    max_tool_iterations: int = int(os.environ.get("MAX_TOOL_ITERATIONS", "8"))

    # Whether /chat is allowed to call remediation tools (restart/scale/delete).
    # /chat has no auth, so this defaults to False; only the autonomous monitor
    # loop gets those tools by default.
    chat_expose_ops_tools: bool = _bool_env("CHAT_EXPOSE_OPS_TOOLS", False)

    log_level: str = os.environ.get("LOG_LEVEL", "INFO")

    # --- Prometheus ---
    prometheus_url: str = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090")
    prometheus_timeout: float = float(os.environ.get("PROMETHEUS_TIMEOUT", "10"))
    prometheus_bearer_token: str = os.environ.get("PROMETHEUS_BEARER_TOKEN", "")

    # --- Kubernetes ---
    kubeconfig_path: str = os.environ.get("KUBECONFIG_PATH", "")
    # Fail-closed: an empty allowlist means no namespace is allowed to be mutated.
    k8s_allowed_namespaces: list[str] = _list_env("K8S_ALLOWED_NAMESPACES", "default")
    # Safety default: no real Kubernetes mutation happens unless explicitly disabled.
    dry_run: bool = _bool_env("DRY_RUN", True)
    max_scale_replicas: int = int(os.environ.get("MAX_SCALE_REPLICAS", "10"))

    # --- Monitoring scheduler ---
    monitor_enabled: bool = _bool_env("MONITOR_ENABLED", True)
    monitor_interval_seconds: int = int(os.environ.get("MONITOR_INTERVAL_SECONDS", "60"))
    monitor_provider: str = os.environ.get("MONITOR_PROVIDER", "ollama")
    rules_path: str = os.environ.get("RULES_PATH", "config/rules.yaml")

    # --- Remediation guardrails ---
    max_remediations_per_cycle: int = int(os.environ.get("MAX_REMEDIATIONS_PER_CYCLE", "3"))
    verify_timeout_seconds: int = int(os.environ.get("VERIFY_TIMEOUT_SECONDS", "120"))
    verify_poll_interval_seconds: int = int(os.environ.get("VERIFY_POLL_INTERVAL_SECONDS", "10"))

    # --- Notifications ---
    notifier: str = os.environ.get("NOTIFIER", "log")
    slack_webhook_url: str = os.environ.get("SLACK_WEBHOOK_URL", "")
    notify_webhook_url: str = os.environ.get("NOTIFY_WEBHOOK_URL", "")


config = Config()
