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
    # This assumes Brainstore preserves compatibility with older APIs within
    # a major release; version numbers alone cannot establish that contract.
    # Unknown tags and major-version migrations must not pass this gate.
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

    Deployment rollouts replace Pods, so use each Pod's declared release tag.
    Container status can report a different alias or only a digest. Neither is
    a portable release identifier; manual in-place Pod image changes are outside
    this check's contract.
    """
    metadata = deployment.get("metadata", {})
    spec = deployment.get("spec", {})
    status = deployment.get("status", {})
    name = metadata.get("name", "unknown")
    if metadata.get("deletionTimestamp"):
        return f"{name}: deployment is being deleted"
    # Do not trust availability status from before the latest spec update.
    if status.get("observedGeneration", 0) < metadata.get("generation", 1):
        return f"{name}: controller has not observed the desired generation"
    desired = spec.get("replicas", 1)
    live = [pod for pod in pods if pod.get("status", {}).get("phase") not in TERMINAL_PHASES]
    if desired == 0:
        return f"{name}: waiting for scaled-down Pods to disappear" if live else None
    # Check desired state too: compatible Pods are insufficient if the
    # Deployment is about to replace them with an incompatible image.
    image = container_image(spec.get("template", {}).get("spec", {}), container)
    if not compatible(image, minimum):
        return f"{name}: desired image {image!r} is below or outside the required release"
    for pod in live:
        image = container_image(pod.get("spec", {}), container)
        if not compatible(image, minimum):
            pod_name = pod.get("metadata", {}).get("name", "unknown")
            return f"{name}: incompatible Pod {pod_name} still exists ({image!r})"
    # Require both current Pod readiness and Deployment availability; the
    # latter also accounts for the Deployment's minReadySeconds setting.
    ready_count = sum(ready(pod, container) for pod in live)
    if ready_count < desired or status.get("availableReplicas", 0) < desired:
        return f"{name}: waiting for {desired} available compatible Pods ({ready_count} ready)"
    return None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        # API reads must not forward this Pod's bearer token to another endpoint.
        raise urllib.error.HTTPError(request.full_url, code, message, headers, response)


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
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=context),
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
        # Inspect every matching backend, including those beyond the first page.
        while True:
            query = urllib.parse.urlencode({
                "labelSelector": selector, "limit": 500, "continue": continuation,
            })
            page = self.get(f"/api/v1/namespaces/{self.namespace}/pods?{query}", deadline)
            items.extend(page.get("items", []))
            continuation = page.get("metadata", {}).get("continue", "")
            if not continuation:
                return items


def check_fleet(client, targets, minimum, deadline):
    for target in targets:
        deployment = client.deployment(target["deployment"], deadline)
        # The chart's Services select app=<deployment name>. Include stray Pods
        # with that label too; they can receive traffic regardless of ownership.
        pods = client.pods("app=" + target["deployment"], deadline)
        reason = check_deployment(deployment, pods, target["container"], minimum)
        if reason:
            return reason
    return None


def wait_for_fleet(client, targets, minimum, timeout, interval,
                  clock=time.monotonic, sleep=time.sleep, log=print):
    # All roles and API requests share one budget, unaffected by wall-clock changes.
    deadline = clock() + timeout
    successes = 0
    last_reason = "no observation yet"
    while clock() < deadline:
        try:
            reason = check_fleet(client, targets, minimum, deadline)
        except urllib.error.HTTPError as error:
            # RBAC and Deployments may be applied after the API Pod is created.
            path = urllib.parse.urlsplit(error.url).path
            reason = f"Kubernetes API returned HTTP {error.code} for {path}"
            error.close()
        except (OSError, ValueError, KeyError, TypeError) as error:
            reason = f"Kubernetes observation failed: {type(error).__name__}"
        if clock() >= deadline:
            break
        # Confirm the entire fleet twice, one polling interval apart. These are
        # observations, not an atomic snapshot or a lock on subsequent rollouts.
        if reason is None:
            successes += 1
            if successes == 2:
                log("Brainstore startup gate complete: compatible capacity is available", flush=True)
                return
            last_reason = "confirming compatible capacity (1/2)"
        else:
            # An unhealthy role or failed API read resets the confirmation count.
            successes = 0
            last_reason = reason
        log(f"Brainstore startup gate waiting: {last_reason}", flush=True)
        sleep(min(interval, max(0, deadline - clock())))
    raise TimeoutError(f"Brainstore startup gate timed out after {timeout}s: {last_reason}")


def main():
    minimum = release_version(os.environ["BRAINSTORE_MINIMUM_VERSION"])
    targets = json.loads(os.environ["BRAINSTORE_DEPLOYMENTS"])
    timeout = int(os.environ["BRAINSTORE_GATE_TIMEOUT_SECONDS"])
    interval = int(os.environ["BRAINSTORE_GATE_POLL_SECONDS"])
    if not targets or timeout <= 0 or interval <= 0 or interval >= timeout:
        raise ValueError("invalid startup gate configuration")
    client = KubernetesClient(os.environ["POD_NAMESPACE"], "/var/run/brainstore-gate")
    wait_for_fleet(client, targets, minimum, timeout, interval)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(str(error), file=sys.stderr, flush=True)
        # Fail closed: Kubernetes retries this init container; the API stays stopped.
        sys.exit(1)
