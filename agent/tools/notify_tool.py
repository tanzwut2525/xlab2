from agent.notifications import get_notifier
from agent.tools.registry import Tool, registry

_notifier = get_notifier()


def send_notification(title: str, message: str, severity: str = "info") -> dict:
    _notifier.send(title=title, message=message, severity=severity)
    return {"status": "sent", "title": title, "severity": severity}


registry.register(
    Tool(
        name="send_notification",
        description=(
            "Send a human-readable notification about what you observed or did. Use "
            "this to add extra context in addition to the automatic incident "
            "notifications the system already sends before and after remediation."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "message": {"type": "string"},
                "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
            },
            "required": ["title", "message"],
        },
        handler=send_notification,
    )
)
