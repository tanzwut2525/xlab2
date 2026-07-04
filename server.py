import logging
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from agent.logging_config import configure_logging

configure_logging()

from agent.clients import prometheus_client  # noqa: E402
from agent.config import config  # noqa: E402
from agent.core import run_turn  # noqa: E402
from agent.monitor import REMEDIATION_TOOL_NAMES, MonitoringAgent  # noqa: E402
from agent.notifications import get_notifier  # noqa: E402
from agent.providers.base import Message  # noqa: E402
from agent.rules import load_rules  # noqa: E402
from agent.scheduler import Scheduler  # noqa: E402

logger = logging.getLogger(__name__)

monitoring_agent: MonitoringAgent | None = None
scheduler: Scheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global monitoring_agent, scheduler

    if config.monitor_enabled:
        rules = load_rules(config.rules_path)
        monitoring_agent = MonitoringAgent(
            rules=rules,
            prometheus=prometheus_client,
            notifier=get_notifier(),
            provider_name=config.monitor_provider,
        )
        scheduler = Scheduler(config.monitor_interval_seconds, monitoring_agent.run_cycle)
        scheduler.start()
        logger.info(
            "Monitoring loop started: %d rule(s), interval=%ds, dry_run=%s, allowed_namespaces=%s",
            len(rules), config.monitor_interval_seconds, config.dry_run, config.k8s_allowed_namespaces,
        )
    else:
        logger.info("Monitoring loop disabled (MONITOR_ENABLED=false)")

    yield

    if scheduler is not None:
        logger.info("Stopping monitoring loop (waiting for any in-flight cycle to finish)...")
        await scheduler.stop()


app = FastAPI(title="AIOps Agent", lifespan=lifespan)

# In-memory conversation state, keyed by session_id. Fine for a single-container
# MVP; swap for Redis/a DB if the agent needs to survive restarts or scale out.
_sessions: dict[str, list[Message]] = {}


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"
    provider: str | None = None  # "anthropic" | "ollama"; defaults to DEFAULT_PROVIDER


class ChatResponse(BaseModel):
    response: str
    session_id: str


def _chat_excluded_tools() -> frozenset[str]:
    # /chat has no auth in front of it, so by default it can't call the same
    # remediation tools (restart/scale/delete) the autonomous monitor loop uses.
    return frozenset() if config.chat_expose_ops_tools else REMEDIATION_TOOL_NAMES


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/status")
def status() -> dict:
    if monitoring_agent is None:
        return {"monitor_enabled": False}

    return {
        "monitor_enabled": True,
        "dry_run": config.dry_run,
        "allowed_namespaces": config.k8s_allowed_namespaces,
        "interval_seconds": config.monitor_interval_seconds,
        "rules": [rule.name for rule in monitoring_agent.rules],
        "last_cycle": monitoring_agent.last_cycle_summary,
        "recent_incidents": [asdict(incident) for incident in monitoring_agent.incidents[-20:]],
    }


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    history = _sessions.setdefault(req.session_id, [])
    try:
        result = run_turn(
            history,
            req.message,
            provider_name=req.provider,
            exclude_tools=_chat_excluded_tools(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ChatResponse(response=result.text, session_id=req.session_id)


@app.delete("/chat/{session_id}")
def reset_session(session_id: str) -> dict:
    _sessions.pop(session_id, None)
    return {"status": "cleared", "session_id": session_id}
