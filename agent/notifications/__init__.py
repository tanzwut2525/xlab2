from agent.config import config
from agent.notifications.base import Notifier
from agent.notifications.log_notifier import LogNotifier
from agent.notifications.slack_notifier import SlackNotifier
from agent.notifications.webhook_notifier import WebhookNotifier


def get_notifier(name: str | None = None) -> Notifier:
    notifier_name = (name or config.notifier).lower()

    if notifier_name == "log":
        return LogNotifier()
    if notifier_name == "slack":
        return SlackNotifier(webhook_url=config.slack_webhook_url)
    if notifier_name == "webhook":
        return WebhookNotifier(url=config.notify_webhook_url)

    raise ValueError(f"Unknown notifier: {notifier_name}")


__all__ = ["get_notifier", "Notifier"]
