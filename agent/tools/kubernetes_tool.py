from agent.clients import kubernetes_client
from agent.clients.kubernetes_client import KubernetesError, RemediationNotAllowed
from agent.tools.registry import Tool, registry


def restart_deployment(namespace: str, name: str) -> dict:
    try:
        return kubernetes_client.restart_deployment(namespace, name)
    except (KubernetesError, RemediationNotAllowed) as exc:
        return {"error": str(exc)}


def scale_deployment(namespace: str, name: str, replicas: int) -> dict:
    try:
        return kubernetes_client.scale_deployment(namespace, name, replicas)
    except (KubernetesError, RemediationNotAllowed) as exc:
        return {"error": str(exc)}


def delete_pod(namespace: str, name: str) -> dict:
    try:
        return kubernetes_client.delete_pod(namespace, name)
    except (KubernetesError, RemediationNotAllowed) as exc:
        return {"error": str(exc)}


def get_deployment_status(namespace: str, name: str) -> dict:
    try:
        return kubernetes_client.get_deployment_status(namespace, name)
    except (KubernetesError, RemediationNotAllowed) as exc:
        return {"error": str(exc)}


registry.register(
    Tool(
        name="restart_deployment",
        description=(
            "Trigger a rolling restart of a Kubernetes Deployment by patching its pod "
            "template annotations. Only works for namespaces in the configured "
            "allowlist; use this as the least disruptive remediation for a hung or "
            "crash-looping service."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "name": {"type": "string", "description": "Deployment name."},
            },
            "required": ["namespace", "name"],
        },
        handler=restart_deployment,
    )
)

registry.register(
    Tool(
        name="scale_deployment",
        description=(
            "Scale a Kubernetes Deployment to a specific number of replicas. The "
            "replica count is clamped to a configured maximum regardless of what "
            "you request."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "name": {"type": "string"},
                "replicas": {"type": "integer", "minimum": 0},
            },
            "required": ["namespace", "name", "replicas"],
        },
        handler=scale_deployment,
    )
)

registry.register(
    Tool(
        name="delete_pod",
        description=(
            "Delete a single Kubernetes Pod so its controller recreates it. Useful "
            "for a single crash-looping/stuck pod when restarting the whole "
            "Deployment would be more disruptive than necessary."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "name": {"type": "string", "description": "Pod name."},
            },
            "required": ["namespace", "name"],
        },
        handler=delete_pod,
    )
)

registry.register(
    Tool(
        name="get_deployment_status",
        description="Get the replica status of a Kubernetes Deployment (desired/ready/available/updated).",
        input_schema={
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "name": {"type": "string"},
            },
            "required": ["namespace", "name"],
        },
        handler=get_deployment_status,
    )
)
