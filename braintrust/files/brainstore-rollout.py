"""Observe the exact Brainstore configuration for one Helm release.

ROLLOUT_TARGETS is a JSON array of deployment/container/revision/image/configChecksum
objects. POD_NAMESPACE selects the namespace; ROLLOUT_TIMEOUT_SECONDS and
ROLLOUT_POLL_SECONDS bound the entire fleet observation. Kubernetes credentials
are projected at /var/run/migration-gate; brainstore_gate.py supplies shared helpers.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse

from brainstore_gate import KubernetesClient, TERMINAL_PHASES, container_image, ready


RELEASE_ANNOTATION = "braintrust.dev/release-revision"
CONFIG_ANNOTATION = "checksum/config"


def configuration_mismatch(resource, target):
    annotations = resource.get("metadata", {}).get("annotations", {})
    if annotations.get(RELEASE_ANNOTATION) != target["revision"]:
        return "release revision"
    if annotations.get(CONFIG_ANNOTATION) != target["configChecksum"]:
        return "config checksum"
    if container_image(resource.get("spec", {}), target["container"]) != target["image"]:
        return "image"
    return None


def check_deployment(deployment, pods, target):
    """Return a wait reason until the desired spec and every backend have rolled."""
    metadata = deployment.get("metadata", {})
    spec = deployment.get("spec", {})
    status = deployment.get("status", {})
    name = target["deployment"]
    if metadata.get("deletionTimestamp"):
        return f"{name}: deployment is being deleted"
    mismatch = configuration_mismatch(spec.get("template", {}), target)
    if mismatch:
        return f"{name}: deployment template {mismatch} does not match the expected release"
    if status.get("observedGeneration", 0) < metadata.get("generation", 1):
        return f"{name}: controller has not observed the desired generation"

    # Scaling is independent of Helm: use the current spec, preserving explicit zero.
    desired = spec.get("replicas", 1)
    live = [pod for pod in pods if pod.get("status", {}).get("phase") not in TERMINAL_PHASES]
    for pod in live:
        pod_name = pod.get("metadata", {}).get("name", "unknown")
        if pod.get("metadata", {}).get("deletionTimestamp"):
            return f"{name}: terminating Pod {pod_name} still exists"
        mismatch = configuration_mismatch(pod, target)
        if mismatch:
            return f"{name}: Pod {pod_name} {mismatch} does not match the expected release"
    if len(live) != desired:
        return f"{name}: waiting for exactly {desired} active service backends ({len(live)} remain)"
    ready_count = sum(ready(pod, target["container"]) for pod in live)
    if ready_count != desired:
        return f"{name}: waiting for {desired} ready updated Pods ({ready_count} ready)"

    # Availability includes minReadySeconds; total replicas excludes old surges.
    # Kubernetes omits zero-valued counters, so a fully scaled-down role can pass.
    for counter in ("updatedReplicas", "readyReplicas", "availableReplicas", "replicas"):
        actual = status.get(counter, 0)
        if actual != desired:
            return f"{name}: rollout incomplete ({counter}={actual}, desired={desired})"
    return None


def check_fleet(client, targets, deadline):
    for target in targets:
        deployment = client.deployment(target["deployment"], deadline)
        # Services route by app, not owner reference. Include stray and terminating
        # Pods, including those from a previous ReplicaSet or release revision.
        pods = client.pods("app=" + target["deployment"], deadline)
        reason = check_deployment(deployment, pods, target)
        if reason:
            return reason
    return None


def wait_for_fleet(client, targets, timeout, interval,
                   clock=time.monotonic, sleep=time.sleep, log=print):
    deadline = clock() + timeout
    successes = 0
    last_reason = "no observation yet"
    while clock() < deadline:
        try:
            reason = check_fleet(client, targets, deadline)
        except urllib.error.HTTPError as error:
            # The deployment or read-only RBAC may not have been applied yet.
            # Never include API response bodies, credentials, or query strings.
            path = urllib.parse.urlsplit(error.url).path[:256]
            reason = f"Kubernetes API returned HTTP {error.code} for {path}"
            error.close()
        except (OSError, ValueError, KeyError, TypeError) as error:
            reason = f"Kubernetes observation failed: {type(error).__name__}"
        if clock() >= deadline:
            last_reason = reason or "deadline expired before rollout confirmation"
            break
        if reason is None:
            successes += 1
            if successes == 2:
                log("Brainstore rollout complete: exact release configuration is ready", flush=True)
                return
            last_reason = "confirming exact release configuration (1/2)"
        else:
            successes = 0
            last_reason = reason
        log(f"Brainstore rollout waiting: {last_reason}", flush=True)
        sleep(min(interval, max(0, deadline - clock())))
    raise TimeoutError(f"Brainstore rollout timed out after {timeout}s: {last_reason}")


def main():
    targets = json.loads(os.environ["ROLLOUT_TARGETS"])
    timeout = int(os.environ["ROLLOUT_TIMEOUT_SECONDS"])
    interval = int(os.environ["ROLLOUT_POLL_SECONDS"])
    fields = ("deployment", "container", "revision", "image", "configChecksum")
    if (not isinstance(targets, list) or not targets
            or any(not isinstance(target, dict)
                   or any(not isinstance(target.get(field), str) or not target[field]
                          for field in fields) for target in targets)
            or timeout <= 0 or interval <= 0 or interval >= timeout):
        raise ValueError("invalid Brainstore rollout configuration")
    client = KubernetesClient(os.environ["POD_NAMESPACE"], "/var/run/migration-gate")
    wait_for_fleet(client, targets, timeout, interval)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(str(error), file=sys.stderr, flush=True)
        sys.exit(1)
