# AIOps Agent

A production-quality, Dockerized autonomous operations agent. It does two things:

1. **`/chat`** — a tool-calling chat agent (FastAPI), talking to either the Anthropic API or a local Ollama model.
2. **The monitoring loop** — runs continuously in the background, polls Prometheus on a configurable interval, and when a metric breaches a configured threshold, uses a local LLM to decide and execute a remediation (restart/scale a Kubernetes Deployment, or delete a Pod), verifies the fix by re-querying Prometheus, and sends a notification describing what happened.

Both share the same underlying tool-calling engine, so any new tool you register is available to the chat endpoint *and* to autonomous remediation.

## Architecture

```
server.py                        FastAPI app: /chat, /status, /health; owns the
                                  scheduler's lifecycle (startup/shutdown)
agent/
  config.py                      All settings, read from environment variables
  logging_config.py              configure_logging(): stdlib logging setup
  core.py                        run_turn(): the tool-calling conversation loop.
                                  Returns TurnResult(text, tool_invocations) so
                                  callers can see exactly which tools fired.
  rules.py                       Rule / RemediationTarget dataclasses + YAML loader
  monitor.py                     MonitoringAgent: the autonomous decision loop
                                  (breach detection -> LLM decision -> verify -> notify)
  scheduler.py                   Runs MonitoringAgent.run_cycle() on an interval
                                  without blocking the FastAPI event loop
  providers/
    base.py                      Message / ToolCall / ModelResponse / ModelProvider
    anthropic_provider.py        Anthropic Claude implementation
    ollama_provider.py           Ollama implementation (OpenAI-style tool calls)
    __init__.py                  get_provider(name) factory
  tools/
    registry.py                  Tool dataclass + ToolRegistry
    datetime_tool.py              get_current_datetime
    prometheus_tool.py             query_prometheus
    kubernetes_tool.py              restart_deployment, scale_deployment,
                                     delete_pod, get_deployment_status
    notify_tool.py                  send_notification
  clients/
    prometheus_client.py          Raw Prometheus HTTP API client (instant queries)
    kubernetes_client.py           Kubernetes Python client wrapper (guardrails live here)
    __init__.py                    Shared singleton clients
  notifications/
    base.py                        Notifier interface
    log_notifier.py                 Default: writes to the app log
    slack_notifier.py               Slack incoming webhook
    webhook_notifier.py             Generic JSON webhook
    __init__.py                     get_notifier(name) factory
config/
  rules.yaml                      Declarative monitoring rules (metric, threshold, target)
prometheus/
  prometheus.yml                  Bundled Prometheus config for local smoke testing
k8s/
  rbac.yaml                       ServiceAccount/Role/RoleBinding for in-cluster deployment
  deployment.yaml                 Example production Deployment manifest
```

### How a monitoring cycle works

Every `MONITOR_INTERVAL_SECONDS` (default 60), `Scheduler` runs `MonitoringAgent.run_cycle()` in a worker thread (so a slow verification poll never blocks `/health`/`/status`). For each rule in `config/rules.yaml`:

1. **Query** — `PrometheusClient.latest_value()` runs the rule's PromQL against the Prometheus HTTP API (`/api/v1/query`).
2. **Evaluate** — compare the value against the rule's threshold/comparator. A query failure or "no data" is logged and skipped (it does *not* reset progress toward a breach, and does *not* look like recovery).
3. **Confirm** — a breach only counts once it's happened `consecutive_breaches_required` cycles in a row (filters out single-sample noise), and only if the rule isn't still in `cooldown_seconds` from a previous action, and only if the per-cycle remediation budget (`MAX_REMEDIATIONS_PER_CYCLE`) isn't already exhausted.
4. **Decide** — on a confirmed breach, the agent sends an "alert firing" notification, then calls the LLM (`MONITOR_PROVIDER`, default `ollama`) with an ops-focused system prompt, the rule's details, its configured remediation target, and the last few incidents for that same rule (so it knows if a restart was already tried recently and failed). The model has the full tool registry available: `query_prometheus`, `get_deployment_status`, `restart_deployment`, `scale_deployment`, `delete_pod`, `send_notification`.
5. **Act** — whichever remediation tool(s) the model calls execute against the real Kubernetes API (or just log what *would* happen, if `DRY_RUN=true`).
6. **Verify** — if any remediation tool fired, the agent re-polls Prometheus for up to `VERIFY_TIMEOUT_SECONDS` to confirm the rule is no longer breached.
7. **Notify** — a final notification reports what was done and whether it worked (`info` if resolved, `critical` if a remediation ran but didn't fix it, `warning` if the model chose not to act).

Every incident (value, actions taken, model's reasoning, verification outcome) is kept in a bounded in-memory list, visible at `GET /status`.

## Safety guardrails

An agent that can restart production workloads needs guardrails that don't depend on the LLM behaving well. These are enforced in code, not just in the prompt:

- **`DRY_RUN=true` by default.** Remediation tools log what they *would* do and return without ever loading a kubeconfig or reaching a cluster. Nothing mutates a real cluster until you explicitly set `DRY_RUN=false`.
- **Fail-closed namespace allowlist (`K8S_ALLOWED_NAMESPACES`).** An empty/unset allowlist means *no* namespace can be mutated — not "allow everything." Every mutating call checks this first, even in dry-run mode.
- **Hard replica ceiling (`MAX_SCALE_REPLICAS`, default 10).** `scale_deployment` clamps whatever replica count the model asks for.
- **Per-cycle remediation budget (`MAX_REMEDIATIONS_PER_CYCLE`, default 3).** Caps how many remediations can fire in a single cycle, so one bad rule (or a genuinely cluster-wide incident) can't cascade into a restart storm across every configured rule.
- **Per-rule cooldown + consecutive-breach confirmation.** Avoids acting on noise and avoids re-firing on a symptom that's still recovering.
- **`/chat` cannot call remediation tools by default (`CHAT_EXPOSE_OPS_TOOLS=false`).** `/chat` has no authentication in front of it, and shares the same tool registry as the monitor loop. Unless you explicitly opt in (and put `/chat` behind auth/network policy), a chat request can only call read-only/informational tools (`query_prometheus`, `get_deployment_status`, `send_notification`, `get_current_datetime`).
- **Single replica + `Recreate` deploy strategy in production.** Rule cooldown/breach-count state lives in the process's memory. Multiple replicas — or even a `RollingUpdate`, which briefly runs two pods during every deploy — would let two instances independently detect and remediate the same breach. See `k8s/deployment.yaml`.
- **Graceful shutdown waits out an in-flight cycle**, bounded by `VERIFY_TIMEOUT_SECONDS`, instead of abandoning a remediation mid-verification.
- **Least-privilege RBAC** (`k8s/rbac.yaml`): a namespaced Role per allowed namespace, scoped to only `deployments`/`deployments/scale`/`pods` — never a ClusterRole.

## Prerequisites

- Docker and Docker Compose
- An Anthropic API key if you plan to use the `anthropic` provider for `/chat` (get one at https://console.anthropic.com/) — not required for the monitoring loop, which defaults to the local `ollama` provider
- For real (non-dry-run) remediation: a Kubernetes cluster and either an in-cluster ServiceAccount (see `k8s/`) or a local kubeconfig

## Install & run from scratch

1. Clone the repo and move into it.

2. Create your env file:

   ```bash
   cp .env.example .env
   ```

   The defaults are safe out of the box: `DRY_RUN=true`, `K8S_ALLOWED_NAMESPACES=default`, `CHAT_EXPOSE_OPS_TOOLS=false`, `NOTIFIER=log`. You don't need a real Kubernetes cluster or Prometheus to try the full loop — see [Smoke test](#smoke-test-the-full-loop-locally) below.

3. Build and start the containers:

   ```bash
   docker compose up --build
   ```

   This starts three services:
   - `agent` — the FastAPI app + monitoring loop, on `http://localhost:8000`
   - `ollama` — a local Ollama server, on `http://localhost:11434`
   - `prometheus` — a bundled Prometheus for local testing, on `http://localhost:9090`

4. Pull a tool-calling-capable Ollama model (only needed once):

   ```bash
   docker compose exec ollama ollama pull llama3.1
   ```

5. Check it's up:

   ```bash
   curl http://localhost:8000/health
   curl http://localhost:8000/status
   ```

   `/status` shows whether monitoring is enabled, `dry_run`, the allowed namespaces, the loaded rule names, the last cycle's per-rule status, and recent incidents.

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

### Running without Docker (local Python)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit it
export $(grep -v '^#' .env | xargs)
uvicorn server:app --reload
```

Note: `OLLAMA_BASE_URL` and `PROMETHEUS_URL` default to the Compose service names (`http://ollama:11434`, `http://prometheus:9090`), which only resolve inside the Compose network. Running outside Docker, point them at `http://localhost:...` instead.

## Smoke test the full loop locally

This walks through triggering a real breach end-to-end — no real application, Prometheus target, or Kubernetes cluster required. The bundled `prometheus` service self-scrapes, so `up{job="prometheus"}` is a real, always-`1` metric you can use as a guaranteed "breach" trigger. Everything below was run and verified against real containers (real Ollama, real Prometheus) while building this agent.

**1. Bring the stack up and pull a model.**

```bash
docker compose up --build -d
docker compose exec ollama ollama pull llama3.1
```

> **Model choice matters.** Small models (e.g. `llama3.2:1b`) are unreliable at tool-calling in this loop — in testing, a 1B model described "I will restart the deployment" in its response text but never actually emitted a tool call. The agent handled that correctly (see step 5 below), but you won't see a real remediation happen. Use `llama3.1` (8B) or larger for `OLLAMA_MODEL`/`MONITOR_PROVIDER` to see the full tool-calling path fire.

**2. Rig a rule to breach immediately.** Edit `config/rules.yaml`'s `service_down` entry:

```yaml
  - name: service_down
    description: "SMOKE TEST: always-true condition for local verification."
    query: 'up{job="prometheus"}'
    comparator: "=="
    threshold: 1
    consecutive_breaches_required: 1   # fire on the very first breach, don't wait for confirmation
    cooldown_seconds: 300
    action_hint: restart_deployment
    target:
      kind: Deployment
      namespace: default
      name: api
```

Optionally also drop the cycle time in `.env` so you don't have to wait as long: `MONITOR_INTERVAL_SECONDS=20`.

**3. Rebuild and recreate the agent.** `config/rules.yaml` is copied into the image at build time, so a plain `docker compose restart agent` will *not* pick up the edit — you need:

```bash
docker compose up -d --build agent
```

**4. Confirm the rule loaded and is breaching:**

```bash
curl -s localhost:8000/status | python3 -m json.tool
```

You should see `"rules": ["service_down", ...]` and, within one cycle, `last_cycle.service_down.status` move from nothing to `"remediated"` (or `"cooldown"` on subsequent cycles).

**5. Tail the logs for the full cycle:**

```bash
docker compose logs -f agent
```

Expect to see, in order:

- `Rule 'service_down' breached (value=1.0 == 1.0, consecutive=1/1)`
- `[WARNING] Alert firing: service_down: ...` — the deterministic pre-remediation notification
- one or more `HTTP Request: POST http://ollama:11434/api/chat` lines — the LLM reasoning + tool-calling round trip(s)
- one of:
  - `[INFO] Remediation resolved: service_down: ...` with `[DRY RUN] Would restart deployment default/api` above it, if the model called `restart_deployment` and the (dry-run) verification passed, or
  - `[WARNING] No action taken: service_down: Actions taken: none` if the model didn't actually call a remediation tool (see the model-choice note above) — this is the safe-failure path, not a bug: the system never claims something was fixed unless a tool call actually ran and Prometheus confirmed recovery.
- `Rule 'service_down' is in cooldown for Ns more; skipping remediation` on the next cycles, until `cooldown_seconds` elapses.

**6. Check the incident record:**

```bash
curl -s localhost:8000/status | python3 -m json.tool
```

`recent_incidents` will show the value, the tool(s) called (if any), the model's reasoning text, and the verification outcome for each breach.

**7. Clean up.** Revert `config/rules.yaml` (`git checkout -- config/rules.yaml`) and any `.env` changes, then `docker compose up -d --build agent` again to restore normal behavior — or `docker compose down` to stop everything.

## Usage

Send a chat message:

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What time is it in Kyiv?", "session_id": "demo"}'
```

Ask it to check on a metric (read-only tools are available by default). Against the bundled demo Prometheus, `job="prometheus"` (its own self-scrape) is the one job that actually has data - see [Adding a monitoring rule](#adding-a-monitoring-rule) for why `config/rules.yaml`'s other example rules still show no data until you point this at a real service:

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is up{job=\"prometheus\"} reporting right now?", "session_id": "demo", "provider": "ollama"}'
```

Clear a session's history:

```bash
curl -X DELETE http://localhost:8000/chat/demo
```

Check monitoring status:

```bash
curl http://localhost:8000/status
```

## Configuration

All configuration is via environment variables (see `.env.example`).

**LLM / chat**

| Variable                | Default           | Description                                                    |
|-------------------------|--------------------|------------------------------------------------------------------|
| `DEFAULT_PROVIDER`      | `anthropic`        | Provider `/chat` uses when a request doesn't specify one.        |
| `ANTHROPIC_API_KEY`     | *(empty)*          | Required to use the `anthropic` provider.                         |
| `ANTHROPIC_MODEL`       | `claude-sonnet-5`  | Anthropic model id.                                               |
| `OLLAMA_BASE_URL`       | `http://ollama:11434` | Base URL of the Ollama server.                                |
| `OLLAMA_MODEL`          | `llama3.1`         | Ollama model tag (must support tool calling).                     |
| `MAX_TOOL_ITERATIONS`   | `8`                | Max tool-call round trips before a turn gives up.                 |
| `CHAT_EXPOSE_OPS_TOOLS` | `false`            | Whether `/chat` may call remediation tools. See safety guardrails.|
| `LOG_LEVEL`             | `INFO`             | Python logging level.                                             |

**Prometheus**

| Variable                   | Default                 | Description                              |
|----------------------------|--------------------------|-------------------------------------------|
| `PROMETHEUS_URL`           | `http://prometheus:9090` | Base URL of the Prometheus HTTP API.       |
| `PROMETHEUS_TIMEOUT`       | `10`                     | Request timeout (seconds).                 |
| `PROMETHEUS_BEARER_TOKEN`  | *(empty)*                | Optional bearer token, if Prometheus needs auth. |

**Kubernetes**

| Variable                 | Default   | Description                                                        |
|---------------------------|-----------|----------------------------------------------------------------------|
| `KUBECONFIG_PATH`        | *(empty)* | Path to a kubeconfig file. Empty = in-cluster config, or the default kubeconfig location. |
| `K8S_ALLOWED_NAMESPACES` | `default` | Comma-separated allowlist. Empty means no namespace may be mutated.  |
| `DRY_RUN`                | `true`    | If true, remediation tools log intent only; no real cluster mutation.|
| `MAX_SCALE_REPLICAS`     | `10`      | Hard ceiling `scale_deployment` clamps to.                            |

**Monitoring scheduler**

| Variable                     | Default             | Description                                        |
|-------------------------------|----------------------|------------------------------------------------------|
| `MONITOR_ENABLED`            | `true`               | Whether the background monitoring loop runs at all.  |
| `MONITOR_INTERVAL_SECONDS`   | `60`                 | Seconds between monitoring cycles.                    |
| `MONITOR_PROVIDER`           | `ollama`              | LLM provider the monitor loop uses for decisions.     |
| `RULES_PATH`                 | `config/rules.yaml`   | Path to the rules file.                               |
| `MAX_REMEDIATIONS_PER_CYCLE` | `3`                  | Cap on remediations fired within one cycle.           |
| `VERIFY_TIMEOUT_SECONDS`     | `120`                | How long to poll Prometheus after a remediation.      |
| `VERIFY_POLL_INTERVAL_SECONDS` | `10`               | Delay between verification polls.                     |

**Notifications**

| Variable             | Default | Description                                  |
|-----------------------|---------|-----------------------------------------------|
| `NOTIFIER`            | `log`   | One of `log`, `slack`, `webhook`.              |
| `SLACK_WEBHOOK_URL`   | *(empty)* | Required if `NOTIFIER=slack`.                |
| `NOTIFY_WEBHOOK_URL`  | *(empty)* | Required if `NOTIFIER=webhook`.              |

## Adding a monitoring rule

Edit `config/rules.yaml`:

```yaml
rules:
  - name: my_new_rule
    description: "Human-readable description shown to the LLM."
    query: 'my_promql_expression'
    comparator: ">"          # one of > >= < <= == !=
    threshold: 1.0
    consecutive_breaches_required: 2   # filters single-sample noise
    cooldown_seconds: 300              # minimum time between remediations
    action_hint: restart_deployment    # suggestion only - the LLM decides
    target:
      kind: Deployment
      namespace: default
      name: my-service
```

Rules are loaded once at startup, and `config/rules.yaml` is copied into the Docker image at build time — so picking up an edit under Docker Compose needs `docker compose up -d --build agent`, not just `docker compose restart agent` (see the smoke test walkthrough above). Outside Docker (running `uvicorn` directly), a plain restart is enough since the file is read from disk.

**About the shipped example rules:** `service_down` queries `up{job="prometheus"}` - the one job the bundled demo Prometheus actually scrapes (itself), so it returns real data out of the box. `high_p99_latency` and `high_5xx_rate` query `http_request_duration_seconds_bucket`/`http_requests_total` - metric names typical web-app instrumentation exposes, which Prometheus does not export about itself. Those two will show `"no_data"` in `GET /status` until you either point `PROMETHEUS_URL` at a real Prometheus scraping an instrumented service, or replace the `job="prometheus"` placeholder in their queries with the label of a real job that exposes those metrics.

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

Then import that module in `agent/tools/__init__.py` so it registers on startup. If the tool can mutate infrastructure, also add its name to `agent.monitor.REMEDIATION_TOOL_NAMES` so it's excluded from `/chat` by default and counted toward the incident record's `actions_taken`.

## Adding a provider

Implement `ModelProvider` (`agent/providers/base.py`) and wire it up in `agent/providers/__init__.py`'s `get_provider()`.

## Adding a notifier

Implement `Notifier` (`agent/notifications/base.py`) and wire it up in `agent/notifications/__init__.py`'s `get_notifier()`.

## Production deployment (Kubernetes)

`k8s/rbac.yaml` and `k8s/deployment.yaml` are a starting point for running the agent in-cluster instead of via Docker Compose:

1. Apply the RBAC manifest (duplicate the Role + RoleBinding per namespace in `K8S_ALLOWED_NAMESPACES`):
   ```bash
   kubectl apply -f k8s/rbac.yaml
   ```
2. Create a ConfigMap/Secret with your environment variables (`PROMETHEUS_URL`, `K8S_ALLOWED_NAMESPACES`, `DRY_RUN=false`, notifier settings, etc.) named to match `k8s/deployment.yaml`'s `envFrom`.
3. Build and push the image, update `image:` in `k8s/deployment.yaml`, then `kubectl apply -f k8s/deployment.yaml`.

Read the comments in `k8s/deployment.yaml` before changing `replicas` or `strategy` — see [Safety guardrails](#safety-guardrails) for why they're pinned.

## Making the agent more agentic over time

Ideas for evolving this beyond the current MVP, roughly in order of effort:

- **Persistent/long-term memory.** Incident history today is in-memory and bounded (last 200, lost on restart). Move it to Postgres/Redis, and add embedding-based retrieval so the LLM can recognize patterns across days/weeks ("this deployment has needed a restart every Monday at 9am for the last month — the real fix is probably a scheduled job, not a restart").
- **Multi-step planning before acting.** Right now the model reasons and acts in one pass. A planning step ("investigate first: check `get_deployment_status` and recent deploys, *then* decide") could reduce unnecessary restarts for issues that are actually upstream (a dependency, a bad config push).
- **LLM-driven anomaly detection instead of fixed thresholds.** Static thresholds in `rules.yaml` are a reasonable start but don't adapt to daily/weekly seasonality. A periodic (e.g. hourly) LLM pass over a metric's recent time series, reasoning about whether the *shape* of the data is anomalous, could complement or eventually replace static thresholds.
- **Global rate/action budget across all rules and time**, not just per-cycle — e.g. "no more than N remediations across the whole fleet per hour," independent of which rules fired.
- **Multi-replica HA.** Move `RuleState`/cooldown/incident tracking to a shared store (Redis) so the single-replica restriction in `k8s/deployment.yaml` can be lifted.
- **Human-in-the-loop approval gate** for higher-risk actions (e.g. `delete_pod` in a namespace tagged "sensitive"), via a Slack interactive message or a webhook that blocks until approved/timed out.

## Limitations

- Conversation history (`/chat`) and monitoring state (cooldowns, incident history) are stored in-memory in the `agent` process; both are lost on restart and won't work if you scale to multiple replicas. See "Making the agent more agentic over time" above for how to lift this.
- `/chat` has no authentication. Keep `CHAT_EXPOSE_OPS_TOOLS=false` (the default) unless it's behind auth/network policy.
- **Local models can occasionally hallucinate over a failed or empty tool result, or mangle a tool call's arguments.** Two distinct issues were found in testing and are now mitigated (not eliminated - see below):
  - *Hallucinating over empty/failed results*: asking `/chat` "what is `up{job=\"api\"}` reporting?" (a query matching no series - `job="api"` isn't scraped by the bundled demo Prometheus) originally produced a confident but fabricated answer ("currently reporting 0") instead of "no data found." `DEFAULT_SYSTEM_PROMPT`/`OPS_SYSTEM_PROMPT` and `query_prometheus`'s tool output (`agent/tools/prometheus_tool.py`) now explicitly instruct against guessing a value, and include a `note` field spelling out "no data ≠ 0." Verified fixed: the same question now reliably returns "the tool call returned: no data" instead of a fabricated value.
  - *Malformed tool-call arguments*: with `llama3.1`, a PromQL query containing an embedded `"` (e.g. `job="api"`) occasionally got truncated in the tool call's JSON arguments (the model failed to escape the inner quote as `\"`), causing Prometheus to reject it with `400 Bad Request`. This was non-deterministic - repeating the identical question 3 times in a row succeeded all 3 times after the one failure. When it does happen, the model now correctly reports the tool failure instead of hallucinating around it (verified: `"The tool call failed: Client error '400 Bad Request'..."`).

  For anything you need to trust, verify against `GET /status`, `docker compose logs -f agent`, or `curl`-ing Prometheus directly rather than taking a single `/chat` answer at face value. Stronger tool-calling models are less prone to both issues; see the model-choice note in the smoke test section above.

## Troubleshooting

- **`500 Internal Server Error` / logs show `httpx.HTTPStatusError: ... 404 Not Found for url 'http://ollama:11434/api/chat'`**
  No model has been pulled into the Ollama container yet. Fix:

  ```bash
  docker compose exec ollama ollama pull llama3.1   # or whatever OLLAMA_MODEL/MONITOR_PROVIDER model is set to
  docker compose exec ollama ollama list             # confirm it's there
  ```

- **`ANTHROPIC_API_KEY is not set`**
  The `anthropic` provider is selected (for `/chat` or `MONITOR_PROVIDER`) but `ANTHROPIC_API_KEY` is empty. Set it and `docker compose restart agent`.

- **Monitoring loop logs `Prometheus query failed ... Name or service not known`**
  `PROMETHEUS_URL` isn't reachable from inside the container - e.g. Prometheus is stopped, or (if you're not using the bundled Prometheus) the URL isn't reachable from the `agent` container's network. `GET /status`'s `last_cycle` will show `"status": "query_failed"` for every affected rule once a cycle runs against the down instance - confirmed by directly stopping the bundled Prometheus container and observing this.

- **`GET /status` still shows `"ok"` right after stopping/breaking Prometheus (or right after any other change)**
  Not a bug - `last_cycle` only reflects the *last completed* monitoring cycle, up to `MONITOR_INTERVAL_SECONDS` old (60s by default). If you check immediately after a change, you're seeing the previous cycle's result; wait one full interval (or lower `MONITOR_INTERVAL_SECONDS` while testing) and check again.

- **A rule never fires even though the metric looks breached**
  Check `consecutive_breaches_required` (needs that many consecutive cycles) and `cooldown_seconds` (recently-fired rules stay quiet for a while) via `GET /status`'s `last_cycle` field, which shows each rule's current status (`ok`, `breached_pending_confirmation`, `cooldown`, `rate_limited`, `remediated`, `query_failed`, `no_data`).

- Check what's actually happening with:

  ```bash
  docker compose logs -f agent
  ```
