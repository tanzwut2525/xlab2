from agent.clients import prometheus_client
from agent.clients.prometheus_client import PrometheusError
from agent.tools.registry import Tool, registry


def query_prometheus(query: str) -> dict:
    try:
        samples = prometheus_client.query(query)
    except PrometheusError as exc:
        return {
            "query": query,
            "error": str(exc),
            "note": "The query failed. Do not infer or guess a value - report that the query failed.",
        }

    if not samples:
        return {
            "query": query,
            "results": [],
            "note": (
                "No series matched this query (empty result). This does not mean the "
                "value is 0 - it means there is no data at all, e.g. the target isn't "
                "being scraped. Report that no data was found - do not guess a value."
            ),
        }

    return {
        "query": query,
        "results": [{"metric": s.metric, "value": s.value, "timestamp": s.timestamp} for s in samples],
    }


registry.register(
    Tool(
        name="query_prometheus",
        description=(
            "Run an instant PromQL query against Prometheus and return the current "
            "value(s). Use this to investigate the state of a metric before or after "
            "taking a remediation action."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": 'A valid PromQL expression, e.g. \'up{job="api"}\'.',
                }
            },
            "required": ["query"],
        },
        handler=query_prometheus,
    )
)
