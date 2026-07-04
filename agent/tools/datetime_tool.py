from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agent.tools.registry import Tool, registry


def get_current_datetime(timezone: str = "UTC") -> dict:
    try:
        tz = ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        return {"error": f"Unknown timezone: {timezone}"}

    now = datetime.now(tz)
    return {
        "iso_8601": now.isoformat(),
        "timezone": timezone,
        "weekday": now.strftime("%A"),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
    }


registry.register(
    Tool(
        name="get_current_datetime",
        description=(
            "Get the current date and time. Optionally pass an IANA timezone "
            "name (e.g. 'America/New_York', 'Europe/Kyiv'). Defaults to UTC."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "IANA timezone name, e.g. 'Europe/Kyiv'. Defaults to 'UTC'.",
                }
            },
            "required": [],
        },
        handler=get_current_datetime,
    )
)
