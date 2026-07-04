import logging

import httpx

from agent.notifications.base import Notifier, Severity

logger = logging.getLogger(__name__)


class WebhookNotifier(Notifier):
    """Posts a generic JSON payload to any webhook URL."""

    def __init__(self, url: str, timeout: float = 10.0) -> None:
        if not url:
            raise ValueError("NOTIFY_WEBHOOK_URL is not set")
        self._url = url
        self._timeout = timeout

    def send(self, title: str, message: str, severity: Severity = "info") -> None:
        try:
            response = httpx.post(
                self._url,
                json={"title": title, "message": message, "severity": severity},
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # Never log the URL itself; webhook URLs are bearer-token-shaped secrets.
            logger.error("Failed to deliver webhook notification (status=%s)", exc)
