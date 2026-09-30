"""Wait for compatible Brainstore capacity before starting an API container.

Uses only Python's standard library and read-only Kubernetes API requests.
The required version is fixed in the API Pod's environment, not a ConfigMap that
changes underneath an older API Pod when the next release is installed.
"""

import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


VERSION = re.compile(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
TERMINAL_PHASES = {"Failed", "Succeeded"}


def release_version(value):
    match = VERSION.fullmatch(value)
    if not match:
        raise ValueError(f"expected a stable release version, got {value!r}")
    return tuple(int(part) for part in match.groups())


def image_version(image):
    # Support registry ports and release tags accompanied by immutable digests.
    name = image.split("@", 1)[0].rsplit("/", 1)[-1]
    if ":" not in name:
        raise ValueError(f"image has no stable release tag: {image!r}")
    return release_version(name.rsplit(":", 1)[-1])


def compatible(image, minimum):
    try:
        version = image_version(image)
    except ValueError:
        return False
    # The supported contract is newer Brainstore releases in the same major.
    # Crossing a major boundary requires a separately designed migration.
    return version[0] == minimum[0] and version >= minimum


def container_image(pod_spec, name):
    return next(
        (container.get("image", "") for container in pod_spec.get("containers", [])
         if container.get("name") == name),
        "",
    )


def running_container(pod, name):
    return next(
        (container for container in pod.get("status", {}).get("containerStatuses", [])
         if container.get("name") == name and "running" in container.get("state", {})),
        None,
    )


def ready(pod, container):
    running = running_container(pod, container)
    return (
        pod.get("status", {}).get("phase") == "Running"
        and not pod.get("metadata", {}).get("deletionTimestamp")
        and running is not None and running.get("ready") is True
        and any(
            condition.get("type") == "Ready" and condition.get("status") == "True"
            for condition in pod.get("status", {}).get("conditions", [])
        )
    )


def check_deployment(deployment, pods, container, minimum):
    """Return a wait reason, or None when every potential backend is compatible.

    We deliberately do not require an exact image or a finished *compatible*
    rollout: older APIs must be able to restart while a later Brainstore release
    rolls. Incompatible terminating Pods still block, because their connections
    may remain usable until the process exits.
    """
    metadata = deployment.get("metadata", {})
    spec = deployment.get("spec", {})
    status = deployment.get("status", {})
    name = metadata.get("name", "unknown")
    if metadata.get("deletionTimestamp"):
        return f"{name}: deployment is being deleted"
    if status.get("observedGeneration", 0) < metadata.get("generation", 1):
        return f"{name}: controller has not observed the desired generation"
    desired = spec.get("replicas", 1)
    live = [pod for pod in pods if pod.get("status", {}).get("phase") not in TERMINAL_PHASES]
    if desired == 0:
        return f"{name}: waiting for scaled-down Pods to disappear" if live else None
    image = container_image(spec.get("template", {}).get("spec", {}), container)
    if not compatible(image, minimum):
        return f"{name}: desired image {image!r} is below or outside the required release"
    for pod in live:
        image = container_image(pod.get("spec", {}), container)
        if not compatible(image, minimum):
            pod_name = pod.get("metadata", {}).get("name", "unknown")
            return f"{name}: incompatible Pod {pod_name} still exists ({image!r})"
        running = running_container(pod, container)
        if running is not None and not compatible(running.get("image", ""), minimum):
            return f"{name}: a running container has not reached the required release"
    ready_count = sum(ready(pod, container) for pod in live)
    if ready_count < desired or status.get("availableReplicas", 0) < desired:
        return f"{name}: waiting for {desired} available compatible Pods ({ready_count} ready)"
    return None


class KubernetesClient:
    def __init__(self, namespace, credentials, request_timeout=5):
        host = os.environ["KUBERNETES_SERVICE_HOST"]
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        self.base_url = f"https://{host}:{port}"
        self.namespace = urllib.parse.quote(namespace, safe="")
        self.credentials = Path(credentials)
        self.request_timeout = request_timeout
        context = ssl.create_default_context(cafile=str(self.credentials / "ca.crt"))
        # Never send a Kubernetes service-account credential through an HTTP proxy.
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context)
        )

    def get(self, path, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("startup wait budget exhausted")
        # Projected tokens rotate; read the current token for every request.
        token = (self.credentials / "token").read_text().strip()
        request = urllib.request.Request(
            self.base_url + path,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        with self.opener.open(request, timeout=min(self.request_timeout, remaining)) as response:
            return json.load(response)

    def deployment(self, name, deadline):
        name = urllib.parse.quote(name, safe="")
        return self.get(f"/apis/apps/v1/namespaces/{self.namespace}/deployments/{name}", deadline)

    def pods(self, selector, deadline):
        items = []
        continuation = ""
        while True:
            query = urllib.parse.urlencode({
                "labelSelector": selector, "limit": 500, "continue": continuation,
            })
            page = self.get(f"/api/v1/namespaces/{self.namespace}/pods?{query}", deadline)
            items.extend(page.get("items", []))
            continuation = page.get("metadata", {}).get("continue", "")
            if not continuation:
                return items


def fleet_snapshot(client, targets, minimum, deadline):
    signature = []
    for target in targets:
        deployment = client.deployment(target["deployment"], deadline)
        # The chart's Services select app=<deployment name>. Include stray Pods
        # with that label too; they can receive traffic regardless of ownership.
        pods = client.pods("app=" + target["deployment"], deadline)
        reason = check_deployment(deployment, pods, target["container"], minimum)
        if reason:
            return reason, None
        # Confirm the same desired Deployment revisions, not identical Pod
        # membership. Compatible Pod churn must not strand an older API during
        # a later rollout once sufficient compatible capacity is available.
        signature.append((
            deployment["metadata"]["uid"], deployment["metadata"]["generation"],
        ))
    return None, signature


def wait_for_fleet(client, targets, minimum, timeout, interval, consecutive,
                  clock=time.monotonic, sleep=time.sleep, log=print):
    deadline = clock() + timeout
    successes = 0
    last_signature = None
    last_reason = "no observation yet"
    while clock() < deadline:
        try:
            reason, signature = fleet_snapshot(client, targets, minimum, deadline)
        except urllib.error.HTTPError as error:
            # RBAC and Deployments may be applied after the API Pod is created.
            reason, signature = f"Kubernetes API returned HTTP {error.code}", None
            error.close()
        except (OSError, ValueError, KeyError, TypeError) as error:
            reason, signature = f"Kubernetes observation failed: {type(error).__name__}", None
        if clock() >= deadline:
            break
        if reason is None:
            successes = successes + 1 if signature == last_signature else 1
            last_signature = signature
            if successes >= consecutive:
                log("Brainstore startup gate complete: compatible capacity is available", flush=True)
                return
            last_reason = f"confirming compatible capacity ({successes}/{consecutive})"
        else:
            successes = 0
            last_signature = None
            last_reason = reason
        log(f"Brainstore startup gate waiting: {last_reason}", flush=True)
        sleep(min(interval, max(0, deadline - clock())))
    raise TimeoutError(f"Brainstore startup gate timed out after {timeout}s: {last_reason}")


def main():
    minimum = release_version(os.environ["BRAINSTORE_MINIMUM_VERSION"])
    targets = json.loads(os.environ["BRAINSTORE_DEPLOYMENTS"])
    timeout = int(os.environ["BRAINSTORE_GATE_TIMEOUT_SECONDS"])
    interval = int(os.environ["BRAINSTORE_GATE_POLL_SECONDS"])
    consecutive = int(os.environ["BRAINSTORE_GATE_CONSECUTIVE_SUCCESSES"])
    if not targets or timeout <= 0 or interval <= 0 or consecutive < 2:
        raise ValueError("invalid startup gate configuration")
    client = KubernetesClient(os.environ["POD_NAMESPACE"], "/var/run/brainstore-gate")
    wait_for_fleet(client, targets, minimum, timeout, interval, consecutive)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(str(error), file=sys.stderr, flush=True)
        sys.exit(1)
