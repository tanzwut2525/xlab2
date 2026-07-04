import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


class PrometheusError(Exception):
    pass


@dataclass
class PrometheusSample:
    metric: dict
    value: float
    timestamp: float


class PrometheusClient:
    """Thin wrapper around the Prometheus HTTP API (instant queries only)."""

    def __init__(self, base_url: str, timeout: float = 10.0, bearer_token: str = "") -> None:
        headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, headers=headers)

    def query(self, promql: str) -> list[PrometheusSample]:
        try:
            response = self._client.get("/api/v1/query", params={"query": promql})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise PrometheusError(f"Prometheus query failed for '{promql}': {exc}") from exc

        payload = response.json()
        if payload.get("status") != "success":
            raise PrometheusError(f"Prometheus returned an error for '{promql}': {payload}")

        data = payload["data"]
        result_type = data["resultType"]

        if result_type == "vector":
            return [
                PrometheusSample(metric=item["metric"], value=float(item["value"][1]), timestamp=float(item["value"][0]))
                for item in data["result"]
            ]
        if result_type == "scalar":
            ts, val = data["result"]
            return [PrometheusSample(metric={}, value=float(val), timestamp=float(ts))]

        raise PrometheusError(f"Unsupported Prometheus result type '{result_type}' for query '{promql}'")

    def latest_value(self, promql: str) -> float | None:
        """Convenience helper for threshold rules: returns the first sample's
        value, or None if the query matched no series."""
        samples = self.query(promql)
        if not samples:
            return None
        if len(samples) > 1:
            logger.warning(
                "Query '%s' matched %d series; using the first one (%s). "
                "Consider aggregating (sum/max/min) in the query.",
                promql, len(samples), samples[0].metric,
            )
        return samples[0].value
