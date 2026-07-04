import logging
from datetime import datetime, timezone

from kubernetes import client as k8s_client_lib
from kubernetes import config as k8s_config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)


class KubernetesError(Exception):
    pass


class RemediationNotAllowed(Exception):
    pass


class KubernetesClient:
    """Wraps the Kubernetes Python client for the handful of remediation
    actions this agent supports. Every mutating method is namespace-gated by
    an allowlist that fails closed (an empty allowlist allows nothing), and
    every mutating method honors dry_run before it ever tries to load a
    kubeconfig or reach a cluster.
    """

    def __init__(
        self,
        allowed_namespaces: list[str],
        kubeconfig_path: str = "",
        dry_run: bool = True,
        max_scale_replicas: int = 10,
    ) -> None:
        self._allowed_namespaces = set(allowed_namespaces)
        self._kubeconfig_path = kubeconfig_path
        self._dry_run = dry_run
        self._max_scale_replicas = max_scale_replicas
        self._loaded = False
        self._apps_v1: k8s_client_lib.AppsV1Api | None = None
        self._core_v1: k8s_client_lib.CoreV1Api | None = None

    def _check_namespace(self, namespace: str) -> None:
        # Fail closed: an empty/unset allowlist means nothing is allowed.
        if not self._allowed_namespaces or namespace not in self._allowed_namespaces:
            raise RemediationNotAllowed(
                f"Namespace '{namespace}' is not in the allowed list {sorted(self._allowed_namespaces)}"
            )

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        try:
            k8s_config.load_incluster_config()
            logger.info("Loaded in-cluster Kubernetes config")
        except k8s_config.ConfigException:
            k8s_config.load_kube_config(config_file=self._kubeconfig_path or None)
            logger.info("Loaded local kubeconfig")
        self._apps_v1 = k8s_client_lib.AppsV1Api()
        self._core_v1 = k8s_client_lib.CoreV1Api()
        self._loaded = True

    def restart_deployment(self, namespace: str, name: str) -> dict:
        self._check_namespace(namespace)
        if self._dry_run:
            logger.warning("[DRY RUN] Would restart deployment %s/%s", namespace, name)
            return {"status": "dry_run", "action": "restart_deployment", "namespace": namespace, "name": name}

        self._ensure_loaded()
        now = datetime.now(timezone.utc).isoformat()
        patch = {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": now}}}}}
        try:
            self._apps_v1.patch_namespaced_deployment(name=name, namespace=namespace, body=patch)
        except ApiException as exc:
            raise KubernetesError(f"Failed to restart deployment {namespace}/{name}: {exc}") from exc

        logger.info("Triggered rollout restart for deployment %s/%s", namespace, name)
        return {
            "status": "restarted",
            "action": "restart_deployment",
            "namespace": namespace,
            "name": name,
            "restarted_at": now,
        }

    def scale_deployment(self, namespace: str, name: str, replicas: int) -> dict:
        self._check_namespace(namespace)
        # Hard ceiling regardless of what the LLM asked for.
        clamped = max(0, min(int(replicas), self._max_scale_replicas))

        if self._dry_run:
            logger.warning("[DRY RUN] Would scale deployment %s/%s to %d replicas", namespace, name, clamped)
            return {
                "status": "dry_run",
                "action": "scale_deployment",
                "namespace": namespace,
                "name": name,
                "replicas": clamped,
            }

        self._ensure_loaded()
        try:
            self._apps_v1.patch_namespaced_deployment_scale(
                name=name, namespace=namespace, body={"spec": {"replicas": clamped}}
            )
        except ApiException as exc:
            raise KubernetesError(f"Failed to scale deployment {namespace}/{name}: {exc}") from exc

        logger.info("Scaled deployment %s/%s to %d replicas", namespace, name, clamped)
        return {"status": "scaled", "action": "scale_deployment", "namespace": namespace, "name": name, "replicas": clamped}

    def delete_pod(self, namespace: str, name: str) -> dict:
        self._check_namespace(namespace)
        if self._dry_run:
            logger.warning("[DRY RUN] Would delete pod %s/%s", namespace, name)
            return {"status": "dry_run", "action": "delete_pod", "namespace": namespace, "name": name}

        self._ensure_loaded()
        try:
            self._core_v1.delete_namespaced_pod(name=name, namespace=namespace)
        except ApiException as exc:
            raise KubernetesError(f"Failed to delete pod {namespace}/{name}: {exc}") from exc

        logger.info("Deleted pod %s/%s", namespace, name)
        return {"status": "deleted", "action": "delete_pod", "namespace": namespace, "name": name}

    def get_deployment_status(self, namespace: str, name: str) -> dict:
        """Read-only, so it is never blocked by dry_run. In dry-run mode with
        no reachable cluster (e.g. the local demo), returns a synthetic
        'unknown' status instead of raising, so the full loop is runnable
        with zero Kubernetes setup."""
        self._check_namespace(namespace)

        try:
            self._ensure_loaded()
        except Exception as exc:
            if self._dry_run:
                return {
                    "status": "unknown",
                    "dry_run": True,
                    "namespace": namespace,
                    "name": name,
                    "note": f"no cluster configured: {exc}",
                }
            raise KubernetesError(f"Failed to load Kubernetes config: {exc}") from exc

        try:
            deployment = self._apps_v1.read_namespaced_deployment(name=name, namespace=namespace)
        except ApiException as exc:
            raise KubernetesError(f"Failed to read deployment {namespace}/{name}: {exc}") from exc

        status = deployment.status
        return {
            "namespace": namespace,
            "name": name,
            "replicas": status.replicas or 0,
            "ready_replicas": status.ready_replicas or 0,
            "available_replicas": status.available_replicas or 0,
            "updated_replicas": status.updated_replicas or 0,
        }
