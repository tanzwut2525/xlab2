import logging

import httpx

from agent.notifications.base import Notifier, Severity

logger = logging.getLogger(__name__)

_SEVERITY_EMOJI = {
    "info": ":information_source:",
    "warning": ":warning:",
    "critical": ":rotating_light:",
}


class SlackNotifier(Notifier):
    """Posts to a Slack incoming webhook URL."""

    def __init__(self, webhook_url: str, timeout: float = 10.0) -> None:
        if not webhook_url:
            raise ValueError("SLACK_WEBHOOK_URL is not set")
        self._webhook_url = webhook_url
        self._timeout = timeout

    def send(self, title: str, message: str, severity: Severity = "info") -> None:
        emoji = _SEVERITY_EMOJI.get(severity, "")
        text = f"{emoji} *{title}*\n{message}"
        try:
            response = httpx.post(self._webhook_url, json={"text": text}, timeout=self._timeout)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # Never log the URL itself; webhook URLs are bearer-token-shaped secrets.
            logger.error("Failed to deliver Slack notification (status=%s)", exc)
