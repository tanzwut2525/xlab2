from agent.clients.kubernetes_client import KubernetesClient
from agent.clients.prometheus_client import PrometheusClient
from agent.config import config

# Module-level singletons: constructors do no I/O (Kubernetes config is loaded
# lazily on first real call, httpx.Client is lazy too), so it's safe to build
# these once at import time and share them between the LLM tool handlers and
# the monitoring loop's direct rule-evaluation queries.
prometheus_client = PrometheusClient(
    base_url=config.prometheus_url,
    timeout=config.prometheus_timeout,
    bearer_token=config.prometheus_bearer_token,
)

kubernetes_client = KubernetesClient(
    allowed_namespaces=config.k8s_allowed_namespaces,
    kubeconfig_path=config.kubeconfig_path,
    dry_run=config.dry_run,
    max_scale_replicas=config.max_scale_replicas,
)

__all__ = ["prometheus_client", "kubernetes_client", "KubernetesClient", "PrometheusClient"]
