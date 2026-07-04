# Docker Agent

A minimal, dockerized chat agent exposed over HTTP (FastAPI). It supports tool
calling and can talk to either the Anthropic API or a local Ollama model,
selected per-request or via a default provider.

## Features

- `POST /chat` — send a message, get a reply. Conversation history is kept
  in-memory per `session_id`.
- `DELETE /chat/{session_id}` — clear a session's history.
- `GET /health` — liveness check.
- Pluggable model providers: `anthropic` (Claude) and `ollama` (local models).
- Tool calling loop (`agent/core.py`) with a simple tool registry
  (`agent/tools/registry.py`); currently ships one tool, `get_current_datetime`
  (`agent/tools/datetime_tool.py`).

## Architecture

```
server.py                     FastAPI app: /chat, /health endpoints
agent/
  config.py                   Reads settings from environment variables
  core.py                     run_turn(): the tool-calling conversation loop
  providers/
    base.py                   Message / ToolCall / ModelResponse / ModelProvider
    anthropic_provider.py     Anthropic Claude implementation
    ollama_provider.py        Ollama implementation (OpenAI-style tool calls)
    __init__.py                get_provider(name) factory
  tools/
    registry.py                Tool dataclass + ToolRegistry
    datetime_tool.py           get_current_datetime tool
```

Each provider translates the shared `Message`/`Tool` representation into its
own API format and normalizes the reply back into a `ModelResponse`. `run_turn`
drives the loop: it calls the model, executes any requested tool calls, feeds
the results back, and repeats until the model stops calling tools or
`MAX_TOOL_ITERATIONS` is reached.

## Prerequisites

- Docker and Docker Compose
- An Anthropic API key if you plan to use the `anthropic` provider
  (get one at https://console.anthropic.com/)

## Install & Run (from scratch)

1. Clone the repo and move into it.

2. Create your env file:

   ```bash
   cp .env.example .env
   ```

3. Edit `.env` and set at least:

   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ```

   Leave `DEFAULT_PROVIDER=anthropic` to use Claude by default, or set it to
   `ollama` to default to the local model instead. See
   [Configuration](#configuration) below for all variables.

4. Build and start the containers:

   ```bash
   docker compose up --build
   ```

   This starts two services:
   - `agent` — the FastAPI app, on `http://localhost:8000`
   - `ollama` — a local Ollama server, on `http://localhost:11434`

5. If you intend to use the `ollama` provider, pull a tool-calling-capable
   model into the running Ollama container (only needed once):

   ```bash
   docker compose exec ollama ollama pull llama3.1
   ```

6. Check it's up:

   ```bash
   curl http://localhost:8000/health
   ```

### Common Docker Compose commands

```bash
docker compose up --build        # build (if needed) and start, attached
docker compose up -d --build      # same, detached (background)
docker compose logs -f agent      # follow the agent container's logs
docker compose restart agent      # restart just the agent (e.g. after editing .env)
docker compose stop               # stop containers, keep them (and volumes) around
docker compose down                # stop and remove containers
docker compose down -v            # also remove the ollama_data volume (wipes pulled models)
```

Rebuild just the agent image after changing code or `requirements.txt`:

```bash
docker compose build agent
docker compose up -d agent
```

### Building and running the agent without Compose

You can also build and run the `agent` image directly with plain Docker,
without the `ollama` container. This only works with the `anthropic` provider
(there is no local Ollama on the network), and you must pass config as
environment variables since there is no `env_file` support outside Compose:

```bash
docker build -t docker-agent .

docker run --rm -p 8000:8000 \
  --name agent \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e DEFAULT_PROVIDER=anthropic \
  docker-agent
```

Or point `--env-file` at your `.env`:

```bash
docker run --rm -p 8000:8000 --name agent --env-file .env docker-agent
```

### Running without Docker (local Python)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit it
export $(grep -v '^#' .env | xargs)   # or use your own env loading
uvicorn server:app --reload
```

Note: `OLLAMA_BASE_URL` defaults to `http://ollama:11434`, which only resolves
inside the Compose network. Running outside Docker, set it to
`http://localhost:11434` (or wherever your Ollama server is).

## Usage

Send a message:

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What time is it in Kyiv?", "session_id": "demo"}'
```

Response:

```json
{"response": "It's currently ... in Europe/Kyiv.", "session_id": "demo"}
```

Override the provider for a single request:

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "hello", "session_id": "demo", "provider": "ollama"}'
```

Clear a session's history:

```bash
curl -X DELETE http://localhost:8000/chat/demo
```

## Configuration

All configuration is via environment variables (see `.env.example`):

| Variable              | Default                  | Description                                   |
|-----------------------|--------------------------|------------------------------------------------|
| `DEFAULT_PROVIDER`    | `anthropic`              | Provider used when a request doesn't specify one (`anthropic` or `ollama`). |
| `ANTHROPIC_API_KEY`   | *(empty)*                | Required to use the `anthropic` provider.     |
| `ANTHROPIC_MODEL`     | `claude-sonnet-5`        | Anthropic model id.                           |
| `OLLAMA_BASE_URL`     | `http://ollama:11434`    | Base URL of the Ollama server.                |
| `OLLAMA_MODEL`        | `llama3.1`               | Ollama model tag (must support tool calling). |
| `MAX_TOOL_ITERATIONS` | `8`                      | Max tool-call round trips before the agent gives up on a turn. |

## Adding a tool

Add a module under `agent/tools/`, define a handler function and register it:

```python
from agent.tools.registry import Tool, registry

def my_handler(some_arg: str) -> dict:
    ...

registry.register(
    Tool(
        name="my_tool",
        description="...",
        input_schema={"type": "object", "properties": {...}, "required": [...]},
        handler=my_handler,
    )
)
```

Then import that module in `agent/tools/__init__.py` so it registers on
startup.

## Adding a provider

Implement `ModelProvider` (`agent/providers/base.py`) and wire it up in
`agent/providers/__init__.py`'s `get_provider()`.

## Troubleshooting

- **`500 Internal Server Error` / logs show `httpx.HTTPStatusError: ... 404 Not Found for url 'http://ollama:11434/api/chat'`**
  The `ollama` provider is selected (`DEFAULT_PROVIDER=ollama` or `"provider": "ollama"` in the request) but no model has been pulled into the Ollama container yet. Fix:

  ```bash
  docker compose exec ollama ollama pull llama3.1   # or whatever OLLAMA_MODEL is set to
  docker compose exec ollama ollama list             # confirm it's there
  ```

- **`ANTHROPIC_API_KEY is not set`**
  The `anthropic` provider is selected but `ANTHROPIC_API_KEY` is empty in `.env`. Set it and run `docker compose restart agent` (or `up -d` again) to pick up the change.

- Check which provider a request actually used and see the full traceback with:

  ```bash
  docker compose logs -f agent
  ```

## Limitations

- Conversation history is stored in-memory in the `agent` process; it is lost
  on restart and won't work if you scale to multiple replicas. Swap
  `_sessions` in `server.py` for Redis or a database if you need persistence.
