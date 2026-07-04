from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from agent.core import run_turn
from agent.providers.base import Message

app = FastAPI(title="Docker Agent")

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


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    history = _sessions.setdefault(req.session_id, [])
    try:
        reply = run_turn(history, req.message, provider_name=req.provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ChatResponse(response=reply, session_id=req.session_id)


@app.delete("/chat/{session_id}")
def reset_session(session_id: str) -> dict:
    _sessions.pop(session_id, None)
    return {"status": "cleared", "session_id": session_id}
