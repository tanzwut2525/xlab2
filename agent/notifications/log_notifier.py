import logging

from agent.notifications.base import Notifier, Severity

logger = logging.getLogger("notifications")

_LOG_FN_BY_SEVERITY = {
    "critical": logger.error,
    "warning": logger.warning,
    "info": logger.info,
}


class LogNotifier(Notifier):
    """Default notifier: writes to the application log. Zero external
    dependencies, so it always works out of the box."""

    def send(self, title: str, message: str, severity: Severity = "info") -> None:
        log_fn = _LOG_FN_BY_SEVERITY.get(severity, logger.info)
        log_fn("[%s] %s: %s", severity.upper(), title, message)
